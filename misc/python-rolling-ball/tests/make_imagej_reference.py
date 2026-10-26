"""Regenerate imagej_reference.npz by running real ImageJ on synthetic images.

Only needed to refresh the reference (for example against a newer ImageJ);
the tests just read the .npz. Needs numpy plus a Java JDK (java and javac on
PATH). The ImageJ jar is downloaded from Maven Central and its SHA-1 checked,
unless --ij-jar points at a local copy.

    uv run --with numpy python make_imagej_reference.py [--ij-version 1.54p] [--ij-jar path/to/ij.jar]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MAVEN = "https://repo1.maven.org/maven2/net/imagej/ij/{v}/ij-{v}.jar"
RAW_DTYPE = {"float": ">f4", "short": ">u2", "byte": "u1"}  # ImageJ processor type -> raw file dtype
NP_DTYPE = {"float": np.float32, "short": np.uint16, "byte": np.uint8}

# (output key, input key, ImageJ type, radius, create background, light background, presmooth)
# Radii cover every shrink factor (1, 2, 4, 8) and the half-way rounding cases 7.5, 15 and 50.
CASES = [
    ("bg_r3", "image", "float", 3, True, False, True),
    ("bg_r7.5", "image", "float", 7.5, True, False, True),
    ("bg_r10", "image", "float", 10, True, False, True),
    ("bg_r15_nosmooth", "image", "float", 15, True, False, False),
    ("bg_r25", "image", "float", 25, True, False, True),
    ("bg_r50", "image", "float", 50, True, False, True),
    ("bg_r50_nosmooth", "image", "float", 50, True, False, False),
    ("bg_r60", "image", "float", 60, True, False, True),
    ("bg_r150", "image", "float", 150, True, False, True),
    ("bg_r50_light", "image_light", "float", 50, True, True, True),
    ("bg_r150_light_nosmooth", "image_light", "float", 150, True, True, False),
    ("bg_tiny_17x9_r150", "tiny_17x9", "float", 150, True, False, True),
    ("bg_tiny_6x40_r50", "tiny_6x40", "float", 50, True, False, True),
    ("sub_u16_r50", "image_u16", "short", 50, False, False, True),
    ("sub_u16_r50_light", "image_u16_light", "short", 50, False, True, True),
    ("sub_u8_r10", "image_u8", "byte", 10, False, False, True),
    ("sub_u8_r10_light_nosmooth", "image_u8_light", "byte", 10, False, True, False),
    ("sub_f32_r50", "image", "float", 50, False, False, True),
    ("sub_f32_r50_light", "image_light", "float", 50, False, True, True),
]


def scene(h: int, w: int, seed: int) -> np.ndarray:
    """Tilted, wavy background + Gaussian blobs of radius 2-25 px + noise, as float32."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:h, :w]
    img = 200 + 0.8 * xx + 0.5 * yy + 60 * np.sin(xx / 23.0) * np.cos(yy / 31.0)
    for _ in range(8):
        cy, cx, r = rng.uniform(0, h), rng.uniform(0, w), rng.uniform(2, 25)
        img += rng.uniform(100, 600) * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r))
    img += rng.normal(0, 8, (h, w))
    return img.astype(np.float32)


def make_inputs() -> dict[str, np.ndarray]:
    image = scene(64, 96, seed=1)
    return {
        "image": image,
        "image_light": (2000 - image).astype(np.float32),
        "image_u16": np.clip(image * 20, 0, 65535).astype(np.uint16),
        "image_u16_light": np.clip((2000 - image) * 20, 0, 65535).astype(np.uint16),
        "image_u8": np.clip(image / 4, 0, 255).astype(np.uint8),
        "image_u8_light": np.clip(255 - image / 4, 0, 255).astype(np.uint8),
        "tiny_17x9": scene(17, 9, seed=2),
        "tiny_6x40": scene(6, 40, seed=3),
    }


def get_jar(version: str, dest: Path) -> Path:
    url = MAVEN.format(v=version)
    jar = dest / f"ij-{version}.jar"
    jar.write_bytes(urllib.request.urlopen(url).read())
    expected = urllib.request.urlopen(url + ".sha1").read().decode().split()[0]
    actual = hashlib.sha1(jar.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError(f"SHA-1 mismatch for {url}: got {actual}, Maven says {expected}")
    return jar


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ij-version", default="1.54p", help="ImageJ release on Maven Central (default 1.54p)")
    p.add_argument("--ij-jar", type=Path, help="use this ImageJ jar instead of downloading one")
    p.add_argument("-o", "--output", type=Path, default=HERE / "imagej_reference.npz")
    args = p.parse_args()

    inputs = make_inputs()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jar = args.ij_jar.resolve() if args.ij_jar else get_jar(args.ij_version, tmp)
        subprocess.run(
            ["javac", "-cp", str(jar), "-d", str(tmp), str(HERE / "RunBackgroundSubtracter.java")], check=True
        )
        lines = []
        for key, src, ijtype, radius, create, light, presmooth in CASES:
            img = inputs[src]
            img.astype(RAW_DTYPE[ijtype]).tofile(tmp / f"{key}.in")
            h, w = img.shape
            flags = " ".join(str(b).lower() for b in (create, light, presmooth))
            lines.append(f"{ijtype} {w} {h} {tmp / f'{key}.in'} {tmp / f'{key}.out'} {radius} {flags}")
        (tmp / "cases.txt").write_text("\n".join(lines) + "\n")
        subprocess.run(
            ["java", "-Djava.awt.headless=true", "-cp", f"{jar}:{tmp}", "RunBackgroundSubtracter", str(tmp / "cases.txt")],
            check=True,
        )
        outputs = {
            key: np.fromfile(tmp / f"{key}.out", RAW_DTYPE[ijtype]).astype(NP_DTYPE[ijtype]).reshape(inputs[src].shape)
            for key, src, ijtype, *_ in CASES
        }
        ij_version = jar.stem.removeprefix("ij-")

    cases = [
        dict(output=k, input=s, radius=r, create_background=c, light_background=l, presmooth=ps)
        for k, s, _, r, c, l, ps in CASES
    ]
    meta = dict(imagej=ij_version, cases=cases)
    np.savez_compressed(args.output, meta=np.array(json.dumps(meta)), **inputs, **outputs)
    print(f"wrote {args.output} ({args.output.stat().st_size / 1024:.0f} KiB, ImageJ {ij_version}, {len(CASES)} cases)")


if __name__ == "__main__":
    main()
