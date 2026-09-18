# Project instructions

This subproject uses `uv` and its local `.venv` for Python dependencies.

- Run Python and project commands from this directory using `uv run`.
- Manage dependencies using `uv add` and `uv remove`; do not use `pip` directly unless a package is incompatible with `uv`.
- If a pip fallback is necessary, install into this project’s `.venv`, document the reason, and do not modify another subproject’s environment.