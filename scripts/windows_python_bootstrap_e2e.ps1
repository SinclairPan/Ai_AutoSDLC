$ErrorActionPreference = "Stop"

function Write-Utf8NoBom {
  param([string]$Path, [string]$Value)
  [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
}

function New-MinimalWheel {
  param([string]$Root)

  $wheelRoot = Join-Path $Root "wheel-root"
  $packageRoot = Join-Path $wheelRoot "dummy_ai_sdlc"
  $distInfo = Join-Path $wheelRoot "dummy_ai_sdlc-3.0.1.dist-info"
  New-Item -ItemType Directory -Force -Path $packageRoot, $distInfo | Out-Null
  Write-Utf8NoBom -Path (Join-Path $packageRoot "__init__.py") -Value @'
def main():
    print("0.0.0")
'@
  Write-Utf8NoBom -Path (Join-Path $distInfo "METADATA") -Value @'
Metadata-Version: 2.1
Name: dummy-ai-sdlc
Version: 3.0.1
'@
  Write-Utf8NoBom -Path (Join-Path $distInfo "WHEEL") -Value @'
Wheel-Version: 1.0
Generator: ai-sdlc-bootstrap-e2e
Root-Is-Purelib: true
Tag: py3-none-any
'@
  Write-Utf8NoBom -Path (Join-Path $distInfo "entry_points.txt") -Value @'
[console_scripts]
ai-sdlc = dummy_ai_sdlc:main
'@
  Write-Utf8NoBom -Path (Join-Path $distInfo "RECORD") -Value @'
dummy_ai_sdlc/__init__.py,,
dummy_ai_sdlc-3.0.1.dist-info/METADATA,,
dummy_ai_sdlc-3.0.1.dist-info/WHEEL,,
dummy_ai_sdlc-3.0.1.dist-info/entry_points.txt,,
dummy_ai_sdlc-3.0.1.dist-info/RECORD,,
'@
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $wheelPath = Join-Path $Root "dummy_ai_sdlc-3.0.1-py3-none-any.whl"
  [IO.Compression.ZipFile]::CreateFromDirectory($wheelRoot, $wheelPath)
  return $wheelPath
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
  throw "This replay must run on a native Windows job."
}

$realPython = (& py -3.11 -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $realPython)) {
  throw "The Windows CI orchestrator Python is unavailable."
}

$evidenceRoot = Join-Path $env:RUNNER_TEMP "windows-clean-online-user-e2e-evidence"
$root = Join-Path $env:RUNNER_TEMP "windows-python-bootstrap-replay"
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $evidenceRoot, $root | Out-Null
$shimRoot = Join-Path $root "shims"
$installRoot = Join-Path $root "runtime"
$ready = Join-Path $root "python-ready"
$eventLog = Join-Path $root "bootstrap.log"
New-Item -ItemType Directory -Force -Path $shimRoot | Out-Null

Write-Utf8NoBom -Path (Join-Path $shimRoot "python.cmd") -Value @'
@echo off
echo python %*>>"%FAKE_BOOTSTRAP_LOG%"
if not exist "%FAKE_PYTHON_READY%" exit /b 1
"%FAKE_INSTALLED_PYTHON%" %*
exit /b %ERRORLEVEL%
'@
Write-Utf8NoBom -Path (Join-Path $shimRoot "py.cmd") -Value @'
@echo off
echo py %*>>"%FAKE_BOOTSTRAP_LOG%"
exit /b 1
'@
Write-Utf8NoBom -Path (Join-Path $shimRoot "winget.cmd") -Value @'
@echo off
echo package-manager-install winget Python.Python.3.11>>"%FAKE_BOOTSTRAP_LOG%"
type nul > "%FAKE_PYTHON_READY%"
exit /b 0
'@

$wheelPath = New-MinimalWheel -Root $root
$saved = @{
  Path = $env:Path
  Python = $env:PYTHON
  NoIndex = $env:PIP_NO_INDEX
  DisableCheck = $env:PIP_DISABLE_PIP_VERSION_CHECK
}
try {
  Remove-Item Env:PYTHON -ErrorAction SilentlyContinue
  $env:FAKE_BOOTSTRAP_LOG = $eventLog
  $env:FAKE_PYTHON_READY = $ready
  $env:FAKE_INSTALLED_PYTHON = $realPython
  $env:PIP_NO_INDEX = "1"
  $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
  $env:Path = "$shimRoot;$env:SystemRoot\System32;$env:SystemRoot"
  $output = @(
    & powershell -NoProfile -ExecutionPolicy Bypass -File `
      (Join-Path $PSScriptRoot "..\packaging\install_online.ps1") `
      -VenvPath $installRoot `
      -PackageSpec $wheelPath 2>&1
  )
  $installerExit = $LASTEXITCODE
} finally {
  $env:Path = $saved.Path
  if ($null -eq $saved.Python) { Remove-Item Env:PYTHON -ErrorAction SilentlyContinue } else { $env:PYTHON = $saved.Python }
  if ($null -eq $saved.NoIndex) { Remove-Item Env:PIP_NO_INDEX -ErrorAction SilentlyContinue } else { $env:PIP_NO_INDEX = $saved.NoIndex }
  if ($null -eq $saved.DisableCheck) { Remove-Item Env:PIP_DISABLE_PIP_VERSION_CHECK -ErrorAction SilentlyContinue } else { $env:PIP_DISABLE_PIP_VERSION_CHECK = $saved.DisableCheck }
}

$outputText = $output -join "`n"
if ($installerExit -ne 0) {
  throw "Windows isolated Python bootstrap failed with exit $installerExit`: $outputText"
}
$events = @(Get-Content -LiteralPath $eventLog)
$installEvents = @($events | Where-Object { $_ -like "package-manager-install *" })
if ($installEvents.Count -ne 1) {
  throw "The Windows replay did not execute exactly one Python package install."
}
$installIndex = [Array]::IndexOf($events, $installEvents[0])
$before = @($events[0..($installIndex - 1)] | Where-Object { $_ -like "python *" })
$after = @($events[($installIndex + 1)..($events.Count - 1)] | Where-Object { $_ -like "python *" })
if ($before.Count -lt 1 -or $after.Count -lt 1) {
  throw "The Windows replay did not prove the missing-to-installed Python transition."
}
foreach ($marker in @(
  "No Python 3.11+ detected. Attempting online installation",
  "Using Python runtime: python",
  "Online installation completed"
)) {
  if (-not $outputText.Contains($marker)) {
    throw "Installer output missing bootstrap marker: $marker"
  }
}
if (-not (Test-Path -LiteralPath (Join-Path $installRoot "Scripts\python.exe"))) {
  throw "The Windows replay did not create the isolated venv Python."
}
if (-not (Test-Path -LiteralPath (Join-Path $installRoot "Scripts\ai-sdlc.exe"))) {
  throw "The Windows replay did not install the console entrypoint."
}

$evidence = [ordered]@{
  platform = "Windows"
  python_missing_before_install = $true
  python_available_after_install = $true
  package_manager_install_calls = $installEvents.Count
  installer_completed = $true
}
$evidencePath = Join-Path $evidenceRoot "python-bootstrap.json"
Write-Utf8NoBom -Path $evidencePath -Value (($evidence | ConvertTo-Json) + "`n")
Write-Host "WINDOWS_PYTHON_BOOTSTRAP_OK"
Write-Host $evidencePath
