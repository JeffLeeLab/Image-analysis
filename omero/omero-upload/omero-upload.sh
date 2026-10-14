#!/bin/bash
# Import one directory (non-recursively) into a same-named Dataset, or import
# explicitly listed files as orphaned images. Files already in OMERO are
# skipped, and the import is retried until every scanned file is verified.
set -uo pipefail

server="omerodavisvm.mvls.gla.ac.uk"
port="4064"
max_attempts=3
scan_only=0
script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P) || exit 1

usage() {
    cat <<EOF
Usage:
  $(basename "$0") [--scan] DIRECTORY
  $(basename "$0") [--scan] FILE [FILE ...]

A directory is scanned only at its top level and imported into a Dataset with
the directory's name. Explicit files are imported without a Dataset target and
therefore appear under Orphaned Images.

Files whose original path is already recorded in OMERO are skipped, so it is
safe to run the same command again. After each import the script checks that
every scanned file is in OMERO and retries the missing ones (up to
$max_attempts attempts in total).

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

# macOS /bin/bash 3.2 treats an empty array as unbound under set -u, hence the
# ${array[@]+...} form below.
if (( scan_only )); then
    run_omero import -f ${depth_args[@]+"${depth_args[@]}"} "${paths[@]}"
    exit $?
fi

work_dir=$(mktemp -d) || exit 1
logged_in=0
cleanup() {
    if (( logged_in )); then
        run_omero logout >/dev/null 2>&1 || true
    fi
    rm -rf -- "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

# Record the first file of each fileset Bio-Formats would import; these are
# checked against OMERO after every attempt.
echo "Scanning with Bio-Formats to list the files to import ..."
if ! run_omero import -f ${depth_args[@]+"${depth_args[@]}"} "${paths[@]}" \
        >"$work_dir/scan.txt" 2>"$work_dir/scan.log"; then
    cat "$work_dir/scan.log" >&2
    echo "Bio-Formats scan failed; nothing was imported." >&2
    exit 1
fi
expect_key=0
: >"$work_dir/keys.txt"
while IFS= read -r line; do
    case $line in
        '# Group: '*) expect_key=1 ;;
        '#'*) ;;
        *)
            if (( expect_key )) && [[ -n $line ]]; then
                printf '%s\n' "$line" >>"$work_dir/keys.txt"
                expect_key=0
            fi
            ;;
    esac
done <"$work_dir/scan.txt"
file_count=$(wc -l <"$work_dir/keys.txt" | tr -d ' ')
if (( file_count == 0 )); then
    echo "Bio-Formats found no importable files; nothing was imported." >&2
    exit 1
fi

read -r -p "OMERO username: " username
if [[ -z "$username" ]]; then
    echo "Username cannot be empty." >&2
    exit 2
fi

login_target="$username@$server:$port"
printf 'Connecting to %s:%s (the password prompt will not echo) ...\n' "$server" "$port"
if ! run_omero login "$login_target"; then
    echo "Login failed; nothing was imported." >&2
    exit 1
fi
logged_in=1

if (( ${#target_args[@]} )); then
    destination="Dataset $(printf '%q' "$dataset_name")"
else
    destination="Orphaned Images"
fi

result=1
for (( attempt = 1; attempt <= max_attempts; attempt++ )); do
    printf '\nAttempt %d of %d: importing %d file(s) into %s, skipping any already in OMERO ...\n' \
        "$attempt" "$max_attempts" "$file_count" "$destination"
    run_omero import -c --exclude clientpath ${depth_args[@]+"${depth_args[@]}"} \
        ${target_args[@]+"${target_args[@]}"} "${paths[@]}"
    import_status=$?
    if (( import_status != 0 )); then
        printf 'The importer reported errors (exit status %d).\n' "$import_status" >&2
    fi

    echo "Checking which files are now in OMERO ..."
    "$conda_bin" run --no-capture-output -n omero python "$script_dir/verify_upload.py" \
        "$login_target" "${dataset_name:-}" "$work_dir/keys.txt" "$work_dir/missing.txt"
    result=$?
    if (( result == 0 )); then
        echo "All files are in OMERO."
        break
    elif (( result != 3 )); then
        echo "Could not check the upload against OMERO." >&2
        break
    fi
    # Retrying only helps if the last attempt made progress.
    if [[ -f "$work_dir/missing.prev" ]] && cmp -s "$work_dir/missing.txt" "$work_dir/missing.prev"; then
        echo "The last attempt imported nothing new; stopping retries." >&2
        break
    fi
    cp "$work_dir/missing.txt" "$work_dir/missing.prev"
done

if (( result == 3 )); then
    cat >&2 <<'EOF'

Some files are still missing (listed above). A file marked "already recorded
as Fileset" is skipped because OMERO holds an earlier import of it: either a
failed one, or an image in another Dataset. Check that Fileset in OMERO.web,
then delete a failed import and run this script again, or link the existing
image to this Dataset. Other missing files failed on every attempt; see the
importer messages above.
EOF
fi
exit "$result"
