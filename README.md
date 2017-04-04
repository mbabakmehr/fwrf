# The feature-weighted receptive field (fwRF) 
Example code and analysis for the paper: The feature-weighted receptive field: an interpretable encoding model for complex feature spaces.

Note that a latex browser extension (like mathjax for github) may be necessary for displaying correctly the following text (Ex: pi=$\pi$ should show pi and the symbol for pi).


## The model

The feature-weighted receptive field is a new approach to building voxelwise encoding models for visual brain areas. The results of this study suggest that the fwRF modeling approach can be used to achieve the performance goals of expressiveness, scalability, interpretability and compatibility laid out in details in the paper. The key design principle of the fwRF modeling approach is space-feature separability, which endows the model with an explicit receptive field-like component that facilitates interpretation, and makes it possible to scale the number of feature maps in the model without incurring a per-pixel increase in model parameters. We find that when this approach is applied to a deep neural network with thousands of feature maps, the resulting encoding model achieves better prediction accuracy than comparable encoding models for most voxels in the visual system.

![diagram](/img/gabor_vs_refnet_fwrf_method.png)

#### Figure 1: The fwRF model. 
(A) A schematic illustration of a fwRF model for a single voxel (grey box on brain, top right). The fwRF predicts the brain activity measured in the voxel, $r$, in response to any visual stimulus, $S$ (bottom left). The stimulus is transformed into one or more feature maps (three feature maps, $\Phi_k$, $\Phi_l$, and $\Phi_m$, are shown in blue with pink borders). The choice of feature maps is entirely up to the user, and reflects her hypotheses about the visual features that are relevant to brain regions of interest. The resolution of the feature maps ($\Delta$, indicated by pink grids) can vary, although each feature map spans the same degree of visual angle as the stimulus $S$. Each feature map is filtered by a 2D Gaussian feature pooling field, $g$, that is sampled from a grid of candidate feature pooling fields (grey box at top left; candidate feature pooling field centers ($\mu_x,\mu_y$) are illustrated by the grid of black points, while candidate feature pooling field radii ($\sigma_\text{g}$) are illustrated by dashed circles). The feature pooling field radius and location are the same for each feature map. The output of the feature pooling filtering operation (illustrated as black dots in the center of the dashed feature pooling fields on each feature map) for each feature map is then weighted by a feature weight (black curves labeled $w_k$, $w_l$, $w_m$). These weighted outputs are summed to produce a prediction of the activity $r$. In the text we describe an algorithm for selecting the optimal feature pooling field and feature weights for each voxel. (B) Gabor wavelet feature maps are constructed by convolving the input images with complex Gabor wavelets followed by a compressive nonlinearity (see text for details). (C) Deepnet feature maps were extracted the layers (labeled $K_i$) of a deep convolutional network pre-trained to label images according to object category.


## Main results

[The data used in this study](https://crcns.org/data-sets/vc/vim-1) consists of estimated voxel activation in response to 1,870 photographs, split into a training and a validation set of size 1,750 and 120 respectively. The figures in this study refer to data from subject 1 of the vim-1 dataset (similar results were obtained for subject 2).

We found that the DNN-based fwRF model (Deepnet-fwRF) had a significant overall advantage over both a Gabor-based fwRF model (Gabor-fwRF) and a layerwise regression model (Deepnet-lReg) based on the same DNN as Deepnet-fwRF. Figure 2 illustrate the model advantage over a large population of voxels. Furthermore, the feature pooling over various feature maps, which acts as a lower bound of pRF estimates, recovered well-known properties of organization of the visual cortex.

![diagram](/img/gabor_vs_refnet_vs_ridge_S1.png)

#### Figure 2: The model advantages.
Each of the four accuracy/advantage plots displays a comparison of prediction accuracies for two models. The position along the vertical axis indicates the average prediction accuracy for the models under comparison; shifts to the right or left along the horizontal axis indicated a relative improvement in prediction accuracy for one of the models (model 1 is presented to the left of model 2). The color of each hexagonal bin indicates the number of voxels in a local region of the plot (log scaled). The histogram at the top of each plot represent the distribution of relative improvements for all voxels whose prediction accuracy is above $0.2$ for at least one of the two models, which correspond graphically to all voxels above the red dashed line. The number on each side represents the fraction of voxels that are improved under that model. In the plots on the left, a shift in the data towards the left indicates an advantage for Gabor-fwRF model. In plots on the right, a shift of the data towards the right indicates an advantage for the Deepnet-lReg model. In all plots, a shift of the data toward the midline indicates an advantage for the Deepnet-fwRF. The upper plots display data for voxels in intermediate and higher visual areas (V4, V3A, V3B, LO, and "other"); the lower plots display data for voxels in the early visual cortex (V1, V2, V3). For intermediate brain areas, the Deepnet-fwRF outperforms both the layerwise regression and Gabor-fwRF models. For early visual areas, the Deepnet-fwRF strongly outperforms the layerwise regression model, but only weakly outperforms the Gabor-fwRF. The Deepnet-fwRF thus seems to have the strongest overall advantage for brain areas that require complex feature spaces. The "banana" shape of the distribution in the lower right suggests that the fwRF model provides strong and appropriate regularization, since voxels with low prediction accuracy under the more complex layerwise regression model are effectively "rescued" by the Deepnet-fwRF.
