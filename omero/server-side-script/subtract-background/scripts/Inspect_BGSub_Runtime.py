#!/usr/bin/env python
"""Optional read-only trusted diagnostic: identify the actual processor runtime."""
import importlib.metadata
import json
import os
import platform
import sys
import omero.scripts as scripts
from omero.rtypes import rstring


def run():
    client = scripts.client("Inspect_BGSub_Runtime.py", "Report the script processor interpreter and package versions; no image writes.")
    try:
        info = {"python_executable": sys.executable, "python": platform.python_version(),
                "platform": platform.platform(), "machine": platform.machine(), "uid": os.getuid()}
        for package in ("omero-py", "numpy", "zeroc-ice"):
            try:
                info[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                info[package] = "not installed in processor interpreter"
        client.setOutput("Message", rstring(json.dumps(info, indent=2)))
    finally:
        client.closeSession()


if __name__ == "__main__":
    run()
