$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  python -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" -m pip install pyinstaller

if (Test-Path "build") {
  Remove-Item -Recurse -Force "build"
}

if (Test-Path "dist") {
  Remove-Item -Recurse -Force "dist"
}

& ".venv\Scripts\pyinstaller.exe" --noconfirm "EmergencyVoiceApp.spec"
Copy-Item "APP_USAGE.txt" "dist\APP_USAGE.txt" -Force
Copy-Item ".env.example" "dist\.env.example" -Force

$outputDir = Join-Path $root "outputs"
if (-not (Test-Path $outputDir)) {
  New-Item -ItemType Directory -Path $outputDir | Out-Null
}

$zipPath = Join-Path $outputDir "EmergencyVoiceApp-package.zip"
if (Test-Path $zipPath) {
  Remove-Item -Force $zipPath
}

Compress-Archive -Path "dist\EmergencyVoiceApp.exe", "dist\APP_USAGE.txt", "dist\.env.example" -DestinationPath $zipPath
