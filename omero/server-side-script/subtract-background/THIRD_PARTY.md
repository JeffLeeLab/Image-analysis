# Algorithm and dependency provenance

`worker/src/omero_bgsub/imagej_rolling_ball.py` is copied **byte-for-byte** from:

- Repository: https://github.com/JeffLeeLab/Image-analysis
- Commit: `e6360e0a7e04580ac30e143281edae66660f9ec6`
- Path: `people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py`
- SHA-256: `0713d5360395a9cf8748829b2f782d316fa3be57df1bc0827adff8a90af55c9c`

The original module docstring is retained. It attributes the rolling-ball implementation to `rdi/background.py` from the owner's RDI-calculator project, commit `d30fae7`, following ImageJ's `BackgroundSubtracter.java`.

Scientific validation is accepted on the owner's confirmation from RDI-calculator. This package does not claim an additional independent ImageJ validation. No repository-wide license was found for the owner's source during packaging; no new upstream license is asserted here. Confirm redistribution rights before publishing this package outside the intended institutional installation.

NumPy, SciPy and Hatchling versions/hashes are recorded in `worker/uv.lock`. Their distributions retain their respective license notices. No OMERO or Ice code is vendored; the adapter uses the institution's existing installations. API usage was checked against local OMERO.py 5.23.0 / Ice 3.6.5.
