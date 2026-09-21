#!/bin/bash
# Import one directory (non-recursively) into a same-named Dataset, or import
# explicitly listed files as orphaned images.
set -uo pipefail

server="omerodavisvm.mvls.gla.ac.uk"
port="4064"
scan_only=0

usage() {
    cat <<EOF
Usage:
  $(basename "$0") [--scan] DIRECTORY
  $(basename "$0") [--scan] FILE [FILE ...]

A directory is scanned only at its top level and imported into a Dataset with
the directory's name. Explicit files are imported without a Dataset target and
therefore appear under Orphaned Images.

--scan  Show which files Bio-Formats recognizes, without logging in or importing.
EOF
}

if [[ ${1:-} == "--scan" ]]; then
    scan_only=1
    shift
fi
if (( $# == 0 )); then
    usage >&2
    exit 2
fi

# Use the named environment even when it has not been activated.
conda_bin=${CONDA_EXE:-}
if [[ ! -x "$conda_bin" ]]; then
    conda_bin=$(command -v conda || true)
fi
if [[ ! -x "$conda_bin" ]]; then
    conda_bin="$HOME/miniforge3/bin/conda"
fi
if [[ ! -x "$conda_bin" ]]; then
    echo "Cannot find conda. Activate Miniforge and try again." >&2
    exit 1
fi

paths=()
for path in "$@"; do
    if [[ ! -e "$path" ]]; then
        printf 'Input does not exist: %s\n' "$path" >&2
        exit 1
    fi
    # Absolute paths cannot be mistaken for importer options.
    parent=$(cd -- "$(dirname -- "$path")" && pwd -P) || exit 1
    paths+=("$parent/$(basename -- "$path")")
done

target_args=()
depth_args=()
if [[ -d "${paths[0]}" ]]; then
    if (( ${#paths[@]} != 1 )); then
        echo "Pass either one directory or one or more files, not a mixture." >&2
        exit 2
    fi
    dataset_name=$(basename -- "${paths[0]}")
    # Depth 1 = the directory's own files; 0 would descend nowhere and find nothing.
    depth_args=(--depth 1)
    target_args=(-T "Dataset:name:$dataset_name")
else
    for path in "${paths[@]}"; do
        if [[ ! -f "$path" ]]; then
            printf 'Not a regular file: %s\n' "$path" >&2
            exit 1
        fi
    done
fi

run_omero() {
    "$conda_bin" run --no-capture-output -n omero omero "$@"
}

if (( scan_only )); then
    run_omero import -f "${depth_args[@]}" "${paths[@]}"
    exit $?
fi

read -r -p "OMERO username: " username
if [[ -z "$username" ]]; then
    echo "Username cannot be empty." >&2
    exit 2
fi

printf 'Connecting to %s:%s (the password prompt will not echo) ...\n' "$server" "$port"
if ! run_omero login "$username@$server:$port"; then
    echo "Login failed; nothing was imported." >&2
    exit 1
fi
logged_in=1
cleanup() {
    if [[ ${logged_in:-0} == 1 ]]; then
        run_omero logout >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM

if (( ${#target_args[@]} )); then
    printf 'Importing top-level files from %s into Dataset %q ...\n' "${paths[0]}" "$dataset_name"
else
    printf 'Importing %d file(s) into Orphaned Images ...\n' "${#paths[@]}"
fi

run_omero import -c "${depth_args[@]}" "${target_args[@]}" "${paths[@]}"
result=$?
if (( result == 0 )); then
    echo "Import completed successfully."
else
    printf 'Import finished with errors (exit status %d).\n' "$result" >&2
fi
exit "$result"
