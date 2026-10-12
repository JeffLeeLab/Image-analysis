#!/usr/bin/env python3
"""Create an installation tarball; never include environments, journals or credentials."""
import hashlib
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"


def main():
    files = []
    for directory in ("adapter", "worker", "scripts", "config", "docs", "tools", "tests"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and not any(p in ("__pycache__", ".venv", "dist") for p in path.parts) and path.suffix != ".pyc":
                files.append(path)
    files.extend(ROOT / name for name in ("README.md", "spec.md", "THIRD_PARTY.md"))
    files = sorted(files)
    algorithm = ROOT / "worker/src/omero_bgsub/imagej_rolling_ball.py"
    if hashlib.sha256(algorithm.read_bytes()).hexdigest() != "0713d5360395a9cf8748829b2f782d316fa3be57df1bc0827adff8a90af55c9c":
        raise RuntimeError("Algorithm differs from the pinned source")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"omero-bgsub-{VERSION}.tar.gz"
    prefix = f"omero-bgsub-{VERSION}"
    manifest = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n" for p in files).encode()
    with tarfile.open(archive, "w:gz") as tar:
        for path in files:
            tar.add(path, arcname=f"{prefix}/{path.relative_to(ROOT)}", recursive=False)
        info = tarfile.TarInfo(prefix + "/SHA256SUMS")
        info.size, info.mode = len(manifest), 0o644
        tar.addfile(info, io.BytesIO(manifest))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + ".sha256")).write_text(checksum + "  " + archive.name + "\n")
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
