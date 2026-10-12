#!/usr/bin/env python
"""Run with the processor's Python as its OS user. Does not connect to OMERO."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "adapter"))
    import numpy as np
    import Ice
    import omero.gateway
    from bgsub_adapter import __version__
    from bgsub_adapter.runtime import load_config, limit_wrapper_memory, Worker, admission
    cfg = load_config(args.config)
    limit_wrapper_memory(cfg)
    with admission(cfg["journal_root"]):
        with Worker(cfg, time.monotonic() + 60) as worker:
            array = np.array([[-2, 0], [1, 42]], dtype=np.float32)
            result, _ = worker.plane(array, "0")
            if not np.array_equal(array, result):
                raise RuntimeError("Worker zero-radius round-trip failed")
            print(json.dumps({"adapter": __version__, "wrapper_python": sys.executable,
                              "omero_py": importlib.metadata.version("omero-py"),
                              "Ice": Ice.stringVersion(), "worker": worker.info,
                              "result": "imports, writable paths, lock, isolated worker and zero-copy IPC OK"}, indent=2))


if __name__ == "__main__":
    main()
