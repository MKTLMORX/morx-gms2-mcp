# 贡献指南

欢迎提交问题和改进。报告时注明操作系统、Python、MCP SDK、IDE、Runtime 版本，以及脱敏后的最小复现；不要上传完整私有游戏或事务备份。

资源描述由 GameMaker 生成的最小空白样例核对。扩展格式时保留已有字段、尾逗号兼容性和原生磁盘结构，避免依赖某个个人游戏。新操作应包含预览、实际执行、重复/无效输入测试及必要的撤销验证；涉及协议时补充真实 stdio 测试。

运行相关测试使用假的构建进程和文件桥接，不会真正编译 GML。修改 `runtime.py`、`runtime_bridge.py` 或桥接安装逻辑后，还应在独立项目上用实际 Igor 编译运行一次，记录 Runtime 版本、构建日志、截图和清理结果。注意 `Windows Run` 不应传 `/of`；Igor 会在运行目录生成输出，传 `/of` 会导致 Runner 加载错误文件。没有实际打开 IDE 时，不要把命令行构建写成 IDE 图形界面验收。

在仓库根目录执行测试及构建：

```powershell
python -m pip install . build
python scripts/check_public_files.py
python -m unittest discover -s tests -t . -v
python -m build
python scripts/build_release.py
```

wheel、sdist 和按白名单生成的公开源码 ZIP 位于 `dist/`。涉及新资源格式时另在独立项目中打开、保存、编译和运行，记录实际版本及可观察结果。勿把 JSON 检查通过写成 IDE 验收通过。

`examples/create_demo.py --project <已绑定的空测试项目>` 可通过真实 MCP 创建验收场景，只应对独立测试项目运行；重复运行会拒绝覆盖。

提交前运行 `python scripts/check_public_files.py` 并人工查看暂存差异。使用 GitHub noreply 提交邮箱；不要提交本机路径、密钥、个人配置、日志、完整游戏或实验备份。CI 同时检查已跟踪文件与提交历史，`.gitignore` 不会自动移除已经跟踪的内容。

代码使用英文标识符，注释和说明优先中文。修改保持聚焦；可复用模块分为 CLI、MCP 接口、资源操作、格式、存储事务与校验。贡献须有权以本项目 MIT 许可证提供。

## 发布

版本号同时写在 `pyproject.toml` 和 `morx_gms2_mcp/__init__.py`，发布前更新 `CHANGELOG.md` 和 README 顶部的静态 PyPI 版本徽章，然后运行测试、`python -m build` 和 `python scripts/build_release.py`。静态徽章不依赖图片代理的实时查询，可以避免新版本刚发布时显示 not found。

PyPI 发布使用 GitHub Actions 的 OpenID Connect（Trusted Publishing），不需要保存 API token。首次发布前，在 PyPI 的 **Publishing** 页面添加一个 pending publisher：

| 字段 | 值 |
| --- | --- |
| PyPI Project Name | `morx-gms2-mcp` |
| Owner | `MKTLMORX` |
| Repository name | `morx-gms2-mcp` |
| Workflow name | `release.yml` |
| Environment name | 留空 |

配置完成后推送版本标签，例如 `v0.5.0`。`.github/workflows/release.yml` 会构建 wheel/sdist，通过 OIDC 发布到 PyPI，并创建同名 GitHub Release。也可以在 GitHub Actions 页面手动运行 `release` 工作流；手动运行只发布 PyPI，不创建 Release。工作流只申请 `id-token: write`，不读取或保存 PyPI 凭据。标签必须与包版本一致（`v0.5.0` 对应 `0.5.0`），否则构建任务会失败。
