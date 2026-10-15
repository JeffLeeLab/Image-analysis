# OMERO server-side scripts

These scripts run on the OMERO server as official (trusted) scripts. OMERO runs each one as a lone file with the server's own Python. So:

- each script is a single self-contained `.py` file;
- any extra package a script needs is installed into the OMERO server's Python venv, and listed in [requirements-scripts.txt](requirements-scripts.txt).

The server needs no conda. The `omero` conda environment is used only on your Mac, for the local tests and for the optional CLI upload.

| Script | Purpose | Extra packages |
|---|---|---|
| [subtract-background/](subtract-background/README.md) | Rolling-ball background subtraction | scipy |
| [z-project/](z-project/README.md) | Z-projection (max, sum or average intensity) | none |
| [Env_Check.py](Env_Check.py) | Reports the script interpreter, import path and package versions | none |

## Server environment

The OMERO administrator upgraded the server in October 2026 and reported:

- OMERO.server 5.6.18;
- Python 3.11;
- NumPy 2.4.6;
- SciPy 1.17.1 and scikit-image 0.26.0, the add-ons in [requirements-scripts.txt](requirements-scripts.txt).

Run `Env_Check.py` to confirm these, and to see the `omero-py` version, which the report did not give. The `omero` conda environment on your Mac has the same Python, NumPy and SciPy versions, so the local tests run against the server's library versions.

## Installing a script

OMERO treats every `.py` file under the server's `lib/scripts/` folder as an official script. There are three ways to put a script there. All of them need OMERO admin rights or sudo on the server.

**OMERO.web (simplest for an OMERO admin).** Admins with the WriteScriptRepo privilege, which every full admin has, can upload from the browser:

1. In the webclient, open the **Scripts** menu in the top toolbar and choose **Upload Script**, the last item. It opens a pop-up window.
2. Choose the `.py` file.
3. In **Upload into folder**, type a folder such as `/jefflee_lab`, or pick an existing one. The folder becomes a sub-menu of the Scripts menu. The window shows the full target path before you upload.
4. Click **Upload script**.

- **Update:** upload the new file with the same file name into the same folder. The button reads **Replace script**, and the script keeps its ID.
- **Remove:** OMERO.web cannot remove scripts. Use the CLI (`omero script delete SCRIPT_ID`) or delete the file on the server.

**Copying files on the server.** With sudo on the server, copy the files into `lib/scripts/`. No OMERO login is needed. The paths below assume the standard install. Check with `ls /opt/omero/server/OMERO.server/lib/scripts`: you should see the bundled `omero/` folder.

```sh
# On your Mac: copy the files to the server
scp omero/server-side-script/Env_Check.py \
    omero/server-side-script/requirements-scripts.txt \
    omero/server-side-script/subtract-background/Rolling_Ball_Background.py \
    omero/server-side-script/z-project/Z_Projection.py \
    USER@OMERO_HOST:/tmp/

# On the server: the folder name becomes the group name in the Scripts menu
SCRIPTS=/opt/omero/server/OMERO.server/lib/scripts/jefflee_lab
sudo install -d -o omero-server -g omero-server "$SCRIPTS"
sudo install -m 644 -o omero-server -g omero-server \
    /tmp/Env_Check.py /tmp/Rolling_Ball_Background.py /tmp/Z_Projection.py "$SCRIPTS"/
```

Then open the **Scripts** menu in OMERO.web. Each listing makes the server rescan `lib/scripts`, so the new scripts appear without a restart.

- **Update:** copy the new file over the old one the same way. The server registers a changed file under a **new script ID** and reads its parameters again on the next listing. OMERO.web finds it automatically; anything that refers to the old ID needs the new one.
- **Remove:** delete the file.

**Uploading from your Mac with the OMERO CLI.** This needs an OMERO admin login:

```sh
conda activate omero
omero login
omero script upload --official omero/server-side-script/Env_Check.py
omero script replace SCRIPT_ID omero/server-side-script/Env_Check.py   # later updates keep the ID
```

**After an OMERO upgrade** that installs a fresh `OMERO.server` directory, only the bundled scripts are present, whichever way you installed yours. Open the Scripts menu and install any missing scripts again. If the venv was recreated, redo the package steps below.

## Adding packages to the server's Python

The packages in [requirements-scripts.txt](requirements-scripts.txt) are already installed (see [Server environment](#server-environment)). Use these steps only to add or change packages.

Install `Env_Check.py` first and run it from OMERO.web. Its `python_executable` is the venv to change. The commands below assume the standard venv `/opt/omero/server/venv3`, owned by `omero-server`.

```sh
VENV=/opt/omero/server/venv3
RUN="sudo -u omero-server"     # the user that owns the venv

# 1. Rollback point (exact versions and sources, including the Ice wheel URL)
$RUN $VENV/bin/pip freeze > ~/omero-venv-freeze-$(date +%F).txt

# 2. Pin OMERO's own packages so add-ons cannot change them
$RUN $VENV/bin/pip list --format=freeze | grep -iE '^(omero-|zeroc-ice|numpy|pillow)' > /tmp/omero-core-constraints.txt

# 3. Install the whole add-on list against those pins, then check
$RUN $VENV/bin/pip install -r /tmp/requirements-scripts.txt -c /tmp/omero-core-constraints.txt
$RUN $VENV/bin/pip check
```

Each script run starts a fresh Python process, so newly installed packages are picked up without a restart.

Then:
1. Run `Env_Check.py` again and confirm the new versions.
2. Pin the resolved versions in `requirements-scripts.txt` (for example `scipy==1.17.1`) and commit the file.

Rules:

- **Always install the whole list against the constraints.** Never `pip install` a single package by hand.
- **If pip refuses because of a constraint,** the package needs a different NumPy, Pillow or Ice than OMERO uses. Don't override the constraint. Run that job off the server instead (see below).
- **Rollback:**
  1. `$RUN $VENV/bin/pip install -r ~/omero-venv-freeze-DATE.txt` restores the earlier versions.
  2. `$RUN $VENV/bin/pip uninstall PACKAGE` removes packages added since then.
- **After an OMERO upgrade that recreates the venv,** repeat steps 2–3. If the server is deployed with the `ome.omero_server` Ansible role, list these packages in its `omero_server_python_addons` variable instead.
- **In scripts, import add-on packages inside `try/except ImportError`.** A missing package then gives a clear message at run time, and the script still appears in OMERO.web.

Keep large or conflicting work off the OMERO server. This includes deep-learning tools (PyTorch, Cellpose, StarDist), packages with conflicting pins, and long or memory-heavy jobs. Run those from a workstation or HPC with `omero-py`, or through BIOMERO, rather than as server scripts.
