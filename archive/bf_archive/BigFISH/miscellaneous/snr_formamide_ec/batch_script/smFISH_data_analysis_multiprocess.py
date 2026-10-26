# Modified: 2024.05.28

import pathlib
import glob
import signal
import queue
import multiprocessing
import threading
import time
import yaml
import scipy
from skimage.morphology import white_tophat, black_tophat, disk
import numpy as np
import pandas as pd
import tifffile
import bigfish.stack as stack
import bigfish.multistack as multistack 
import bigfish.detection as detection
import bigfish.plot as plot


# calculate psf (thank you MK), with edit for consistent nomenclature
def calculate_psf(voxel_size_z, voxel_size_yx, Ex, Em, NA, RI, microscope):
    """
    Use the formula implemented in Matlab version (sigma_PSF_BoZhang_v1)
    to calculate the theoretical PSF size.
    """
    if microscope == "widefield":
        psf_yx = 0.225 * Em / NA
        psf_z = 0.78 * RI * Em / (NA ** 2)
    elif microscope in ("confocal", "nipkow"):
        psf_yx = 0.225 / NA * Ex * Em / np.sqrt(Ex ** 2 + Em ** 2)
        psf_z = 0.78 * RI / NA ** 2 * Ex * Em / np.sqrt(Ex ** 2 + Em ** 2)
    else:
        # Unrecognised microscope
        raise Exception(
            "Unrecognised microscope argument for function calculate_psf()."
        )
    return psf_z, psf_yx


# subtract background
def subtract_background(image, radius, light_bg=False):
    # you can also use 'ball' here to get a slightly smoother result at the
    # cost of increased computing time
    str_el = disk(radius)
    if light_bg:
        return black_tophat(image, str_el)
    else:
        return white_tophat(image, str_el)


def image_processing_function(image_loc, config):
    # Read the image into a numpy array of format ZCYX
    image_name = pathlib.Path(image_loc).stem
    image = tifffile.imread(image_loc)

    print(" ")
    print("Processing: ", image_name)

    # Calculate PSF
    psf_z, psf_yx = calculate_psf(
        config["voxel_size_z"],
        config["voxel_size_yx"],
        config["ex"],
        config["em"],
        config["NA"],
        config["RI"],
        config["microscope"],
    )

    spot_radius_px = detection.get_object_radius_pixel(
        voxel_size_nm=(config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
        object_radius_nm=(psf_z, psf_yx, psf_yx),
        ndim=3)
    
    # process single spot detection + cluster decomposition per channel
    for image_channel in config["channels"]:
        # - - - - - detect spots
        rna = image[:, image_channel, :, :]
        # subtract background
        rna_no_bg = []
        for z in rna:
            z_no_bg = subtract_background(z, config["bg_radius"])
            rna_no_bg.append(z_no_bg)
        rna = np.array(rna_no_bg)

        # LoG filter
        rna_log = stack.log_filter(rna, spot_radius_px)

        # local maximum detection
        mask = detection.local_maximum_detection(
            rna_log, min_distance=spot_radius_px)

        # thresholding
        if config["auto_threshold"] == True:
            elbow_plot_output_path = pathlib.Path(config["output_qc_dir"]).joinpath(
                f"{image_name}_ch{image_channel}_elbow_plot"
            )
            plot.plot_elbow(
                rna,
                voxel_size=(
                    config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
                spot_radius=(psf_z, psf_yx, psf_yx),
                path_output=str(elbow_plot_output_path),
                ext="png",
                show=False
            )
            threshold = detection.automated_threshold_setting(rna_log, mask)
        else:
            if image_channel == config["smFISH_ch1"]:
                threshold = config["smFISH_ch1_thresh"]
            elif image_channel == config["smFISH_ch2"]:
                threshold = config["smFISH_ch2_thresh"]
            else:
                print("smFISH channel and threshold not correctly defined!")

        spots, _ = detection.spots_thresholding(rna_log, mask, threshold)


        # - - - - - detect and decompose clusters
        # get the reference spot
        kernel_size = detection.get_object_radius_pixel(
            voxel_size_nm=(config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            object_radius_nm=(psf_z, psf_yx, psf_yx),
            ndim=3
        )
        large_kernel_size = tuple(
            [kernel_size_ * 5 for kernel_size_ in kernel_size])

        rna_denoised = stack.remove_background_gaussian(
            rna, sigma=large_kernel_size)

        reference_spot = detection.build_reference_spot(
            image=rna_denoised,
            spots=spots,
            voxel_size=(config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            spot_radius=(psf_z, psf_yx, psf_yx),
            alpha=config["alpha"]
        )

        reference_spot_undenoised = detection.build_reference_spot(
            image=rna,
            spots=spots,
            voxel_size=(config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            spot_radius=(psf_z, psf_yx, psf_yx),
            alpha=config["alpha"]
        )

        # save undenoised ref spot
        spot_output_path = pathlib.Path(config["output_qc_dir"]).joinpath(
            f"{image_name}_ch{image_channel}_referencespot"
        )
        stack.save_image(
            reference_spot_undenoised, str(spot_output_path), "tif"
        )

        # decompose dense regions
        sigma_z, sigma_yx, amplitude, background = detection.modelize_spot(
            reference_spot=reference_spot,
            voxel_size=(config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            spot_radius=(psf_z, psf_yx, psf_yx)
        )

        regions_to_decompose, spots_out_regions, region_size = detection.get_dense_region(
            image=rna_denoised,
            spots=spots,
            voxel_size=(config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            spot_radius=(psf_z, psf_yx, psf_yx),
            beta=config["beta"]
        )

        max_grid = max(200, region_size + 1)
        precomputed_gaussian = detection.precompute_erf(
            ndim = 3,
            voxel_size = (config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            sigma = (sigma_z, sigma_yx, sigma_yx),
            max_grid = max_grid
        )

        spots_in_regions, _ = detection.simulate_gaussian_mixture(
            image = rna_denoised,
            candidate_regions = regions_to_decompose,
            voxel_size = (config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            sigma = (sigma_z, sigma_yx, sigma_yx),
            amplitude = amplitude,
            background = background,
            precomputed_gaussian = precomputed_gaussian)

        spots_post_decomposition = np.concatenate((spots_out_regions, spots_in_regions[:, :3]), axis=0)

        # subpixel fitting
        spots_post_decomposition_subpixel = detection.fit_subpixel(
            image = rna,
            spots = spots_post_decomposition,
            voxel_size = (config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            spot_radius = (psf_z, psf_yx, psf_yx)
        )

        # cluster decomposed spots
        spots_post_clustering, clusters = detection.detect_clusters(
            spots = spots_post_decomposition_subpixel, 
            voxel_size = (config["voxel_size_z"], config["voxel_size_yx"], config["voxel_size_yx"]),
            radius = config["bf_radius"],
            nb_min_spots = config["nb_min_spots"],
        )   

        # plot final detection
        final_detection_plot_output = pathlib.Path(config["output_qc_dir"]).joinpath(
            f"{image_name}_ch{image_channel}_finaldetection"
        )
        rna_mip = stack.maximum_projection(rna)
        plot.plot_detection(rna_mip,
                            spots=[spots_post_decomposition_subpixel,
                                clusters[:, :3]],
                            shape=["circle", "polygon"],
                            radius=[spot_radius_px[-1],
                                    config["bf_radius"]/config["voxel_size_yx"]],
                            color=["orange", "cyan"],
                            linewidth=[1, 2],
                            fill=[False, True],
                            framesize=(20, 16),
                            contrast=True,
                            path_output=str(final_detection_plot_output),
                            ext="png",
                            show=False
                            )


        # save spots and clusters results in a npz file
        npz_output_path = pathlib.Path(config["output_dir"]).joinpath(
            f"{image_name}_ch{image_channel}_bfoutput"
        )
        np.savez(str(npz_output_path), spots_post_clustering = spots_post_clustering, clusters = clusters)

        # save pandas df of spots_post_clustering for quick output not requiring npz handling
        df_output_path = pathlib.Path(config["output_dir"]).joinpath(
            f"{image_name}_ch{image_channel}_bfoutput_dataframe.csv"
        )
        
        spots_post_clustering_df = pd.DataFrame(
            data = spots_post_clustering[0:],
            columns = ["z", "y", "x", "cluster_index"]
        )

        spots_post_clustering_df.to_csv(df_output_path, index = False)


def worker_function(jobs, results):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while not jobs.empty():
        try:
            job = jobs.get(block=False)
            results.put(image_processing_function(*job))
        except queue.Empty:
            pass


def main():
    jobs = multiprocessing.Queue()
    results = multiprocessing.Queue()

    # Load the config file
    with open("smFISH_analysis_config.yaml") as fi:
        config = yaml.load(fi, Loader=yaml.Loader)

    # Check if output directories exists; try to create them if they don't
    pathlib.Path(config["output_dir"]).mkdir(exist_ok=True)
    pathlib.Path(config["output_qc_dir"]).mkdir(exist_ok=True)

    # Fill the job queue either with local files identified using the input path pattern
    image_paths = glob.glob(config["input_pattern"])
    for image_path in image_paths:
        jobs.put((image_path, config))

    # Start workers
    workers = []
    for i in range(config["number_of_workers"]):
        p = multiprocessing.Process(
            target=worker_function, args=(jobs, results)
        )
        p.start()
        workers.append(p)

    # Wait for workers to complete
    try:
        for worker in workers:
            worker.join()
    except KeyboardInterrupt:
        for worker in workers:
            worker.terminate()
            worker.join()


if __name__ == "__main__":
    # Set the process start method; spawn is default on Windows
    multiprocessing.set_start_method("spawn")
    # Call the main function
    main()
