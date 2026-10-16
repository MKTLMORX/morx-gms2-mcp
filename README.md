# MORX GMS2 MCP

[![CI](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**让 AI 助手为 GameMaker 创建资源，并把每一次修改保持在可预览、可核对、可撤销的流程中。**

Local MCP tools for GameMaker Studio 2 resource creation and management, with dry-run previews, backups and rollback. Works with Codex and Claude Code.

[快速开始](#快速开始) · [连接客户端](#连接客户端) · [使用示例](#使用示例) · [工具清单](#工具清单) · [兼容与限制](#兼容与限制) · [English](#english-quick-start)

## 能做什么

| 资源与操作 | 能力 |
| --- | --- |
| 脚本与物体 | 创建脚本、物体和常用事件，配置精灵引用、父物体及常驻状态 |
| 房间与实例 | 创建房间和实例图层，放置物体，设置启动房间 |
| 美术与音频 | 导入多帧 PNG、PCM WAV，创建 GLSL ES 着色器 |
| 项目结构 | 创建多级中文分组，移动虚拟分组，校验资源引用 |
| 修改与恢复 | 默认预览、固定项目绑定、冲突检测、事务备份与撤销 |

**18 个工具 · 本地 stdio · 默认只读 · MIT**

项目展示名为 **MORX GMS2 MCP**，仓库、安装包及命令名为 `morx-gms2-mcp`，Python 模块为 `morx_gms2_mcp`。这是独立社区项目；GMS2 名称不代表所有历史版本均已兼容，实际验收范围见下文。

## 快速开始

以下为 Windows PowerShell 示例。准备 Python 3.10+，以及由 GameMaker 创建并保存的独立测试项目。

**1. 安装到独立环境**

```powershell
git clone https://github.com/MKTLMORX/morx-gms2-mcp.git
cd morx-gms2-mcp
python -m venv .venv
$mcpPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $mcpPython -m pip install .
& $mcpPython -m morx_gms2_mcp --version
```

也可安装自行构建的 wheel。**尚未发布到 PyPI**；不要假定同名线上包来自本项目。依赖范围见 [pyproject.toml](pyproject.toml)。

**2. 绑定并检查项目**

```powershell
$gameProject = 'D:\Games\MyGame' # 替换为你的测试项目
& $mcpPython -m morx_gms2_mcp bind --project $gameProject
& $mcpPython -m morx_gms2_mcp check --project $gameProject
```

`--project` 也接受 `.yyp` 文件路径。目录须恰有一个现代 `GMProject v1` 的 `.yyp`；服务不创建空项目。

## 连接客户端

**3. 生成对应客户端的配置**

```powershell
# 按你使用的客户端选择一条
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client codex --write
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client claude --write
```

| 客户端 | 将生成内容合并到 | 示例 |
| --- | --- | --- |
| Codex | 游戏项目的 `.codex/config.toml` | [TOML 示例](examples/codex.config.toml) |
| Claude Code | 游戏项目的 `.mcp.json` | [JSON 示例](examples/claude.mcp.json) |

`client-config` 只打印配置，不修改客户端；保留已有的其他服务。Claude Code 对项目配置可能要求信任确认。去掉 `--write` 可保持服务只读，允许预览但拒绝实际修改。

<details>
<summary>Claude Code 本地作用域注册与项目迁移说明</summary>

也可以在游戏项目目录使用 Claude Code 的本地作用域注册：

```powershell
claude mcp add --scope local --transport stdio morx-gms2-mcp -- "D:\Tools\gms2-mcp\.venv\Scripts\python.exe" -m morx_gms2_mcp serve --project "D:\Games\MyGame" --write
claude mcp get morx-gms2-mcp
```

若 Windows PowerShell 的 `claude.ps1` 包装器吞掉 `--`，请直接调用 Claude Code 的原生 `claude.exe`，或使用生成的 JSON 配置。

绑定文件位于项目 `.codex/gms2-resource-binding.json`，记录项目绝对路径；迁移项目后需人工核对并重新绑定，不要直接复制旧绑定。名为 `参考项目` 的目录及其子目录禁止绑定。

早期内部版本曾名为 `gms2-resource-mcp`；首次公开版本统一采用当前名称。项目中 `.codex/gms2-resource-binding.json`、锁和备份目录保留原有文件名，以兼容已创建的绑定与事务，不需要迁移或删除备份。

</details>

重新加载客户端后，先调用 `server_info` 核对目标。一个服务绑定一个项目，工具调用不能切换根目录。服务不需要 GameMaker 正在运行。

## 使用示例

连接后，可以向 AI 助手提出这样的请求：

> 检查当前绑定项目，并列出资源引用错误，先不要修改。

> 在“脚本/game”分组创建 scr_demo_init，先给我看修改预览。

> 将我提供的两张等尺寸 PNG 导入为 spr_demo，设置播放速度和原点。

> 列出备份，预览撤销最近一次资源创建会影响哪些文件。

## 使用流程与写入保护

1. 保存 IDE 中尚未落盘的内容，调用 `server_info` 和 `inspect_project`。
2. 调用修改工具，默认 `dry_run=true`，查看拟改文件。
3. 确认目标和请求后以相同参数及 `dry_run=false` 执行；服务还必须带 `--write` 启动。没有 `--write` 时实际写入被拒绝。
4. 再次检查项目，然后在 IDE 打开、编译并验证结果。结构校验不等于编译成功。

例如 `create_script` 参数：

```json
{"name":"scr_demo_init","group":"脚本/game","code":"function scr_demo_init() { return 1; }","dry_run":true}
```

预览不会改动游戏资源，但读取/预览可能创建项目锁文件。已有资源和事件拒绝覆盖；已有 GML 的编辑交给客户端文件编辑工具。

每次写入在 `.codex/gms2-resource-backups/` 保存原字节、结果快照和事务清单。`list_backups` 查询事务，`restore_backup` 用 `transaction_id` 预览或执行撤销。恢复必须从最新未撤销事务开始；发现外部改动会拒绝覆盖。撤销新建操作会移除其新建文件。不要把包含游戏代码、素材及本机路径的备份目录上传到公共仓库。

跨进程锁只协调本工具，IDE 不遵守此锁。单文件使用原子替换，多文件整体不是原子事务；请勿同时在 IDE 保存和执行 MCP 修改。失败会尝试恢复，有未完成恢复的事务时阻止新写入。

## 工具清单

<details>
<summary>展开全部 18 个工具</summary>

| 工具 | 功能 |
| --- | --- |
| `server_info` | 版本、目标、写入模式、支持范围 |
| `inspect_project` | 资源、分组、引用、房间顺序和帧结构校验 |
| `inspect_resource` | 资源描述及源文件哈希 |
| `ensure_group` | 创建多级虚拟分组 |
| `create_script` | 创建脚本与初始 GML |
| `create_object` | 创建物体 |
| `add_object_event` | 新增事件与初始 GML |
| `configure_object` | 常驻、可见、精灵及父物体引用 |
| `move_resource` | 调整虚拟分组，保持磁盘原生目录 |
| `create_room` | 创建房间与 Instances 图层 |
| `add_instance_layer` | 新增实例图层 |
| `place_object` | 放入实例并登记创建顺序 |
| `set_start_room` | 调整启动房间 |
| `import_sprite` | 复制等尺寸 PNG 帧并创建可编辑精灵 |
| `import_sound` | 复制 PCM WAV 并创建声音 |
| `create_shader` | 创建 GLSL ES 顶点/片段源文件 |
| `list_backups` | 查看事务及状态 |
| `restore_backup` | 撤销最新事务 |

</details>

默认分组：脚本、物体/object、物体/system（`sys_`）、物体/ui（`ui_`）、房间、精灵、声音、着色器；均可通过参数指定其他分组。

事件支持 Create、Destroy、Step/BeginStep/EndStep、Draw/DrawGUI/DrawBegin/DrawEnd/DrawGUIBegin/DrawGUIEnd、CleanUp、RoomStart/RoomEnd，以及 Alarm 0～11、User 0～15。Alarm/User 通过 `event` 与 `number` 分别指定。

PNG 支持 1～256 帧，每帧尺寸相同、单文件最多 64 MiB、单边最多 8192。WAV 最多 128 MiB，单/双声道，8/16/24 位 PCM。原始导入文件不修改。`configure_object` 中 null 表示保留，sprite/parent 空字符串表示清空。

## 兼容与限制

| 验证项 | 已观察结果 |
| --- | --- |
| 自动测试 | 25 项测试；Windows / Python 3.10、3.12 的 [CI 已通过](https://github.com/MKTLMORX/morx-gms2-mcp/actions/runs/38952755796) |
| 打包 | wheel、sdist 和公开源码 ZIP 构建通过 |
| MCP | 真实 stdio 握手、工具发现、预览、写入、错误及恢复测试通过 |
| IDE | GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23，Windows VM 隔离场景编译运行通过 |

<details>
<summary>查看 IDE 验收细节</summary>

- Windows、Python 3.10.9；MCP SDK 1.11.0 和 1.30.0 的自动测试与真实 stdio 握手通过。
- Steam GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23：隔离项目中脚本、物体、房间、中文分组、双帧 PNG、WAV、GLSL ES 均被识别，保存后结构校验通过，Windows VM 编译运行通过。
- 运行画面验证双帧精灵、着色器编译状态、声音资源存在、Alarm 和 DrawGUI。声音示例是静音文件，不代表听觉验证。
- IDE 打开期间新增脚本自动出现；其他资源类型与所有编辑冲突场景的热加载尚未逐一验证。

</details>

**当前不包含的能力：**

- 不控制 IDE、不自动编译、不创建新项目；不提供资源删除/重命名、已有事件覆盖、字体/路径/时间线/序列/扩展创建、OGG/MP3 导入。
- 暂不提供已有精灵的逐帧时长/排序/图像编辑，以及项目窗口/全屏/标题等设置接口；整体动画速度和原点目前只在新建精灵时指定。
- 结构校验只覆盖实现支持的关系，不是 GameMaker 官方完整格式验证器。其他 IDE 版本、平台与复杂现有项目需自行验证。

## 开发与发布

```powershell
python -m pip install . build
python scripts/check_public_files.py
python -m unittest discover -s tests -t . -v
python -m build
python scripts/build_release.py
```

最后一条命令按白名单生成不含测试项目、缓存、本机配置及游戏素材的 GitHub 源码 ZIP。wheel/sdist 在 `dist/`。`examples/create_demo.py --project <已绑定的空测试项目>` 经真实 MCP 创建可运行验收场景，只应对独立测试项目运行；重复运行会拒绝覆盖。

`check_public_files.py` 检查 Git 跟踪文件和提交历史中的常见私人路径、凭据、私有邮箱及不应提交的文件；它是辅助检查，不能代替人工审阅。安装、备份和实验目录默认排除，不要把生成的本机配置提交到仓库。

开发约定见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全与数据边界见 [SECURITY.md](SECURITY.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。报告问题请提交脱敏后的最小复现。

## English quick start

MORX GMS2 MCP is an independent local stdio server for GameMaker resource workflows. It exposes 18 tools for scripts, objects, events, rooms, PNG sprites, PCM WAV audio, GLSL ES shaders, virtual groups and transaction rollback.

1. Clone this repository, create a Python 3.10+ virtual environment, and install with `python -m pip install .` inside that environment. No official PyPI release is available yet.
2. Create and save a test project in GameMaker. Run `python -m morx_gms2_mcp bind --project <project>` and then `check --project <project>`.
3. Generate configuration with `client-config --project <project> --client codex` or `--client claude`. Add `--write` only when resource writes are intended. Merge the result into the client configuration and reconnect.
4. Verify the target with `server_info`. Mutation tools default to `dry_run=true`; execution requires both a writable server and explicit `dry_run=false`.

Existing GML and events are not overwritten. Do not save in the IDE while the server is writing. Multi-file transactions are not fully atomic. This server does not control or compile the IDE, edit existing animation frames, or change window/fullscreen project settings. See the compatibility table above for tested versions.

Licensed under [MIT](LICENSE). Contributions and sanitized bug reports are welcome.

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、[Claude Code MCP 配置](https://code.claude.com/docs/en/mcp)。
