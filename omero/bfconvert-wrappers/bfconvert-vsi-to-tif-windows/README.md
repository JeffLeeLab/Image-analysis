# Portable Windows VSI-to-TIFF converter

This builds a Windows-native, portable ZIP containing Bio-Formats and its own
Java runtime. The finished converter does not need Conda, an installed Java, or
administrator privileges.

## Build the distributable ZIP

On Windows 10 or 11, right-click `build-portable.ps1`, choose **Run with
PowerShell**, or run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-portable.ps1
```

The default output is `dist\bfconvert-vsi-to-tif-windows-x64.zip`. Use
`-Architecture aarch64` to build for Windows on ARM. The builder downloads
Bio-Formats 8.1.1 and the latest Java 21 JRE from Eclipse Adoptium, verifies both
SHA-256 checksums, and records exact versions in `VERSIONS.txt`.

## Use the converter

1. Extract the entire ZIP to a local folder. Do not run it from inside the ZIP.
2. Double-click `bfconvert-vsi-to-tif.cmd` and select the folder containing the
   VSI files. You can also drag a folder onto the CMD file.
3. Keep each VSI companion data folder beside its `.vsi` file.

Only lowercase `.vsi` files directly inside the selected folder are converted;
subfolders are not scanned. The converter writes `{basename}.tif` beside each
input, converts series 0, and skips existing TIFFs. A filename containing `%` is
reported as an error because Bio-Formats treats percent sequences as output
templates.

The CMD launcher sets no machine-wide environment variables and writes nothing
outside the selected image folder. The included PowerShell file is part of the
launcher and must remain beside it.
