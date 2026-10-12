# Local implementation checks — 12 October 2026

Version 0.1.0 passed 15 focused unittest checks. These exercise new selection/planning, script parameters, destination combinations, fixed naming, strict channel layout, durable journals/admission, uncertain-creation handling, fatal-error resume and protection of previously completed outputs.

Real worker subprocess checks used a separately installed, non-editable uv environment outside the repository: Python 3.11.13, NumPy 2.2.6 and SciPy 1.15.3. They exercised finite zero-radius copying, positive-radius protocol/dtype handling, non-finite rejection and temporary-file cleanup. A fake OMERO pixel store exercised actual worker output through the new adapter's Z/C/T iteration, big-endian upload serialization, hashes, completion and no-upload resume path.

The adapter/form checks used the existing local Miniforge `omero` environment: Python 3.11, OMERO.py 5.23.0, Ice 3.6.5 and NumPy 2.4.6. Actual OMERO script parameter discovery returned 28 inputs; radius fields retained optional/no-default semantics. The installation checker passed locally with temporary operator paths. Syntax checks passed for all Python files.

The vendored algorithm matches the pinned SHA-256 exactly. Scientific validation is accepted from the owner's RDI-calculator work and was not repeated.

No live OMERO server was contacted. HQL queries, server-side metadata persistence, rendering, permissions, actual image creation/dataset linking, service cancellation and Linux resource enforcement still need the installation guide's live-server check. No measured server performance claim is made. The test pixel store is not a substitute for that check.
