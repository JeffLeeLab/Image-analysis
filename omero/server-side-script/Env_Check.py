#!/usr/bin/env python
"""Read-only diagnostic: report the Python that runs OMERO scripts and its key packages.

Upload once as an official script and run it after changing the server's Python
packages (see README.md). Keep the package list in step with requirements-scripts.txt.
"""
import importlib.metadata
import json
import os
import platform
import sys
import omero.scripts as scripts
from omero.rtypes import rstring


def run():
    client = scripts.client("Env_Check.py", "Report the script interpreter, import path and package versions; no writes.")
    try:
        info = {"python_executable": sys.executable, "python": platform.python_version(),
                "platform": platform.platform(), "machine": platform.machine(), "uid": os.getuid(),
                "sys_path": sys.path}
        for package in ("omero-py", "zeroc-ice", "numpy", "Pillow", "scipy"):
            try:
                info[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                info[package] = "not installed in processor interpreter"
        client.setOutput("Message", rstring(json.dumps(info, indent=2)))
    finally:
        client.closeSession()


if __name__ == "__main__":
    run()
