# Big-FISH smFISH spot quantification scripts

Pre-process and quantify single-molecule Fluorescence in situ Hybridisation images using a python package Big-FISH: https://github.com/fish-quant/big-fish.

Measure (1) individual spot counts in the cell + (2) spot counts within signal dense regions.

## INSTALLATION

### Set up virtual conda environment 

Make sure to use x86 version of Miniconda3, not the arm64 version (even if using M1 mac computers): https://docs.conda.io/en/latest/miniconda.html

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

## smFISH QUANTIFICATION

### Optimise parameters 

Use `smFISH-quantification_optimise-parameters.ipynb` notebook to determine smFISH quantification parameters on a few subset of images.

    cd /path/to/working/directory/
    jupyter lab

Following should be determined from the notebook:

* Image voxel size and PSF parameters
* Image channels to quantify (0-based)
* Whether auto thresholding will be used 
    * requires a fair number of spots within the image
    * signal-to-background ratio of smFISH spots should be > 2 in raw images for reliable use
* If not using automated thresholding, find a suitable manual intensity threshold 
    * Draw several line profiles over smFISH spots in `rna_log.tif` on ImageJ to find a suitable threshold
* Dense region decomposition parameters
    * how wide the dense regions should be? (`bf_radius` in nanometers)
    * minimum number of spots within a dense region for it to be considered a cluster (`nb_min_spots`)

### Batch processing multiple images 

Port the pre-determined quantification parameters to `smFISH_analysis_config.yaml` file. 

#### general configuration 

* `number_of_workers`: Number of CPUs to use
* `input_pattern`: Input image directory - use wildcard to grab the images 
* `output_dir`: Directory where spot and cluster coordinates will be saved 
* `output_qc_dir`: Directory where quality control files will be saved (e.g. reference spot, elbow plot..etc)

#### cellpose configuration
* `seg_chan`: channel to segmeng (0-based)  
* `clip_intensity`: Boolean - whether to clip the intensity value before segmentation  
* `clip_value`: Integer - if clip_intensity == True, intensity value to clip  
* `median_filter`: Median filter radius  
* `diameter`: Diameter (nm) of the cell 
* `flow_threshold`: cellpose flow threshold - affects number of cells found  
* `cellprob_threshold`: cellpose cellprob threshold - affects size of the cells  
* `use_gpu`: Boolean - whether to use GPU  
* `do_3D`: Boolean - whether to do 3D segmentation (currently not supported with bigfish)  

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

* `smFISH_ch1`: First smFISH channel number - should match the `channels` configuration 
* `smFISH_ch1_thresh`: LoG filtered spot intensity threshold for the first channel 
* `smFISH_ch2`: Second smFISH channel number 
* `smFISH_ch2_thresh`: LoG filtered spot intensity threshold for the second channel 

* `alpha`: 0.5 - Do not change 
* `beta`: 1 - Do not change 
* `bf_radius`: Cluster radius in nanometer
* `nb_min_spots`: Mimimum number of spots required for a dense region to be considered a cluster. 

#### start batch process

Start the batch process by running the multiprocess python script. Make sure the YAML configuration file is in the same directory as the python script. 

    cd /path/to/python/script/
    python smFISH_data_analysis_multiprocess.py

### BigFISH output 

Batch processing produces the main output in `.npz` format as well as several quality control data. 

#### spot and cluster coordinates 

`.npz` file contains the centroid coordinates of spots and clusters. Separate files will be produced for each cell and smFISH channels. 

`.npz` files contain two main lists:  

* `"rna_coord"`: Information of individual spots  
    * spot centroid z coordinate   
    * spot centroid y coordinate  
    * spot centroid x coordinate  
    * cluster index number if assigned - '-1' if not in a cluster
* `"foci"`: Information of clusters  
    * cluster centroid z coordinate  
    * cluster centroid y coordinate  
    * cluster centroid x coordinate  
    * number of individual spots in cluster  
    * cluster index number  

It can be parsed individually or in batch to summarise the experiment (see the section below).  

    import numpy as np
    npz_output = np.load("/path/to/npz/file")

    ## Get individual spot coordinates
    npz_output["rna_coord"]

    ## Get cluster coordinates 
    npz_output["foci"]

#### quality control data

These can be useful to confirm veracity of the analysis and/or to perform troubleshooting. 

* Final detection plot: A plot of individual spots and clusters that are detected  
* Reference spot image: A .tif image of the reference spot  
    * This image is the *undenoised* version, which can be used to extrapolate spot counts via integrated intensity method    
* Elbow plot: An elbow plot of automated thresholding - only produced if `auto_threshold = True`   

## GET FINAL CSV FROM BATCH PROCESSING

Use `parse_npz.ipynb` to collate individual `.npz` files for each cell/channel. This notebook will create a pandas dataframe with summary statistics of the smFISH quantificaiton. 







