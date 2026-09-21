# OMERO CLI environment

Named Miniforge environment: `omero`, with Python 3.11,
OMERO.py 5.23.0, ZeroC Ice 3.6.5 and Bio-Formats CLI tools 8.1.1
(`ome::bftools`, with OpenJDK).

From any directory:

```sh
conda activate omero
omero version
omero login
showinf -version
```

To recreate on macOS Apple Silicon using Miniforge, run from this directory:

```sh
mamba env create --file environment.yml
```

The environment definition uses the macOS universal2 Ice wheel supporting
Apple Silicon and Intel. Installation follows the
[OMERO CLI guide](https://omero.readthedocs.io/en/stable/users/cli/installation.html)
and its linked [Ice wheels](https://www.glencoesoftware.com/blog/2023/12/08/ice-binaries-for-omero.html).

Server login requires your OMERO server address and credentials. Image import
also requires a supported Java runtime; OMERO downloads the required JARs on
first import. Server login and image import have not been tested by this setup.

## Upload images to the Glasgow OMERO server

`omero-upload.sh` uses the OMERO command-line importer directly; it does not
need a YAML bulk-import file. It connects to
`omerodavisvm.mvls.gla.ac.uk:4064`, prompts for the username and then lets the
OMERO CLI securely prompt for the password.

To scan a directory first, without connecting or uploading:

```sh
./omero-upload.sh --scan "/path/to/experiment"
```

To import all Bio-Formats-recognized files directly inside one directory (not
its subdirectories) into a Dataset named after that directory:

```sh
./omero-upload.sh "/path/to/experiment"
```

If a Dataset with that name exists, OMERO uses the most recently created
matching Dataset; otherwise it creates one. To import explicitly selected files
as Orphaned Images, pass the files rather than their directory:

```sh
./omero-upload.sh "/path/to/image 1.tif" "/path/to/image 2.vsi"
```

“Orphaned Images” is a view of images not linked to a Dataset, not a real
Dataset itself. The script therefore omits the import target for explicit file
arguments. It logs out when the import finishes. `-c` asks the importer to
continue with later files if one import fails; check the final exit status and
messages for errors.

## Convert VSI files to TIFF

Double-click `bfconvert-vsi-to-tif.command` in Finder to open Terminal and the
folder picker. The launcher waits for Return after conversion so you can read
the results.

Run `./bfconvert-vsi-to-tif.sh` to select an input folder using the macOS folder
picker, or run `./bfconvert-vsi-to-tif.sh "/path/to/folder"` to supply it
directly.
The script uses the named `omero` environment automatically. It converts only
series 0 from files ending in lowercase `.vsi` directly in that folder (no
recursion), writing `{basename}.tif` alongside each input. Existing outputs are
skipped. Keep any VSI companion data folders in place so Bio-Formats can read
them.

### Portable Windows version

`bfconvert-vsi-to-tif-windows/` contains a Windows CMD launcher and a PowerShell
packager. The packager creates a portable ZIP with Bio-Formats 8.1.1 and its own
Eclipse Temurin Java 21 runtime, so the destination computer needs neither
Conda nor Java and no administrator privileges are required. See
`bfconvert-vsi-to-tif-windows/README.md` for build and usage instructions.
