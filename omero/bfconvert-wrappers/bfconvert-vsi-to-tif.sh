#!/bin/bash
# Select a folder in macOS, or pass a folder as the only argument.
set -uo pipefail

if (( $# > 1 )); then
    echo "Usage: $0 [folder]" >&2
    exit 2
fi

if (( $# == 1 )); then
    folder=$1
else
    folder=$(osascript -e 'POSIX path of (choose folder with prompt "Select the folder containing .vsi files")') || exit 0
fi

cd -- "$folder" || exit 1

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

shopt -s nullglob dotglob
converted=0
skipped=0
failed=0
for input in ./*.vsi; do
    [[ -f "$input" ]] || continue
    output="${input%.vsi}.tif"
    if [[ -e "$output" || -L "$output" ]]; then
        printf 'Skipping existing output: %s\n' "$output"
        skipped=$((skipped + 1))
        continue
    fi
    # Bio-Formats interprets percent sequences as output filename templates.
    if [[ "$output" == *%* ]]; then
        printf 'Cannot convert filename containing %%: %s\n' "$input" >&2
        failed=$((failed + 1))
        continue
    fi
    printf 'Converting: %s -> %s\n' "$input" "$output"
    if "$conda_bin" run --no-capture-output -n omero bfconvert \
        -no-upgrade -nooverwrite -series 0 "$input" "$output"; then
        converted=$((converted + 1))
    else
        printf 'Conversion failed: %s (check for a partial output before retrying)\n' "$input" >&2
        failed=$((failed + 1))
    fi
done

printf 'Done: %d converted, %d skipped, %d failed.\n' "$converted" "$skipped" "$failed"
(( failed == 0 ))
