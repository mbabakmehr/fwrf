import numpy as np
import math
import matplotlib.pyplot as plt
import matplotlib.colors as colors
import matplotlib.cm as cmx
from matplotlib.pyplot import cm 
from matplotlib.ticker import FormatStrFormatter
import seaborn as sns


def display_candidate_loss(scores, nx, ny, ns):
    dis_y = ns // 3 if ns%3==0 else ns//3+1
    s = scores.reshape((nx, ny, ns)).transpose((1,0,2))[::-1,:,:] ## The transpose and flip is just so that the candidate 
    #coordinate maatch the normal cartesian coordinate of the rf position when viewed through imshow.
    idxs = np.unravel_index(np.argmin(s), (nx,ny,ns))
    best = plt.Circle((idxs[1], idxs[0]), 0.5, color='r', fill=False, lw=2)
    
    fig = plt.figure(figsize=(15, 5*dis_y))
    smin = np.min(s)
    smax = np.max(s)
    print "score range = (%f, %f)" % (smin, smax)
    for i in range(ns):
        plt.subplot(dis_y, 3, i+1)
        plt.imshow(s[:,:,i], interpolation='None', cmap='jet')
        plt.title('sigma canditate = %d' % i)
        plt.clim(smin, smax)
        plt.grid('off')
        if(idxs[2]==i):
            ax = plt.gca()
            ax.add_artist(best)
    return fig


def plot_rf_as_circles(rfs, smin, smax):
    cNorm  = colors.Normalize(vmin=smin, vmax=smax)
    scalarMap = cmx.ScalarMappable(norm=cNorm, cmap=plt.get_cmap('cubehelix_r') )
    print scalarMap.get_clim()
    for rf in rfs:
        colorVal = scalarMap.to_rgba(rf[2])
        c = plt.Circle((rf[0], rf[1]), rf[2], color=colorVal, fill=False, lw=0.7)
        plt.gca().add_artist(c)
    plt.xlim([-15,15])
    plt.ylim([-15,15])
    plt.xlabel('x (degree)')
    plt.ylabel('y (degree)', labelpad=0)
    plt.gca().set_aspect('equal')


def plot_pretty_scatter(X, Y, threshold, xlim, ylim):
    lim = 256.
    start = map(lambda x: 1.*x/lim, (245,236,225)) #oatmeal
    stop = map(lambda x: .5*x/lim, (137,205,187)) #aqua
    cmap = sns.blend_palette([start,stop], as_cmap=True)
    color1 = '#084990'
    color2 = '#3989c1'
    y = (X+Y)/2
    x = (X-Y)
    
    g = sns.JointGrid(x, y, size=8, xlim=xlim, ylim=ylim)
    # marg. plot
    mask = np.logical_or(X>threshold, Y>threshold) #np.where(Xt[1]>threshold)[0]
    _=g.plot_joint(plt.hexbin, bins='log', gridsize=30, cmap='Blues', extent=xlim+ylim)
    ax1=g.ax_marg_x.hist(x[np.logical_and(mask, x<0)],log=True, color=color1, bins=50, range=xlim) #distplot(color=".5",kde=False) #hist_kws={'log':True}
    ax2=g.ax_marg_x.hist(x[np.logical_and(mask, x>=0)],log=True, color=color2, bins=50, range=xlim) 
    
    adv = np.sum(ax1[0]) / (np.sum(ax1[0])+np.sum(ax2[0]))
    g.ax_marg_x.text(-0.55, 50., '%.2f' % adv, horizontalalignment='left', fontsize=18, color=color1, weight='bold')
    g.ax_marg_x.text( 0.45, 50., '%.2f' % (1.-adv), horizontalalignment='left', fontsize=18, color=color2, weight='bold')
    g.ax_marg_x.set_ylim([0.5, 5e2])
    
    g.ax_marg_x.get_yaxis().reset_ticks()
    g.ax_marg_x.get_yaxis().set_ticks([1e0, 1e1, 1e2])
    g.ax_marg_x.get_yaxis().set_ticklabels([1e0, 1e1, 1e2])
    g.ax_marg_x.set_ylabel('Count', labelpad=10)
    g.ax_marg_x.get_yaxis().grid(True)
    g.ax_marg_x.get_yaxis().set_major_formatter(FormatStrFormatter('%d'))
    g.ax_marg_y.set_visible(False)

    g.ax_joint.plot(np.zeros(len(ylim)), ylim, ':k', lw=2)
    #g.ax_joint.plot(xlim, np.ones(len(xlim)) * threshold, '--r', lw=2)
    g.ax_joint.plot([0., xlim[1]], [threshold, threshold -  xlim[1] / 2], '--r', lw=2)
    g.ax_joint.plot([xlim[0], 0.], [threshold +  xlim[0] / 2, threshold], '--r', lw=2)
    return g
    

