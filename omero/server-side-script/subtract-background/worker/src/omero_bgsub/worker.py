"""One persistent process per image, fixed file paths, JSON-lines control protocol."""
import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys


def protect_process(parent_pid, memory_bytes):
    if sys.platform.startswith("linux"):
        # Ensure a SIGKILL of the OMERO script does not leave SciPy running.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
        if os.getppid() != parent_pid:
            raise RuntimeError("Parent exited before worker startup")
        if memory_bytes:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def correct_plane(plane, radius):
    import numpy as np
    from .imagej_rolling_ball import (rolling_ball_background, subtract_background_image,
                                      imagej_shrink_factor)
    if plane.ndim != 2 or 0 in plane.shape or plane.dtype.name not in ("uint8", "uint16", "float32"):
        raise ValueError("Worker requires a nonempty 2D uint8/uint16/float32 plane")
    radius = float(radius)
    if not math.isfinite(radius) or radius < 0 or not np.isfinite(plane).all():
        raise ValueError("Non-finite pixels or invalid radius")
    stats = {"pixels": int(plane.size), "clipped_pixels": 0, "saturated_pixels": 0, "shrink": 0}
    if radius == 0:
        return plane.copy(), stats
    shrink = imagej_shrink_factor(radius)
    if shrink > 1 and min(dim // shrink for dim in plane.shape) < 2:
        shrink = 1
    stats["shrink"] = shrink
    background = rolling_ball_background(plane, radius, presmooth=True, light_background=False)
    result, fraction = subtract_background_image(plane, background)
    stats["clipped_pixels"] = int(round(fraction * plane.size))
    if plane.dtype.kind == "u":
        maximum = np.iinfo(plane.dtype).max
        stats["saturated_pixels"] = int(np.count_nonzero(result > maximum))
        result = np.floor(np.clip(result, 0, maximum) + 0.5)
    if not np.isfinite(result).all() or np.max(result) > np.finfo(np.float32).max:
        raise ValueError("Corrected values are not finite/representable")
    return result.astype(plane.dtype), stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--memory-bytes", type=int, default=0)
    args = parser.parse_args()
    protect_process(args.parent_pid, args.memory_bytes)
    import numpy as np
    import scipy
    from . import __version__, ALGORITHM_SHA256
    algorithm = Path(__file__).with_name("imagej_rolling_ball.py")
    if hashlib.sha256(algorithm.read_bytes()).hexdigest() != ALGORITHM_SHA256:
        raise RuntimeError("Vendored algorithm hash mismatch")
    root = args.directory.resolve(strict=True)
    print(json.dumps({"protocol": 1, "ready": True, "version": __version__,
                      "algorithm_sha256": ALGORITHM_SHA256, "python": sys.version.split()[0],
                      "numpy": np.__version__, "scipy": scipy.__version__}), flush=True)
    while True:
        line = sys.stdin.buffer.readline(8193)
        if not line:
            return
        if len(line) > 8192:
            raise ValueError("Control message too large")
        request = json.loads(line)
        if request.get("op") == "stop":
            return
        try:
            if request.get("protocol") != 1 or request.get("op") != "plane":
                raise ValueError("Unsupported protocol")
            plane = np.load(root / "input.npy", allow_pickle=False)
            result, stats = correct_plane(plane, request["radius"])
            np.save(root / "output.npy", result, allow_pickle=False)
            print(json.dumps({"ok": True, "sequence": request["sequence"], "stats": stats}), flush=True)
            del plane, result
        except Exception as exc:
            print(json.dumps({"ok": False, "fatal": isinstance(exc, (MemoryError, OSError)),
                              "error": type(exc).__name__ + ": " + str(exc)[:1000]}), flush=True)


if __name__ == "__main__":
    main()
