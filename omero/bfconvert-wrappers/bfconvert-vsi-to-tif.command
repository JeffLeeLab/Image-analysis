#!/bin/bash
# Finder launcher for the conversion script beside this file.
script_dir=$(cd -- "$(dirname -- "$0")" && pwd) || exit 1
/bin/bash "$script_dir/bfconvert-vsi-to-tif.sh" "$@"
result=$?
printf '\nPress Return to finish.'
read -r _
exit "$result"
