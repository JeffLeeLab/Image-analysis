[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Folder
)

$ErrorActionPreference = 'Stop'
$appDirectory = $PSScriptRoot
$java = Join-Path $appDirectory 'runtime\bin\java.exe'
$bioFormatsDirectory = Join-Path $appDirectory 'bftools'
$bioFormatsJar = Join-Path $bioFormatsDirectory 'bioformats_package.jar'
$classPath = "$bioFormatsDirectory;$bioFormatsJar"

if (-not (Test-Path -LiteralPath $java -PathType Leaf)) {
    throw "Bundled Java was not found: $java"
}
if (-not (Test-Path -LiteralPath $bioFormatsJar -PathType Leaf)) {
    throw "Bio-Formats was not found: $bioFormatsJar"
}

if ([string]::IsNullOrWhiteSpace($Folder)) {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'Select the folder containing .vsi files'
    $picker.ShowNewFolderButton = $false
    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host 'Cancelled.'
        exit 0
    }
    $Folder = $picker.SelectedPath
}

$resolvedFolder = (Resolve-Path -LiteralPath $Folder -ErrorAction Stop).ProviderPath
if (-not (Test-Path -LiteralPath $resolvedFolder -PathType Container)) {
    throw "Not a folder: $resolvedFolder"
}

$inputs = @(Get-ChildItem -LiteralPath $resolvedFolder -File | Where-Object {
    $_.Extension -ceq '.vsi'
} | Sort-Object Name)

$converted = 0
$skipped = 0
$failed = 0

foreach ($inputFile in $inputs) {
    $output = Join-Path $inputFile.DirectoryName ($inputFile.BaseName + '.tif')

    if (Test-Path -LiteralPath $output) {
        Write-Host "Skipping existing output: $output"
        $skipped++
        continue
    }
    if ($output.Contains('%')) {
        Write-Error "Cannot convert a filename containing %: $($inputFile.FullName)" -ErrorAction Continue
        $failed++
        continue
    }

    Write-Host "Converting: $($inputFile.FullName) -> $output"
    & $java '-Xmx2g' '-Dbioformats_can_do_upgrade_check=false' '-cp' $classPath `
        'loci.formats.tools.ImageConverter' '-no-upgrade' '-nooverwrite' '-series' '0' `
        $inputFile.FullName $output

    if ($LASTEXITCODE -eq 0) {
        $converted++
    }
    else {
        Write-Error "Conversion failed: $($inputFile.FullName) (check for a partial output before retrying)" -ErrorAction Continue
        $failed++
    }
}

Write-Host "Done: $converted converted, $skipped skipped, $failed failed."
if ($failed -gt 0) { exit 1 }
exit 0
