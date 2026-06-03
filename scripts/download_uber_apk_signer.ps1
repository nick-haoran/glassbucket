$ErrorActionPreference = "Stop"

$Version = "1.3.0"
$OutDir = Join-Path $PSScriptRoot "..\tools\uber-apk-signer"
$JarName = "uber-apk-signer-$Version.jar"
$Url = "https://github.com/patrickfav/uber-apk-signer/releases/download/v$Version/$JarName"
$OutFile = Join-Path $OutDir $JarName

New-Item -ItemType Directory -Force $OutDir | Out-Null
Invoke-WebRequest -Uri $Url -OutFile $OutFile

Write-Host "Downloaded $OutFile"
