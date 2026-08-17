# AI-SDLC 2.0.0 新用户使用手册

> 面向第一次使用 AI-SDLC 的用户。选择与你当前情况完全一致的一条路径，从头到尾执行即可。<br>
> 本手册是 `Downloads & Docs` 中独立打开的使用指南；产品页不重复这些安装步骤。

[返回 Downloads & Docs](../index.html#downloads-docs)

<a id="choose-path"></a>

## 先选择唯一适合你的路径

先回答两个问题：

1. 业务代码是否已经存在？存在就选“已有项目”，还没有项目目录就选“全新项目”。
2. 目标电脑能否访问 GitHub？不能或不希望安装时联网，就选“离线包”；可以联网，就选“在线安装”。

| 你的情况 | Windows 11 / amd64 | macOS / Apple Silicon | Linux / amd64 |
| --- | --- | --- | --- |
| 已有项目 + 离线包 | [路径 1A](#path-1a) | [路径 1B](#path-1b) | [路径 1C](#path-1c) |
| 已有项目 + 在线安装 | [路径 2A](#path-2a) | [路径 2B](#path-2b) | [路径 2C](#path-2c) |
| 全新项目 + 离线包 | [路径 3A](#path-3a) | [路径 3B](#path-3b) | [路径 3C](#path-3c) |
| 全新项目 + 在线安装 | [路径 4A](#path-4a) | [路径 4B](#path-4b) | [路径 4C](#path-4c) |

版本与来源：

- 正式版本：`v2.0.0`
- 固定源码提交：`737bda39e05c53450e180a20581b7b7a70db9cf0`
- 正式发布页：[AI-SDLC v2.0.0](https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0)
- 正式离线包只提供下载链接，不内嵌在本地产品站中。
- 立即安装和初始化时使用安装器返回的 `Direct CLI`，不要依赖旧终端中的同名裸命令。

离线包的平台范围是 Windows amd64、macOS Apple Silicon 和 Linux amd64。系统或 CPU 架构不匹配时不要尝试安装。

---

<a id="path-1a"></a>

## 路径 1A：已有项目 + Windows 离线包

> 当前路径：已有项目 · 离线包 · Windows amd64<br>
> 完成顺序：Install → Verify → Initialize → Start

开始前，请把以下两个文件提前下载到当前 Windows 用户的 `Downloads` 文件夹：

- [ai-sdlc-offline-2.0.0-windows-amd64.zip](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip)
- [ai-sdlc-offline-2.0.0-windows-amd64.zip.sha256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip.sha256)

### 1A-1 Install：校验并安装

**本步要完成什么**

确认当前业务项目，校验离线包，然后把 AI-SDLC 安装到业务项目之外的长期目录。

**在哪里执行**

在已有项目根目录打开 PowerShell。项目目录中应能看到你的源码或项目配置文件。

**复制并运行**

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Location).Path
$InstallRoot = Join-Path $HOME "AI-SDLC"
$BundleName = "ai-sdlc-offline-2.0.0-windows-amd64"
$PackageName = "$BundleName.zip"
$PackagePath = Join-Path $HOME "Downloads\$PackageName"
$ChecksumPath = "$PackagePath.sha256"

Write-Host "Project: $ProjectRoot"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "未检测到 Git。离线包只内置 Python runtime；请先安装 Git。"
}
git status --short --branch
if (-not (Test-Path -LiteralPath $PackagePath) -or -not (Test-Path -LiteralPath $ChecksumPath)) {
  throw "Downloads 中缺少离线包或同名 .sha256 文件。"
}

$ChecksumParts = (Get-Content -LiteralPath $ChecksumPath -Raw).Trim() -split '\s+', 2
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
if ($ChecksumParts.Count -ne 2 -or $ChecksumParts[1] -ne $PackageName -or $ChecksumParts[0].ToLowerInvariant() -ne $ActualHash) {
  throw "SHA256 verification failed for $PackageName"
}
Write-Host "SHA256 verified: $PackageName"

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallRoot -Force
$BundleRoot = Join-Path $InstallRoot $BundleName
$env:PIP_NO_INDEX = "1"
Push-Location $BundleRoot
try {
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\install_offline.ps1" -AddToPath
  if ($LASTEXITCODE -ne 0) {
    throw "Offline installer failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}
$DirectCli = Join-Path $BundleRoot ".venv\Scripts\ai-sdlc.exe"
```

**你应该看到**

输出包含 `SHA256 verified`、`Result`、`Offline installation completed`、`Next` 和 `Direct shim`。

**如果结果不同**

- 摘要不一致：立即停止，删除这两个下载文件，重新从正式发布页下载。
- 提示平台、runtime 或 manifest 不匹配：停止使用当前压缩包，确认电脑是 Windows amd64。
- 提示包内 Python 不可用：不要手工修改 `.venv`，重新取得匹配平台的正式离线包。

**下一步**

保留当前 PowerShell 窗口，继续验证 Direct CLI。

### 1A-2 Verify：确认版本

**本步要完成什么**

确认刚安装的可执行文件确实是 AI-SDLC 2.0.0。

**在哪里执行**

仍在刚才的 PowerShell 窗口中，当前目录不限。

**复制并运行**

```powershell
$InstalledVersion = (& $DirectCli --version | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $InstalledVersion -ne "2.0.0") {
  throw "Expected AI-SDLC 2.0.0, got '$InstalledVersion'"
}
```

**你应该看到**

版本输出包含且必须是 `2.0.0`。

**如果结果不同**

不要继续初始化。确认 `$DirectCli` 指向 `$HOME\AI-SDLC\ai-sdlc-offline-2.0.0-windows-amd64\.venv\Scripts\ai-sdlc.exe`，然后重新执行版本检查。

**下一步**

版本正确后，回到已有项目并初始化。

### 1A-3 Initialize：初始化已有项目

**本步要完成什么**

让 AI-SDLC 在已有项目中安装治理规则，并查看新增或修改的项目文件。

**在哪里执行**

在同一个 PowerShell 窗口中执行。命令会切回先前记录的已有项目根目录。

**复制并运行**

```powershell
Set-Location $ProjectRoot
if (Get-Command git -ErrorAction SilentlyContinue) {
  Write-Host "Before init:"
  git status --short
}
& $DirectCli init .
if ($LASTEXITCODE -ne 0) { throw "AI-SDLC init failed." }
if (Get-Command git -ErrorAction SilentlyContinue) {
  Write-Host "After init:"
  git status --short
}
```

在交互菜单中选择你真正用于聊天开发的 AI 工具，再选择项目使用的 Shell。使用 Codex App 或 Codex CLI 就选 Codex；使用 Cursor Agent 就选 Cursor。不要按操作系统猜选项。

**你应该看到**

成功输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 和 `下一步 / Next`。已有项目还可能显示 `Detected existing project`。`open gates` 表示需求或证据尚未补齐，不表示初始化失败。

**如果结果不同**

- 命令非零退出：停止，不要继续执行 `adopt`，先按屏幕错误修复后重跑本步。
- 选错 AI 工具或 Shell：在项目根目录依次执行 `& $DirectCli adapter select` 和 `& $DirectCli adapter shell-select`，在两个交互菜单中重新选择。
- Git 变化超出 `.ai-sdlc/` 和所选适配器规则范围：先检查差异再继续。

**下一步**

初始化成功后，接入已有任务资料。

### 1A-4 Start：接入已有进度并开始工作

**本步要完成什么**

让 AI-SDLC 只读识别已有任务资料，生成继续点，然后把增量需求交给刚才选择的 AI 工具。

**在哪里执行**

仍在已有项目根目录的 PowerShell 中。

**复制并运行**

```powershell
& $DirectCli adopt .
if ($LASTEXITCODE -ne 0) { throw "AI-SDLC adopt failed." }
```

**你应该看到**

输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`、`已识别来源` 和 `已识别任务`。

**如果结果不同**

- 推荐继续点不准确：执行 `& $DirectCli adopt . --prefer "你正在处理的功能关键词"`。
- `已识别任务` 为 0：不必反复执行；在 AI 工具中直接说明目标、范围和验收标准。
- 命令提示尚未初始化：先重新执行本路径的 Initialize 步骤，成功后再运行 `adopt`。

**下一步**

用刚才选择的 AI 工具打开同一个项目目录，直接输入真实增量需求。AI-SDLC 会先细化目标、验收标准与约束；普通成功路径不需要手工运行 `adapter status` 或 `run --dry-run`。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-1b"></a>

## 路径 1B：已有项目 + macOS 离线包

> 当前路径：已有项目 · 离线包 · macOS Apple Silicon<br>
> 完成顺序：Install → Verify → Initialize → Start

开始前，请把以下两个文件提前下载到当前 macOS 用户的 `Downloads` 文件夹：

- [ai-sdlc-offline-2.0.0-macos-arm64.tar.gz](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz)
- [ai-sdlc-offline-2.0.0-macos-arm64.tar.gz.sha256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz.sha256)

### 1B-1 Install：校验并安装

**本步要完成什么**

确认已有项目和 Apple Silicon 架构，校验离线包，并安装到项目目录之外。

**在哪里执行**

在已有项目根目录打开 Terminal，使用 zsh 或 bash。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "未检测到 Git。离线包只内置 Python runtime；请先安装 Git。" >&2
  exit 1
}
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/Applications/AI-SDLC"
BUNDLE_NAME="ai-sdlc-offline-2.0.0-macos-arm64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_PATH="$HOME/Downloads/$PACKAGE_NAME"
CHECKSUM_PATH="$PACKAGE_PATH.sha256"

[ "$(uname -s):$(uname -m)" = "Darwin:arm64" ] || {
  echo "This v2.0.0 offline package requires macOS Apple Silicon." >&2
  exit 1
}
printf 'Project: %s\n' "$PROJECT_ROOT"
git status --short --branch || true
[ -f "$PACKAGE_PATH" ] && [ -f "$CHECKSUM_PATH" ] || {
  echo "Downloads 中缺少离线包或同名 .sha256 文件。" >&2
  exit 1
}
(cd "$HOME/Downloads" && shasum -a 256 -c "$PACKAGE_NAME.sha256")

mkdir -p "$INSTALL_ROOT"
tar xzf "$PACKAGE_PATH" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && PIP_NO_INDEX=1 ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
```

**你应该看到**

输出包含 `$PACKAGE_NAME: OK`、`当前结果 / Result`、`Offline installation completed` 和 `下一步 / Next`。

**如果结果不同**

- 摘要不是 `OK`：停止，删除下载文件并从正式发布页重新取得。
- 架构不是 `Darwin:arm64`：不要使用该包。
- 报 runtime、manifest 或 ABI 不匹配：不要修改包内容，改用匹配平台的正式包。

**下一步**

保留当前 Terminal，继续验证 Direct CLI。

### 1B-2 Verify：确认版本

**本步要完成什么**

确认长期安装目录中的 Direct CLI 版本。

**在哪里执行**

仍在刚才的 Terminal 中。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

停止初始化，确认路径是 `$HOME/Applications/AI-SDLC/ai-sdlc-offline-2.0.0-macos-arm64/.venv/bin/ai-sdlc` 后重试。

**下一步**

版本正确后，初始化已有项目。

### 1B-3 Initialize：初始化已有项目

**本步要完成什么**

安装 AI-SDLC 项目规则，并对比初始化前后的 Git 变化。

**在哪里执行**

在同一个 Terminal 中执行，命令会回到已有项目根目录。

**复制并运行**

```bash
cd "$PROJECT_ROOT"
printf '%s\n' "Before init:"
git status --short || true
"$DIRECT_CLI" init .
printf '%s\n' "After init:"
git status --short || true
```

在菜单中选择实际用于聊天开发的 AI 工具和实际 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 和 `下一步 / Next`。`open gates` 是待补齐事项，不是失败。

**如果结果不同**

非零退出时停止，不要运行 `adopt`；按终端错误修复后重新执行。Git 变化超出 AI-SDLC 状态和适配器规则时，先检查差异。

**下一步**

初始化成功后接入已有进度。

### 1B-4 Start：接入已有进度并开始工作

**本步要完成什么**

生成已有项目的任务桥接结果和推荐继续点。

**在哪里执行**

仍在已有项目根目录的 Terminal 中。

**复制并运行**

```bash
"$DIRECT_CLI" adopt .
```

**你应该看到**

输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`、`已识别来源` 和 `已识别任务`。

**如果结果不同**

推荐不准确时执行 `"$DIRECT_CLI" adopt . --prefer "你正在处理的功能关键词"`；任务数为 0 时直接向 AI 工具说明需求，不要反复扫描。

**下一步**

用已选择的 AI 工具打开同一个项目目录，输入真实增量需求；遵循 CLI 的 `Next`，无需额外执行诊断命令。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-1c"></a>

## 路径 1C：已有项目 + Linux 离线包

> 当前路径：已有项目 · 离线包 · Linux amd64<br>
> 完成顺序：Install → Verify → Initialize → Start

开始前，请把以下两个文件提前下载到当前 Linux 用户的 `Downloads` 目录：

- [ai-sdlc-offline-2.0.0-linux-amd64.tar.gz](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz)
- [ai-sdlc-offline-2.0.0-linux-amd64.tar.gz.sha256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz.sha256)

### 1C-1 Install：校验并安装

**本步要完成什么**

确认已有项目和 amd64 架构，校验离线包，并在严格离线模式安装。

**在哪里执行**

在已有项目根目录打开 bash。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "未检测到 Git。离线包只内置 Python runtime；请先安装 Git。" >&2
  exit 1
}
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC"
BUNDLE_NAME="ai-sdlc-offline-2.0.0-linux-amd64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_PATH="$HOME/Downloads/$PACKAGE_NAME"
CHECKSUM_PATH="$PACKAGE_PATH.sha256"

[ "$(uname -s):$(uname -m)" = "Linux:x86_64" ] || {
  echo "This v2.0.0 offline package requires Linux x86_64." >&2
  exit 1
}
printf 'Project: %s\n' "$PROJECT_ROOT"
git status --short --branch || true
[ -f "$PACKAGE_PATH" ] && [ -f "$CHECKSUM_PATH" ] || {
  echo "Downloads 中缺少离线包或同名 .sha256 文件。" >&2
  exit 1
}
(cd "$HOME/Downloads" && sha256sum -c "$PACKAGE_NAME.sha256")

mkdir -p "$INSTALL_ROOT"
tar xzf "$PACKAGE_PATH" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && PIP_NO_INDEX=1 ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
```

**你应该看到**

输出包含 `$PACKAGE_NAME: OK`、`当前结果 / Result`、`Offline installation completed` 和 `下一步 / Next`。

**如果结果不同**

摘要失败、架构不符、runtime 或 manifest 不匹配时立即停止；不要尝试绕过包内检查。

**下一步**

保留当前 bash，继续验证版本。

### 1C-2 Verify：确认版本

**本步要完成什么**

确认 Direct CLI 是 2.0.0。

**在哪里执行**

仍在当前 bash 中。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

停止初始化，确认路径是 `$HOME/.local/share/AI-SDLC/ai-sdlc-offline-2.0.0-linux-amd64/.venv/bin/ai-sdlc` 后重试。

**下一步**

版本正确后初始化已有项目。

### 1C-3 Initialize：初始化已有项目

**本步要完成什么**

安装项目治理规则，并检查 Git 变化。

**在哪里执行**

在同一 bash 中，命令会回到已有项目根目录。

**复制并运行**

```bash
cd "$PROJECT_ROOT"
printf '%s\n' "Before init:"
git status --short || true
"$DIRECT_CLI" init .
printf '%s\n' "After init:"
git status --short || true
```

在交互菜单中选择你真正使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 和 `下一步 / Next`；`open gates` 不代表初始化失败。

**如果结果不同**

非零退出时停止，不运行 `adopt`。按错误提示修复后重跑；若 Git 变化异常，先审查差异。

**下一步**

初始化成功后接入已有任务资料。

### 1C-4 Start：接入已有进度并开始工作

**本步要完成什么**

让 AI-SDLC 生成已有项目的桥接结果。

**在哪里执行**

仍在已有项目根目录的 bash 中。

**复制并运行**

```bash
"$DIRECT_CLI" adopt .
```

**你应该看到**

输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`、`已识别来源` 和 `已识别任务`。

**如果结果不同**

推荐点不准确时运行 `"$DIRECT_CLI" adopt . --prefer "你正在处理的功能关键词"`；任务数为 0 时直接在 AI 工具中说明目标和验收标准。

**下一步**

用已选择的 AI 工具打开同一个项目目录并输入真实需求，按 CLI 的 `Next` 继续。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-2a"></a>

## 路径 2A：已有项目 + Windows 在线安装

> 当前路径：已有项目 · 在线安装 · Windows amd64<br>
> 完成顺序：Install → Verify → Initialize → Start

### 2A-1 Install：取得固定版本并安装

**本步要完成什么**

从正式仓库取得 v2.0.0，校验固定提交，再执行仓内在线安装器。安装目录与业务项目目录分离。

**在哪里执行**

在已有项目根目录打开 PowerShell。电脑需要能访问 GitHub，并已安装 Git。不要把下载和执行远程脚本写成一条管道命令。

**复制并运行**

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Location).Path
$InstallEnv = Join-Path $HOME "AI-SDLC\online-2.0.0"
$SourceParent = Join-Path $env:TEMP ("ai-sdlc-v2.0.0-source-" + [guid]::NewGuid().ToString("N"))
$SourceRoot = Join-Path $SourceParent "Ai_AutoSDLC"
$ExpectedCommit = "737bda39e05c53450e180a20581b7b7a70db9cf0"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "未检测到 Git。请先安装 Git，再重新执行本步。"
}
git status --short --branch
git clone --branch v2.0.0 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git $SourceRoot
if ($LASTEXITCODE -ne 0) { throw "v2.0.0 source download failed." }
$ActualCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
if ($ActualCommit -ne $ExpectedCommit) {
  throw "v2.0.0 commit mismatch: $ActualCommit"
}

powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "packaging\install_online.ps1") -VenvPath $InstallEnv -PackageSpec $SourceRoot -AddToPath
if ($LASTEXITCODE -ne 0) {
  throw "Online installer failed with exit code $LASTEXITCODE"
}
$DirectCli = Join-Path $InstallEnv "Scripts\ai-sdlc.exe"
```

**你应该看到**

源码提交校验通过后，安装输出包含 `Using Python runtime`、`Result`、`Online installation completed`、`Next` 和 `Direct shim`。缺少 Python 3.11+ 时，安装器可能通过 winget 或 choco 尝试安装。

**如果结果不同**

- Git 不存在、clone 失败或提交不等于固定值：立即停止，不执行安装器。
- Python 自动安装失败：按安装器提示提供 winget 或 choco 可用环境后重跑。
- 公司代理阻断 GitHub 或 pip：改选 Windows 离线包路径。

**下一步**

保留当前 PowerShell，验证 Direct CLI。

### 2A-2 Verify：确认版本

**本步要完成什么**

确认在线安装结果严格为 2.0.0。

**在哪里执行**

仍在当前 PowerShell 中。

**复制并运行**

```powershell
$InstalledVersion = (& $DirectCli --version | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $InstalledVersion -ne "2.0.0") {
  throw "Expected AI-SDLC 2.0.0, got '$InstalledVersion'"
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

不要使用裸 `ai-sdlc` 猜测版本；确认 `$DirectCli` 指向 `$HOME\AI-SDLC\online-2.0.0\Scripts\ai-sdlc.exe`。版本仍不正确时停止。

**下一步**

版本正确后初始化已有项目。

### 2A-3 Initialize：初始化已有项目

**本步要完成什么**

初始化项目并检查治理文件变化。

**在哪里执行**

在同一个 PowerShell 中，命令会回到已有项目根目录。

**复制并运行**

```powershell
Set-Location $ProjectRoot
Write-Host "Before init:"
git status --short
& $DirectCli init .
if ($LASTEXITCODE -ne 0) { throw "AI-SDLC init failed." }
Write-Host "After init:"
git status --short
```

按交互菜单选择实际使用的 AI 工具和 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 与 `下一步 / Next`。

**如果结果不同**

非零退出时停止，不运行 `adopt`；按终端错误修复后重新执行。Git 变化异常时先检查差异。

**下一步**

初始化成功后接入已有进度。

### 2A-4 Start：接入已有进度并开始工作

**本步要完成什么**

扫描已有任务资料，生成推荐继续点。

**在哪里执行**

仍在已有项目根目录的 PowerShell 中。

**复制并运行**

```powershell
& $DirectCli adopt .
if ($LASTEXITCODE -ne 0) { throw "AI-SDLC adopt failed." }
```

**你应该看到**

输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`、`已识别来源` 和 `已识别任务`。

**如果结果不同**

推荐不准确时运行 `& $DirectCli adopt . --prefer "你正在处理的功能关键词"`；识别任务为 0 时直接向 AI 工具说明目标、范围和验收标准。

**下一步**

在已选择的 AI 工具中打开同一项目目录并输入真实增量需求。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-2b"></a>

## 路径 2B：已有项目 + macOS 在线安装

> 当前路径：已有项目 · 在线安装 · macOS<br>
> 完成顺序：Install → Verify → Initialize → Start

### 2B-1 Install：取得固定版本并安装

**本步要完成什么**

克隆正式 v2.0.0、校验提交并执行在线安装器。

**在哪里执行**

在已有项目根目录打开 Terminal。需要 Git 和可用网络；安装器在缺少 Python 3.11+ 时可尝试通过 Homebrew 安装。不要使用 `curl | bash`。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "Git is required before running the online installer." >&2
  exit 1
}
PROJECT_ROOT="$PWD"
INSTALL_ENV="$HOME/Applications/AI-SDLC/online-2.0.0"
SOURCE_PARENT="$(mktemp -d)"
SOURCE_ROOT="$SOURCE_PARENT/Ai_AutoSDLC"
EXPECTED_COMMIT="737bda39e05c53450e180a20581b7b7a70db9cf0"

git status --short --branch || true
git clone --branch v2.0.0 --depth 1 \
  https://github.com/SinclairPan/Ai_AutoSDLC.git \
  "$SOURCE_ROOT"
ACTUAL_COMMIT="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
[ "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo "v2.0.0 commit mismatch: $ACTUAL_COMMIT" >&2
  exit 1
}

AI_SDLC_PACKAGE_SPEC="$SOURCE_ROOT" \
  bash "$SOURCE_ROOT/packaging/install_online.sh" \
  "$INSTALL_ENV" --add-to-path
DIRECT_CLI="$INSTALL_ENV/bin/ai-sdlc"
```

**你应该看到**

安装输出包含 `Using Python runtime`、`当前结果 / Result`、`Online installation completed` 和 `下一步 / Next`。

**如果结果不同**

Git 缺失、clone 失败或提交不匹配时立即停止。Homebrew/Python 权限或网络不可用时，改选 macOS 离线包路径。

**下一步**

验证 Direct CLI。

### 2B-2 Verify：确认版本

**本步要完成什么**

确认安装结果严格为 2.0.0。

**在哪里执行**

仍在当前 Terminal 中。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认路径为 `$HOME/Applications/AI-SDLC/online-2.0.0/bin/ai-sdlc`。不要继续初始化错误版本。

**下一步**

初始化已有项目。

### 2B-3 Initialize：初始化已有项目

**本步要完成什么**

安装项目规则，并查看初始化前后的 Git 变化。

**在哪里执行**

同一个 Terminal，命令会回到已有项目根目录。

**复制并运行**

```bash
cd "$PROJECT_ROOT"
printf '%s\n' "Before init:"
git status --short || true
"$DIRECT_CLI" init .
printf '%s\n' "After init:"
git status --short || true
```

选择实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 与 `下一步 / Next`。

**如果结果不同**

非零退出时停止，不运行 `adopt`；修复终端提示的问题后重跑本步。

**下一步**

接入已有任务资料。

### 2B-4 Start：接入已有进度并开始工作

**本步要完成什么**

生成任务桥接结果和推荐继续点。

**在哪里执行**

仍在已有项目根目录的 Terminal 中。

**复制并运行**

```bash
"$DIRECT_CLI" adopt .
```

**你应该看到**

输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`、`已识别来源` 和 `已识别任务`。

**如果结果不同**

推荐不准时运行 `"$DIRECT_CLI" adopt . --prefer "你正在处理的功能关键词"`；任务数为 0 时直接向 AI 工具描述需求。

**下一步**

使用已选择的 AI 工具打开同一项目目录，输入真实增量需求。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-2c"></a>

## 路径 2C：已有项目 + Linux 在线安装

> 当前路径：已有项目 · 在线安装 · Linux<br>
> 完成顺序：Install → Verify → Initialize → Start

### 2C-1 Install：取得固定版本并安装

**本步要完成什么**

克隆 v2.0.0、校验提交并执行在线安装器。

**在哪里执行**

在已有项目根目录打开 bash。需要 Git 和网络；安装器在缺少 Python 3.11+ 时可尝试 apt、dnf 或 yum。不要执行远程脚本管道。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "Git is required before running the online installer." >&2
  exit 1
}
PROJECT_ROOT="$PWD"
INSTALL_ENV="$HOME/.local/share/AI-SDLC/online-2.0.0"
SOURCE_PARENT="$(mktemp -d)"
SOURCE_ROOT="$SOURCE_PARENT/Ai_AutoSDLC"
EXPECTED_COMMIT="737bda39e05c53450e180a20581b7b7a70db9cf0"

git status --short --branch || true
git clone --branch v2.0.0 --depth 1 \
  https://github.com/SinclairPan/Ai_AutoSDLC.git \
  "$SOURCE_ROOT"
ACTUAL_COMMIT="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
[ "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo "v2.0.0 commit mismatch: $ACTUAL_COMMIT" >&2
  exit 1
}

AI_SDLC_PACKAGE_SPEC="$SOURCE_ROOT" \
  bash "$SOURCE_ROOT/packaging/install_online.sh" \
  "$INSTALL_ENV" --add-to-path
DIRECT_CLI="$INSTALL_ENV/bin/ai-sdlc"
```

**你应该看到**

输出包含 `Using Python runtime`、`当前结果 / Result`、`Online installation completed` 和 `下一步 / Next`。

**如果结果不同**

Git、网络、固定提交或 Python 安装任一失败都应停止；无法联网时改选 Linux 离线包路径。

**下一步**

验证 Direct CLI。

### 2C-2 Verify：确认版本

**本步要完成什么**

确认安装版本严格为 2.0.0。

**在哪里执行**

仍在当前 bash 中。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认路径为 `$HOME/.local/share/AI-SDLC/online-2.0.0/bin/ai-sdlc`，错误版本不得继续。

**下一步**

初始化已有项目。

### 2C-3 Initialize：初始化已有项目

**本步要完成什么**

安装项目规则并检查 Git 变化。

**在哪里执行**

同一个 bash，命令会回到已有项目根目录。

**复制并运行**

```bash
cd "$PROJECT_ROOT"
printf '%s\n' "Before init:"
git status --short || true
"$DIRECT_CLI" init .
printf '%s\n' "After init:"
git status --short || true
```

选择实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 与 `下一步 / Next`。

**如果结果不同**

非零退出时停止，不运行 `adopt`；按屏幕错误修复后重试。

**下一步**

接入已有任务资料。

### 2C-4 Start：接入已有进度并开始工作

**本步要完成什么**

生成任务桥接结果。

**在哪里执行**

仍在已有项目根目录的 bash 中。

**复制并运行**

```bash
"$DIRECT_CLI" adopt .
```

**你应该看到**

输出包含 `接入已有项目：已生成桥接结果`、`原任务文件不会被修改`、`推荐继续点`、`已识别来源` 和 `已识别任务`。

**如果结果不同**

推荐不准确时运行 `"$DIRECT_CLI" adopt . --prefer "你正在处理的功能关键词"`；任务数为 0 时直接描述需求。

**下一步**

在已选择的 AI 工具中打开同一项目目录并输入真实需求。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-3a"></a>

## 路径 3A：全新项目 + Windows 离线包

> 当前路径：全新项目 · 离线包 · Windows amd64<br>
> 完成顺序：Install → Verify → Initialize → Start<br>
> 全新项目不执行 `adopt`。

开始前，把以下文件提前下载到当前 Windows 用户的 `Downloads` 文件夹：

- [ai-sdlc-offline-2.0.0-windows-amd64.zip](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip)
- [ai-sdlc-offline-2.0.0-windows-amd64.zip.sha256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip.sha256)

### 3A-1 Install：校验并安装

**本步要完成什么**

校验离线包并把 AI-SDLC 安装到长期目录。

**在哪里执行**

打开 PowerShell，当前目录不限。

**复制并运行**

```powershell
$ErrorActionPreference = "Stop"
$InstallRoot = Join-Path $HOME "AI-SDLC"
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$BundleName = "ai-sdlc-offline-2.0.0-windows-amd64"
$PackageName = "$BundleName.zip"
$PackagePath = Join-Path $HOME "Downloads\$PackageName"
$ChecksumPath = "$PackagePath.sha256"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "未检测到 Git。离线包只内置 Python runtime；请先安装 Git。"
}
if (-not (Test-Path -LiteralPath $PackagePath) -or -not (Test-Path -LiteralPath $ChecksumPath)) {
  throw "Downloads 中缺少离线包或同名 .sha256 文件。"
}
$ChecksumParts = (Get-Content -LiteralPath $ChecksumPath -Raw).Trim() -split '\s+', 2
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
if ($ChecksumParts.Count -ne 2 -or $ChecksumParts[1] -ne $PackageName -or $ChecksumParts[0].ToLowerInvariant() -ne $ActualHash) {
  throw "SHA256 verification failed for $PackageName"
}
Write-Host "SHA256 verified: $PackageName"

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallRoot -Force
$BundleRoot = Join-Path $InstallRoot $BundleName
$env:PIP_NO_INDEX = "1"
Push-Location $BundleRoot
try {
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\install_offline.ps1" -AddToPath
  if ($LASTEXITCODE -ne 0) {
    throw "Offline installer failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}
$DirectCli = Join-Path $BundleRoot ".venv\Scripts\ai-sdlc.exe"
```

**你应该看到**

输出包含 `SHA256 verified`、`Result`、`Offline installation completed`、`Next` 和 `Direct shim`。

**如果结果不同**

摘要、平台、runtime 或 manifest 任一检查失败都应停止。重新取得匹配 Windows amd64 的正式离线包，不要手工改包。

**下一步**

验证 Direct CLI。

### 3A-2 Verify：确认版本

**本步要完成什么**

确认安装版本是 2.0.0。

**在哪里执行**

仍在当前 PowerShell。

**复制并运行**

```powershell
$InstalledVersion = (& $DirectCli --version | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $InstalledVersion -ne "2.0.0") {
  throw "Expected AI-SDLC 2.0.0, got '$InstalledVersion'"
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

停止，确认 Direct CLI 指向刚解压安装包内的 `.venv\Scripts\ai-sdlc.exe`。

**下一步**

创建全新项目并初始化。

### 3A-3 Initialize：创建并初始化全新项目

**本步要完成什么**

创建固定的新项目目录并安装 AI-SDLC 项目规则。

**在哪里执行**

仍在同一 PowerShell。

**复制并运行**

```powershell
if (Test-Path -LiteralPath $ProjectRoot) {
  if (Get-ChildItem -LiteralPath $ProjectRoot -Force | Select-Object -First 1) {
    throw "全新项目目录不是空目录：$ProjectRoot"
  }
} else {
  New-Item -ItemType Directory -Path $ProjectRoot | Out-Null
}
Set-Location $ProjectRoot
& $DirectCli init .
if ($LASTEXITCODE -ne 0) { throw "AI-SDLC init failed." }
```

按菜单选择你实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 和 `下一步 / Next`。新空项目出现 `open gates` 是正常状态，表示需求和证据尚未录入。

**如果结果不同**

非零退出时按屏幕错误修复后重新执行。确认当前目录是 `$HOME\projects\my-new-project`，不是 AI-SDLC 安装目录。

**下一步**

初始化成功后直接输入第一条需求；不要运行 `adopt`。

### 3A-4 Start：输入第一条需求

**本步要完成什么**

让 AI-SDLC 从真实业务目标开始需求澄清、验收标准与任务分解。

**在哪里执行**

在刚才选择的 AI 工具中打开 `$HOME\projects\my-new-project`。

**复制并运行**

```text
我要开始一个全新项目。
请先澄清业务目标、用户、范围、约束和验收标准，再给出技术方案与任务拆分。
如果涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

**你应该看到**

AI 先询问缺失信息或形成结构化需求，不应直接跳到无约束代码生成。

**如果结果不同**

确认 AI 工具打开的是同一个项目目录，并已读取该目录中的 AI-SDLC 规则；继续强调“先需求、后实现”。

**下一步**

逐项回答澄清问题，确认技术方案后再允许进入实现。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-3b"></a>

## 路径 3B：全新项目 + macOS 离线包

> 当前路径：全新项目 · 离线包 · macOS Apple Silicon<br>
> 完成顺序：Install → Verify → Initialize → Start<br>
> 全新项目不执行 `adopt`。

开始前，把以下两个文件提前下载到当前 macOS 用户的 `Downloads` 文件夹：

- [ai-sdlc-offline-2.0.0-macos-arm64.tar.gz](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz)
- [ai-sdlc-offline-2.0.0-macos-arm64.tar.gz.sha256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz.sha256)

### 3B-1 Install：校验并安装

**本步要完成什么**

确认 Apple Silicon 架构，校验并离线安装 AI-SDLC。

**在哪里执行**

打开 Terminal，使用 zsh 或 bash。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "未检测到 Git。离线包只内置 Python runtime；请先安装 Git。" >&2
  exit 1
}
INSTALL_ROOT="$HOME/Applications/AI-SDLC"
PROJECT_ROOT="$HOME/projects/my-new-project"
BUNDLE_NAME="ai-sdlc-offline-2.0.0-macos-arm64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_PATH="$HOME/Downloads/$PACKAGE_NAME"
CHECKSUM_PATH="$PACKAGE_PATH.sha256"

[ "$(uname -s):$(uname -m)" = "Darwin:arm64" ] || {
  echo "This v2.0.0 offline package requires macOS Apple Silicon." >&2
  exit 1
}
[ -f "$PACKAGE_PATH" ] && [ -f "$CHECKSUM_PATH" ] || {
  echo "Downloads 中缺少离线包或同名 .sha256 文件。" >&2
  exit 1
}
(cd "$HOME/Downloads" && shasum -a 256 -c "$PACKAGE_NAME.sha256")
mkdir -p "$INSTALL_ROOT"
tar xzf "$PACKAGE_PATH" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && PIP_NO_INDEX=1 ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
```

**你应该看到**

输出包含 `$PACKAGE_NAME: OK`、`当前结果 / Result`、`Offline installation completed` 和 `下一步 / Next`。

**如果结果不同**

摘要、架构、runtime 或 manifest 检查失败时停止，不要绕过安装器。

**下一步**

验证 Direct CLI。

### 3B-2 Verify：确认版本

**本步要完成什么**

确认版本严格为 2.0.0。

**在哪里执行**

仍在当前 Terminal。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认 Direct CLI 是 `$HOME/Applications/AI-SDLC/ai-sdlc-offline-2.0.0-macos-arm64/.venv/bin/ai-sdlc`；错误版本不得继续。

**下一步**

创建并初始化项目。

### 3B-3 Initialize：创建并初始化全新项目

**本步要完成什么**

创建项目目录并安装 AI-SDLC 规则。

**在哪里执行**

仍在同一个 Terminal。

**复制并运行**

```bash
if [ -d "$PROJECT_ROOT" ] && [ -n "$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "全新项目目录不是空目录：$PROJECT_ROOT" >&2
  exit 1
fi
mkdir -p "$PROJECT_ROOT"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

选择实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 和 `下一步 / Next`；空项目的 `open gates` 是待录入事项。

**如果结果不同**

确认当前目录是 `$HOME/projects/my-new-project`，按错误提示修复后重试。

**下一步**

直接输入第一条需求，不执行 `adopt`。

### 3B-4 Start：输入第一条需求

**本步要完成什么**

开始受 AI-SDLC 约束的需求澄清。

**在哪里执行**

在已选择的 AI 工具中打开 `$HOME/projects/my-new-project`。

**复制并运行**

```text
我要开始一个全新项目。
请先澄清业务目标、用户、范围、约束和验收标准，再给出技术方案与任务拆分。
如果涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

**你应该看到**

AI 先询问缺失信息或形成结构化需求，而不是直接写实现代码。

**如果结果不同**

确认 AI 工具打开同一项目目录并读取了 AI-SDLC 规则，然后重新强调先需求后实现。

**下一步**

回答澄清问题，确认方案后继续。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-3c"></a>

## 路径 3C：全新项目 + Linux 离线包

> 当前路径：全新项目 · 离线包 · Linux amd64<br>
> 完成顺序：Install → Verify → Initialize → Start<br>
> 全新项目不执行 `adopt`。

开始前，把以下两个文件提前下载到当前 Linux 用户的 `Downloads` 目录：

- [ai-sdlc-offline-2.0.0-linux-amd64.tar.gz](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz)
- [ai-sdlc-offline-2.0.0-linux-amd64.tar.gz.sha256](https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz.sha256)

### 3C-1 Install：校验并安装

**本步要完成什么**

确认 Linux x86_64，校验离线包并严格离线安装。

**在哪里执行**

打开 bash。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "未检测到 Git。离线包只内置 Python runtime；请先安装 Git。" >&2
  exit 1
}
INSTALL_ROOT="$HOME/.local/share/AI-SDLC"
PROJECT_ROOT="$HOME/projects/my-new-project"
BUNDLE_NAME="ai-sdlc-offline-2.0.0-linux-amd64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_PATH="$HOME/Downloads/$PACKAGE_NAME"
CHECKSUM_PATH="$PACKAGE_PATH.sha256"

[ "$(uname -s):$(uname -m)" = "Linux:x86_64" ] || {
  echo "This v2.0.0 offline package requires Linux x86_64." >&2
  exit 1
}
[ -f "$PACKAGE_PATH" ] && [ -f "$CHECKSUM_PATH" ] || {
  echo "Downloads 中缺少离线包或同名 .sha256 文件。" >&2
  exit 1
}
(cd "$HOME/Downloads" && sha256sum -c "$PACKAGE_NAME.sha256")
mkdir -p "$INSTALL_ROOT"
tar xzf "$PACKAGE_PATH" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && PIP_NO_INDEX=1 ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
```

**你应该看到**

输出包含 `$PACKAGE_NAME: OK`、`当前结果 / Result`、`Offline installation completed` 和 `下一步 / Next`。

**如果结果不同**

摘要、架构、runtime 或 manifest 检查失败时停止；不要修改离线包内容。

**下一步**

验证 Direct CLI。

### 3C-2 Verify：确认版本

**本步要完成什么**

确认安装版本严格为 2.0.0。

**在哪里执行**

仍在当前 bash。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认 Direct CLI 是 `$HOME/.local/share/AI-SDLC/ai-sdlc-offline-2.0.0-linux-amd64/.venv/bin/ai-sdlc`；错误版本不得继续。

**下一步**

创建并初始化项目。

### 3C-3 Initialize：创建并初始化全新项目

**本步要完成什么**

创建项目目录并安装 AI-SDLC 规则。

**在哪里执行**

仍在同一个 bash。

**复制并运行**

```bash
if [ -d "$PROJECT_ROOT" ] && [ -n "$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "全新项目目录不是空目录：$PROJECT_ROOT" >&2
  exit 1
fi
mkdir -p "$PROJECT_ROOT"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

选择实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 与 `下一步 / Next`；空项目的 `open gates` 是正常待办状态。

**如果结果不同**

确认当前目录是 `$HOME/projects/my-new-project`，按错误提示修复后重试。

**下一步**

直接输入第一条需求，不运行 `adopt`。

### 3C-4 Start：输入第一条需求

**本步要完成什么**

开始需求澄清与任务分解。

**在哪里执行**

在已选择的 AI 工具中打开 `$HOME/projects/my-new-project`。

**复制并运行**

```text
我要开始一个全新项目。
请先澄清业务目标、用户、范围、约束和验收标准，再给出技术方案与任务拆分。
如果涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

**你应该看到**

AI 先询问缺失信息或形成结构化需求，不直接跳过治理过程。

**如果结果不同**

确认 AI 工具打开的是同一个项目目录并读取了 AI-SDLC 规则，然后重新输入需求。

**下一步**

逐项确认需求与方案，再进入实现。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-4a"></a>

## 路径 4A：全新项目 + Windows 在线安装

> 当前路径：全新项目 · 在线安装 · Windows amd64<br>
> 完成顺序：Install → Verify → Initialize → Start<br>
> 全新项目不执行 `adopt`。

### 4A-1 Install：取得固定版本并安装

**本步要完成什么**

克隆 v2.0.0、核对固定提交并在线安装到长期目录。

**在哪里执行**

打开 PowerShell。需要 Git 和可访问 GitHub 的网络。不要直接执行未经校验的远程脚本。

**复制并运行**

```powershell
$ErrorActionPreference = "Stop"
$InstallEnv = Join-Path $HOME "AI-SDLC\online-2.0.0"
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$SourceParent = Join-Path $env:TEMP ("ai-sdlc-v2.0.0-source-" + [guid]::NewGuid().ToString("N"))
$SourceRoot = Join-Path $SourceParent "Ai_AutoSDLC"
$ExpectedCommit = "737bda39e05c53450e180a20581b7b7a70db9cf0"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "未检测到 Git。请先安装 Git，再重新执行本步。"
}
git clone --branch v2.0.0 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git $SourceRoot
if ($LASTEXITCODE -ne 0) { throw "v2.0.0 source download failed." }
$ActualCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
if ($ActualCommit -ne $ExpectedCommit) {
  throw "v2.0.0 commit mismatch: $ActualCommit"
}

powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "packaging\install_online.ps1") -VenvPath $InstallEnv -PackageSpec $SourceRoot -AddToPath
if ($LASTEXITCODE -ne 0) {
  throw "Online installer failed with exit code $LASTEXITCODE"
}
$DirectCli = Join-Path $InstallEnv "Scripts\ai-sdlc.exe"
```

**你应该看到**

安装输出包含 `Using Python runtime`、`Result`、`Online installation completed`、`Next` 和 `Direct shim`。缺少 Python 3.11+ 时，安装器可尝试 winget 或 choco。

**如果结果不同**

Git、网络、固定提交或 Python 自动安装失败时停止。网络受限时改选 Windows 离线包路径。

**下一步**

验证 Direct CLI。

### 4A-2 Verify：确认版本

**本步要完成什么**

确认安装结果为 2.0.0。

**在哪里执行**

仍在当前 PowerShell。

**复制并运行**

```powershell
$InstalledVersion = (& $DirectCli --version | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $InstalledVersion -ne "2.0.0") {
  throw "Expected AI-SDLC 2.0.0, got '$InstalledVersion'"
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认 Direct CLI 是 `$HOME\AI-SDLC\online-2.0.0\Scripts\ai-sdlc.exe`；错误版本不得继续。

**下一步**

创建并初始化全新项目。

### 4A-3 Initialize：创建并初始化全新项目

**本步要完成什么**

创建固定项目目录并安装 AI-SDLC 规则。

**在哪里执行**

仍在同一个 PowerShell。

**复制并运行**

```powershell
if (Test-Path -LiteralPath $ProjectRoot) {
  if (Get-ChildItem -LiteralPath $ProjectRoot -Force | Select-Object -First 1) {
    throw "全新项目目录不是空目录：$ProjectRoot"
  }
} else {
  New-Item -ItemType Directory -Path $ProjectRoot | Out-Null
}
Set-Location $ProjectRoot
& $DirectCli init .
if ($LASTEXITCODE -ne 0) { throw "AI-SDLC init failed." }
```

在菜单中选择实际用于聊天开发的 AI 工具和 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 和 `下一步 / Next`；`open gates` 是尚待录入的需求和证据。

**如果结果不同**

确认项目目录不是 AI-SDLC 安装目录，按错误提示修复后重新执行。

**下一步**

直接输入第一条需求，不运行 `adopt`。

### 4A-4 Start：输入第一条需求

**本步要完成什么**

从业务目标开始受治理的开发过程。

**在哪里执行**

在已选择的 AI 工具中打开 `$HOME\projects\my-new-project`。

**复制并运行**

```text
我要开始一个全新项目。
请先澄清业务目标、用户、范围、约束和验收标准，再给出技术方案与任务拆分。
如果涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

**你应该看到**

AI 先澄清需求与验收标准，再进入方案和任务阶段。

**如果结果不同**

确认 AI 工具打开同一项目目录且加载了 AI-SDLC 规则，然后重新输入需求。

**下一步**

回答澄清问题并确认方案。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-4b"></a>

## 路径 4B：全新项目 + macOS 在线安装

> 当前路径：全新项目 · 在线安装 · macOS<br>
> 完成顺序：Install → Verify → Initialize → Start<br>
> 全新项目不执行 `adopt`。

### 4B-1 Install：取得固定版本并安装

**本步要完成什么**

克隆 v2.0.0、核对提交并执行在线安装器。

**在哪里执行**

打开 Terminal。需要 Git 和网络；不要使用 `curl | bash`。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "Git is required before running the online installer." >&2
  exit 1
}
INSTALL_ENV="$HOME/Applications/AI-SDLC/online-2.0.0"
PROJECT_ROOT="$HOME/projects/my-new-project"
SOURCE_PARENT="$(mktemp -d)"
SOURCE_ROOT="$SOURCE_PARENT/Ai_AutoSDLC"
EXPECTED_COMMIT="737bda39e05c53450e180a20581b7b7a70db9cf0"

git clone --branch v2.0.0 --depth 1 \
  https://github.com/SinclairPan/Ai_AutoSDLC.git \
  "$SOURCE_ROOT"
ACTUAL_COMMIT="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
[ "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo "v2.0.0 commit mismatch: $ACTUAL_COMMIT" >&2
  exit 1
}
AI_SDLC_PACKAGE_SPEC="$SOURCE_ROOT" \
  bash "$SOURCE_ROOT/packaging/install_online.sh" \
  "$INSTALL_ENV" --add-to-path
DIRECT_CLI="$INSTALL_ENV/bin/ai-sdlc"
```

**你应该看到**

安装输出包含 `Using Python runtime`、`当前结果 / Result`、`Online installation completed` 和 `下一步 / Next`。

**如果结果不同**

Git、网络、固定提交或 Homebrew/Python 安装失败时停止；网络受限时改用 macOS 离线包。

**下一步**

验证 Direct CLI。

### 4B-2 Verify：确认版本

**本步要完成什么**

确认版本严格为 2.0.0。

**在哪里执行**

仍在当前 Terminal。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认 Direct CLI 是 `$HOME/Applications/AI-SDLC/online-2.0.0/bin/ai-sdlc`，错误版本不得继续。

**下一步**

创建并初始化项目。

### 4B-3 Initialize：创建并初始化全新项目

**本步要完成什么**

创建固定项目目录并安装 AI-SDLC 规则。

**在哪里执行**

仍在同一个 Terminal。

**复制并运行**

```bash
if [ -d "$PROJECT_ROOT" ] && [ -n "$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "全新项目目录不是空目录：$PROJECT_ROOT" >&2
  exit 1
fi
mkdir -p "$PROJECT_ROOT"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

选择实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 与 `下一步 / Next`；`open gates` 是正常待办状态。

**如果结果不同**

确认项目目录不是安装目录，按屏幕错误修复后重试。

**下一步**

直接输入第一条需求，不执行 `adopt`。

### 4B-4 Start：输入第一条需求

**本步要完成什么**

开始需求澄清和受控实现过程。

**在哪里执行**

在已选择的 AI 工具中打开 `$HOME/projects/my-new-project`。

**复制并运行**

```text
我要开始一个全新项目。
请先澄清业务目标、用户、范围、约束和验收标准，再给出技术方案与任务拆分。
如果涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

**你应该看到**

AI 先补齐目标、边界与验收标准，不直接跳到代码。

**如果结果不同**

确认 AI 工具打开同一项目目录并加载了规则，然后重新输入需求。

**下一步**

确认需求和技术方案后继续。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

<a id="path-4c"></a>

## 路径 4C：全新项目 + Linux 在线安装

> 当前路径：全新项目 · 在线安装 · Linux<br>
> 完成顺序：Install → Verify → Initialize → Start<br>
> 全新项目不执行 `adopt`。

### 4C-1 Install：取得固定版本并安装

**本步要完成什么**

克隆 v2.0.0、校验提交并执行在线安装器。

**在哪里执行**

打开 bash。需要 Git 和网络；不要执行远程脚本管道。

**复制并运行**

```bash
set -e
command -v git >/dev/null 2>&1 || {
  echo "Git is required before running the online installer." >&2
  exit 1
}
INSTALL_ENV="$HOME/.local/share/AI-SDLC/online-2.0.0"
PROJECT_ROOT="$HOME/projects/my-new-project"
SOURCE_PARENT="$(mktemp -d)"
SOURCE_ROOT="$SOURCE_PARENT/Ai_AutoSDLC"
EXPECTED_COMMIT="737bda39e05c53450e180a20581b7b7a70db9cf0"

git clone --branch v2.0.0 --depth 1 \
  https://github.com/SinclairPan/Ai_AutoSDLC.git \
  "$SOURCE_ROOT"
ACTUAL_COMMIT="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
[ "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo "v2.0.0 commit mismatch: $ACTUAL_COMMIT" >&2
  exit 1
}
AI_SDLC_PACKAGE_SPEC="$SOURCE_ROOT" \
  bash "$SOURCE_ROOT/packaging/install_online.sh" \
  "$INSTALL_ENV" --add-to-path
DIRECT_CLI="$INSTALL_ENV/bin/ai-sdlc"
```

**你应该看到**

安装输出包含 `Using Python runtime`、`当前结果 / Result`、`Online installation completed` 和 `下一步 / Next`。

**如果结果不同**

Git、网络、固定提交、apt/dnf/yum 或 Python 任一失败都应停止；无法联网时改用 Linux 离线包。

**下一步**

验证 Direct CLI。

### 4C-2 Verify：确认版本

**本步要完成什么**

确认版本严格为 2.0.0。

**在哪里执行**

仍在当前 bash。

**复制并运行**

```bash
INSTALLED_VERSION="$("$DIRECT_CLI" --version)"
[ "$INSTALLED_VERSION" = "2.0.0" ] || {
  echo "Expected AI-SDLC 2.0.0, got '$INSTALLED_VERSION'" >&2
  exit 1
}
```

**你应该看到**

版本必须为 `2.0.0`。

**如果结果不同**

确认 Direct CLI 是 `$HOME/.local/share/AI-SDLC/online-2.0.0/bin/ai-sdlc`，错误版本不得继续。

**下一步**

创建并初始化项目。

### 4C-3 Initialize：创建并初始化全新项目

**本步要完成什么**

创建固定项目目录并安装 AI-SDLC 规则。

**在哪里执行**

仍在同一个 bash。

**复制并运行**

```bash
if [ -d "$PROJECT_ROOT" ] && [ -n "$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "全新项目目录不是空目录：$PROJECT_ROOT" >&2
  exit 1
fi
mkdir -p "$PROJECT_ROOT"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

选择实际使用的 AI 工具与 Shell。

**你应该看到**

输出包含 `Initialized AI-SDLC project`、`当前结果 / Result` 与 `下一步 / Next`；空项目的 `open gates` 是待录入事项。

**如果结果不同**

确认项目目录不是安装目录，按屏幕错误修复后重试。

**下一步**

直接输入第一条需求，不运行 `adopt`。

### 4C-4 Start：输入第一条需求

**本步要完成什么**

启动需求澄清和任务分解。

**在哪里执行**

在已选择的 AI 工具中打开 `$HOME/projects/my-new-project`。

**复制并运行**

```text
我要开始一个全新项目。
请先澄清业务目标、用户、范围、约束和验收标准，再给出技术方案与任务拆分。
如果涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

**你应该看到**

AI 先澄清业务目标、约束和验收标准，不直接生成无约束代码。

**如果结果不同**

确认 AI 工具打开同一项目目录并加载了规则，然后重新输入需求。

**下一步**

回答澄清问题，确认方案后进入实现。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)

---

## 就地故障处理

以下内容是补充索引；每条主路径已经在可能失败的步骤旁给出处理方法。

### 当前终端找不到 `ai-sdlc`

安装后当前终端可能尚未刷新 PATH。这不影响使用：继续使用路径中已经定义的 `$DirectCli` 或 `DIRECT_CLI`。新开终端后才使用裸 `ai-sdlc`。

### `init` 显示 open gates

只要命令成功结束，并出现 `Initialized AI-SDLC project`、`Result` 和 `Next`，open gates 就表示需求、设计、任务或验证证据尚待补齐。继续进入 AI 对话即可。

### 什么时候运行诊断命令

普通成功路径不需要手工运行 `adapter status` 或 `run --dry-run`。只有 CLI 的错误信息或 `Next` 明确要求排查时，才执行它给出的诊断命令。

### 安装目录能否删除或移动

不要删除或移动正在使用的 AI-SDLC 长期安装目录。Direct CLI 和新终端中的命令入口都依赖该目录。需要重新安装时，先保留业务项目，再按所选路径安装到新的版本目录。

## 本手册的事实边界

- 离线包必须来自正式 v2.0.0 Release，并与同名 `.sha256` 一起校验。
- 在线路径绑定固定提交，防止安装时无意漂移到其他源码版本；v2.0.0 的 annotated tag 本身不包含发布者签名，因此提交核对不是第三方身份签名。
- 离线安装设置 `PIP_NO_INDEX=1`，避免安装器在严格断网环境中尝试访问软件索引。
- `init` 负责项目初始化和安全预演；已有项目随后执行 `adopt`，全新项目不执行 `adopt`。
- 本手册不会把安装包嵌入离线网站，只提供正式下载链接。

[返回路径选择](#choose-path) · [返回 Downloads & Docs](../index.html#downloads-docs)
