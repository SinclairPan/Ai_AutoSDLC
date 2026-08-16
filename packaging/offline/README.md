# AI-SDLC 2.0.0 离线打包说明

离线打包用于在可联网构建机准备完整制品，再交付到无法访问软件源的 Windows、macOS 或 Linux 环境。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

当前源码与公开稳定版本均为 `2.0.0` / `v2.0.0`。下列命令既用于发布前候选验证，也用于复验正式制品。

## 包内容

每个离线包包含：

- AI-SDLC 2.0.0 wheel；
- 运行依赖 wheelhouse；
- `install_offline.ps1`、`install_offline.bat`、`install_offline.sh`；
- `bundle-manifest.json`；
- 包内 `SHA256SUMS` 完整性清单；
- 压缩包同名 `.sha256` 校验文件；
- 可选的便携 Python 运行时。

平台制品名称：

- `ai-sdlc-offline-2.0.0-windows-amd64.zip`
- `ai-sdlc-offline-2.0.0-macos-arm64.tar.gz`
- `ai-sdlc-offline-2.0.0-linux-amd64.tar.gz`

## 通用构建

公开稳定源码固定为已发布的 `v2.0.0`：

```bash
git clone --branch v2.0.0 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git
cd Ai_AutoSDLC
```

2.0.0 候选构建只能在已审查 PR 的当前工作树根目录执行：

```bash
uv sync
bash packaging/offline/build_offline_bundle.sh
```

脚本从 `pyproject.toml` 读取版本，产物写入 `dist-offline/`。

## 平台构建参数

### Windows AMD64

建议在 `windows-latest` 或等价 Windows AMD64 构建机执行：

```bash
PYTHON="$RUNTIME_PYTHON" \
AI_SDLC_OFFLINE_PYTHON_RUNTIME="$RUNTIME_ROOT" \
AI_SDLC_OFFLINE_PYTHON_VERSIONS="3.11,3.12" \
AI_SDLC_OFFLINE_TARGET_PLATFORM="win_amd64" \
AI_SDLC_OFFLINE_ASSET_SUFFIX="-windows-amd64" \
bash packaging/offline/build_offline_bundle.sh
```

### macOS ARM64

```bash
PYTHON="$RUNTIME_PYTHON" \
AI_SDLC_OFFLINE_PYTHON_RUNTIME="$RUNTIME_ROOT" \
AI_SDLC_OFFLINE_ASSET_SUFFIX="-macos-arm64" \
bash packaging/offline/build_offline_bundle.sh
```

### Linux AMD64

```bash
PYTHON="$RUNTIME_PYTHON" \
AI_SDLC_OFFLINE_PYTHON_RUNTIME="$RUNTIME_ROOT" \
AI_SDLC_OFFLINE_ASSET_SUFFIX="-linux-amd64" \
bash packaging/offline/build_offline_bundle.sh
```

`AI_SDLC_OFFLINE_PYTHON_RUNTIME` 必须指向可复制、可执行并包含 `venv` 的 Python 3.11+ 运行时目录。

## 完整性验证

先使用压缩包旁的同名 `.sha256` 文件校验下载结果，再解压制品。解压后执行：

```powershell
python packaging/offline/verify_offline_bundle.py <bundle-dir> --require-bundled-runtime --require-checksums --expected-package-version 2.0.0 --archive-checksum <archive> <archive>.sha256
```

安装 smoke 后补充安装日志：

```powershell
python packaging/offline/verify_offline_bundle.py <bundle-dir> --require-bundled-runtime --expected-package-version 2.0.0 --archive-checksum <archive> <archive>.sha256 --install-log <install-log>
```

安装前命令会检查 tag 对应版本、目录名、manifest、wheel、包内文件摘要、压缩包摘要、Python 运行时、平台一致性和逃逸符号链接。安装会新增 `.venv/`，因此安装后命令不再要求原始文件集合完全相等，只复验版本、归档摘要、运行时和安装回执。

## Windows 安装 smoke

```powershell
$Bundle = "ai-sdlc-offline-2.0.0-windows-amd64"
Set-Location $Bundle
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_offline.ps1 -AddToPath
.\.venv\Scripts\ai-sdlc.exe --version
.\.venv\Scripts\ai-sdlc.exe --help
```

创建临时项目并验证 Codex：

```powershell
New-Item -ItemType Directory -Path smoke-project -Force | Out-Null
Set-Location smoke-project
..\.venv\Scripts\ai-sdlc.exe init . --agent-target codex --shell powershell
..\.venv\Scripts\ai-sdlc.exe adapter status
..\.venv\Scripts\ai-sdlc.exe run --dry-run
```

## macOS / Linux 安装 smoke

```bash
cd ai-sdlc-offline-2.0.0-<platform>
./install_offline.sh --add-to-path
./.venv/bin/ai-sdlc --version
./.venv/bin/ai-sdlc --help
```

```bash
mkdir -p smoke-project
cd smoke-project
../.venv/bin/ai-sdlc init . --agent-target codex --shell powershell
../.venv/bin/ai-sdlc adapter status
../.venv/bin/ai-sdlc run --dry-run
```

## GitHub Actions

- `.github/workflows/release-build.yml`：按平台构建、安装 smoke 并上传制品；
- `.github/workflows/release-artifact-smoke.yml`：下载正式制品并执行安装 smoke；
- `.github/workflows/windows-offline-smoke.yml`：验证 Windows 构建、安装、Codex 初始化和 dry-run；
- `.github/workflows/posix-offline-smoke.yml`：验证 macOS 与 Linux 安装路径。

工作流默认验证 `v2.0.0`；只有普通跨平台 smoke 和发布检查全部通过后才上传 Draft Release，发布后还必须重复制品 smoke。

## 交付要求

- 包版本、目录名、manifest 和 wheel 版本均为 `2.0.0`；
- 使用目标操作系统和 CPU 架构完成 smoke；
- `--version`、`--help`、Codex 初始化、adapter status 与 dry-run 均成功；
- 完整性验证通过；
- 日志与制品中不包含凭据；
- 检查清单全部完成。

详细清单见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。
