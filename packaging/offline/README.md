# AI-SDLC 1.0.0 离线打包说明

离线打包用于在可联网构建机准备完整制品，再交付到无法访问软件源的 Windows、macOS 或 Linux 环境。

项目地址：<https://github.com/SinclairPan/Ai_AutoSDLC>

## 包内容

每个离线包包含：

- AI-SDLC 1.0.0 wheel；
- 运行依赖 wheelhouse；
- `install_offline.ps1`、`install_offline.bat`、`install_offline.sh`；
- `bundle-manifest.json`；
- SHA256 完整性校验；
- 可选的便携 Python 运行时。

平台制品名称：

- `ai-sdlc-offline-1.0.0-windows-amd64.zip`
- `ai-sdlc-offline-1.0.0-macos-arm64.tar.gz`
- `ai-sdlc-offline-1.0.0-linux-amd64.tar.gz`

## 通用构建

在仓库根目录执行：

```bash
git clone https://github.com/SinclairPan/Ai_AutoSDLC.git
cd Ai_AutoSDLC
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

先解压制品，再执行：

```powershell
python packaging/offline/verify_offline_bundle.py <bundle-dir> --require-bundled-runtime
```

安装 smoke 后补充安装日志：

```powershell
python packaging/offline/verify_offline_bundle.py <bundle-dir> --require-bundled-runtime --install-log <install-log>
```

验证器会检查 manifest、wheel、Python 运行时、平台一致性、逃逸符号链接和安装回执。

## Windows 安装 smoke

```powershell
$Bundle = "ai-sdlc-offline-1.0.0-windows-amd64"
Set-Location $Bundle
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_offline.ps1 -AddToPath
.\.venv\Scripts\ai-sdlc.exe --version
.\.venv\Scripts\ai-sdlc.exe --help
```

创建临时项目并验证 Codex：

```powershell
New-Item -ItemType Directory -Path smoke-project -Force | Out-Null
Set-Location smoke-project
..\ai-sdlc-offline-1.0.0-windows-amd64\.venv\Scripts\ai-sdlc.exe init . --agent-target codex --shell powershell
..\ai-sdlc-offline-1.0.0-windows-amd64\.venv\Scripts\ai-sdlc.exe adapter status
..\ai-sdlc-offline-1.0.0-windows-amd64\.venv\Scripts\ai-sdlc.exe run --dry-run
```

## macOS / Linux 安装 smoke

```bash
cd ai-sdlc-offline-1.0.0-<platform>
./install_offline.sh --add-to-path
./.venv/bin/ai-sdlc --version
./.venv/bin/ai-sdlc --help
```

```bash
mkdir -p smoke-project
cd smoke-project
../ai-sdlc-offline-1.0.0-<platform>/.venv/bin/ai-sdlc init . --agent-target codex --shell powershell
../ai-sdlc-offline-1.0.0-<platform>/.venv/bin/ai-sdlc adapter status
../ai-sdlc-offline-1.0.0-<platform>/.venv/bin/ai-sdlc run --dry-run
```

## GitHub Actions

- `.github/workflows/release-build.yml`：按平台构建、安装 smoke 并上传制品；
- `.github/workflows/release-artifact-smoke.yml`：下载正式制品并执行安装 smoke；
- `.github/workflows/windows-offline-smoke.yml`：验证 Windows 构建、安装、Codex 初始化和 dry-run；
- `.github/workflows/posix-offline-smoke.yml`：验证 macOS 与 Linux 安装路径。

工作流默认发布标识为 `v1.0.0`。上传动作必须由有权限的维护者明确触发。

## 交付要求

- 包版本、目录名、manifest 和 wheel 版本均为 `1.0.0`；
- 使用目标操作系统和 CPU 架构完成 smoke；
- `--version`、`--help`、Codex 初始化、adapter status 与 dry-run 均成功；
- 完整性验证通过；
- 日志与制品中不包含凭据；
- 检查清单全部完成。

详细清单见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。
