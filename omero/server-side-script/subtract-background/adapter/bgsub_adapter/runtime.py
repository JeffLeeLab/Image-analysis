"""Operator config, durable local journals, admission lock and isolated worker IPC."""
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import ALGORITHM_SHA256, __version__
from .planning import ValidationError


class SystemicError(RuntimeError):
    """Stop the batch; a per-image retry would be misleading."""


def atomic_json(path, value):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_config(path):
    with open(path, encoding="utf-8") as stream:
        cfg = json.load(stream)
    for key in ("worker_python", "journal_root", "temporary_root"):
        value = cfg.get(key, "")
        if not value or not Path(value).is_absolute():
            raise ValidationError("Operator configuration requires absolute " + key)
    required = ("max_radius_pixels", "max_images_per_batch", "max_planes_per_batch",
                "max_xy_plane_pixels", "max_output_bytes", "max_name_length",
                "max_batch_seconds", "worker_timeout_seconds", "worker_memory_bytes", "wrapper_memory_bytes",
                "min_temporary_free_bytes")
    for key in required:
        value = cfg.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValidationError("Operator configuration requires positive " + key)
    if cfg.get("max_concurrent_jobs") != 1:
        raise ValidationError("This release supports max_concurrent_jobs=1 per lock domain")
    for key in ("journal_root", "temporary_root"):
        if not Path(cfg[key]).is_dir() or not os.access(cfg[key], os.W_OK):
            raise ValidationError("Service account needs a writable directory: " + cfg[key])
    if not os.path.isfile(cfg["worker_python"]) or not os.access(cfg["worker_python"], os.X_OK):
        raise ValidationError("Worker Python is missing/not executable: " + cfg["worker_python"])
    return cfg


def limit_wrapper_memory(cfg):
    """Bound this script process, never the long-running processor/server service."""
    if not sys.platform.startswith("linux"):
        return  # local macOS checks; production target is Linux
    import resource
    cap = int(cfg["wrapper_memory_bytes"])
    with open("/proc/self/status", encoding="ascii") as stream:
        for line in stream:
            if line.startswith("VmSize:") and int(line.split()[1]) * 1024 >= cap:
                raise SystemicError("Wrapper already exceeds configured address-space cap; review installation limits")
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (cap if hard == resource.RLIM_INFINITY else min(cap, hard), hard))


@contextlib.contextmanager
def admission(root):
    path = Path(root) / "processing.lock"
    with open(path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemicError("Background subtraction is busy; another job holds the admission lock") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Journal:
    def __init__(self, root, run_id, initial=None):
        self.path = Path(root) / (run_id + ".json")
        if initial is None:
            with self.path.open(encoding="utf-8") as stream:
                self.data = json.load(stream)
        else:
            if self.path.exists():
                raise SystemicError("Run ID already exists")
            self.data = initial
            self.save()

    def save(self):
        self.data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_json(self.path, self.data)


class Worker:
    def __init__(self, cfg, deadline):
        self.cfg, self.deadline = cfg, deadline
        self.proc = self.selector = None
        self.root = None
        self.sequence = 0

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="bgsub-", dir=self.cfg["temporary_root"]))
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": str(self.root),
               "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        try:
            self.proc = subprocess.Popen(
                [self.cfg["worker_python"], "-I", "-m", "omero_bgsub.worker",
                 "--directory", str(self.root), "--parent-pid", str(os.getpid()),
                 "--memory-bytes", str(int(self.cfg["worker_memory_bytes"]))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, start_new_session=True, bufsize=0)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.proc.stdout, selectors.EVENT_READ, "stdout")
            self.selector.register(self.proc.stderr, selectors.EVENT_READ, "stderr")
            self.info = self.receive()
            if (not self.info.get("ready") or self.info.get("protocol") != 1
                    or self.info.get("algorithm_sha256") != ALGORITHM_SHA256
                    or self.info.get("version") != __version__):
                raise SystemicError("Worker version/protocol/algorithm mismatch")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def receive(self):
        end = min(self.deadline, time.monotonic() + self.cfg["worker_timeout_seconds"])
        data, errors = bytearray(), bytearray()
        while time.monotonic() < end:
            for key, _ in self.selector.select(min(1, max(0, end - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    raise SystemicError("Worker exited: " + errors.decode("utf-8", "replace")[-2000:])
                if key.data == "stderr":
                    errors.extend(chunk)
                    del errors[:-4096]
                    continue
                data.extend(chunk)
                if len(data) > 8192:
                    raise SystemicError("Worker reply exceeds protocol limit")
                if b"\n" in data:
                    return json.loads(data)
        raise SystemicError("Worker/batch deadline exceeded")

    def plane(self, plane, radius):
        import numpy as np
        if shutil.disk_usage(self.root).free < max(self.cfg["min_temporary_free_bytes"], plane.nbytes * 3):
            raise SystemicError("Insufficient worker temporary disk space")
        np.save(self.root / "input.npy", plane, allow_pickle=False)
        self.sequence += 1
        request = {"protocol": 1, "op": "plane", "sequence": self.sequence, "radius": radius}
        self.proc.stdin.write((json.dumps(request) + "\n").encode())
        reply = self.receive()
        if not reply.get("ok"):
            if reply.get("fatal"):
                raise SystemicError("Worker capacity/storage error: " + reply.get("error", "unknown"))
            raise ValueError("Worker rejected plane: " + reply.get("error", "unknown"))
        if reply.get("sequence") != self.sequence:
            raise SystemicError("Worker sequence mismatch")
        result = np.load(self.root / "output.npy", allow_pickle=False)
        if result.shape != plane.shape or result.dtype != plane.dtype or not np.isfinite(result).all():
            raise SystemicError("Worker returned invalid shape/dtype/values")
        for name in ("input.npy", "output.npy"):
            (self.root / name).unlink()
        return result, reply["stats"]

    def __exit__(self, *args):
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait()
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()
        if self.selector is not None:
            self.selector.close()
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)
