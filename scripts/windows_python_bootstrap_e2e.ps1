param(
  [ValidateSet("winget", "choco")]
  [string]$PackageManager = "winget"
)

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
$windowsPowerShell = (Get-Command powershell -ErrorAction Stop).Source

$evidenceRoot = Join-Path $env:RUNNER_TEMP "windows-clean-online-user-e2e-evidence"
$root = Join-Path $env:RUNNER_TEMP "windows-python-bootstrap-replay-$PackageManager"
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $evidenceRoot, $root | Out-Null
$shimRoot = Join-Path $root "shims"
$installRoot = Join-Path $root "runtime"
$eventLog = Join-Path $root "bootstrap.log"
$fakeLocalAppData = Join-Path $root "local-app-data"
New-Item -ItemType Directory -Force -Path $shimRoot | Out-Null

Write-Utf8NoBom -Path (Join-Path $shimRoot "py.cmd") -Value @'
@echo off
echo py %*>>"%FAKE_BOOTSTRAP_LOG%"
exit /b 1
'@
if ($PackageManager -eq "winget") {
  Write-Utf8NoBom -Path (Join-Path $shimRoot "winget.cmd") -Value @'
@echo off
echo package-manager-install winget Python.Python.3.11>>"%FAKE_BOOTSTRAP_LOG%"
set "PYTHON_PARENT=%LOCALAPPDATA%\Programs\Python"
set "PYTHON_TARGET=%PYTHON_PARENT%\Python311"
if not exist "%PYTHON_TARGET%" mkdir "%PYTHON_TARGET%"
xcopy "%FAKE_INSTALLED_PYTHON_ROOT%\*" "%PYTHON_TARGET%\" /E /I /Q /Y >nul
if errorlevel 1 exit /b %ERRORLEVEL%
if not exist "%PYTHON_TARGET%\python.exe" exit /b 1
exit /b 0
'@
} else {
  Write-Utf8NoBom -Path (Join-Path $shimRoot "choco.cmd") -Value @'
@echo off
echo package-manager-install choco python311>>"%FAKE_BOOTSTRAP_LOG%"
if not exist "%FAKE_MACHINE_PYTHON_ROOT%" mkdir "%FAKE_MACHINE_PYTHON_ROOT%"
xcopy "%FAKE_INSTALLED_PYTHON_ROOT%\*" "%FAKE_MACHINE_PYTHON_ROOT%\" /E /I /Q /Y >nul
if errorlevel 1 exit /b %ERRORLEVEL%
if not exist "%FAKE_MACHINE_PYTHON_ROOT%\python.exe" exit /b 1
"%FAKE_WINDOWS_POWERSHELL%" -NoProfile -Command "[Environment]::SetEnvironmentVariable('Path', $env:FAKE_MACHINE_PYTHON_ROOT + ';' + $env:FAKE_ISOLATED_MACHINE_PATH, 'Machine')"
if errorlevel 1 exit /b %ERRORLEVEL%
exit /b 0
'@
}

$wheelPath = New-MinimalWheel -Root $root
$fakeMachinePythonRoot = Join-Path $root "machine-python\Python311"
$saved = @{
  Path = $env:Path
  MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
  Python = $env:PYTHON
  NoIndex = $env:PIP_NO_INDEX
  DisableCheck = $env:PIP_DISABLE_PIP_VERSION_CHECK
  LocalAppData = $env:LOCALAPPDATA
}
try {
  Remove-Item Env:PYTHON -ErrorAction SilentlyContinue
  $isolatedMachinePath = "$env:SystemRoot\System32;$env:SystemRoot"
  [Environment]::SetEnvironmentVariable("Path", $isolatedMachinePath, "Machine")
  [Environment]::SetEnvironmentVariable("Path", "", "User")
  $env:FAKE_BOOTSTRAP_LOG = $eventLog
  $env:FAKE_INSTALLED_PYTHON_ROOT = Split-Path -Parent $realPython
  $env:FAKE_MACHINE_PYTHON_ROOT = $fakeMachinePythonRoot
  $env:FAKE_ISOLATED_MACHINE_PATH = $isolatedMachinePath
  $env:FAKE_WINDOWS_POWERSHELL = $windowsPowerShell
  $env:LOCALAPPDATA = $fakeLocalAppData
  $env:PIP_NO_INDEX = "1"
  $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
  $env:Path = "$shimRoot;$env:SystemRoot\System32;$env:SystemRoot"
  $output = @(
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File `
      (Join-Path $PSScriptRoot "..\packaging\install_online.ps1") `
      -VenvPath $installRoot `
      -PackageSpec $wheelPath 2>&1
  )
  $installerExit = $LASTEXITCODE
} finally {
  $env:Path = $saved.Path
  [Environment]::SetEnvironmentVariable("Path", $saved.MachinePath, "Machine")
  [Environment]::SetEnvironmentVariable("Path", $saved.UserPath, "User")
  if ($null -eq $saved.Python) { Remove-Item Env:PYTHON -ErrorAction SilentlyContinue } else { $env:PYTHON = $saved.Python }
  if ($null -eq $saved.NoIndex) { Remove-Item Env:PIP_NO_INDEX -ErrorAction SilentlyContinue } else { $env:PIP_NO_INDEX = $saved.NoIndex }
  if ($null -eq $saved.DisableCheck) { Remove-Item Env:PIP_DISABLE_PIP_VERSION_CHECK -ErrorAction SilentlyContinue } else { $env:PIP_DISABLE_PIP_VERSION_CHECK = $saved.DisableCheck }
  if ($null -eq $saved.LocalAppData) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue } else { $env:LOCALAPPDATA = $saved.LocalAppData }
}

$outputText = $output -join "`n"
Write-Utf8NoBom -Path (Join-Path $evidenceRoot "python-bootstrap-$PackageManager-output.txt") -Value ($outputText + "`n")
if (Test-Path -LiteralPath $eventLog) {
  Copy-Item -LiteralPath $eventLog -Destination (Join-Path $evidenceRoot "python-bootstrap-$PackageManager-events.txt") -Force
}
$installedPython = if ($PackageManager -eq "winget") {
  Join-Path $fakeLocalAppData "Programs\Python\Python311\python.exe"
} else {
  Join-Path $fakeMachinePythonRoot "python.exe"
}
if ($installerExit -ne 0) {
  $installedState = "standard_python_exists=$(Test-Path -LiteralPath $installedPython)"
  throw "Windows isolated Python bootstrap failed with exit $installerExit ($installedState): $outputText"
}
$events = @(Get-Content -LiteralPath $eventLog)
$installEvents = @($events | Where-Object { $_ -like "package-manager-install *" })
if ($installEvents.Count -ne 1) {
  throw "The Windows replay did not execute exactly one Python package install."
}
$installIndex = [Array]::IndexOf($events, $installEvents[0])
$before = @($events[0..($installIndex - 1)] | Where-Object { $_ -like "py *" })
if ($before.Count -lt 1) {
  throw "The Windows replay did not prove Python was missing before $PackageManager."
}
if (-not (Test-Path -LiteralPath $installedPython)) {
  throw "The Windows replay did not materialize Python through $PackageManager."
}
$runtimeDisplay = if ($PackageManager -eq "winget") { $installedPython } else { "python" }
foreach ($marker in @(
  "No Python 3.11+ detected. Attempting online installation",
  "Using Python runtime: $runtimeDisplay",
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
  package_manager = $PackageManager
  python_missing_before_install = $true
  python_available_after_install = $true
  package_manager_install_calls = $installEvents.Count
  installer_completed = $true
}
$evidencePath = Join-Path $evidenceRoot "python-bootstrap-$PackageManager.json"
Write-Utf8NoBom -Path $evidencePath -Value (($evidence | ConvertTo-Json) + "`n")
Write-Host "WINDOWS_PYTHON_BOOTSTRAP_OK"
Write-Host $evidencePath
