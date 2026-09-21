# Image conversion and OMERO workflow

This workflow is evolving and is not exhaustive. See `README.md` for setup.

- Use the global Miniforge environment: `conda activate omero`. Do not create an environment inside this repository. Dependencies are recorded in `environment.yml`.
- Convert with `./vsi-to-tif.sh`: choose a folder in the macOS GUI, or pass its path. The script uses `bfconvert` from the `omero` environment automatically.
- Convert only lowercase `*.vsi` files directly in that folder, producing `{basename}.tif` alongside each source. No recursion; skip existing outputs and preserve source files and VSI companion folders.
- Upload/import to OMERO after conversion. Start with `omero login`; server credentials and the target project/dataset must come from the user or established context. `omero-upload/` is currently empty; upload commands and automation are not yet documented or validated.
- Optional downstream analysis uses FIJI's OMERO Batch Plugin; see `omero_batch_plugin/README.md`. Computation runs locally against images stored in OMERO.
- Keep changes lightweight and update the README when the workflow changes. Do not present untested conversion, login, or import steps as verified.
