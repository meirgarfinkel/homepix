# Makes $Out (a folder) hold ExifTool for Windows with its own Perl, for the bundled app:
#   exiftool.exe        ExifTool's official Windows launcher (renamed from "exiftool(-k).exe",
#                       which would wait for a key press after each run)
#   exiftool_files\     its Perl and modules
# The download is checked against ExifTool's published checksum.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\bundle_exiftool.ps1 OUT_DIR
param([Parameter(Mandatory = $true)][string]$Out)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # (Expand-Archive is many times slower with its progress bar)

$Version = "13.59"  # update both lines together (checksums: https://exiftool.org/checksums.txt)
$Sha256 = "44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec"

$Cache = Join-Path (Split-Path -Parent $Out) "downloads"
$Zip = Join-Path $Cache "exiftool-$($Version)_64.zip"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
function Matches { (Test-Path $Zip) -and ((Get-FileHash -Algorithm SHA256 $Zip).Hash -eq $Sha256) }
if (-not (Matches)) {
    # curl (part of Windows 10 and 11): PowerShell's own download gets SourceForge's web page instead
    curl.exe -fsSL -o "$Zip.part" "https://sourceforge.net/projects/exiftool/files/exiftool-$($Version)_64.zip/download"
    if ($LASTEXITCODE -ne 0) { throw "ExifTool couldn't be downloaded" }
    Move-Item -Force "$Zip.part" $Zip
}
if (-not (Matches)) { throw "ExifTool download doesn't match its checksum" }

$Work = Join-Path $Cache "exiftool-unpacked"
if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
Expand-Archive -Path $Zip -DestinationPath $Work
$Src = Join-Path $Work "exiftool-$($Version)_64"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Copy-Item -Recurse (Join-Path $Src "exiftool_files") (Join-Path $Out "exiftool_files")
Copy-Item (Join-Path $Src "exiftool(-k).exe") (Join-Path $Out "exiftool.exe")
Remove-Item -Recurse -Force $Work

$Got = & (Join-Path $Out "exiftool.exe") -ver
if ($Got -ne $Version) { throw "The bundled ExifTool doesn't run (it said: $Got)" }
Write-Host "ExifTool $Got ready in $Out"
