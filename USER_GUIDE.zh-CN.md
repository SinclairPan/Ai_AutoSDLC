# AI-SDLC 3.0.1 中文用户指南

本指南面向第一次接触 AI-SDLC 的普通用户。当前公开稳定版本与比赛最终版本均为 `v3.0.1`，所有安装器、离线包、校验文件和安装后版本必须保持一致。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

外部 stable shim 与 `python -m ai_sdlc` 是 Windows 支持即时更新和原命令重放的入口。Windows 运行时目录内的 direct `Scripts\ai-sdlc.exe` 活动时不能安全替换：它只给出迁移提示、零安装并让当前业务命令继续一次；显式 direct self-update 不修改安装且返回非零。`-AddToPath` 或 `--add-to-path` 成功后，新终端中的裸 `ai-sdlc` 是日常入口；当前安装窗口使用路线内给出的 module 命令。

初始化会让你选择实际用于聊天开发的 AI 代理入口和 Shell。可选代理包括 Claude Code、Codex、Cursor、VS Code、其他-通用；Shell 按当前系统选择 PowerShell、Bash、Zsh 或 Cmd。

`v3.0.1` 的正式离线资产是：

- <https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-windows-amd64.zip>
- <https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-windows-amd64.zip.sha256>
- <https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-macos-arm64.tar.gz>
- <https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-macos-arm64.tar.gz.sha256>
- <https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-linux-amd64.tar.gz>
- <https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/ai-sdlc-offline-3.0.1-linux-amd64.tar.gz.sha256>

每个归档旁都必须同时下载完全同名并追加 `.sha256` 的 sidecar。

<!-- AI-SDLC-USER-GUIDE-MATRIX: 2x2x3=12 -->

## 路线选择器

先判断项目目录是空的还是已有业务文件，再选择渠道和操作系统。每条路线都包含准备、获取、校验、安装、初始化或接入、成功证据和就地恢复。

| 项目状态 | 渠道 | Windows AMD64 | macOS Apple Silicon | Linux AMD64 |
| --- | --- | --- | --- | --- |
| 全新用户 + 全新空项目 | 在线 | [路线 1](#route-new-online-windows-amd64) | [路线 2](#route-new-online-macos-arm64) | [路线 3](#route-new-online-linux-amd64) |
| 全新用户 + 全新空项目 | 离线 | [路线 4](#route-new-offline-windows-amd64) | [路线 5](#route-new-offline-macos-arm64) | [路线 6](#route-new-offline-linux-amd64) |
| 全新用户 + 已有项目 | 在线 | [路线 7](#route-existing-online-windows-amd64) | [路线 8](#route-existing-online-macos-arm64) | [路线 9](#route-existing-online-linux-amd64) |
| 全新用户 + 已有项目 | 离线 | [路线 10](#route-existing-offline-windows-amd64) | [路线 11](#route-existing-offline-macos-arm64) | [路线 12](#route-existing-offline-linux-amd64) |

### 1.1 Windows

全新空项目选择路线 1 或 4；已有项目选择路线 7 或 10。

### 1.2 macOS（Apple Silicon）

全新空项目选择路线 2 或 5；已有项目选择路线 8 或 11。

### 1.3 Linux（amd64）

全新空项目选择路线 3 或 6；已有项目选择路线 9 或 12。

### 1.4 选择 AI 适配器和 Shell

空项目在各自路线的初始化步骤选择实际使用的 AI 代理入口与 Shell。

### 2.4 选择 AI 适配器和 Shell

已有项目同样先完成初始化选择，再执行 `adopt`；不得跳过初始化直接扫描项目。

## 第一章：全新用户 + 全新空项目

<a id="route-new-online-windows-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: new|online|windows-amd64 -->
## 路线 1：全新空项目 · 在线安装 · Windows AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 64 位 Windows（`windows-amd64`）和 PowerShell。需要联网访问 GitHub，并允许在当前用户目录创建项目与运行环境；安装器负责 Python、venv 和依赖，但在线 Git 安装源要求主机已有 Git。

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$InstallRoot = Join-Path $HOME "AI-SDLC\online-v3.0.1"
$VenvRoot = Join-Path $InstallRoot ".venv"
$DownloadRoot = Join-Path $env:TEMP "ai-sdlc-v3.0.1-online"
New-Item -ItemType Directory -Force -Path $ProjectRoot, $InstallRoot, $DownloadRoot | Out-Null
if ((Get-ChildItem -LiteralPath $ProjectRoot -Force).Count -ne 0) { throw "Project directory must be empty" }
$GitCommand = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitCommand) { throw "Git is required. Run: winget install --id Git.Git -e, then reopen PowerShell." }
git --version
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

```powershell
$InstallerName = "install_online.ps1"
$InstallerUrl = "https://raw.githubusercontent.com/SinclairPan/Ai_AutoSDLC/v3.0.1/packaging/install_online.ps1"
$InstallerPath = Join-Path $DownloadRoot $InstallerName
Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```powershell
$PinnedTag = "v3.0.1"
if (-not (Select-String -LiteralPath $InstallerPath -SimpleMatch $PinnedTag -Quiet)) { throw "Installer is not pinned to v3.0.1" }
Write-Host "After install verify with: python -m ai_sdlc --version"
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```powershell
# 固定标签安装器：install_online.ps1 -AddToPath
powershell -NoProfile -ExecutionPolicy Bypass -File $InstallerPath -VenvPath $VenvRoot -AddToPath
$ModulePython = Join-Path $VenvRoot "Scripts\python.exe"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化

```powershell
Set-Location $ProjectRoot
# 新终端等价入口：ai-sdlc init .
& $ModulePython -m ai_sdlc init .
```

选择实际使用的 Claude Code、Codex、Cursor、VS Code 或其他-通用，再选择 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```powershell
& $ModulePython -m ai_sdlc --version
& $ModulePython -m ai_sdlc status
```

必须看到 `3.0.1`、`Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点；空项目不会出现示例业务代码。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

下载失败时停止并重试固定标签 URL，不要改用未发布分支。PowerShell 阻止脚本时继续使用上面的单次 Bypass 命令。Windows 运行时目录内的 direct `Scripts\ai-sdlc.exe` 活动时不能安全原地替换；显式 direct self-update 零安装并返回非零，请改用 `python -m ai_sdlc`（本路线即 `& $ModulePython -m ai_sdlc self-update install --version 3.0.1`）或新终端中的 stable `ai-sdlc`。裸命令不可用时运行 `& $ModulePython -m ai_sdlc status`；若出现 `No module named ai_sdlc`，重跑 `install_online.ps1 -AddToPath`。若显示 `open gates`，按 CLI 提示查看详情；代理或 Shell 选错时运行 `ai-sdlc adapter select`、`ai-sdlc adapter shell-select`。

<a id="route-new-online-macos-arm64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: new|online|macos-arm64 -->
## 路线 2：全新空项目 · 在线安装 · macOS Apple Silicon

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 Apple Silicon macOS（`macos-arm64`）和 Terminal 中的 zsh/bash。需要联网访问 GitHub；安装器负责 Python 运行环境，但在线 Git 安装源要求主机已有 Git。

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/Applications/AI-SDLC/online-v3.0.1"
VENV_ROOT="$INSTALL_ROOT/.venv"
DOWNLOAD_ROOT="$(mktemp -d)"
mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT"
test -z "$(ls -A "$PROJECT_ROOT")" || { echo "Project directory must be empty"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "Git is required. Run: xcode-select --install, then reopen Terminal." >&2; exit 1; }
git --version
if ! command -v brew >/dev/null 2>&1; then
  if test ! -x /opt/homebrew/bin/brew; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
brew --version
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

```bash
INSTALLER_NAME="install_online.sh"
INSTALLER_URL="https://raw.githubusercontent.com/SinclairPan/Ai_AutoSDLC/v3.0.1/packaging/install_online.sh"
INSTALLER_PATH="$DOWNLOAD_ROOT/$INSTALLER_NAME"
curl --fail --location --retry 3 --output "$INSTALLER_PATH" "$INSTALLER_URL"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
PINNED_TAG="v3.0.1"
grep -F "$PINNED_TAG" "$INSTALLER_PATH" >/dev/null || { echo "Installer is not pinned to v3.0.1"; exit 1; }
echo 'After install verify with: python -m ai_sdlc --version'
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
# 固定标签安装器：install_online.sh --add-to-path
bash "$INSTALLER_PATH" "$VENV_ROOT" --add-to-path
MODULE_PYTHON="$VENV_ROOT/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化

```bash
cd "$PROJECT_ROOT"
# 新终端等价入口：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
```

必须看到 `3.0.1`、`Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点；空目录不会出现示例业务文件。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

网络错误时停止并重试固定 URL。若缺少 Homebrew，运行 `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`，再运行 `eval "$(/opt/homebrew/bin/brew shellenv)"` 后重试安装。裸命令不可用时运行 `"$MODULE_PYTHON" -m ai_sdlc status`；`No module named ai_sdlc` 时重跑 `install_online.sh --add-to-path`。出现 `open gates` 时按 CLI 指示查看详情；入口选择错误使用 `ai-sdlc adapter select`、`ai-sdlc adapter shell-select`。

<a id="route-new-online-linux-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: new|online|linux-amd64 -->
## 路线 3：全新空项目 · 在线安装 · Linux AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 64 位 Linux（`linux-amd64`）和 bash。需要联网访问 GitHub，当前用户应能写入 `$HOME/.local/share`；在线 Git 安装源要求主机已有 Git。

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC/online-v3.0.1"
VENV_ROOT="$INSTALL_ROOT/.venv"
DOWNLOAD_ROOT="$(mktemp -d)"
mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT"
test -z "$(ls -A "$PROJECT_ROOT")" || { echo "Project directory must be empty"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "Git is required. Install it with apt/dnf/yum, then reopen the shell." >&2; exit 1; }
git --version
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

```bash
INSTALLER_NAME="install_online.sh"
INSTALLER_URL="https://raw.githubusercontent.com/SinclairPan/Ai_AutoSDLC/v3.0.1/packaging/install_online.sh"
INSTALLER_PATH="$DOWNLOAD_ROOT/$INSTALLER_NAME"
curl --fail --location --retry 3 --output "$INSTALLER_PATH" "$INSTALLER_URL"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
PINNED_TAG="v3.0.1"
grep -F "$PINNED_TAG" "$INSTALLER_PATH" >/dev/null || { echo "Installer is not pinned to v3.0.1"; exit 1; }
echo 'After install verify with: python -m ai_sdlc --version'
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
# 固定标签安装器：install_online.sh --add-to-path
bash "$INSTALLER_PATH" "$VENV_ROOT" --add-to-path
MODULE_PYTHON="$VENV_ROOT/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化

```bash
cd "$PROJECT_ROOT"
# 新终端等价入口：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
```

必须看到 `3.0.1`、`Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点，且空目录没有示例业务代码。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

下载或权限错误时停止，确认安装目录可写后重跑固定标签的 `install_online.sh --add-to-path`。裸命令不可用时使用 module 路径；`No module named ai_sdlc` 时重跑安装器。`open gates`、代理和 Shell 问题分别按 CLI 提示、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 恢复。

<a id="route-new-offline-windows-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: new|offline|windows-amd64 -->
## 路线 4：全新空项目 · 离线安装 · Windows AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `windows-amd64`。联网机器下载包和同名 `.sha256`，目标机器可以完全离线。准备空项目目录和可写安装目录。

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$InstallRoot = Join-Path $HOME "AI-SDLC"
$DownloadRoot = Join-Path $HOME "Downloads\ai-sdlc-v3.0.1"
New-Item -ItemType Directory -Force -Path $ProjectRoot, $InstallRoot, $DownloadRoot | Out-Null
if ((Get-ChildItem -LiteralPath $ProjectRoot -Force).Count -ne 0) { throw "Project directory must be empty" }
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

离线包内包含 `install_offline.ps1`。联网机器下载两项后原样复制到目标机器。

```powershell
$PackageName = "ai-sdlc-offline-3.0.1-windows-amd64.zip"
$PackageUrl = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/$PackageName"
Invoke-WebRequest -Uri $PackageUrl -OutFile (Join-Path $DownloadRoot $PackageName)
Invoke-WebRequest -Uri "$PackageUrl.sha256" -OutFile (Join-Path $DownloadRoot "$PackageName.sha256")
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$InstallRoot = Join-Path $HOME "AI-SDLC"
$DownloadRoot = Join-Path $HOME "Downloads\ai-sdlc-v3.0.1"
$PackageName = "ai-sdlc-offline-3.0.1-windows-amd64.zip"
New-Item -ItemType Directory -Force -Path $ProjectRoot, $InstallRoot, $DownloadRoot | Out-Null
if ((Get-ChildItem -LiteralPath $ProjectRoot -Force).Count -ne 0) { throw "Project directory must be empty" }
$PackagePath = Join-Path $DownloadRoot $PackageName
$Parts = (Get-Content -LiteralPath "$PackagePath.sha256" -Raw).Trim() -split '\s+', 2
$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
if ($Parts.Count -ne 2 -or $Parts[1] -ne $PackageName -or $Parts[0].ToLowerInvariant() -ne $Actual) { throw "SHA256 verification failed for $PackageName" }
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```powershell
Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallRoot -Force
$BundleRoot = Join-Path $InstallRoot "ai-sdlc-offline-3.0.1-windows-amd64"
Push-Location $BundleRoot
try { powershell -NoProfile -ExecutionPolicy Bypass -File ".\install_offline.ps1" -AddToPath } finally { Pop-Location }
$ModulePython = Join-Path $BundleRoot ".venv\Scripts\python.exe"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化

```powershell
Set-Location $ProjectRoot
# 新终端等价入口：ai-sdlc init .
& $ModulePython -m ai_sdlc init .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```powershell
& $ModulePython -m ai_sdlc --version
& $ModulePython -m ai_sdlc status
```

应看到 `Offline installation completed`、`3.0.1`、`Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点；空项目没有示例业务代码。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

出现 `SHA256 verification failed` 时停止，删除包和 sidecar 后重新获取。权限错误使用单次 Bypass。Windows 运行时目录内的 direct `Scripts\ai-sdlc.exe` 活动时不能安全原地替换；显式 direct self-update 零安装并返回非零，请改用 `python -m ai_sdlc`（本路线即 `& $ModulePython -m ai_sdlc self-update install --version 3.0.1`）或新终端中的 stable `ai-sdlc`。裸命令不可用时运行 `& $ModulePython -m ai_sdlc status`；`No module named ai_sdlc` 时重跑 `install_offline.ps1 -AddToPath`。`open gates`、代理和 Shell 问题分别按 CLI 指示、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

<a id="route-new-offline-macos-arm64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: new|offline|macos-arm64 -->
## 路线 5：全新空项目 · 离线安装 · macOS Apple Silicon

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `macos-arm64`。联网机器下载归档和 sidecar，目标 Mac 可离线安装。不要移动安装完成后的运行目录。

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/Applications/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
test -z "$(ls -A "$PROJECT_ROOT")" || { echo "Project directory must be empty"; exit 1; }
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

归档内包含 `install_offline.sh`。联网机器下载两项后原样复制到目标 Mac。

```bash
PACKAGE_NAME="ai-sdlc-offline-3.0.1-macos-arm64.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/$PACKAGE_NAME"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$PACKAGE_URL.sha256"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/Applications/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
PACKAGE_NAME="ai-sdlc-offline-3.0.1-macos-arm64.tar.gz"
mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
test -z "$(ls -A "$PROJECT_ROOT")" || { echo "Project directory must be empty"; exit 1; }
(cd "$DOWNLOAD_ROOT" && shasum -a 256 -c "$PACKAGE_NAME.sha256")
```

只有 `$PACKAGE_NAME: OK` 才能继续。

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/ai-sdlc-offline-3.0.1-macos-arm64"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
MODULE_PYTHON="$BUNDLE_ROOT/.venv/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化

```bash
cd "$PROJECT_ROOT"
# 新终端等价入口：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
```

应看到 `Offline installation completed`、`3.0.1`、`Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点，项目目录仍只包含初始化工件。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

`shasum -a 256` 不一致时停止，视同 `SHA256 verification failed`，重新复制归档和 sidecar。权限错误时确认安装目录属于当前用户，再重跑 `install_offline.sh --add-to-path`。命令不可用或 `No module named ai_sdlc` 时使用 module 路径或重装。`open gates`、代理和 Shell 问题分别按 CLI 指引、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

<a id="route-new-offline-linux-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: new|offline|linux-amd64 -->
## 路线 6：全新空项目 · 离线安装 · Linux AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `linux-amd64`。联网机器下载归档与 sidecar，目标机器可离线；安装目录必须由当前用户写入。

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
test -z "$(ls -A "$PROJECT_ROOT")" || { echo "Project directory must be empty"; exit 1; }
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

归档内包含 `install_offline.sh`。联网机器下载两项后原样复制到目标机。

```bash
PACKAGE_NAME="ai-sdlc-offline-3.0.1-linux-amd64.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/$PACKAGE_NAME"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$PACKAGE_URL.sha256"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
PACKAGE_NAME="ai-sdlc-offline-3.0.1-linux-amd64.tar.gz"
mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
test -z "$(ls -A "$PROJECT_ROOT")" || { echo "Project directory must be empty"; exit 1; }
(cd "$DOWNLOAD_ROOT" && sha256sum -c "$PACKAGE_NAME.sha256")
```

只有 `$PACKAGE_NAME: OK` 才能继续。

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/ai-sdlc-offline-3.0.1-linux-amd64"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
MODULE_PYTHON="$BUNDLE_ROOT/.venv/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化

```bash
cd "$PROJECT_ROOT"
# 新终端等价入口：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
```

应看到 `Offline installation completed`、`3.0.1`、`Initialized AI-SDLC project`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点；空目录未写入示例业务文件。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

`sha256sum` 失败时立即停止，重新复制归档与 `.sha256`。权限错误先修正 `$INSTALL_ROOT` 的当前用户写权限，再重跑 `install_offline.sh --add-to-path`。命令不可用时使用 module 路径；`No module named ai_sdlc` 时重装。`open gates`、代理和 Shell 问题分别按 CLI 指引、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

## 第二章：全新用户 + 已有项目

<a id="route-existing-online-windows-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: existing|online|windows-amd64 -->
## 路线 7：已有项目 · 在线安装 · Windows AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `windows-amd64`。在已有项目根目录打开 PowerShell，先提交或备份当前工作；AI-SDLC 接入不得静默修改业务文件。

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Location).Path
$InstallRoot = Join-Path $HOME "AI-SDLC\online-v3.0.1"
$VenvRoot = Join-Path $InstallRoot ".venv"
$DownloadRoot = Join-Path $env:TEMP "ai-sdlc-v3.0.1-online"
New-Item -ItemType Directory -Force -Path $InstallRoot, $DownloadRoot | Out-Null
$GitCommand = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitCommand) { throw "Git is required. Run: winget install --id Git.Git -e, then reopen PowerShell." }
git --version
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -eq 0) { git status --short --untracked-files=all }
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

```powershell
$InstallerName = "install_online.ps1"
$InstallerUrl = "https://raw.githubusercontent.com/SinclairPan/Ai_AutoSDLC/v3.0.1/packaging/install_online.ps1"
$InstallerPath = Join-Path $DownloadRoot $InstallerName
Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```powershell
$PinnedTag = "v3.0.1"
if (-not (Select-String -LiteralPath $InstallerPath -SimpleMatch $PinnedTag -Quiet)) { throw "Installer is not pinned to v3.0.1" }
Write-Host "After install verify with: python -m ai_sdlc --version"
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```powershell
# 固定标签安装器：install_online.ps1 -AddToPath
powershell -NoProfile -ExecutionPolicy Bypass -File $InstallerPath -VenvPath $VenvRoot -AddToPath
$ModulePython = Join-Path $VenvRoot "Scripts\python.exe"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化并接入

```powershell
Set-Location $ProjectRoot
# 新终端等价第一步：ai-sdlc init .
& $ModulePython -m ai_sdlc init .
& $ModulePython -m ai_sdlc adopt .
```

在 `init` 中选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd；随后 `adopt` 只扫描并生成桥接结果。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```powershell
& $ModulePython -m ai_sdlc --version
& $ModulePython -m ai_sdlc status
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -eq 0) { git status --short --untracked-files=all }
```

应看到 `3.0.1`、`Initialized AI-SDLC project`、`接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点；Git 差异只应包含用户确认的 AI-SDLC 工件。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

下载或安装错误时停止，不改用开发分支。PowerShell 受限时使用单次 Bypass。Windows 运行时目录内的 direct `Scripts\ai-sdlc.exe` 活动时不能安全原地替换；显式 direct self-update 零安装并返回非零，请改用 `python -m ai_sdlc`（本路线即 `& $ModulePython -m ai_sdlc self-update install --version 3.0.1`）或新终端中的 stable `ai-sdlc`。裸命令不可用时运行 `& $ModulePython -m ai_sdlc status`；`No module named ai_sdlc` 时重跑 `install_online.ps1 -AddToPath`。若 `git status --short --untracked-files=all` 出现未预期业务文件，停止并人工检查。`open gates`、代理和 Shell 问题分别按 CLI 指示、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

<a id="route-existing-online-macos-arm64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: existing|online|macos-arm64 -->
## 路线 8：已有项目 · 在线安装 · macOS Apple Silicon

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `macos-arm64`。在已有项目根目录打开 Terminal并保存当前工作。

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/Applications/AI-SDLC/online-v3.0.1"
VENV_ROOT="$INSTALL_ROOT/.venv"
DOWNLOAD_ROOT="$(mktemp -d)"
mkdir -p "$INSTALL_ROOT"
command -v git >/dev/null 2>&1 || { echo "Git is required. Run: xcode-select --install, then reopen Terminal." >&2; exit 1; }
git --version
if ! command -v brew >/dev/null 2>&1; then
  if test ! -x /opt/homebrew/bin/brew; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
brew --version
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

```bash
INSTALLER_NAME="install_online.sh"
INSTALLER_URL="https://raw.githubusercontent.com/SinclairPan/Ai_AutoSDLC/v3.0.1/packaging/install_online.sh"
INSTALLER_PATH="$DOWNLOAD_ROOT/$INSTALLER_NAME"
curl --fail --location --retry 3 --output "$INSTALLER_PATH" "$INSTALLER_URL"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
PINNED_TAG="v3.0.1"
grep -F "$PINNED_TAG" "$INSTALLER_PATH" >/dev/null || { echo "Installer is not pinned to v3.0.1"; exit 1; }
echo 'After install verify with: python -m ai_sdlc --version'
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
# 固定标签安装器：install_online.sh --add-to-path
bash "$INSTALLER_PATH" "$VENV_ROOT" --add-to-path
MODULE_PYTHON="$VENV_ROOT/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化并接入

```bash
cd "$PROJECT_ROOT"
# 新终端等价第一步：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
"$MODULE_PYTHON" -m ai_sdlc adopt .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

输出应包含 `3.0.1`、`Initialized AI-SDLC project`、`接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

网络错误时停止并重试固定标签 URL。若缺少 Homebrew，运行 `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`，再运行 `eval "$(/opt/homebrew/bin/brew shellenv)"` 后重试安装。裸命令不可用时运行 `"$MODULE_PYTHON" -m ai_sdlc status`；`No module named ai_sdlc` 时重跑 `install_online.sh --add-to-path`。若 Git 显示未预期业务差异，停止并检查。`open gates`、代理和 Shell 问题分别按 CLI 指示、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 恢复。

<a id="route-existing-online-linux-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: existing|online|linux-amd64 -->
## 路线 9：已有项目 · 在线安装 · Linux AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `linux-amd64`。在已有项目根目录使用 bash并保存当前工作，确认当前用户可写安装目录。

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC/online-v3.0.1"
VENV_ROOT="$INSTALL_ROOT/.venv"
DOWNLOAD_ROOT="$(mktemp -d)"
mkdir -p "$INSTALL_ROOT"
command -v git >/dev/null 2>&1 || { echo "Git is required. Install it with apt/dnf/yum, then reopen the shell." >&2; exit 1; }
git --version
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

```bash
INSTALLER_NAME="install_online.sh"
INSTALLER_URL="https://raw.githubusercontent.com/SinclairPan/Ai_AutoSDLC/v3.0.1/packaging/install_online.sh"
INSTALLER_PATH="$DOWNLOAD_ROOT/$INSTALLER_NAME"
curl --fail --location --retry 3 --output "$INSTALLER_PATH" "$INSTALLER_URL"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
PINNED_TAG="v3.0.1"
grep -F "$PINNED_TAG" "$INSTALLER_PATH" >/dev/null || { echo "Installer is not pinned to v3.0.1"; exit 1; }
echo 'After install verify with: python -m ai_sdlc --version'
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
# 固定标签安装器：install_online.sh --add-to-path
bash "$INSTALLER_PATH" "$VENV_ROOT" --add-to-path
MODULE_PYTHON="$VENV_ROOT/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化并接入

```bash
cd "$PROJECT_ROOT"
# 新终端等价第一步：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
"$MODULE_PYTHON" -m ai_sdlc adopt .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

应看到 `3.0.1`、`Initialized AI-SDLC project`、`接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

网络或权限错误时停止，修正权限后重跑固定标签的 `install_online.sh --add-to-path`。裸命令不可用时使用 module 路径；`No module named ai_sdlc` 时重装。若业务文件出现非预期差异，停止并检查。`open gates`、代理和 Shell 问题分别按 CLI 指示、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

<a id="route-existing-offline-windows-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: existing|offline|windows-amd64 -->
## 路线 10：已有项目 · 离线安装 · Windows AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `windows-amd64`。在已有项目根目录打开 PowerShell并保存当前工作；联网机器下载，目标机器可离线。

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Location).Path
$InstallRoot = Join-Path $HOME "AI-SDLC"
$DownloadRoot = Join-Path $HOME "Downloads\ai-sdlc-v3.0.1"
New-Item -ItemType Directory -Force -Path $InstallRoot, $DownloadRoot | Out-Null
$GitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($GitCommand) {
    git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -eq 0) { git status --short --untracked-files=all }
}
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

包内包含 `install_offline.ps1`。联网机器下载两项后原样复制到目标机。

```powershell
$PackageName = "ai-sdlc-offline-3.0.1-windows-amd64.zip"
$PackageUrl = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/$PackageName"
Invoke-WebRequest -Uri $PackageUrl -OutFile (Join-Path $DownloadRoot $PackageName)
Invoke-WebRequest -Uri "$PackageUrl.sha256" -OutFile (Join-Path $DownloadRoot "$PackageName.sha256")
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Location).Path
$InstallRoot = Join-Path $HOME "AI-SDLC"
$DownloadRoot = Join-Path $HOME "Downloads\ai-sdlc-v3.0.1"
$PackageName = "ai-sdlc-offline-3.0.1-windows-amd64.zip"
New-Item -ItemType Directory -Force -Path $InstallRoot, $DownloadRoot | Out-Null
$GitCommand = Get-Command git -ErrorAction SilentlyContinue
$PackagePath = Join-Path $DownloadRoot $PackageName
$Parts = (Get-Content -LiteralPath "$PackagePath.sha256" -Raw).Trim() -split '\s+', 2
$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
if ($Parts.Count -ne 2 -or $Parts[1] -ne $PackageName -or $Parts[0].ToLowerInvariant() -ne $Actual) { throw "SHA256 verification failed for $PackageName" }
```

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```powershell
Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallRoot -Force
$BundleRoot = Join-Path $InstallRoot "ai-sdlc-offline-3.0.1-windows-amd64"
Push-Location $BundleRoot
try { powershell -NoProfile -ExecutionPolicy Bypass -File ".\install_offline.ps1" -AddToPath } finally { Pop-Location }
$ModulePython = Join-Path $BundleRoot ".venv\Scripts\python.exe"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化并接入

```powershell
Set-Location $ProjectRoot
# 新终端等价第一步：ai-sdlc init .
& $ModulePython -m ai_sdlc init .
& $ModulePython -m ai_sdlc adopt .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```powershell
& $ModulePython -m ai_sdlc --version
& $ModulePython -m ai_sdlc status
if ($GitCommand) {
    git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -eq 0) { git status --short --untracked-files=all }
}
```

必须看到 `Offline installation completed`、`3.0.1`、`Initialized AI-SDLC project`、`接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

`SHA256 verification failed` 时停止并重新获取包与 sidecar。权限错误使用单次 Bypass；Windows 运行时目录内的 direct `Scripts\ai-sdlc.exe` 活动时不能安全原地替换；显式 direct self-update 零安装并返回非零，请改用 `python -m ai_sdlc`（本路线即 `& $ModulePython -m ai_sdlc self-update install --version 3.0.1`）或新终端中的 stable `ai-sdlc`。命令不可用时使用 `& $ModulePython -m ai_sdlc status`，`No module named ai_sdlc` 时重跑 `install_offline.ps1 -AddToPath`。若 Git 显示非预期业务变化，停止检查。`open gates`、代理和 Shell 问题分别按 CLI 指引、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

<a id="route-existing-offline-macos-arm64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: existing|offline|macos-arm64 -->
## 路线 11：已有项目 · 离线安装 · macOS Apple Silicon

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `macos-arm64`。在已有项目根目录打开 Terminal并保存当前工作；联网机器下载，目标 Mac 离线安装。

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/Applications/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
mkdir -p "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

归档内包含 `install_offline.sh`。联网机器下载两项后原样复制。

```bash
PACKAGE_NAME="ai-sdlc-offline-3.0.1-macos-arm64.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/$PACKAGE_NAME"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$PACKAGE_URL.sha256"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/Applications/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
PACKAGE_NAME="ai-sdlc-offline-3.0.1-macos-arm64.tar.gz"
mkdir -p "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
(cd "$DOWNLOAD_ROOT" && shasum -a 256 -c "$PACKAGE_NAME.sha256")
```

只有 `$PACKAGE_NAME: OK` 才能继续。

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/ai-sdlc-offline-3.0.1-macos-arm64"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
MODULE_PYTHON="$BUNDLE_ROOT/.venv/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化并接入

```bash
cd "$PROJECT_ROOT"
# 新终端等价第一步：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
"$MODULE_PYTHON" -m ai_sdlc adopt .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

应看到 `Offline installation completed`、`3.0.1`、`Initialized AI-SDLC project`、`接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

`shasum -a 256` 不通过时停止并重新复制归档和 `.sha256`。权限错误时修复当前用户目录权限，再重跑 `install_offline.sh --add-to-path`。裸命令不可用或 `No module named ai_sdlc` 时使用 module 路径或重装。若业务文件异常变化，停止接入。`open gates`、代理和 Shell 问题分别按 CLI 指引、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 恢复。

<a id="route-existing-offline-linux-amd64"></a>
<!-- AI-SDLC-USER-GUIDE-ROUTE: existing|offline|linux-amd64 -->
## 路线 12：已有项目 · 离线安装 · Linux AMD64

<!-- AI-SDLC-USER-GUIDE-STEP: prerequisites -->
### 1. 准备

适用于 `linux-amd64`。在已有项目根目录使用 bash并保存当前工作；联网机器下载，目标机离线安装。

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
mkdir -p "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

<!-- AI-SDLC-USER-GUIDE-STEP: acquire -->
### 2. 获取

归档内包含 `install_offline.sh`。联网机器下载两项后原样复制。

```bash
PACKAGE_NAME="ai-sdlc-offline-3.0.1-linux-amd64.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v3.0.1/$PACKAGE_NAME"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$PACKAGE_URL.sha256"
```

<!-- AI-SDLC-USER-GUIDE-STEP: verify -->
### 3. 校验

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC/offline-v3.0.1"
DOWNLOAD_ROOT="$HOME/Downloads/ai-sdlc-v3.0.1"
PACKAGE_NAME="ai-sdlc-offline-3.0.1-linux-amd64.tar.gz"
mkdir -p "$INSTALL_ROOT" "$DOWNLOAD_ROOT"
(cd "$DOWNLOAD_ROOT" && sha256sum -c "$PACKAGE_NAME.sha256")
```

只有 `$PACKAGE_NAME: OK` 才能继续。

<!-- AI-SDLC-USER-GUIDE-STEP: install -->
### 4. 安装

```bash
tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/ai-sdlc-offline-3.0.1-linux-amd64"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
MODULE_PYTHON="$BUNDLE_ROOT/.venv/bin/python"
```

<!-- AI-SDLC-USER-GUIDE-STEP: initialize -->
### 5. 初始化并接入

```bash
cd "$PROJECT_ROOT"
# 新终端等价第一步：ai-sdlc init .
"$MODULE_PYTHON" -m ai_sdlc init .
"$MODULE_PYTHON" -m ai_sdlc adopt .
```

选择 Claude Code、Codex、Cursor、VS Code 或其他-通用，以及 PowerShell、Bash、Zsh 或 Cmd。

<!-- AI-SDLC-USER-GUIDE-STEP: success -->
### 6. 成功证据

```bash
"$MODULE_PYTHON" -m ai_sdlc --version
"$MODULE_PYTHON" -m ai_sdlc status
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short --untracked-files=all; fi
```

应看到 `Offline installation completed`、`3.0.1`、`Initialized AI-SDLC project`、`接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`当前结果 / Result`、`下一步 / Next` 和推荐继续点。

<!-- AI-SDLC-USER-GUIDE-STEP: recover -->
### 7. 就地恢复

`sha256sum` 失败时停止并重新复制归档与 sidecar。目录权限错误时修复当前用户写权限，再重跑 `install_offline.sh --add-to-path`。裸命令不可用时使用 `"$MODULE_PYTHON" -m ai_sdlc status`；`No module named ai_sdlc` 时重装。若业务文件出现非预期变化，停止检查。`open gates`、代理和 Shell 问题分别按 CLI 指引、`ai-sdlc adapter select`、`ai-sdlc adapter shell-select` 处理。

## 异常情况速查

- 校验失败：立即停止，重新获取归档与同名 `.sha256`，不要跳过摘要校验。
- 裸命令不可用：在当前窗口使用对应路线保存的 module Python；重开终端后再检查 PATH。
- `No module named ai_sdlc`：保持原安装目录不动，重跑同一路线的正式安装器。
- `open gates`：按 CLI 的 `当前结果 / Result` 与 `下一步 / Next` 处理，不手工改状态文件。
- 已有项目出现非预期业务差异：停止并检查 Git，不继续接入或执行。

## 安装后的统一入口

每条路线完成后，重开终端并进入项目目录，优先执行：

```text
ai-sdlc --version
ai-sdlc status
ai-sdlc run
```

已有项目若需要在新终端重新执行接入，必须先完成 `ai-sdlc init .`，再运行 `ai-sdlc adopt .`。

只有在 PATH 尚未刷新或 CLI 明确要求排障时，才使用路线保存的 module Python。不要移动或删除安装目录，也不要用开发分支、源码 worktree 或手工依赖安装替代正式路线。
