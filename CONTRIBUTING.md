# 贡献指南

欢迎提交问题、功能建议和代码改动。提交前请先搜索现有 Issues；涉及较大改动时，建议先开 Issue 说明目标和范围。

## 报告问题

请在 Issue 中提供：

- 操作系统、Python、MCP SDK、GameMaker IDE 和 Runtime 版本；
- 使用的 MCP 客户端、安装方式和启动参数；
- 最小复现步骤、预期结果与实际结果；
- 相关日志或错误信息，并先移除凭据、私有素材和完整游戏内容。

运行相关问题请注明验证方式：自动化测试、命令行 Igor 构建、IDE 图形界面操作或游戏内观察。

## 开发与测试

需要 Windows、Python 3.10+ 和 Git。建议在独立虚拟环境中开发：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install . build
```

在仓库根目录执行：

```powershell
python scripts/check_public_files.py
python -m unittest discover -s tests -t . -v
python -m build
python scripts/build_release.py
```

wheel、sdist 和按白名单生成的公开源码 ZIP 会写入 `dist/`。

新增或修改工具时，测试至少覆盖预览、实际执行、重复输入和无效输入；涉及撤销的操作应验证备份恢复；涉及协议时补充真实 stdio 测试。资源描述以 GameMaker 生成的最小空白样例为基准，扩展格式时保留已有字段、尾逗号兼容性和原生磁盘结构，不要依赖某个个人项目。

运行相关测试使用假的构建进程和文件桥接，不会真正编译 GML。修改 `runtime.py`、`runtime_bridge.py` 或桥接安装逻辑后，还应在独立测试项目上用实际 Igor 构建运行，记录 Runtime 版本、构建日志、截图和清理结果。`Windows Run` 不应传 `/of`：Igor 会在运行目录生成输出，传参会令 Runner 加载错误的文件。

`examples/create_demo.py --project <已绑定的空测试项目>` 可通过真实 MCP 创建验收场景。它只应对独立测试项目运行，重复运行会拒绝覆盖。

## 代码约定

- 代码标识符使用英文，注释和文档优先中文。
- 修改保持聚焦；提交前查看暂存差异，避免混入无关格式化或重命名。
- 可复用逻辑按 CLI、MCP 接口、资源操作、格式解析、存储事务和校验分层。
- 新依赖需说明用途，并保持 Python 3.10+ 兼容。
- 提交的改动须为原创，或你有权按本项目 MIT 许可证提供。

## 提交与隐私

提交前运行 `python scripts/check_public_files.py`，并人工检查暂存内容。不要提交本机路径、密钥、个人配置、日志、完整游戏或实验备份；备份和运行产物可能包含绝对路径与项目内容，应按源码同等级别管理。

请使用 GitHub noreply 邮箱提交。CI 会检查已跟踪文件和提交历史，`.gitignore` 不会移除已经进入历史的文件。

## 发布（维护者）

版本号同时写在 `pyproject.toml` 和 `morx_gms2_mcp/__init__.py`。发布前更新 `CHANGELOG.md` 和 README 顶部的静态 PyPI 版本徽章，然后运行测试、`python -m build` 和 `python scripts/build_release.py`。静态徽章不依赖图片代理的实时查询，可避免新版本刚发布时显示 not found。

PyPI 发布使用 GitHub Actions 的 OpenID Connect（Trusted Publishing），不需要保存 API token。首次发布前，在 PyPI 的 **Publishing** 页面添加 pending publisher：

| 字段 | 值 |
| --- | --- |
| PyPI Project Name | `morx-gms2-mcp` |
| Owner | `MKTLMORX` |
| Repository name | `morx-gms2-mcp` |
| Workflow name | `release.yml` |
| Environment name | 留空 |

配置完成后推送与包版本一致的标签，例如 `v0.5.0`。`.github/workflows/release.yml` 会构建 wheel 和 sdist，通过 OIDC 发布到 PyPI，并创建同名 GitHub Release。也可以在 Actions 页面手动运行 `release` 工作流；手动运行只发布 PyPI，不创建 Release。工作流只申请 `id-token: write`，不读取或保存 PyPI 凭据。标签与包版本不一致时，构建任务会失败。
