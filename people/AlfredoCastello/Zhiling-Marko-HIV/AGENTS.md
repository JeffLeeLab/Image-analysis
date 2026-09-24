# Project instructions

This subproject uses `uv` and its local `.venv` for Python dependencies.

- Run Python and project commands from this directory using `uv run`.
- Manage dependencies using `uv add` and `uv remove`; do not use `pip` directly unless a package is incompatible with `uv`.
- Exception: `cellpose-sam-workbook.ipynb` runs on the `cellpose-sam-applem1` kernel (`Segmentation/Cellpose-SAM-AppleM1/.venv`). Keep that kernelspec; do not add Cellpose to this project.
- If a pip fallback is necessary, install into this project’s `.venv`, document the reason, and do not modify another subproject’s environment.
## Excluded images

Do not analyse these images further. Their array shape does not match the other images in the dataset:

- `160323_GFP_Dox_INF-48hpi_010.tif`
- `160323_MARF1_NoDox_INF-48hpi_006.tif`

Skip them in batch runs, notebooks and downstream quantification, and leave their rows out of combined results.
