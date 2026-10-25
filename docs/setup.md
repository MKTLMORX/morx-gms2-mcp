# 安装与客户端配置

[返回首页](../README.md) · [工具与用法](reference.md)

需要 Windows、Python 3.10+ 和支持本地 stdio 的 MCP 客户端。项目须先由 GameMaker 创建并保存，目录内恰有一个现代 `GMProject v1` 的 `.yyp`。

## 安装

首页提供 venv 安装步骤，不需要激活环境或把命令加入 PATH。后续命令使用同一 PowerShell 会话中的 `$mcpPython` 和 `$gameProject`；重新打开终端后，先设置它们：

```powershell
$mcpPython = (Resolve-Path 'D:\Tools\gms2-mcp\.venv\Scripts\python.exe').Path
$gameProject = 'D:\Games\MyGame'
& $mcpPython -m morx_gms2_mcp --version
```

请将示例路径替换为自己的安装目录和项目路径。生成的客户端配置记录 Python 的绝对路径，移动或删除 venv 后需要重新生成配置。

### pipx

已安装 [pipx](https://pipx.pypa.io/) 并配置好其命令路径时，也可以使用独立命令：

```powershell
pipx install morx-gms2-mcp
morx-gms2-mcp --version
morx-gms2-mcp bind --project 'D:\Games\MyGame'
morx-gms2-mcp check --project 'D:\Games\MyGame'
```

后文的 `& $mcpPython -m morx_gms2_mcp` 可换为 `morx-gms2-mcp`。

### 从源码安装

```powershell
git clone https://github.com/MKTLMORX/morx-gms2-mcp.git
cd morx-gms2-mcp
python -m venv .venv
$mcpPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $mcpPython -m pip install .
```

依赖版本见 [pyproject.toml](../pyproject.toml)，开发与测试见[贡献指南](../CONTRIBUTING.md)。

## 客户端配置

```powershell
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client codex --write
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client claude --write
```

选择对应命令，将输出合并到下表文件，保留已有服务后重新加载客户端。生成配置不会自动修改这些文件。

| 客户端 | 游戏项目中的配置文件 | 示例 |
| --- | --- | --- |
| Codex | `.codex/config.toml` | [TOML](../examples/codex.config.toml) |
| Claude Code | `.mcp.json` | [JSON](../examples/claude.mcp.json) |

`--write` 允许显式 `dry_run=false` 的资源变更；省略时只能查询和预览。运行权限由 `--allow-run` 单独控制。Claude Code 可能要求确认项目信任。

连接后调用 `server_info` 核对项目、版本和权限。一个服务绑定一个项目；多个项目分别配置服务。迁移项目后需重新绑定，绑定文件位于 `.codex/gms2-resource-binding.json`，已有不同绑定不会自动覆盖。

生成配置中的工具超时为 180 秒，Claude Code 的 `timeout` 单位为毫秒。较早版本若不支持该字段，可在启动客户端前设置 `MCP_TOOL_TIMEOUT=180000`。

### Claude Code 命令行注册

在游戏项目目录中执行，可使用本地作用域注册：

```powershell
claude mcp add --scope local --transport stdio morx-gms2-mcp -- "D:\Tools\gms2-mcp\.venv\Scripts\python.exe" -m morx_gms2_mcp serve --project "D:\Games\MyGame" --write
claude mcp get morx-gms2-mcp
```

若 PowerShell 的 `claude.ps1` 包装器未正确传递参数，可调用 `claude.exe`，或使用上面的 JSON 配置。

## 启用编译与运行

```powershell
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client codex --write `
  --allow-run --runtime-root '<Runtime 目录>' --user-directory '<已登录用户目录>'
```

将 `codex` 换为 `claude` 可生成 Claude Code 配置。合并输出并重新加载客户端。

| 参数 | 要求 |
| --- | --- |
| `--runtime-root` | 已安装的 Windows x64 GameMaker Runtime 根目录 |
| `--user-directory` | 已登录 GameMaker 的用户目录，其下直接包含 `licence.plist` |
| `--allow-run` | 允许以当前用户权限编译、运行受信任项目 |
| `--write` | 安装测试桥接、修改 GML 和资源时需要；不代替运行权限 |

服务仅检查许可证文件是否存在，不读取或复制其内容。运行前需安装项目桥接，启动房间须为非继承 `GMRoom v1`；只编译不要求桥接。详见[运行与观察](reference.md#运行与观察)。

## 升级

在原 venv 中执行：

```powershell
& $mcpPython -m pip install --upgrade morx-gms2-mcp
& $mcpPython -m morx_gms2_mcp --version
```

pipx 用户执行 `pipx upgrade morx-gms2-mcp`。升级后重新连接客户端，通过 `server_info` 核对版本；若 `runtime_bridge.compatible=false`，先预览再显式执行 `install_runtime_bridge` 同步项目桥接。服务不会自动修改桥接，只编译不要求同步。

## 连接排查

| 现象 | 检查 |
| --- | --- |
| 命令不存在 | venv 用户使用 Python 绝对路径；pipx 用户检查其命令目录是否在 PATH 中 |
| 工具未加载或版本未更新 | 核对配置中的 Python 路径，检查配置格式并重连客户端 |
| 拒绝绑定 | 项目目录中应恰有一个受支持的 `.yyp`，且不能为符号链接、目录联接或受保护目录 |
| 拒绝写入 | 确认服务带 `--write`，执行参数为 `dry_run=false`，并重新查询最新哈希 |
| 运行未就绪 | 查看 `runtime_status` 与 `get_runtime_output`，区分编译失败、桥接不兼容和等待心跳；不要把进程启动当作编译成功 |

仍有问题时，请按[贡献指南](../CONTRIBUTING.md#报告问题)提供版本与最小复现步骤。
