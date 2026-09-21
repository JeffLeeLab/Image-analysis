[CmdletBinding()]
param(
    [string] $Destination = (Join-Path $PSScriptRoot 'dist'),
    [ValidateSet('x64', 'aarch64')]
    [string] $Architecture = 'x64'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$bioFormatsVersion = '8.1.1'
$bioFormatsSha256 = '338cade0a24c989b398993f5a4ef013e79d54bb5d8b133356755db2243dc630e'
$javaMajorVersion = '21'
$packageName = "bfconvert-vsi-to-tif-windows-$Architecture"
$packageDirectory = Join-Path $Destination $packageName
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))

function Get-FileChecked {
    param(
        [Parameter(Mandatory)] [string] $Uri,
        [Parameter(Mandatory)] [string] $OutFile,
        [Parameter(Mandatory)] [string] $Sha256
    )
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $OutFile
    $actual = (Get-FileHash -LiteralPath $OutFile -Algorithm SHA256).Hash
    if ($actual -ine $Sha256) {
        throw "Checksum mismatch for $Uri. Expected $Sha256; received $actual."
    }
}

try {
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    if (Test-Path -LiteralPath $packageDirectory) {
        throw "Destination already exists: $packageDirectory"
    }

    Write-Host "Downloading Bio-Formats $bioFormatsVersion..."
    $bfZip = Join-Path $temporaryDirectory 'bftools.zip'
    $bfUri = "https://downloads.openmicroscopy.org/bio-formats/$bioFormatsVersion/artifacts/bftools.zip"
    Get-FileChecked -Uri $bfUri -OutFile $bfZip -Sha256 $bioFormatsSha256
    Expand-Archive -LiteralPath $bfZip -DestinationPath $packageDirectory

    Write-Host "Resolving Eclipse Temurin Java $javaMajorVersion for Windows $Architecture..."
    $assetsUri = "https://api.adoptium.net/v3/assets/latest/$javaMajorVersion/hotspot?architecture=$Architecture&heap_size=normal&image_type=jre&jvm_impl=hotspot&os=windows&project=jdk&vendor=eclipse"
    $assets = @(Invoke-RestMethod -UseBasicParsing -Uri $assetsUri)
    if ($assets.Count -lt 1) { throw 'Adoptium returned no matching Java runtime.' }
    $javaPackage = $assets[0].binary.package
    if (-not $javaPackage.link -or -not $javaPackage.checksum) {
        throw 'Adoptium metadata did not include a download URL and checksum.'
    }

    Write-Host "Downloading $($javaPackage.name)..."
    $javaZip = Join-Path $temporaryDirectory 'java.zip'
    Get-FileChecked -Uri $javaPackage.link -OutFile $javaZip -Sha256 $javaPackage.checksum
    $javaExpanded = Join-Path $temporaryDirectory 'java-expanded'
    Expand-Archive -LiteralPath $javaZip -DestinationPath $javaExpanded
    $javaRoot = Get-ChildItem -LiteralPath $javaExpanded -Directory | Select-Object -First 1
    if (-not $javaRoot) { throw 'The Java archive did not contain a runtime directory.' }
    Move-Item -LiteralPath $javaRoot.FullName -Destination (Join-Path $packageDirectory 'runtime')

    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'bfconvert-vsi-to-tif.cmd') -Destination $packageDirectory
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'convert-vsi-to-tif.ps1') -Destination $packageDirectory

    $versionText = @(
        "Bio-Formats: $bioFormatsVersion"
        "Bio-Formats SHA-256: $bioFormatsSha256"
        "Java: $($assets[0].version.semver)"
        "Java archive: $($javaPackage.name)"
        "Java SHA-256: $($javaPackage.checksum)"
        "Architecture: $Architecture"
    )
    Set-Content -LiteralPath (Join-Path $packageDirectory 'VERSIONS.txt') -Value $versionText -Encoding ASCII

    $zipPath = "$packageDirectory.zip"
    if (Test-Path -LiteralPath $zipPath) {
        throw "Archive already exists: $zipPath"
    }
    Compress-Archive -LiteralPath $packageDirectory -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "Created portable package: $zipPath"
}
finally {
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}
