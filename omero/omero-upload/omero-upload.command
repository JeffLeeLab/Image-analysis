#!/bin/bash
# Double-click this file to choose a directory or one or more image files. You
# can also drag a directory/files onto it in Finder. Inputs are scanned with
# Bio-Formats first, and upload starts only after confirmation.
set -u

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
uploader="$script_dir/omero-upload.sh"

pause_before_exit() {
    local status=$1
    printf '\n'
    if (( status == 0 )); then
        echo "Finished."
    else
        printf 'Stopped with exit status %d.\n' "$status" >&2
    fi
    read -r -p "Press Return to close this window..." _
}

if [[ ! -x "$uploader" ]]; then
    printf 'Cannot run the uploader: %s\n' "$uploader" >&2
    pause_before_exit 1
    exit 1
fi

paths=("$@")

if (( ${#paths[@]} == 0 )); then
    selection_type=$(osascript <<'APPLESCRIPT'
try
    set choice to button returned of (display dialog "What would you like to upload to OMERO?" buttons {"Cancel", "Choose Files", "Choose Folder"} default button "Choose Folder" cancel button "Cancel" with title "OMERO Upload")
    return choice
on error number -128
    return "Cancel"
end try
APPLESCRIPT
    )

    if [[ "$selection_type" == "Cancel" ]]; then
        echo "Upload cancelled."
        pause_before_exit 0
        exit 0
    fi

    if [[ "$selection_type" == "Choose Folder" ]]; then
        selected_path=$(osascript <<'APPLESCRIPT'
try
    set selectedFolder to choose folder with prompt "Choose the directory to upload to OMERO"
    return POSIX path of selectedFolder
on error number -128
    return ""
end try
APPLESCRIPT
        )
        if [[ -n "$selected_path" ]]; then
            paths=("$selected_path")
        fi
    else
        selected_paths=$(osascript <<'APPLESCRIPT'
try
    set selectedFiles to choose file with prompt "Choose one or more image files to upload to Orphaned Images" with multiple selections allowed
    set output to ""
    repeat with selectedFile in selectedFiles
        set output to output & POSIX path of selectedFile & linefeed
    end repeat
    return output
on error number -128
    return ""
end try
APPLESCRIPT
        )
        if [[ -n "$selected_paths" ]]; then
            while IFS= read -r selected_path; do
                [[ -n "$selected_path" ]] && paths+=("$selected_path")
            done <<< "$selected_paths"
        fi
    fi

    if (( ${#paths[@]} == 0 )); then
        echo "Nothing selected."
        pause_before_exit 0
        exit 0
    fi
fi

echo "Scanning the selection with Bio-Formats (nothing will be uploaded yet) ..."
echo
"$uploader" --scan "${paths[@]}"
scan_status=$?
if (( scan_status != 0 )); then
    echo
    echo "The Bio-Formats scan failed. Upload has not started." >&2
    pause_before_exit "$scan_status"
    exit "$scan_status"
fi

echo
echo "Scan completed. Review the results above."
confirmation=$(osascript <<'APPLESCRIPT'
try
    set choice to button returned of (display dialog "The Bio-Formats scan completed. Review the Terminal output, then choose Upload to continue." buttons {"Cancel", "Upload"} default button "Upload" cancel button "Cancel" with title "OMERO Upload")
    return choice
on error number -128
    return "Cancel"
end try
APPLESCRIPT
)

if [[ "$confirmation" != "Upload" ]]; then
    echo "Upload cancelled after scanning; nothing was uploaded."
    pause_before_exit 0
    exit 0
fi

echo
"$uploader" "${paths[@]}"
status=$?
pause_before_exit "$status"
exit "$status"
