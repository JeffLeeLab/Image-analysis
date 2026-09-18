# Cellpose-SAM on Apple Silicon

uv-managed Python 3.12 environment with Cellpose 4+, PyTorch/torchvision,
NumPy, pandas, SciPy, scikit-image (`import skimage`), JupyterLab and ipykernel.
Exact package versions are recorded in `uv.lock`; the environment is `.venv`.

## Start JupyterLab

From the repository root:

```sh
cd Segmentation/Cellpose-SAM-AppleM1
uv run jupyter lab
```

The default Python kernel uses this environment. For terminal Python sessions,
use `uv run python` or activate with `source .venv/bin/activate`.

## Use the Apple GPU

PyTorch's native macOS ARM64 distribution includes Metal Performance Shaders
(MPS) support. No CUDA installation is needed.

```python
import torch
from cellpose import models

assert torch.backends.mps.is_available()
model = models.CellposeModel(gpu=True, device=torch.device("mps"))
# masks, flows, styles = model.eval(image)
```

Or run the CLI on a folder of images:

```sh
uv run python -m cellpose --dir /path/to/images --use_gpu --save_tif
```

Model weights download on first use into `~/.cellpose/models`.
Some mask postprocessing runs on the CPU even with MPS enabled.
This installation supports notebooks and the CLI; the optional desktop GUI
can be added with `uv add 'cellpose[gui]>=4'`.

## Recreate or upgrade

```sh
uv sync --locked
# To update packages and record new versions:
uv lock --upgrade
uv sync
```

References: [Cellpose Apple Silicon installation](https://cellpose.readthedocs.io/en/latest/installation.html)
and [Cellpose project](https://github.com/MouseLand/cellpose).
