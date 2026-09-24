# Zhiling-Marko-HIV

Segmentation of HIV transcription foci and measurement of nucleus/foci FISH intensities.

- `segment-hiv-transcription-foci-workbook.ipynb`: choose the threshold method on example images.
- `src/segment-hiv-transcription-foci-batch.py`: apply that method to every image in a folder.
- `measure-foci-nucleus-fish-intensities.ipynb` and `src/nucleus_foci_quant.py`: per-nucleus foci and FISH intensity quantification.
- `cellpose-sam-workbook.ipynb`: Cellpose-SAM nucleus segmentation (copied from `Segmentation/Cellpose-SAM-AppleM1`).

Run everything from this directory with `uv run`, except `cellpose-sam-workbook.ipynb`: it uses the `Cellpose-SAM Apple M1 (.venv)` kernel, which runs `Segmentation/Cellpose-SAM-AppleM1/.venv`. Cellpose is not installed in this project's `.venv`.

## Excluded images

These two images are left out of all further analysis because their shape does not match the other images in the dataset:

- `160323_GFP_Dox_INF-48hpi_010.tif`
- `160323_MARF1_NoDox_INF-48hpi_006.tif`

Move them out of the input folder before a batch run (the batch script has no exclude option), and drop their rows from any combined results.
