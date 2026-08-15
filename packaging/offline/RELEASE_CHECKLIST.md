# AI-SDLC 1.0.5 离线发布检查清单

当前仓库源码版本为 `1.0.5`，公开稳定版本仍为 `v1.0.2`。以下条目用于普通候选构建与发布前验证。

## 版本与源码

- [ ] `pyproject.toml` 为 `1.0.5`；
- [ ] 两个 `ai_sdlc/__init__.py` 回退版本均为 `1.0.5`；
- [ ] `uv.lock` 中本项目版本为 `1.0.5`；
- [ ] Git 地址为 `https://github.com/SinclairPan/Ai_AutoSDLC`；
- [ ] 工作树只包含本次授权变更。

## 质量门禁

- [ ] `uv run pytest -q` 通过；
- [ ] `uv run ruff check src tests scripts` 通过；
- [ ] `uv run ai-sdlc verify constraints` 通过；
- [ ] `uv run python scripts/validate_public_release_identity.py .` 通过；
- [ ] `uv build` 通过。

## 制品构建

- [ ] Windows AMD64 zip 已生成；
- [ ] macOS ARM64 tar.gz 已生成；
- [ ] Linux AMD64 tar.gz 已生成；
- [ ] 每个制品包含 AI-SDLC wheel 与完整依赖 wheelhouse；
- [ ] 每个制品包含安装脚本和 `bundle-manifest.json`；
- [ ] 每个制品包含 `SHA256SUMS`，正式压缩包带同名 `.sha256` 文件；
- [ ] 每个制品包含可执行的 Python 3.11+ 运行时；
- [ ] 制品名称、目录名、manifest 与 wheel 版本一致。

## 完整性验证

- [ ] `verify_offline_bundle.py` 通过；
- [ ] 包内 `SHA256SUMS` 与压缩包 `.sha256` 均校验通过；
- [ ] 无逃逸符号链接；
- [ ] 运行时平台与制品平台一致；
- [ ] 安装日志被验证器接受。

## 平台 smoke

- [ ] Windows 解压与 `install_offline.ps1 -AddToPath` 成功；
- [ ] macOS 解压与 `install_offline.sh --add-to-path` 成功；
- [ ] Linux 解压与 `install_offline.sh --add-to-path` 成功；
- [ ] 三个平台 `ai-sdlc --version` 输出 `1.0.5`；
- [ ] 三个平台 `ai-sdlc --help` 成功；
- [ ] Codex + PowerShell 初始化成功；
- [ ] `ai-sdlc adapter status` 成功；
- [ ] `ai-sdlc run --dry-run` 产生明确 Result 与 Next。

## 发布与复验

- [ ] README、用户指南和打包说明中的包名一致；
- [ ] GitHub Actions 默认候选标识为 `v1.0.5`，且 PR1 的三个发布开关均为字符串 `false`；
- [ ] 平台工作流 artifact 完整；
- [x] v1.0.4 上传与发布动作保持禁止，未补传、替换或复用制品；
- [ ] 从全新目录安装正式制品并重复 smoke；
- [ ] 日志、制品和仓库不包含令牌或本地绝对路径。
