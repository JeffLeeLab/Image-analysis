# Big-FISH HCRv3 spot quantification scripts

Pre-process and quantify single-molecule Fluorescence in situ Hybridisation images using a python package Big-FISH: https://github.com/fish-quant/big-fish.
NOTE: These scripts are stripped-down version of smFISH analysis with no cluster decomposition steps. These scripts will only output HCRv3 spot counts. 

> Objective: Measure spot counts from of HCRv3 *in situ* hybridisation images

## INSTALLATION

### Set up virtual conda environment 

Make sure to use x86 version of Miniconda3, not the arm64 version (even if using M1 mac computers): https://docs.conda.io/en/latest/miniconda.html  
Compatible with existing smFISH conda environment. 

    conda create -n bigfish_v6 python=3.7
    conda activate bigfish_v6

    # virtual environment can be detactivated as follows
    conda deactivate

### BigFISH installation

    pip install big-fish
    pip install ipykernel
    pip install jupyterlab

    # if segmenting tissue culture cells also install cellpose (the version may need to be 0.7.2)
    pip install cellpose 

## HCRv3 QUANTIFICATION

### Optimise parameters 

Use `HCRv3-quantification_optimise-parameters.ipynb` notebook to determine HCRv3 quantification parameters on a few subset of images.

    cd /path/to/working/directory/
    jupyter lab

Following should be determined from the notebook:

* Image voxel size and PSF parameters
* Image channels to quantify (0-based)
* Whether auto thresholding will be used 
    * requires a fair number of spots within the image
    * signal-to-background ratio of HCRv3 spots should be > 2 in raw images for reliable use
* If not using automated thresholding, find a suitable manual intensity threshold 
    * Draw several line profiles over HCRv3 spots in `rna_log.tif` on ImageJ to find a suitable threshold

### Batch processing multiple images 

Port the pre-determined quantification parameters to `HCRv3_analysis_config.yaml` file. 

#### general configuration 

* `number_of_workers`: Number of CPUs to use
* `input_pattern`: Input image directory - use wildcard to grab the images 
* `output_dir`: Directory where spot and cluster coordinates will be saved 
* `output_qc_dir`: Directory where quality control files will be saved (e.g. final detection plot, elbow plot..etc)

#### bigfish configuration

* `voxel_size_yx`: XY voxel size in nm
* `voxel_size_z`: Z step voxel size in nm
* `ex`: Dye excitation maxima
* `em`: Dye emission maxima
* `NA`: Numerical aperture of the microscope objective
* `RI`: Refractive index of the mounting media
* `microscope`: Either 'confocal' or 'widefield'

* `bg_radius`: Background subtraction kernel radius. Usually 5 is okay. 

* `channels`: Image channels to quantify (0-based). Use [2, 3] or [2] format 

* `auto_threshold`: True/False - Whether automated thresholding will be used

* `HCRv3_ch1`: First HCRv3 channel number - should match the `channels` configuration 
* `HCRv3_ch1_thresh`: LoG filtered spot intensity threshold for the first channel 
* `HCRv3_ch2`: Second HCRv3 channel number 
* `HCRv3_ch2_thresh`: LoG filtered spot intensity threshold for the second channel 

#### start batch process

Start the batch process by running the multiprocess python script. Make sure the YAML configuration file is in the same directory as the python script. 

    cd /path/to/python/script/
    python HCRv3_data_analysis_multiprocess.py

### BigFISH output 

Batch processing produces the main output in `.npz` format as well as several quality control data. 

#### spot and cluster coordinates 

`.npz` file contains the centroid coordinates of spots. Separate files will be produced for each image and HCRv3 channels. 

`.npz` files contain two variables:  

* `"spots_post_subpixel"`: Information of individual spots  
    * spot centroid z coordinate   
    * spot centroid y coordinate  
    * spot centroid x coordinate  
    * cluster index number if assigned - '-1' if not in a cluster

It can be parsed individually or in batch to summarise the experiment.  

    import numpy as np
    npz_output = np.load("/path/to/npz/file")
    sorted(npz_output)

    ## Get individual spot coordinates
    npz_output["spots_post_clustering"]

#### quality control data

These can be useful to confirm veracity of the analysis and/or to perform troubleshooting. 

* Final detection plot: A plot of individual spots that are detected  
* Elbow plot: An elbow plot of automated thresholding - only produced if `auto_threshold = True`   

## SEGMENTATION AND SPOT DISTRIBUTION

See `HCRv3-quantification_get-cell-level-results.ipynb` for help. 





