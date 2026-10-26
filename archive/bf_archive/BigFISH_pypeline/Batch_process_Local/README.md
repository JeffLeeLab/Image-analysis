# Bigfish batch process local images

## Pre-requisite 

First optimise `cellpose` and `bigfish` parameters using `smFISH_data_exploration_Local-image.ipynb`.

Following should be defined beforehand:

    # Cellpose configuration
    ## Channel to segment (0-based)
    seg_ch: 1
    ## If using FISH channel as mask, clipping the intensity value can help aid segmentation
    ## Define if clipping will be applied and if true, the maximum intensity value to clip
    cp_apply_clip: False
    cp_clip_value: 50
    ## Median filter radius to aid segmentation
    median_filter: 20
    ## Cellpose parameters
    diameter: 325
    flow_threshold: 0.9
    cellprob_threshold: -6
    do_3D: False # Enable 3D segmentation of 4D data
    ## Boolean flag to toggle GPU support in Cellpose
    gpu: False

    # Bigfish configuration
    ## Image voxel sizes
    voxel_size_yx: 65
    voxel_size_z: 200
    ## PSF parameters
    ex: 570
    em: 610
    NA: 1.4
    RI: 1.364
    microscope: confocal # widefield or nipkow
    ## Bigfish single-spot detection parameters
    bg_radius: 5
    smFISH_ch1: 2
    smFISH_ch1_thresh: 50
    smFISH_ch2: 3
    smFISH_ch2_thresh: 30
    # Bigfish cluster decomp and foci detection parameters
    alpha: 0.7
    beta: 1
    bf_radius: 350
    nb_min_spots: 4

## Modify the YAML configuration file 

Transfer the optimised parameters to `smFISH_analysis_config.yaml` file. All the images will be batch processed using the same configuration. 

Choose how many threads to run in parallel.

    ## The number of processes in the pool of processes
    number_of_workers: 4

## Define input and output directories in the YAML configuration file 

Define Input directory where the images are stored. Give a wildcard + extension (e.g. *.tif) so that the script loops over all the images in the folder. 

    ## The directory with images
    input_pattern: /Users/jefflee/Downloads/Batch_Image_Export/*.tif

Also define output directories. Quantification results are saved as `.npz` file per segmented cell. Reference spot images are also saved (per image), which can be used to extrapolate RNA counts in cells with spatially saturated FISH signal. 

    ## The directory where the big-fish output data will be stored
    output_dir: /Users/jefflee/Downloads/Batch_Image_Export/output
    ## The directory where the reference spot output data will be stored
    output_refspot_dir: /Users/jefflee/Downloads/Batch_Image_Export/output_ref_spot

## Run batch process

The `smFISH_data_analysis_multiprocess.py` script should be in the same directory as the `smFISH_analysis_config.yaml`. 

    ## Command-line
    python smFISH_data_analysis_multiprocess.py

## Parse .npz files 

All the outputs are stored in `.npz` files (per cell) in the output folder. Use `parse_npz.ipynb` notebook to make a final output dataframe. 




