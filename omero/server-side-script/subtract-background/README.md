# OMERO rolling-ball background subtraction

Implementation of [spec.md](spec.md), version 0.1.0. The algorithm is unchanged from the pinned owner-validated RDI-calculator port. The adapter and isolated worker have passed local checks; they have **not been run against the university OMERO server**.

Start with the [installation guide](docs/INSTALL.md), then the [user guide](docs/USAGE.md).

- `scripts/Batch_Rolling_Ball_Background.py`: trusted OMERO entry point.
- `adapter/bgsub_adapter/`: parameter planning, image I/O, reports and durable resume.
- `worker/`: separately locked uv package; no OMERO/Ice dependency.
- `config/operator.example.json`: initial limits and deployment paths to review.
- `tools/check_installation.py`: service-account import/worker check without server writes.
- `tools/build_release.py`: creates `dist/omero-bgsub-0.1.0.tar.gz` with checksums.
- `tests/test_integration_local.py`: focused new-integration checks, not repeated scientific validation.

The script accepts image IDs or one dataset ID and writes all results into one explicit existing/new dataset. Radius 0 copies that channel unchanged. Names are fixed, e.g. `sample_bgs-0-50-100.tif`; `.tif` is an OMERO label, not an exported TIFF file.

Runtime scope: Linux processor deployment; one admitted job per journal/lock directory. Linux enforces the worker address-space limit and kills the worker if its parent is killed. macOS was used for local development/IPC checks, not as the target service platform. The worker environment is external to the repository. No packages are installed into the existing OMERO environment.

Recovery uses durable local journals and run/source description markers. It resumes tracked incomplete outputs, verifies completed outputs, and refuses uncertain creation outcomes that cannot be reconciled automatically. See the installation guide's recovery section.
