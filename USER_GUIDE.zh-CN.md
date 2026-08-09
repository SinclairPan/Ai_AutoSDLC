# AI-SDLC 1.0.2 中文用户指南

AI-SDLC 会把项目规则、需求澄清、技术方案、任务执行、质量门禁和交付证据接入你实际使用的 AI 开发工具。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

> 发布可用性：`v1.0.4 terminal NO-GO / not released`，即 `v1.0.4 未发布`且该源码候选已经终止，不能使用任何 `releases/download/v1.0.4` 路径，也不得由 009 恢复或启用。本指南继续安装最后一个实际发布且保持原样的离线版本 `v1.0.2`；`only future WorkItem 010 may migrate to v1.0.5`，并且必须先满足受保护发布环境的外部 GO 前置条件。

未来 010 只有在两项远端保护均经独立验证后才可启用：`release-publish` environment 以 required reviewers 阻断未审历史 writer，且禁止自批与管理员 bypass；`active no-bypass tag ruleset protects software and Certificate tags`，精确覆盖软件 tag 和 generation-0 Certificate tag，并拒绝更新、删除及非快进变更。009 中两个验证开关均为字符串 `false`；任何部分创建或保护失败都属于 terminal generation burn，禁止清理、恢复或重跑。

本指南只包含两条完整路径：

- 项目目录还是空的：直接阅读第一章。
- 项目中已经有代码或文档：直接阅读第二章。

每章都能独立完成安装和初始化，不需要来回查找其他章节。命令在终端执行；需求文字在你选择的 AI 工具对话入口输入。

> 重要：解压后的 AI-SDLC 安装目录是长期运行环境。安装完成后不要移动或删除它，否则手册中的包内直接命令入口会失效。安装目录不是你的业务项目目录。

## 第一章：全新用户 + 全新空项目

选择你的操作系统，只执行对应小节。

### 1.1 Windows

以下命令在 PowerShell 中执行。命令会把示例项目创建到当前用户目录下的 `projects\my-new-project`，把 AI-SDLC 长期安装到当前用户目录下的 `AI-SDLC`。

复制并执行：

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$InstallRoot = Join-Path $HOME "AI-SDLC"
$DownloadRoot = Join-Path $env:TEMP "ai-sdlc-v1.0.2-download"
$BundleName = "ai-sdlc-offline-1.0.2-windows-amd64"
$PackageName = "$BundleName.zip"
$PackageUrl = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-windows-amd64.zip"
$ChecksumUrl = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-windows-amd64.zip.sha256"

New-Item -ItemType Directory -Force -Path $ProjectRoot, $InstallRoot, $DownloadRoot | Out-Null
$PackagePath = Join-Path $DownloadRoot $PackageName
$ChecksumPath = "$PackagePath.sha256"
Invoke-WebRequest -Uri $PackageUrl -OutFile $PackagePath
Invoke-WebRequest -Uri $ChecksumUrl -OutFile $ChecksumPath

$ChecksumParts = (Get-Content -LiteralPath $ChecksumPath -Raw).Trim() -split '\s+', 2
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
if ($ChecksumParts.Count -ne 2 -or $ChecksumParts[1] -ne $PackageName -or $ChecksumParts[0].ToLowerInvariant() -ne $ActualHash) {
  throw "SHA256 verification failed for $PackageName"
}
Write-Host "SHA256 verified: $PackageName"

Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallRoot -Force
$BundleRoot = Join-Path $InstallRoot $BundleName
Push-Location $BundleRoot
try {
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\install_offline.ps1" -AddToPath
} finally {
  Pop-Location
}
$DirectCli = Join-Path $BundleRoot ".venv\Scripts\ai-sdlc.exe"
& $DirectCli --version
```

成功时会看到这些稳定内容：

```text
SHA256 verified: ai-sdlc-offline-1.0.2-windows-amd64.zip
Result
  Offline installation completed. The installer created the runtime and installed AI-SDLC.
Next
Direct shim:
1.0.2
```

安装器还会显示一条 `Codex + PowerShell project init` 示例。那只是一个专用示例；你不需要因此选择 Codex，继续使用下面的通用交互式命令即可。

初始化空项目：

```powershell
Set-Location $ProjectRoot
& $DirectCli init .
```

命令会停下来让你选择 AI 代理入口和 Shell。选择方法见本章 1.4。

如果当前 PowerShell 窗口已经关闭，重新打开后执行：

```powershell
$ProjectRoot = Join-Path $HOME "projects\my-new-project"
$DirectCli = Join-Path $HOME "AI-SDLC\ai-sdlc-offline-1.0.2-windows-amd64\.venv\Scripts\ai-sdlc.exe"
Set-Location $ProjectRoot
& $DirectCli init .
```

### 1.2 macOS（Apple Silicon）

以下命令在 Terminal 的 zsh 或 bash 中执行。v1.0.2 的正式 macOS 离线包适用于 Apple Silicon。

复制并执行：

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/Applications/AI-SDLC"
DOWNLOAD_ROOT="$(mktemp -d)"
BUNDLE_NAME="ai-sdlc-offline-1.0.2-macos-arm64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-macos-arm64.tar.gz"
CHECKSUM_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-macos-arm64.tar.gz.sha256"

mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$CHECKSUM_URL"
(cd "$DOWNLOAD_ROOT" && shasum -a 256 -c "$PACKAGE_NAME.sha256")

tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
"$DIRECT_CLI" --version
```

成功时会看到这些稳定内容：

```text
ai-sdlc-offline-1.0.2-macos-arm64.tar.gz: OK
当前结果 / Result
  离线安装完成。安装脚本已创建运行环境并安装 AI-SDLC。
  Offline installation completed. The installer created the runtime and installed AI-SDLC.
下一步 / Next
1.0.2
```

初始化空项目：

```bash
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

命令会停下来让你选择 AI 代理入口和 Shell。选择方法见本章 1.4。

如果当前 Terminal 已经关闭，重新打开后执行：

```bash
PROJECT_ROOT="$HOME/projects/my-new-project"
DIRECT_CLI="$HOME/Applications/AI-SDLC/ai-sdlc-offline-1.0.2-macos-arm64/.venv/bin/ai-sdlc"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

### 1.3 Linux（amd64）

以下命令在 bash 中执行。

复制并执行：

```bash
set -e
PROJECT_ROOT="$HOME/projects/my-new-project"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC"
DOWNLOAD_ROOT="$(mktemp -d)"
BUNDLE_NAME="ai-sdlc-offline-1.0.2-linux-amd64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-linux-amd64.tar.gz"
CHECKSUM_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-linux-amd64.tar.gz.sha256"

mkdir -p "$PROJECT_ROOT" "$INSTALL_ROOT"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$CHECKSUM_URL"
(cd "$DOWNLOAD_ROOT" && sha256sum -c "$PACKAGE_NAME.sha256")

tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
"$DIRECT_CLI" --version
```

成功时会看到这些稳定内容：

```text
ai-sdlc-offline-1.0.2-linux-amd64.tar.gz: OK
当前结果 / Result
  离线安装完成。安装脚本已创建运行环境并安装 AI-SDLC。
  Offline installation completed. The installer created the runtime and installed AI-SDLC.
下一步 / Next
1.0.2
```

初始化空项目：

```bash
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

命令会停下来让你选择 AI 代理入口和 Shell。选择方法见本章 1.4。

如果当前终端已经关闭，重新打开后执行：

```bash
PROJECT_ROOT="$HOME/projects/my-new-project"
DIRECT_CLI="$HOME/.local/share/AI-SDLC/ai-sdlc-offline-1.0.2-linux-amd64/.venv/bin/ai-sdlc"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
```

### 1.4 选择 AI 适配器和 Shell

初始化时会看到：

```text
请选择当前实际用于聊天开发的 AI 代理入口
```

选择标准只有一个：你准备把开发需求发给哪个 AI 工具，就选择哪个适配器。

| 选项 | 什么时候选择 |
| --- | --- |
| Claude Code | 实际在 Claude Code 中输入需求并让 AI 开发 |
| Codex | 实际使用 Codex App 或 Codex CLI 开发 |
| Cursor | 实际使用 Cursor Chat/Agent 开发 |
| VS Code | 实际使用 VS Code 的 AI/Copilot 对话入口开发 |
| 其他-通用 | 实际使用的 AI 工具不在以上四项中 |

不要按操作系统、模型名称或终端外面的编辑器窗口来选。例如：

- 在 VS Code 终端运行 Claude Code，应选择 Claude Code，不是 VS Code。
- 通过 Cursor Agent 提交需求，应选择 Cursor。

Windows 会显示编号菜单：

```text
1. Claude Code
2. Codex
3. Cursor
4. VS Code
5. 其他-通用
```

输入编号并回车；直接回车会接受当前标有“默认”的选项。macOS、Linux 使用上下方向键选择并按回车。默认项会因当前工具和项目文件不同而变化，先核对再确认。

接着选择当前项目默认使用的 Shell：

- Windows 通常选择 PowerShell。
- macOS Terminal 通常选择 zsh。
- Linux 通常选择 bash。
- 已经明确使用 cmd、其他 Shell 或希望自动判断时，选择实际选项。

### 1.5 判断初始化是否成功

成功输出会包含：

```text
AI 代理入口: 你选择的工具
Project shell: 你选择的 Shell
Initialized AI-SDLC project
当前结果 / Result
下一步 / Next
```

新空项目中出现 open gates 是正常的：它表示需求、设计、任务或测试证据还没有补齐，不表示初始化失败。只要命令成功结束，并且 `下一步 / Next` 要求进入 AI 对话，就可以继续。

用刚才选择的 AI 工具打开同一个项目目录：

- Windows：`$HOME\projects\my-new-project`
- macOS/Linux：`$HOME/projects/my-new-project`

然后把下面文字复制到该 AI 工具的对话入口：

```text
我准备在当前项目开发一个新功能。

目标：
使用者：
需要完成的功能：
明确不做的内容：
验收标准：

请先帮我补齐需求和验收标准，再进入技术方案与任务分解。
如果需求涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

## 第二章：全新用户 + 已有项目

先在已有项目根目录打开终端，然后选择你的操作系统，只执行对应小节。

`init` 和 `adopt` 会创建或维护 AI-SDLC 的项目文件；`adopt` 不会修改原任务文件。已有业务代码仍应由 Git 或你自己的备份流程保护。

### 2.1 Windows

在已有项目根目录的 PowerShell 中复制并执行：

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Location).Path
$InstallRoot = Join-Path $HOME "AI-SDLC"
$DownloadRoot = Join-Path $env:TEMP "ai-sdlc-v1.0.2-download"
$BundleName = "ai-sdlc-offline-1.0.2-windows-amd64"
$PackageName = "$BundleName.zip"
$PackageUrl = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-windows-amd64.zip"
$ChecksumUrl = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-windows-amd64.zip.sha256"

$GitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($GitCommand) {
  git status --short --branch
  if ($LASTEXITCODE -ne 0) {
    Write-Host "当前目录不是 Git 仓库；确认项目目录后继续安装。"
  }
} else {
  Write-Host "未检测到 Git；确认当前目录是目标项目根目录后继续安装。"
}
New-Item -ItemType Directory -Force -Path $InstallRoot, $DownloadRoot | Out-Null
$PackagePath = Join-Path $DownloadRoot $PackageName
$ChecksumPath = "$PackagePath.sha256"
Invoke-WebRequest -Uri $PackageUrl -OutFile $PackagePath
Invoke-WebRequest -Uri $ChecksumUrl -OutFile $ChecksumPath

$ChecksumParts = (Get-Content -LiteralPath $ChecksumPath -Raw).Trim() -split '\s+', 2
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackagePath).Hash.ToLowerInvariant()
if ($ChecksumParts.Count -ne 2 -or $ChecksumParts[1] -ne $PackageName -or $ChecksumParts[0].ToLowerInvariant() -ne $ActualHash) {
  throw "SHA256 verification failed for $PackageName"
}
Write-Host "SHA256 verified: $PackageName"

Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallRoot -Force
$BundleRoot = Join-Path $InstallRoot $BundleName
Push-Location $BundleRoot
try {
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\install_offline.ps1" -AddToPath
} finally {
  Pop-Location
}
$DirectCli = Join-Path $BundleRoot ".venv\Scripts\ai-sdlc.exe"
& $DirectCli --version
```

如果 `git status` 显示 `not a git repository`，AI-SDLC 仍可初始化；先确认当前目录确实是目标项目根目录。存在未提交改动时可以继续，但建议先确认这些改动属于你当前要保留的工作。

成功安装时会看到：

```text
SHA256 verified: ai-sdlc-offline-1.0.2-windows-amd64.zip
Offline installation completed. The installer created the runtime and installed AI-SDLC.
Direct shim:
1.0.2
```

安装器显示的 `Codex + PowerShell project init` 只是专用示例；仍然执行下面的通用命令，在菜单中选择自己的 AI 工具：

```powershell
Set-Location $ProjectRoot
& $DirectCli init .
```

完成适配器和 Shell 选择后，接入已有任务资料：

```powershell
& $DirectCli adopt .
```

如果终端已关闭，重新打开后执行：

```powershell
$ProjectRoot = (Get-Location).Path
$DirectCli = Join-Path $HOME "AI-SDLC\ai-sdlc-offline-1.0.2-windows-amd64\.venv\Scripts\ai-sdlc.exe"
Set-Location $ProjectRoot
& $DirectCli init .
& $DirectCli adopt .
```

### 2.2 macOS（Apple Silicon）

在已有项目根目录的 Terminal 中复制并执行：

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/Applications/AI-SDLC"
DOWNLOAD_ROOT="$(mktemp -d)"
BUNDLE_NAME="ai-sdlc-offline-1.0.2-macos-arm64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-macos-arm64.tar.gz"
CHECKSUM_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-macos-arm64.tar.gz.sha256"

if ! git status --short --branch; then
  printf '%s\n' "当前目录不是 Git 仓库；确认项目目录后继续安装。"
fi
mkdir -p "$INSTALL_ROOT"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$CHECKSUM_URL"
(cd "$DOWNLOAD_ROOT" && shasum -a 256 -c "$PACKAGE_NAME.sha256")

tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
"$DIRECT_CLI" --version
```

如果 `git status` 显示 `not a git repository`，AI-SDLC 仍可初始化；先确认当前目录确实是目标项目根目录。

成功安装时会看到：

```text
ai-sdlc-offline-1.0.2-macos-arm64.tar.gz: OK
Offline installation completed. The installer created the runtime and installed AI-SDLC.
1.0.2
```

初始化并接入已有项目：

```bash
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
"$DIRECT_CLI" adopt .
```

`init` 会先停下来等待适配器和 Shell 选择；确认后才会继续执行下一条 `adopt`。

如果 Terminal 已经关闭，重新打开后执行：

```bash
PROJECT_ROOT="$PWD"
DIRECT_CLI="$HOME/Applications/AI-SDLC/ai-sdlc-offline-1.0.2-macos-arm64/.venv/bin/ai-sdlc"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
"$DIRECT_CLI" adopt .
```

### 2.3 Linux（amd64）

在已有项目根目录的 bash 中复制并执行：

```bash
set -e
PROJECT_ROOT="$PWD"
INSTALL_ROOT="$HOME/.local/share/AI-SDLC"
DOWNLOAD_ROOT="$(mktemp -d)"
BUNDLE_NAME="ai-sdlc-offline-1.0.2-linux-amd64"
PACKAGE_NAME="$BUNDLE_NAME.tar.gz"
PACKAGE_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-linux-amd64.tar.gz"
CHECKSUM_URL="https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/ai-sdlc-offline-1.0.2-linux-amd64.tar.gz.sha256"

if ! git status --short --branch; then
  printf '%s\n' "当前目录不是 Git 仓库；确认项目目录后继续安装。"
fi
mkdir -p "$INSTALL_ROOT"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$CHECKSUM_URL"
(cd "$DOWNLOAD_ROOT" && sha256sum -c "$PACKAGE_NAME.sha256")

tar xzf "$DOWNLOAD_ROOT/$PACKAGE_NAME" -C "$INSTALL_ROOT"
BUNDLE_ROOT="$INSTALL_ROOT/$BUNDLE_NAME"
(cd "$BUNDLE_ROOT" && ./install_offline.sh --add-to-path)
DIRECT_CLI="$BUNDLE_ROOT/.venv/bin/ai-sdlc"
"$DIRECT_CLI" --version
```

如果 `git status` 显示 `not a git repository`，AI-SDLC 仍可初始化；先确认当前目录确实是目标项目根目录。

成功安装时会看到：

```text
ai-sdlc-offline-1.0.2-linux-amd64.tar.gz: OK
Offline installation completed. The installer created the runtime and installed AI-SDLC.
1.0.2
```

初始化并接入已有项目：

```bash
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
"$DIRECT_CLI" adopt .
```

`init` 会先停下来等待适配器和 Shell 选择；确认后才会继续执行下一条 `adopt`。

如果终端已经关闭，重新打开后执行：

```bash
PROJECT_ROOT="$PWD"
DIRECT_CLI="$HOME/.local/share/AI-SDLC/ai-sdlc-offline-1.0.2-linux-amd64/.venv/bin/ai-sdlc"
cd "$PROJECT_ROOT"
"$DIRECT_CLI" init .
"$DIRECT_CLI" adopt .
```

### 2.4 选择 AI 适配器和 Shell

`init` 会询问“请选择当前实际用于聊天开发的 AI 代理入口”。选择你实际提交开发需求的入口：

| 选项 | 什么时候选择 |
| --- | --- |
| Claude Code | 实际在 Claude Code 中输入需求并让 AI 开发 |
| Codex | 实际使用 Codex App 或 Codex CLI 开发 |
| Cursor | 实际使用 Cursor Chat/Agent 开发 |
| VS Code | 实际使用 VS Code 的 AI/Copilot 对话入口开发 |
| 其他-通用 | 实际使用的 AI 工具不在以上四项中 |

在 VS Code 终端运行 Claude Code 时选择 Claude Code；通过 Cursor Agent 提交需求时选择 Cursor。适配器不是按操作系统或 Shell 选择。

Windows 输入编号后回车；macOS、Linux 使用上下方向键和回车。自动检测只负责预选，默认项可能不同，确认前先核对。

Shell 选择当前项目实际使用的命令语法：

- Windows 通常选择 PowerShell。
- macOS Terminal 通常选择 zsh。
- Linux 通常选择 bash。
- 项目明确使用 cmd、其他 Shell 或自动判断时，选择对应选项。

### 2.5 判断 `init` 和 `adopt` 是否成功

已有项目初始化会先显示：

```text
Detected existing project — running deep scan...
```

成功结束时会包含：

```text
Initialized AI-SDLC project
当前结果 / Result
下一步 / Next
```

open gates 表示项目还缺少需求、设计、任务或验证证据，不等于初始化失败。按照 `下一步 / Next` 进入所选 AI 工具即可。

`adopt` 成功时会包含：

```text
接入已有项目：已生成桥接结果
原任务文件不会被修改。
接入已有项目
推荐继续点
已识别来源
已识别任务
```

根据结果选择下一步：

1. 推荐继续点正确：直接把本章 2.6 的需求模板发给所选 AI 工具。
2. 推荐继续点不正确：按自己的关键词重新执行，例如：

   ```powershell
   ai-sdlc adopt . --prefer "支付回调"
   ```

   当前终端找不到裸命令时，Windows 使用 `& $DirectCli adopt . --prefer "支付回调"`，macOS/Linux 使用 `"$DIRECT_CLI" adopt . --prefer "支付回调"`。
3. `已识别任务` 为 0，或者项目没有任务资料：不需要反复执行 `adopt`；直接在所选 AI 工具中说明当前目标、范围和验收标准。

### 2.6 把增量需求交给所选 AI 工具

使用刚才选择的 AI 工具打开同一个已有项目目录，然后复制：

```text
请先读取当前项目的代码、文档和 AI-SDLC 规则，再处理下面的增量需求。

本次目标：
必须保留的现有行为：
允许修改的范围：
明确不能修改的范围：
验收标准：

请先确认你对现状和需求的理解，指出缺失信息，再给出实施方案。
如果需求涉及页面、组件或浏览器交互，请先给出默认技术栈建议和高级可选方案，等我确认后再实现。
```

## 异常情况速查

### 下载失败、超时或返回 404

确认下载地址中包含完整版本和资产名：

```text
https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v1.0.2/
```

然后重新执行所在平台的两条下载命令。不要把地址改成分支地址。

### 出现 `SHA256 verification failed` 或校验不是 `OK`

不要解压或安装该文件。删除本次下载目录中的压缩包和同名 `.sha256`，重新下载后再次校验。摘要仍不一致时停止使用该文件。

Windows：

```powershell
Remove-Item -LiteralPath $PackagePath, $ChecksumPath -Force -ErrorAction SilentlyContinue
Invoke-WebRequest -Uri $PackageUrl -OutFile $PackagePath
Invoke-WebRequest -Uri $ChecksumUrl -OutFile $ChecksumPath
```

macOS/Linux：

```bash
rm -f "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME" "$PACKAGE_URL"
curl --fail --location --retry 3 --output "$DOWNLOAD_ROOT/$PACKAGE_NAME.sha256" "$CHECKSUM_URL"
```

重新执行对应平台的校验命令。

### PowerShell 阻止执行安装脚本

使用手册给出的进程级绕过命令，不需要修改整台电脑的永久执行策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $BundleRoot "install_offline.ps1") -AddToPath
```

### 当前终端提示找不到 `ai-sdlc`

安装脚本写入 PATH 后，当前窗口可能还没有刷新。成功路径直接使用包内命令，不依赖 PATH：

Windows：

```powershell
& $DirectCli --version
```

macOS/Linux：

```bash
"$DIRECT_CLI" --version
```

输出应为 `1.0.2`。

### 出现 `No module named ai_sdlc`

这通常表示执行了系统 Python，而不是离线包自带运行环境。不要另外拼装 pip 命令；回到本章对应小节，重新设置 `$DirectCli` 或 `DIRECT_CLI`，再使用包内命令。

### 移动或删除了安装目录

重新执行对应平台的下载、校验、解压和安装步骤，并恢复到手册中的长期安装位置。仅重新写 PATH 不能恢复已经删除的运行环境。

### 在 `init` 中选错 AI 适配器

进入项目根目录，根据实际工具复制对应命令：

```powershell
ai-sdlc adapter select --agent-target claude_code
ai-sdlc adapter select --agent-target codex
ai-sdlc adapter select --agent-target cursor
ai-sdlc adapter select --agent-target vscode
ai-sdlc adapter select --agent-target generic
```

只执行其中一条。裸命令不可用时，把开头的 `ai-sdlc` 换为本章保存的包内 Direct CLI 调用方式。

### 在 `init` 中选错 Shell

进入项目根目录，根据实际 Shell 只执行其中一条：

```powershell
ai-sdlc adapter shell-select --shell powershell
ai-sdlc adapter shell-select --shell bash
ai-sdlc adapter shell-select --shell zsh
ai-sdlc adapter shell-select --shell cmd
ai-sdlc adapter shell-select --shell auto
```

### `init` 显示 open gates

先读 `当前结果 / Result` 和 `下一步 / Next`。如果下一步要求切换到 AI 对话、补充需求或证据，这属于正常初始化结果。普通成功路径不需要额外执行 `adapter status` 或 `run --dry-run`。

### 所选 AI 工具没有按项目规则工作

先确认该 AI 工具打开的是刚刚执行 `init` 的同一目录，并确认适配器选择正确。只有 CLI 明确要求进一步诊断时，再在项目根目录执行：

```powershell
ai-sdlc adapter status --details
```

### `adopt` 提示尚未初始化

回到项目根目录先执行：

```powershell
ai-sdlc init .
```

完成适配器和 Shell 选择后，再执行：

```powershell
ai-sdlc adopt .
```
