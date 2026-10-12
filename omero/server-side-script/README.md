# OMERO server-side scripts

These scripts are uploaded to the OMERO server as official (trusted) scripts. OMERO runs each one as a lone file with the server's own Python. So:

- each script is a single self-contained `.py` file;
- any extra package a script needs is installed into the OMERO server's Python, and listed in [requirements-scripts.txt](requirements-scripts.txt).

| Script | Purpose | Extra packages |
|---|---|---|
| [subtract-background/](subtract-background/README.md) | Batch rolling-ball background subtraction | scipy |
| [Env_Check.py](Env_Check.py) | Reports the script interpreter, import path and package versions | none |

## Adding packages to the server's Python

You need sudo on the OMERO server host. The commands below assume the standard install: venv `/opt/omero/server/venv3`, owned by `omero-server`. Before starting, run `Env_Check.py` and check that its `python_executable` matches that venv. Copy `requirements-scripts.txt` to the server first.

```sh
VENV=/opt/omero/server/venv3
RUN="sudo -u omero-server"     # the user that owns the venv

# 1. Rollback point (exact versions and sources, including the Ice wheel URL)
$RUN $VENV/bin/pip freeze > ~/omero-venv-freeze-$(date +%F).txt

# 2. Pin OMERO's own packages so add-ons cannot change them
$RUN $VENV/bin/pip list --format=freeze | grep -iE '^(omero-|zeroc-ice|numpy|pillow)' > omero-core-constraints.txt

# 3. Install the whole add-on list against those pins, then check
$RUN $VENV/bin/pip install -r requirements-scripts.txt -c omero-core-constraints.txt
$RUN $VENV/bin/pip check
```

Each script run starts a fresh Python process, so newly installed packages are picked up without a restart.

Then:
1. Run `Env_Check.py` from OMERO.web and confirm the new versions.
2. Pin the resolved versions in `requirements-scripts.txt` (for example `scipy==1.15.3`) and commit the file.

Rules:

- **Always install the whole list against the constraints.** Never `pip install` a single package by hand.
- **If pip refuses because of a constraint,** the package needs a different NumPy, Pillow or Ice than OMERO uses. Don't override the constraint. Run that job off the server instead (see below).
- **Rollback:**
  1. `$RUN $VENV/bin/pip install -r ~/omero-venv-freeze-DATE.txt` restores the earlier versions.
  2. `$RUN $VENV/bin/pip uninstall PACKAGE` removes packages added since then.
- **After an OMERO upgrade that recreates the venv,** repeat steps 2–3. If the server is deployed with the `ome.omero_server` Ansible role, list these packages in its `omero_server_python_addons` variable instead.
- **In scripts, import add-on packages inside `try/except ImportError`.** A missing package then gives a clear message at run time, and the script still appears in OMERO.web.

Keep large or conflicting work off the OMERO server. This includes deep-learning tools (PyTorch, Cellpose, StarDist), packages with conflicting pins, and long or memory-heavy jobs. Run those from a workstation or HPC with `omero-py`, or through BIOMERO, rather than as server scripts.

## Uploading a script

```sh
conda activate omero
omero login
omero script upload --official Env_Check.py
omero script list
```

Per-script instructions, including `script replace` for updates, are in each script's README.
