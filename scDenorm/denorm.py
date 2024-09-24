# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_scDenorm.ipynb.

# %% auto 0
__all__ = ['logger', 'formatter', 'console_handler', 'scdenorm', 'denorm', 'solve_bc', 'auto_detect', 'unscale_mat',
           'select_base', 'check_unscale', 'get_scaling_factor', 'get_scaling_factor_by_top2',
           'get_scaling_factor_by_reg', 'solve_s', 'check_plot']

# %% ../nbs/00_scDenorm.ipynb 4
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import scanpy as sc
from anndata import AnnData
from scipy.sparse import diags,issparse,csr_matrix,isspmatrix_csr
from scipy.optimize import minimize
from scipy.io import mmwrite
from tqdm import tqdm
from pathlib import Path
from fastcore.script import *
import multiprocessing


import logging
import colorlog

# Create a logger
logger = logging.getLogger('my_logger')
logger.setLevel(logging.DEBUG)
#logging.getLogger().setLevel(logging.INFO)

# Create a colorlog formatter
formatter = colorlog.ColoredFormatter(
    '%(log_color)s%(levelname)s:%(name)s:%(message)s',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'black',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    }
)

# Create a console handler and set the formatter
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Add the console handler to the logger
logger.addHandler(console_handler)

# Log messages with different levels
#logger.debug('This is a debug message')
#logger.info('This is an info message')
#logger.warning('This is a warning message')
#logger.error('This is an error message')
#logger.critical('This is a critical message')

# %% ../nbs/00_scDenorm.ipynb 6
@call_parse
def scdenorm(fin: str,  # The input file or AnnData
             fout: str = None,  # The path of output file if provided
             by: str = None, # Split AnnData by sample or batch
             gxc: bool = False,  # Change to True if the data is stored with gene by cell
             base: float or str = None, # Give the base if it is known or auto
             cont: float = 1.0,  # The constant plused after scaling
             rint: bool = True,  # Round elements of the result to the nearest integer
             method: str = 'Top2',  # Top2 or Reg
             float16: bool = False, # Coverting value to float16 for preventing error in numerical calculation
             cutoff: float = 0.05,
             verbose: int = 0):  
    """
    denormalize takes a cell * gene expression matrix that has been normalized according to single-cell RNA
    sequencing preprocessing procedure and return a recovered count matrix by reversing the logarithmization step
    and then the total-count normalization step utilizing the unscale function. If the input matrix is not
    normalized by first total-count scaling then logarithmization, then the function produces an error
    indicating so. We also handle matrices that have not been logarithmized.
    denormalize: csr_matrix -> csr_matrix
    """
    levels = [logging.WARNING, logging.INFO, logging.DEBUG]
    logger.setLevel(levels[verbose] if verbose < 3 else logging.INFO)
    if isinstance(fin, str):
        fin = Path(fin)
        logger.info(f'Reading input file: {fin}')
        ad = sc.read(fin)
    elif isinstance(fin, AnnData):
        ad = fin.copy()
    else:
        raise logger.error("Invalid input format. Anndata file path or Anndata object.")

    #make sure ad.X have data and is dense matrix
    if ad.shape[0]<1 or ad.shape[1]<1:
        raise logger.error("The anndata don't have cells or genes")
    if issparse(ad.X):
        ad.X.eliminate_zeros() #remove potential 0s.
        if not isspmatrix_csr(ad.X):
            ad.X = csr_matrix(ad.X)
    else:
        ad.X = csr_matrix(ad.X)

    arr = ad.X.data
    if np.array_equal(arr, np.round(arr)):
        logger.info(f"All counts are integers")
    elif np.average(np.abs(arr-np.round(arr)))<cutoff:
        logger.info(f"All counts are close to integers. Rounding them to intergers")
        arr=np.round(arr)
    else:
        if gxc: #if data is gene by cell
            logger.info(f'The data is gene by cell. It is transposed to cell by gene {ad.T.shape}')
            ad=ad.T
        logger.info(f'The dimensions of this data are {ad.shape}.')
            
        # Check if the data is split by sample
        if by is not None and by in ad.obs.columns:
            samples = ad.obs[by].unique()
            result = []
            for sample in samples:
                logger.info(f"Processing sample: {sample}")
                sample_ad = ad[ad.obs['sample'] == sample].copy()
                sample_ad = denorm(sample_ad, gxc, base, cont, rint, method, float16, cutoff, logger)
                result.append(sample_ad)
            ad = sc.concat(result, axis=0)
        else:
            ad = denorm(ad, gxc, base, cont, rint, method, float16, cutoff, logger)
    
    if fout is not None:
        #write output
        logger.info(f'Writing output file: {fout}')
        if fout.endswith('.mtx'):
            mmwrite(fout, ad.X, field = 'integer')
        else:
            ad.write(fout) 
    else:
        return ad


def denorm(ad:AnnData, # The input AnnData
             gxc:bool = False, # Change to True if the data is stored with gene by cell
             base:float or str = None, # Give the base if it is known or auto
             cont:float = 1.0, # The constant plused after scaling
             rint:bool = True, # Round elements of the result to the nearest integer
             method:str = 'Top2', # Top2 or Reg
             float16: bool = False,
             cutoff:float = 0.05, 
             logger: logging = None):
    """
    denormalize takes a cell * gene expression matrix that has been normalized according to single-cell RNA 
    sequencing preprocessing procedure and return a recovered count matrix by reversing the logarithmization step
    and then the total-count normalization step utilizing the unscale function. If the imput matrix is not 
    normalized by first total-count scaling then logarithmization, then the function produces an error
    indicating so. We also handle matrices that have not been logarithmized.
    denormalize: csr_matrix -> csr_matrix
    """
    smtx=ad.X  #must be cell by gene
    
    #1.de-transformation
    #select base and denormlize
    if base=='auto':
        base,cont = auto_detect(smtx,1e-6)
    elif base is None:
        logger.info('Selecting base')
        base,cont = select_base(smtx.getrow(0).data.copy(),cont,cutoff,method,float16)
        if base is None and cont is None:
            #fully auto detect
            base,cont = auto_detect(smtx,1e-6)
    
    #2.de-normalization
    if check_unscale(smtx.getrow(0).data.copy(),base,cont,cutoff,method,float16):
        logger.info(f'Denormlizing ...the base is {base}')
        counts,success_cells=unscale_mat(smtx,base,cont,cutoff,rint,method,float16)
        ad=ad[success_cells].copy() #filter failed cells
    else:
        logger.error('Denormlization has failed. Output the orignal data')
        counts=smtx
        
    counts.data=counts.data.astype(ad.X.dtype)
    ad.X=counts
    
    return ad      

# %% ../nbs/00_scDenorm.ipynb 7
def solve_bc(p,y1,y2):
    return sum((np.power(p[0],y2)-2*np.power(p[0],y1)+p[1])**2)

# %% ../nbs/00_scDenorm.ipynb 8
def auto_detect(smtx,cutoff=1e-6):
    ys=[]
    for c_idx in range(100):
        try:
            N = 2
            c = pd.Series(smtx.getrow(c_idx).data)
            y = np.array(c.value_counts().sort_index().head(N).index)
            ys.append(y)
        except:
            print(c_idx)

    ys=np.array(ys)

    y1=ys[:,0]
    y2=ys[:,1]

    res = minimize(solve_bc, [20,1], method='L-BFGS-B', tol=cutoff,bounds=[(2, None), (1e-6, None)],args=(ys[:,0],ys[:,1]))
    print(res)
    return res.x

# %% ../nbs/00_scDenorm.ipynb 9
def unscale_mat(smtx,base=np.e,cont=1,cutoff=0.05,rint=True,method='Top2',f16=False,gpu=False):
    """
    unscale takes a cell * gene expression matrix that has been quality-controlled and scaled according to 
    single-cell RNA sequencing preprocessing procedure and return a recovered count matrix 
    by finding the scaling factor for each cell,
    or produce an error indicating the matrix has not been processed by our assumption.
    unscale: csr_matrix -> csr_matrix
    """
    scaled_counts=smtx.copy()
    if base is not None:
        scaled_counts.data = base ** scaled_counts.data - cont
    else:
        #without transformation
        scaled_counts.data = scaled_counts.data - cont
        
    #get scale factors
    scaling_factors,success_cells=[],[]
    for i in tqdm(range(scaled_counts.shape[0])):
        try:
            scaling_factors.append(get_scaling_factor(scaled_counts.getrow(i).data,cutoff,method,f16))
            success_cells.append(i)
        except:
            logger.warning(f"Warning:The cell {i} fails to denormlize, and be deleted")
    
    # Remove failed cells from the scaled_counts matrix
    scaled_counts=scaled_counts[success_cells,:]
    if gpu:
        pass
    else:
        scaling_factors = diags(scaling_factors)
        counts = scaling_factors*scaled_counts
    if rint:
        counts=counts.rint()
    counts.sort_indices()  #keep the increase order of indices
    return counts,success_cells

def select_base(x,cont=1,cutoff=0.05,method='Top2',f16=False,plot=False):
    for b in [np.e,None,2,10]:
        print('b is', b)
        if cont is None:
            for c in [1,0.1,0.01,0.001,0]:
                if check_unscale(x,b,c,cutoff,method,f16,plot):
                    return b,c
        else:
            if check_unscale(x,b,cont,cutoff,method,f16,plot):
                return b,cont
    return None,None

def check_unscale(x,base=np.e,cont=1,cutoff=0.05,method='Top2',f16=False,plot=True):
    if base is not None:
        x=base**x-cont
    else:
        x=x-cont  #without transformation
    try:
        get_scaling_factor(x,cutoff,method,f16)
        return True
    except:
        if plot:
            logger.info(f'The base {base} is not match with the data.')
            check_plot(pd.Series(x),base)
    return False

def get_scaling_factor(x,cutoff=0.05,method='Top2',f16=False):
    """
    get_scaling_factor takes a cell vector and its index in the gene expression matrix 
    that has been scaled according to single-cell RNA sequencing preprocessing procedure 
    and return the scaling factor for the cell,
    or produce an error indicating the cell has not been processed by our assumption.
    get_scaling_factor: ndarray Nat (Num) -> Num
    """
    if method=='Top2':
        s=get_scaling_factor_by_top2(x,cutoff,f16)
    elif method=='Reg':
        s=get_scaling_factor_by_reg(x,cutoff,f16)
    else:
        raise logger.error("Please choose Top2 or Reg methods")
    return s
    
def get_scaling_factor_by_top2(x,cutoff=0.05,f16=False):
    """
    get_scaling_factor takes a cell vector and its index in the gene expression matrix 
    that has been scaled according to single-cell RNA sequencing preprocessing procedure 
    and return the scaling factor for the cell,
    or produce an error indicating the cell has not been processed by our assumption.
    """
    if f16:
        x=x.astype('float16') #To prevent error in numerical calculation
    x=pd.Series(x).value_counts().sort_index()
    if x.shape[0]<2:
        raise logger.warning(f"Cell has only one value. {x}")
    if not np.alltrue(x.index[:2]==x.sort_values(ascending=False).index[:2]):
        raise logger.warning(f"TOP 2 counts and ranks are not consistent. {x}")
    x=np.array(x.index)
    xs=x
    xm=x[0]
    x=x/xm
    if np.abs(x-x.round()).mean()>cutoff:
        raise logger.info(f"Failed to obtain scale factor. Error is {np.abs(x-x.round()).mean()}. x is {x}")
    return 1/xm #1/(xs[1]-xs[0])


def get_scaling_factor_by_reg(x,cutoff=0.05,f16=False):
    """
    get_scaling_factor takes a cell vector and its index in the gene expression matrix 
    that has been scaled according to single-cell RNA sequencing preprocessing procedure 
    and return the scaling factor for the cell,
    or produce an error indicating the cell has not been processed by our assumption.
    """
    if f16:
        x=x.astype('float16') #To prevent error in numerical calculation
    x=pd.Series(x).value_counts().sort_index()
    if x.shape[0]<2:
        raise logger.warning(f"Cell has only one value. {x}")
    if not np.alltrue(x.index[:2]==x.sort_values(ascending=False).index[:2]):
        raise logger.warning(f"TOP 2 counts and ranks are not consistent. {x}")
    xs=x
    x=np.array(xs.index)
    for i in range(3,10): # try different number of ranks
        y = np.array(xs.head(i).index)
        c = np.arange(1, y.shape[0]+1)
        res = minimize(solve_s,[1], method='L-BFGS-B', tol=1e-5,args=(c,y))
        if res.fun<1e-5:
            x=x/res.x[0]
            if np.abs(x-x.round()).mean()<cutoff:
                return 1/res.x[0]
    x=x/res.x[0]
    if np.abs(x-x.round()).mean()>cutoff:
        raise logger.info(f"Failed to obtain scale factor. Error is {np.abs(x-x.round()).mean()}. x is {x}")
    return 1/res.x[0]

def solve_s(s,C,X):
    return sum((C*s-X)**2)

# %% ../nbs/00_scDenorm.ipynb 12
def check_plot(c,idx,n=10):
    """
    Check_plot takes a cell vector and its index in the gene expression matrix and produce a plot of the first
    N most frequent values against their ranks. Such a plot is used for error-checking in the unscaling process.
    """
    y = np.array(c.value_counts().sort_index().head(n).index)
    x = np.arange(1, n+1)
    plt.scatter(x, y, label=f'Base {idx}')
    plt.legend()
    plt.xlabel('Rank in cell histogram')
    plt.ylabel('Scaled count')
    plt.xticks(x)
    plt.show()
