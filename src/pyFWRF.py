########################################################################
### PACKAGE VERSIONS:												 ###
### theano: 	0.8.2												 ###
### lasagne: 	0.2dev1												 ###
### numpy:
###                               
########################################################################

import sys
import struct
import time
import numpy as np
from tqdm import tqdm
import pickle
import math

import theano
import theano.tensor as T

import lasagne
import lasagne.layers as L
import lasagne.regularization as R
import lasagne.nonlinearities as NL
import lasagne.objectives as O
import lasagne.init as I

import src.pyNumpyUtility as pnu
import src.pyLasagneUtility as plu


fpX = np.float32
print "theano floatX: %s" % theano.config.floatX
print "numpy floatX: %s" % fpX

########################################################################
###              SUPPORT FUNCTIONS                                   ###
########################################################################

class subdivision_1d(object):
    def __init__(self, n_div=1, dtype=np.float32):
        self.length = n_div
        self.dtype = dtype
        
    def __call__(self, center, width):
        '''	returns a list of point positions '''
        return [center] * self.length
    
class linspace(subdivision_1d):    
    def __init__(self, n_div, right_bound=False, dtype=np.float32, **kwargs):
        super(linspace, self).__init__(n_div, dtype=np.float32, **kwargs)
        self.__rb = right_bound
        
    def __call__(self, center, width):
        if self.length<=1:
            return [center]     
        if self.__rb:
            d = width/(self.length-1)
            vmin, vmax = center, center+width  
        else:
            d = width/self.length
            vmin, vmax = center+(d-width)/2, center+width/2 
        return np.arange(vmin, vmax+1e-12, d).astype(dtype=self.dtype)
    
class logspace(subdivision_1d):    
    def __init__(self, n_div, dtype=np.float32, **kwargs):
        super(logspace, self).__init__(n_div, dtype=np.float32, **kwargs)
               
    def __call__(self, start, stop):    
        if self.length <= 1:
            return [start]
        lstart = np.log(start+1e-12)
        lstop = np.log(stop+1e-12)
        dlog = (lstop-lstart)/(self.length-1)
        return np.exp(np.arange(lstart, lstop+1e-12, dlog)).astype(self.dtype)



def iterate_range(start, length, batchsize):
    batch_count = length // batchsize 
    residual = length % batchsize
    for i in range(batch_count):
        yield range(start+i*batchsize, start+(i+1)*batchsize),batchsize
    if(residual>0):
        yield range(start+batch_count*batchsize,start+length),residual

def iterate_bounds(start, length, batchsize):
    batch_count = length // batchsize 
    residual = length % batchsize
    for i in range(batch_count):
        yield [start+i*batchsize, start+(i+1)*batchsize], batchsize
    if(residual>0):
        yield [start+batch_count*batchsize, start+length], residual	

def iterate_slice(start, length, batchsize):
    batch_count = length // batchsize 
    residual = length % batchsize
    for i in range(batch_count):
        yield slice(start+i*batchsize, start+(i+1)*batchsize), batchsize
    if(residual>0):
        yield slice(start+batch_count*batchsize,start+length), residual
        
def slice_arraylist(inputs, excerpt):            
    return [i[excerpt] for i in inputs]  

def iterate_minibatches(inputs, targets, batchsize):
    '''return inputs.shape[0]//batchsize batches plus one residual batches smaller than batchsize if needed'''
    assert len(inputs) == len(targets)
    for start_idx in range(0, len(inputs), batchsize):
        excerpt = slice(start_idx, start_idx+batchsize)
        yield inputs[excerpt], targets[excerpt]
        
def iterate_multiminibatches(inputs, targets, batchsize):
    '''return inputs.shape[0]//batchsize batches plus one residual batches smaller than batchsize if needed'''
    length = len(targets)
    batch_count = len(targets) // batchsize 
    residual = length % batchsize    
    for start_idx in range(0, length-residual, batchsize):
        excerpt = slice(start_idx, start_idx+batchsize)
        yield [i[excerpt] for i in inputs] + [targets[excerpt]]
    if(residual>0):
        excerpt = slice(length-residual, length)
        yield [i[excerpt] for i in inputs] + [targets[excerpt]]


def unique_rel_to_abs_models(rel_models, rx, ry, rs):
    '''converts a list of relative models to the absolute model specified by the range parameters rx, ry, rs
    returns a matrix of size (number of models, 3)
    '''
    nv = len(rel_models)
    nx, ny, ns = len(rx[1]), len(ry[1]), len(rs[1])
    assert nv==len(rx[0])
    ixs, iys, iss = np.unravel_index(rel_models, (nx, ny, ns))
    abs_models = np.ndarray(shape=(nv, 3), dtype=fpX)
    for v in range(nv):
        abs_models[v] = [rx[v,ixs[v]], ry[v,iys[v]], rs[v,iss[v]]]
    return abs_models

def rel_to_abs_shared_models(rel_models, rx, ry, rs):
    '''converts a list of relative models to the absolute model specified by the range parameters rx, ry, rs
    returns a matrix of size (number of models, 3)'''
    nv = len(rel_models)
    nx, ny, ns = len(rx), len(ry), len(rs)
    ixs, iys, iss = np.unravel_index(rel_models, (nx, ny, ns))
    return np.stack([rx[ixs[:]], ry[iys[:]], rs[iss[:]]], axis=1)


def pRF(fwrf_weights, fmap_rf, pool_rf):
    '''
    fwrf_weights is [nv, nf]
    fmap_rf is [nf] i.e. it specifies a gaussian sigma value for each feature map
    pool_rf is [nv, 3] i.e. it specifies a gaussian population pooling fct for each voxel 
    
    returns [nv,3], a rf x, y, and sigma for each voxel
    '''
    # we'd be better off performing the implicit convolution first.
    vsigma = np.zeros(shape=(nv), dtype=fpX)
    for v in pool_rf:
        vsigma[v] = np.average(np.sqrt(np.square(fmap_rf) + np.square(pool_rf[v,2,np.newaxis])), weights=fwrf_weights[v,:])
    return np.stack([pool_rf[:,0:1], pool_rf[:,1:2], vsigma[:,np.newaxis]], axis=1)

########################################################################
###                                                                  ###
########################################################################

def create_shared_batched_feature_maps_gaussian_weights(fmap_sizes, batch_v, batch_t, verbose=True):
    nf = 0
    _sRFWs = []
    mem_approx = 0
    rep_approx = 0
    for i,a in enumerate(fmap_sizes):
        nf += a[1]
        n_pix = a[2]
        assert n_pix==a[3], "Non square feature map not supported"
        _sRFWs += [theano.shared(np.zeros(shape=(batch_v, batch_t, n_pix, n_pix), dtype=fpX)),]
        mem_approx += 4*batch_v*batch_t*n_pix**2
        rep_approx += 4*a[1]*n_pix**2
        if verbose:
            print "> rfw %d with shape %s" % (i, (batch_v, batch_t, n_pix, n_pix))
    if verbose:        
        print "  total number of feature maps = %d, in %d layers" % (nf, len(fmap_sizes))
        print "  shared rfw using approx %.1f Mb of memory (VRAM and RAM)" % (fpX(mem_approx) /(1024*1024))
    return _sRFWs, nf

def set_shared_batched_feature_maps_gaussian_weights(_psRFWs, xs, ys, ss, size=20.):
    '''
    The interpretation of receptive field weight factor is that they correspond, for each voxel, to the probability of this voxel of seeing 
    (through the weighted average) a given feature map pixel through its receptive field size and position in visual space. 
    Whether that feature map pixel is relevant to the representation of that particular voxel is left to the voxel encoding model to decide.
    '''
    nf = 0
    (nv, nt) = (len(xs), 1) if xs.ndim==1 else xs.shape[0:2]
    (sv, st) = _psRFWs[0].get_value().shape[0:2]
    assert nv==sv and nt==st, "non conformal (%d,%d)!=(%d,%d)" % (nv, nt, sv, st)
    for a in _psRFWs:
        n_pix = a.get_value().shape[2]
        _,_,rfw = pnu.make_gaussian_mass_stack(xs.flatten(), ys.flatten(), ss.flatten(), n_pix, size=size, dtype=fpX)
        a.set_value(rfw.reshape((nv, nt, n_pix, n_pix)))
    return _psRFWs
    
def set_shared_parameters(shared_vars, values):
    for i,var in enumerate(shared_vars):
        var.set_value(values[i])    





########################################################################
###              SPECIAL LASAGNE LAYER AND MODEL                     ###
########################################################################
class pvFWRFLayer(L.Layer):
    '''
    pvFWRFLayer is a new lasagne layer for 'per voxel (pv)' candidate receptive field models. It assumes an input
    of shape (bn, nf, bv, bt) where bn is a batch of the time series, nf are the total number of features, bv is a batch of voxels and bt is a batch of candidate rf.

    The return values correspond to the predicted voxel activities, of shape (bn, nv, nt)
    '''
    def __init__(self, incoming, W=lasagne.init.Normal(0.01),  b=lasagne.init.Constant(0.), nonlinearity=None, **kwargs):
        super(pvFWRFLayer, self).__init__(incoming, **kwargs)
        self.nf, self.nv, self.nt = self.input_shape[1:4]
        self.W = self.add_param(W, (self.nf, self.nv, self.nt), name='W')
        self.b = self.add_param(b, (1, self.nv, self.nt), name='b', regularizable=False)
        self.b = T.patternbroadcast(self.b, (True, False, False))
        self.nonlinearity = (NL.identity if nonlinearity is None else nonlinearity)
        
    def get_output_for(self, input, **kwargs):
        _pred = T.batched_tensordot(input.flatten(ndim=3).dimshuffle((2,0,1)), \
                self.W.flatten(ndim=2).dimshuffle((1,0)), axes=[[2],[1]]) \
                .dimshuffle((1,0)).reshape((input.shape[0],self.nv,self.nt))
        if self.b is not None:
            _pred = _pred + self.b
        return self.nonlinearity(_pred)

    def get_output_shape_for(self, input_shape):
        return (input_shape[0], self.nv, self.nt)
    
class svFWRFLayer(L.Layer):
    '''
    svFWRFLayer is a new lasagne layer for 'shared voxel (sv)' candidate receptive field models. It assumes an input
    of shape (bn, nf, bt) where bn is a batch of the time series, nf are the total number of features, bv is a batch of voxels
    and bt is a batch of candidate rf.

    The return values correspond to the predicted voxel activities, of shape (bn, nv, nt)
    '''
    def __init__(self, incoming, nvoxels, W=lasagne.init.Normal(0.01),  b=lasagne.init.Constant(0.), nonlinearity=None, **kwargs):
        super(svFWRFLayer, self).__init__(incoming, **kwargs)
        self.nf = self.input_shape[1]
        self.nt = self.input_shape[2]
        self.nv = nvoxels
        self.W = self.add_param(W, (self.nf, self.nv, self.nt), name='W')
        self.b = self.add_param(b, (1, self.nv, self.nt), name='b', regularizable=False)
        self.b = T.patternbroadcast(self.b, (True, False, False))
        self.nonlinearity = (NL.identity if nonlinearity is None else nonlinearity)
        
    def get_output_for(self, input, **kwargs):
        _pred = T.batched_tensordot(input.dimshuffle((2,0,1)), self.W.dimshuffle((2,0,1)), axes=[[2],[1]]).dimshuffle((1,2,0))
        if self.b is not None:
            _pred = _pred + self.b
        return self.nonlinearity(_pred)

    def get_output_shape_for(self, input_shape):
        return (input_shape[0], self.nv, self.nt)





def RFW_data(__fmaps, __sRFWs): 
    '''Apply a tentative fwrf model of the classification network intermediary representations.
    _fmaps is a list of grouped feature maps at different resolutions. F maps in total.
    _sRFWs is a matching resolution stack of batch_t RF model candidates.
    returns a symbolic tensor of receptive field candiate weighted feature maps (bn, features, bv, bt)'''
    __rfwfmaps = [T.tensordot(_fm, __sRFWs[i], [[2,3], [2,3]])  for i,_fm in enumerate(__fmaps)]
    __rfw_data = T.concatenate(__rfwfmaps, axis=1)
    return __rfw_data



def pvFWRF(__rfw_data, nf, nv, nt): 
    '''
    Create a symbolic lasagne network for the per voxel candidate case.
    returns a symbolic outpuy of shape (bn, bv, bt).
    '''
    _input = L.InputLayer((None, nf, nv, nt), input_var=__rfw_data.reshape((-1,nf,nv,nt)))
    ## try to add a parametrized local nonlinearity layer.
    _pred  = pvFWRFLayer(_input, W=I.Normal(0.02), b=I.Constant(0.), nonlinearity=None)
    #print "> input using approx %.1f x batch_size Mb of memory (VRAM and RAM)" % (fpX(4*nf*nv*nt) /(1024*1024))
    #print "> output using approx %.3f x batch_size Mb of memory (VRAM and RAM)" % (fpX(4*nv*nt) /(1024*1024))
    return _pred


def svFWRF(__rfw_data, nf, nv, nt): 
    '''
    Create a symbolic lasagne network for the shared voxel candidate case.
    returns a symbolic outpuy of shape (bn, bv, bt).
    '''
    _input = L.InputLayer((None, nf, nt), input_var=__rfw_data.reshape((-1,nf,nt)))
    _pred  = svFWRFLayer(_input, nvoxels=nv, W=I.Normal(0.02), b=I.Constant(0.), nonlinearity=None) #NL.tanh
    #print "> input using approx %.1f x batch_size Mb of memory (VRAM and RAM)" % (fpX(4*nf*nv*nt) /(1024*1024))
    #print "> output using approx %.3f x batch_size Mb of memory (VRAM and RAM)" % (fpX(4*nv*nt) /(1024*1024))
    return _pred



########################################################################
###              THE MAIN MODEL CLASS                                ###
########################################################################
class FWRF_model(object):
    def __init__(self, 
            _symbolicFeatureMaps=[], featureMapSizes=[],
            _symbolicInputVars=[], inputVarSizes=[],\
            batches_p=(1,1,1), batches_o=(1,1,1), batches_t=(1,1),\
            view_angle=20., verbose=True):
        '''
        This object creates all the expressions needed to train the model by dividing the problem in managable batches, but the specific of the models, 
        like the values of the candidate receptive fields are provided later at training time.
        
        DEFINITIONS:
            n:  the length of the time series (the number of samples)
            nv: the number of voxels
            nt: the total number of candidate receptive fields = nx*ny*ns
            nf: the total number of feature maps (all resolutions)

            bn: the sample batch size
            bv: the voxel batch size
            bt: the candidate batch size

        INPUTS:
            _symbolicFeatureMaps: a list of symbolic theano variables that describe the feature maps
            _symbolicInputVars: a list of symbolic theano variables that describe the form of the input (often, this will be the same as the feature maps)
            featureMapSizes: a list of tuple describing the size of the feature maps (the first dimension may change at any point so it is largely arbitrary at this point)
            inputVarSizes: a list of tuple describing the size of the inputs
            batches_p: a 2-tuple for the batch sizes of the preprocessing along the dimensions (bn, bt)
            batches_o: a 3-tuple for the batch sizes of the optimization along the dimensions (bn, bv, bt)
            batches_t: a 2-tuple for the batch sizes of the preprocessing along the dimensions (bn, bv)
        '''
        self.view_angle=view_angle

        self.batch_n_p, self.batch_t_p = batches_p
        self.batch_n_o, self.batch_v_o, self.batch_t_o = batches_o
        self.batch_n_t, self.batch_v_t = batches_t
        
        print 'CREATING SYMBOLS\n'
        self._sRFWs_o, self.nf = create_shared_batched_feature_maps_gaussian_weights(featureMapSizes, 1, self.batch_t_p, verbose=verbose)
        self._sRFWs_t,_        = create_shared_batched_feature_maps_gaussian_weights(featureMapSizes, self.batch_v_t, 1, verbose=False)
        #alias self.numFeatures = self.nf       
        _rfw_data_o = RFW_data(_symbolicFeatureMaps, self._sRFWs_o)
        _fwrf_t = pvFWRF(RFW_data(_symbolicFeatureMaps, self._sRFWs_t), self.nf, self.batch_v_t, 1)     
        
        _V  = T.matrix()
        __V = _V.dimshuffle((0,1,'x'))

        self.__lr = theano.shared(fpX(0))
        self.__l2 = theano.shared(fpX(0))        
        ###   
        self.fwrf_t_params = L.get_all_params(_fwrf_t, trainable=True)
        _fwrf_t_val_pred = L.get_output(_fwrf_t, deterministic=True)
        #_fwrf_t_val_cc = ((_fwrf_t_val_pred * __V).mean(axis=0) - _fwrf_t_val_pred.mean(axis=0) * __V.mean(axis=0)) / \
        #    (T.sqrt((T.sqr(_fwrf_t_val_pred).mean(axis=0) - T.sqr(_fwrf_t_val_pred.mean(axis=0))) * (T.sqr(__V).mean(axis=0) - T.sqr(__V.mean(axis=0)))))
        ### alt corr coeff    
        _fwrf_t_val_cc = ((_fwrf_t_val_pred - _fwrf_t_val_pred.mean(axis=0, keepdims=True)) * (__V - __V.mean(axis=0, keepdims=True))).mean(axis=0) / \
            T.sqrt(T.sqr(_fwrf_t_val_pred - _fwrf_t_val_pred.mean(axis=0, keepdims=True)).mean(axis=0) * T.sqr(__V - __V.mean(axis=0, keepdims=True)).mean(axis=0)) 
        ###  
        _rfw_data_f = T.tensor4()
        _fwrf_f = pvFWRF(_rfw_data_f, self.nf, self.batch_v_t, 1)   
        self.fwrf_f_params = L.get_all_params(_fwrf_f, trainable=True)
        
        _fwrf_f_val_pred = L.get_output(_fwrf_f, deterministic=True)   
        _fwrf_f_val_cc = ((_fwrf_f_val_pred - _fwrf_f_val_pred.mean(axis=0, keepdims=True)) * (__V - __V.mean(axis=0, keepdims=True))).mean(axis=0) / \
            T.sqrt(T.sqr(_fwrf_f_val_pred - _fwrf_f_val_pred.mean(axis=0, keepdims=True)).mean(axis=0) * T.sqr(__V - __V.mean(axis=0, keepdims=True)).mean(axis=0))         
        ###
        self.__rfw_sdata = theano.shared(np.asarray([], dtype=fpX).reshape((0,0,0,0)))
        self.__vox_sdata = theano.shared(np.asarray([], dtype=fpX).reshape((0,0)))
        self.__range = T.ivector()

        _srfw_batch = self.__rfw_sdata[self.__range[0]:self.__range[1]]
        _fwrf_o = svFWRF(_srfw_batch, self.nf, self.batch_v_o, self.batch_t_o)
        if verbose:
            print "\n"
            plu.PrintLasagneNet(_fwrf_o, skipnoparam=False)

        ### define and compile the training expressions.       
        _fwrf_o_reg = self.__l2 * R.regularize_layer_params(_fwrf_o, R.l2)
        self.fwrf_o_params = L.get_all_params(_fwrf_o, trainable=True)

        _sV = self.__vox_sdata[self.__range[0]:self.__range[1]].dimshuffle((0,1,'x'))

        _fwrf_o_trn_pred = L.get_output(_fwrf_o, deterministic=False)
        _fwrf_o_trn_preloss = O.squared_error(_fwrf_o_trn_pred, _sV).mean(axis=0)
        _fwrf_o_trn_loss = _fwrf_o_trn_preloss.sum() + _fwrf_o_reg

        _fwrf_o_val_pred = L.get_output(_fwrf_o, deterministic=True)
        _fwrf_o_val_preloss = O.squared_error(_fwrf_o_val_pred, _sV).mean(axis=0) #average across the batch elements
        ###
        self.__fwrf_o_updates = lasagne.updates.sgd(_fwrf_o_trn_loss, self.fwrf_o_params, learning_rate=self.__lr)
#        self.__fwrf_o_updates = lasagne.updates.adam(_fwrf_o_trn_loss, self.fwrf_o_params, learning_rate=self.__lr, beta1=0.5, epsilon=1e-12)
        
        print '\nCOMPILING...'
        sys.stdout.flush()
        comp_t = time.time()
        # first the expression for the precomputing
        self.rfw_o_data_fn  = theano.function(_symbolicInputVars, _rfw_data_o)
        # then the expressions for testing
        self.fwrf_t_pred_fn = theano.function(_symbolicInputVars, _fwrf_t_val_pred)
        self.fwrf_t_test_fn = theano.function(_symbolicInputVars+[_V], [_fwrf_t_val_pred, _fwrf_t_val_cc])
        #
        self.fwrf_f_pred_fn = theano.function([_rfw_data_f], _fwrf_f_val_pred)
        self.fwrf_f_test_fn = theano.function([_rfw_data_f, _V], [_fwrf_f_val_pred, _fwrf_f_val_cc])        
        # finally the batched optimization expressions
        self.fwrf_o_trn_fn = theano.function([self.__range], updates=self.__fwrf_o_updates)
        self.fwrf_o_val_fn = theano.function([self.__range], _fwrf_o_val_preloss)
        print '%.2f seconds to compile theano functions' % (time.time()-comp_t)


        
    def svModelSpace(self, sharedModel_specs):
        vm = np.asarray(sharedModel_specs[0])
        nt = np.prod([sms.length for sms in sharedModel_specs[1]])           
        rx, ry, rs = [sms(vm[i,0], vm[i,1]) for i,sms in enumerate(sharedModel_specs[1])]
        xs, ys, ss = np.meshgrid(rx, ry, rs, indexing='ij')
        return xs.reshape((1,nt)).astype(dtype=fpX), ys.reshape((1,nt)).astype(dtype=fpX), ss.reshape((1,nt)).astype(dtype=fpX) 



    def __precompute_rfw_o_data(self, datas, sharedModel_specs, verbose=True, dry_run=False, nonlinearity=None, zscore=False, trn_size=None, epsilon=1.0):  
        n = len(datas[0])
        bn, bt = self.batch_n_p, self.batch_t_p
        vm = np.asarray(sharedModel_specs[0])
        nt = np.prod([sms.length for sms in sharedModel_specs[1]])           
        #rx, ry, rs = [sms(vm[i,0], vm[i,1]) for i,sms in enumerate(sharedModel_specs[1])]
        mx, my, ms = self.svModelSpace(sharedModel_specs)
        if verbose:
            print "\n>> Storing the full precomputed candidate feature time series will require approx %.03fGb of RAM!"\
                % (fpX(n*self.nf*nt*4) / 1024**3)
            print ">> Will be divided in chunks of %.03fGb of VRAM!" % ((fpX(n*self.nf*self.batch_t_o*4) / 1024**3))
            sys.stdout.flush()  
        start_time = time.time()
        print "\nPrecomputing rfw candidate responses..."
        sys.stdout.flush()
        nbt = nt // bt
        rbt = nt - nbt * bt
        assert rbt==0, "the candidate batch size must be an exact divisor of the total number of candidates"
        rfw_data = np.ndarray(shape=(n,self.nf,1,nt), dtype=fpX)
        if dry_run:
            return rfw_data
        for t in tqdm(range(nbt)): ## CANDIDATE BATCH LOOP     
            # set the receptive field weight for this batch of voxelmodel
            set_shared_batched_feature_maps_gaussian_weights(self._sRFWs_o, mx[:,t*bt:(t+1)*bt], my[:,t*bt:(t+1)*bt], ms[:,t*bt:(t+1)*bt], size=self.view_angle)
            for excerpt, size in iterate_slice(0, n, bn):
                args = slice_arraylist(datas, excerpt)  
                rfw_data[excerpt,:,:,t*bt:(t+1)*bt] = self.rfw_o_data_fn(*args)
        full_time = time.time() - start_time
        print "%d rfw candidate responses took %.3fs @ %.3f models/s" % (nt, full_time, fpX(nt)/full_time)
        if nonlinearity:
            print "Applying nonlinearity to modelspace tensor..."
            sys.stdout.flush()
            for rr, rl in tqdm(iterate_slice(0, rfw_data.shape[3], bt)): 
                rfw_data[:,:,:,rr] = nonlinearity(rfw_data[:,:,:,rr])
        if zscore:
            if trn_size==None:
                trn_size = len(rfw_data)
            print "Z-scoring modelspace tensor..."
            sys.stdout.flush()
            for rr, rl in tqdm(iterate_slice(0, rfw_data.shape[3], bt)):   
                rfw_avg = np.mean(rfw_data[:trn_size,:,:,rr], axis=0, dtype=np.float64, keepdims=True).astype(fpX)
                rfw_std = np.std(rfw_data[:trn_size,:,:,rr], axis=0, dtype=np.float64, keepdims=True).astype(fpX)
                rfw_data[:,:,:,rr] -= rfw_avg
                rfw_data[:,:,:,rr] /= (epsilon + rfw_std)
                rfw_data[:,:,:,rr] = np.nan_to_num(rfw_data[:,:,:,rr])
        #if np.isnan(rfw_data).any():
        #    print "There are NaNs values in the modelspace tensor! Replacing with 0."
        #    rfw_data = np.nan_to_num(rfw_data)
        return rfw_data
   

    

    def __optimize_shared_models(self, rfw_data, voxels, params, val_test_size=100, num_epochs=1, output_val_scores=True, verbose=True, dry_run=False):
        bn, bv, bt = self.batch_n_o, self.batch_v_o, self.batch_t_o
        n, nv = voxels.shape
        nt = rfw_data.shape[3]
        
        val_scores = []
        if output_val_scores:
            val_scores  = np.zeros(shape=(num_epochs, nv, nt), dtype=fpX)
        val_batch_scores = np.zeros((bv, bt), dtype=fpX)
        
        best_scores = np.full(shape=(nv), fill_value=np.inf, dtype=fpX)
        best_models = np.zeros(shape=(nv), dtype=int)

        W, b = self.fwrf_o_params
        best_params = [np.zeros(p.shape, dtype=fpX) for p in params]      
        
        nbv, nbt = nv // bv, nt // bt
        rbv, rbt = nv - nbv * bv, nt - nbt * bt
        assert rbt==0, "the model batch size must be an divisor of the total number of models"
        if verbose:
            print "%d voxel batches of size %d with residual %d" % (nbv, bv, rbv)
            print "%d candidate batches of size %d with residual %d" % (nbt, bt, rbt)
            print "for %d voxelmodel fits." % (nv*nt)
            sys.stdout.flush()    
        if dry_run:
            return val_scores, best_scores, best_models, best_params
        ### voxel loop
        for rv, lv in tqdm(iterate_range(0, nv, bv)): ## VOXEL BATCH LOOP
            voxelSlice = voxels[:,rv] 
            best_scores_slice = best_scores[rv]
            best_models_slice = best_models[rv] 
            rW = params[0][rv,:]
            rb = params[1][rv]
            if lv<bv: #PATCH UP MISSING DATA FOR THE FIXED VOXEL BATCH SIZE
                voxelSlice = np.concatenate((voxelSlice, np.zeros(shape=(n, bv-lv), dtype=fpX)), axis=1)
                rW = np.concatenate((rW, np.zeros(shape=(bv-lv, self.nf), dtype=fpX)), axis=0)
                rb = np.concatenate((rb, np.zeros(shape=(bv-lv), dtype=fpX)), axis=0)       
            pW = np.repeat(rW.T, repeats=bt).reshape((self.nf,bv,bt)) # ALL CANDIDATE MODELS GET THE SAME INITIAL PARAMETER VALUES
            pb = np.repeat(rb, repeats=bt).reshape((1, bv,bt))      
                    
            set_shared_parameters([self.__vox_sdata], [voxelSlice])
            ### candidate loop
            for t in range(nbt): ## CANDIDATE BATCH LOOP
                # need to recompile to reset the solver!!! (depending on the solver used)
                self.fwrf_o_trn_fn = theano.function([self.__range], updates=self.__fwrf_o_updates)
                # set the shared parameter values for this candidates. Every candidate restart at the same point.
                set_shared_parameters(self.fwrf_o_params+[self.__rfw_sdata], [pW, pb, rfw_data[:,:,:,t*bt:(t+1)*bt]])
                print "\n  Voxel %d:%d of %d, Candidate %d:%d of %d" % (rv[0], rv[-1]+1, nv, t*bt, (t+1)*bt, nt)
                ### epoch loop
                epoch_start = time.time()
                for epoch in range(num_epochs):
                    ######## ONE EPOCH OF TRAINING ###########
                    #trn_batch_scores.fill(0)
                    val_batch_scores.fill(0)  
                    # In each epoch, we do a full pass over the training data:
                    for rb, lb in iterate_bounds(0, n-val_test_size, bn):
                        self.fwrf_o_trn_fn(rb)
                    # and one pass over the validation set.  
                    val_batches = 0
                    for rb, lb in iterate_bounds(n-val_test_size, val_test_size, bn): 
                        loss = self.fwrf_o_val_fn(rb)
                        val_batch_scores += loss
                        val_batches += lb
                    val_batch_scores /= val_batches
                    if verbose:
                        print "    validation <loss>: %.6f" % (val_batch_scores.mean())
                    if output_val_scores:
                        val_scores[epoch, rv, t*bt:(t+1)*bt] = val_batch_scores[:lv,:]
                    ##### RECORD MINIMUM SCORE AND MODELS #####
                    best_models_for_this_epoch = np.argmin(val_batch_scores[:lv,:], axis=1)
                    best_scores_for_this_epoch = np.amin(val_batch_scores[:lv,:], axis=1)
                    # This updates the BEST RELATIVE MODELS, along with their associated scores 
                    best_scores_mask = (best_scores_for_this_epoch<best_scores_slice) #all the voxels that show an improvement
#                    if np.sum(best_scores_mask)==0:
#                        print "    end of improvement"
#                        break
                    np.copyto(best_scores_slice, best_scores_for_this_epoch, casting='same_kind', where=best_scores_mask)      
                    np.copyto(best_models_slice, best_models_for_this_epoch + t*bt, casting='same_kind', where=best_scores_mask) #notice the +t*bt to return the best model across all models, not just the batch's
                    #to select the weight slices we need, we need to specify the voxels that showed improvement AND the models that correspond to these improvements.
                    update_vm_pos = np.zeros((bv, bt), dtype=bool)
                    update_vm_pos[np.arange(lv)[best_scores_mask], best_models_for_this_epoch[best_scores_mask]] = True
                    update_vm_idx = np.arange(bv*bt)[update_vm_pos.flatten()]
                    # update the best parameter values based on the voxelmodel validation scores.
                    best_params[0][np.asarray(rv)[best_scores_mask], :] = (W.get_value().reshape((self.nf,-1))[:,update_vm_idx]).T
                    best_params[1][np.asarray(rv)[best_scores_mask]]    = b.get_value().reshape((-1))[update_vm_idx]   

                batch_time = time.time()-epoch_start
                print "    %d Epoch for %d voxelmodels took %.3fs @ %.3f voxelmodels/s" % (num_epochs, lv*bt, batch_time, fpX(lv*bt)/batch_time)
                sys.stdout.flush()
            #end candidate loop    
            best_scores[rv] = np.copy(best_scores_slice) ##NECESSARY TO COPY BACK
            best_models[rv] = np.copy(best_models_slice)   
        # end voxel loop
        return val_scores, best_scores, best_models, best_params



    
    def precompute_rfw_data(self, datas, sharedModel_specs, verbose=False, dry_run=False, nonlinearity=None, zscore=False, trn_size=None, epsilon=1.0):
        return self.__precompute_rfw_o_data(datas, sharedModel_specs, verbose=verbose, dry_run=dry_run, nonlinearity=nonlinearity, zscore=zscore, trn_size=trn_size, epsilon=epsilon) 
        
        

    def shared_model_training(self, datas, voxels, sharedModel_specs, params, val_test_size=100, lr=1e-4, l2=0.0, num_epochs=1, output_val_scores=True, verbose=True, dry_run=False, use_data_as_rfw_data=False):
        ''' 
        Train the specified models
        '''  
        n, nv = voxels.shape
        vm = np.asarray(sharedModel_specs[0])
        nt = np.prod([sms.length for sms in sharedModel_specs[1]])           
        rx, ry, rs = [sms(vm[i,0], vm[i,1]) for i,sms in enumerate(sharedModel_specs[1])] # needed to map rf's back to visual space
        if use_data_as_rfw_data:
            assert len(datas)==len(voxels), "data/target length mismatch"   
            rfw_data = datas
        else:
            assert len(datas[0])==len(voxels), "data/target length mismatch"   
            rfw_data = self.__precompute_rfw_o_data(datas, sharedModel_specs, verbose=verbose, dry_run=dry_run) #(n, nf, 1, nt)
        ### request shared memory
        self.__rfw_sdata.set_value(np.zeros(shape=(n, self.nf, 1, self.batch_t_o), dtype=fpX))
        self.__vox_sdata.set_value(np.zeros(shape=(n, self.batch_v_o), dtype=fpX))   
        self.__lr.set_value(fpX(lr))
        self.__l2.set_value(fpX(l2))        
  
        ### THIS IS WHERE THE MODEL OPTIMIZATION IS PERFORMED ### 
        print "\nVoxel-Candidates model optimization..."
        start_time = time.time()
        val_scores, best_scores, best_rel_models, best_params = self.__optimize_shared_models(\
            rfw_data, voxels, params, val_test_size=val_test_size,\
            num_epochs=num_epochs, output_val_scores=output_val_scores, verbose=verbose, dry_run=dry_run)  
        
        # free shared vram
        self.__rfw_sdata.set_value(np.asarray([], dtype=fpX).reshape((0,0,0,0)))
        self.__vox_sdata.set_value(np.asarray([], dtype=fpX).reshape((0,0)))

        best_abs_models = rel_to_abs_shared_models(best_rel_models, rx, ry, rs) ### put back the models in absolute coordinate a.k.a model spec for the next iteration

        full_time = time.time() - start_time
        print "\n---------------------------------------------------------------------"
        print "%d Epoch for %d voxelmodels took %.3fs @ %.3f voxelmodels/s" % (num_epochs, nv*nt, full_time, fpX(nv*nt)/full_time)

        return val_scores, best_scores, best_abs_models, best_rel_models, best_params
    



    def validate_models(self, datas, voxels, voxelmodels, params, use_data_as_rfw_data=False):
        bn, bv = self.batch_n_t, self.batch_v_t
        n, nv = voxels.shape
        assert n<=bn
        nt = 1
             
        predictions = np.zeros(shape=(n, nv), dtype=fpX)
        val_scores  = np.zeros(shape=(nv), dtype=fpX)

        nbv = nv // bv
        rbv = nv - nbv * bv
        print "%d voxel batches of size %d with residual %d" % (nbv, bv, rbv)
        sys.stdout.flush()
        ##
        if use_data_as_rfw_data:
            assert len(datas)==len(voxels)
            assert len(voxelmodels)==nv ## voxelmodels interpreted as relative model        
            for rv, lv in tqdm(iterate_range(0, nv, bv)): ## VOXEL BATCH LOOP
                #print "\n  Voxel %d:%d of %d" % (rv[0], rv[-1]+1, nv)
                start_time = time.time()
                voxelSlice = voxels[:,rv]
                vm_slice = voxelmodels[rv]
                rW = params[0][rv,:]
                rb = params[1][rv]
                if lv<bv: #PATCH UP MISSING DATA FOR THE FIXED BATCH SIZE
                    voxelSlice = np.concatenate((voxelSlice, np.zeros(shape=(n, bv-lv), dtype=fpX)), axis=1)
                    vm_slice = np.concatenate((vm_slice, np.zeros(shape=(bv-lv), dtype=int)), axis=0)
                    rW = np.concatenate((rW, np.zeros(shape=(bv-lv, self.nf), dtype=fpX)), axis=0)
                    rb = np.concatenate((rb, np.zeros(shape=(bv-lv), dtype=fpX)), axis=0) 
                pW = rW.T.reshape((self.nf,bv,1))
                pb = rb.reshape((1,bv,1))      

                pv_rfw_data = datas[:, :, 0, vm_slice, np.newaxis]
                set_shared_parameters(self.fwrf_f_params, [pW, pb])
                prep_time = time.time()
                ###            
                pred, cc = self.fwrf_f_test_fn(pv_rfw_data, voxelSlice)
                predictions[:, rv], val_scores[rv] = pred[:,:lv,0], cc[:lv,0]
                ###
                #print "prep time = %.3fs, calc time = %.3fs" % (prep_time-start_time, time.time()-prep_time)        
        else:
            assert len(datas[0])==len(voxels)
            assert voxelmodels.shape[0]==nv
            assert voxelmodels.shape[1]==3     
            for rv, lv in tqdm(iterate_range(0, nv, bv)): ## VOXEL BATCH LOOP
                #print "\n  Voxel %d:%d of %d" % (rv[0], rv[-1]+1, nv)
                start_time = time.time()
                voxelSlice = voxels[:,rv]
                vm_slice = voxelmodels[rv,:]
                rW = params[0][rv,:]
                rb = params[1][rv]
                if lv<bv: #PATCH UP MISSING DATA FOR THE FIXED BATCH SIZE
                    voxelSlice = np.concatenate((voxelSlice, np.zeros(shape=(n, bv-lv), dtype=fpX)), axis=1)
                    vm_slice = np.concatenate((vm_slice, np.ones(shape=(bv-lv,3), dtype=fpX)), axis=0)
                    rW = np.concatenate((rW, np.zeros(shape=(bv-lv, self.nf), dtype=fpX)), axis=0)
                    rb = np.concatenate((rb, np.zeros(shape=(bv-lv), dtype=fpX)), axis=0) 
                pW = rW.T.reshape((self.nf,bv,1))
                pb = rb.reshape((1,bv,1))      

                set_shared_parameters(self.fwrf_t_params, [pW, pb])
                set_shared_batched_feature_maps_gaussian_weights(self._sRFWs_t, vm_slice[:,0], vm_slice[:,1], vm_slice[:,2], size=self.view_angle) 
                prep_time = time.time()
                ###            
                args = datas+[voxelSlice] 
                pred, cc = self.fwrf_t_test_fn(*args)
                predictions[:, rv], val_scores[rv] = pred[:,:lv,0], cc[:lv,0]
                ###
                #print "prep time = %.3fs, calc time = %.3fs" % (prep_time-start_time, time.time()-prep_time)
        return predictions, val_scores
    
    

    
    
    def prediction(self, datas, voxelmodels, params):
        bn, bv = self.batch_n_t, self.batch_v_t
        n = datas[0].shape[0]
        nv = voxelmodels.shape[0]
        assert voxelmodels.shape[1]==3
        nt = 1
           
        predictions = np.zeros(shape=(n, nv), dtype=fpX)
        
        nbv = nv // bv
        rbv = nv - nbv * bv
        print "%d voxel batches of size %d with residual %d" % (nbv, bv, rbv)
        ##
        for rv, lv in tqdm(iterate_range(0, nv, bv)): ## VOXEL BATCH LOOP
            #print "\n  Voxel %d:%d of %d" % (rv[0], rv[-1]+1, nv)
            voxelSlice = voxels[:,rv]
            vm_slice = voxelmodels[rv,:]
            rW = params[0][rv,:]
            rb = params[1][rv]
            if lv<bv: #PATCH UP MISSING DATA FOR THE FIXED BATCH SIZE
                voxelSlice = np.concatenate((voxelSlice, np.zeros(shape=(n, bv-lv), dtype=fpX)), axis=1)
                vm_slice = np.concatenate((vm_slice, np.ones(shape=(bv-lv,3), dtype=fpX)), axis=0)
                rW = np.concatenate((rW, np.zeros(shape=(bv-lv, self.nf), dtype=fpX)), axis=0)
                rb = np.concatenate((rb, np.zeros(shape=(bv-lv), dtype=fpX)), axis=0) 
            pW = rW.T.reshape((self.nf,bv,1))
            pb = rb.reshape((1,bv,1))      

            set_shared_parameters(self.fwrf_t_params, [pW, pb])
            set_shared_batched_feature_maps_gaussian_weights(self._sRFWs_t, vm_slice[:,0], vm_slice[:,1], vm_slice[:,2], size=self.view_angle) 
            ###       
            for rb, lb in iterate_slice(0, n, bn):
                args = slice_arraylist(datas, rb)
                pred = self.fwrf_t_pred_fn(*args) 
                predictions[rb, rv] = pred[:,:lv,0]
            ###
        return predictions



    
def infer_voxel_noise_model(voxels, predictions):
    ''' 
    voxels and predictions are (T, V)
    '''    
    vox_avg = np.mean(voxels-predictions, axis=0)
    vox_std = np.sqrt(np.var(voxels, axis=0) - np.var(predictions, axis=0))
    return vox_avg, vox_std
    
def apply_per_voxel_noise_model(voxels, noise_avg, noise_std):
    ''' 
    voxels is (T, V)
    noise_mean and noise_std are (V,)
    '''
    noise = np.random.normal(size=voxels.shape).astype(fpX)
    noise = (noise * noise_std[np.newaxis, :]) + noise_avg[np.newaxis, :]
    return voxels + noise

def symbolic_per_voxel_noise_model(_voxels, noise_avg, noise_std):
    rng = T.raw_random.RandomStreamsBase()
    _noise_std = theano.shared(noise_avg.astype(fpX)[np.newaxis,:])
    _noise_avg = theano.shared(noise_std.astype(fpX)[np.newaxis,:])
    _noise = (rng.normal(size=voxels.shape) * _noise_std) + _noise_avg
    return _voxels + _noise