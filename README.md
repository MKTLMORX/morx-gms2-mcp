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
| 精灵动画 | 查询已有位图精灵，调整速度、原点、帧顺序和逐帧时长 |
| 项目结构 | 创建多级中文分组，移动虚拟分组，校验资源引用 |
| 修改与恢复 | 默认预览、固定项目绑定、冲突检测、事务备份与撤销 |

**21 个工具 · 本地 stdio · 默认只读 · MIT**

当前源码为 **0.2.0.dev1 开发版**，尚未发布；新增精灵编辑功能已在下文指定 GameMaker 版本完成隔离项目保存、编译及运行验收。

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

> 查询 spr_demo 的动画，将帧倒序，让第一帧停留更久，先预览修改。

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
<summary>展开全部 21 个工具</summary>

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
| `inspect_sprite` | 查询帧 ID、顺序、时长、速度、原点及描述文件哈希 |
| `configure_sprite` | 修改已有位图精灵的速度、速度单位及自定义原点 |
| `set_sprite_animation` | 重排现有帧、设置逐帧时长，不修改 PNG |
| `import_sound` | 复制 PCM WAV 并创建声音 |
| `create_shader` | 创建 GLSL ES 顶点/片段源文件 |
| `list_backups` | 查看事务及状态 |
| `restore_backup` | 撤销最新事务 |

</details>

默认分组：脚本、物体/object、物体/system（`sys_`）、物体/ui（`ui_`）、房间、精灵、声音、着色器；均可通过参数指定其他分组。

事件支持 Create、Destroy、Step/BeginStep/EndStep、Draw/DrawGUI/DrawBegin/DrawEnd/DrawGUIBegin/DrawGUIEnd、CleanUp、RoomStart/RoomEnd，以及 Alarm 0～11、User 0～15。Alarm/User 通过 `event` 与 `number` 分别指定。

PNG 支持 1～256 帧，每帧尺寸相同、单文件最多 64 MiB、单边最多 8192。WAV 最多 128 MiB，单/双声道，8/16/24 位 PCM。原始导入文件不修改。`configure_object` 中 null 表示保留，sprite/parent 空字符串表示清空。

### 编辑已有精灵动画

先用 `inspect_sprite` 取得帧 ID 和描述文件 `sha256`。`configure_sprite` 的 `speed_type` 为 `frames_per_second` 或 `frames_per_game_frame`，null 保留现有值；设置任一原点坐标会切换为自定义原点，另一坐标保持原值，允许原点位于图像外。

`set_sprite_animation` 的 `frame_order` 必须恰好包含每个现有帧 ID 一次，不接受帧索引、不增删帧。`frame_durations` 按**结果帧顺序**提供有限正数，单位是动画时间轴长度，不是秒。例如速度为 10 FPS 时，长度 2 对应 0.2 秒；如果采用每游戏帧速度单位，实际秒数还取决于游戏帧率。省略顺序则保持当前顺序；省略时长则每帧携带自己的原时长参与重排。

例如，查询到三个帧 ID 后，可预览倒序并调整时长（替换占位值）：

```json
{"name":"spr_demo","frame_order":["<第三帧 ID>","<第二帧 ID>","<第一帧 ID>"],"frame_durations":[2,1,3],"expected_sha256":"<查询得到的 sha256>","dry_run":true}
```

两个修改工具均建议带 `expected_sha256`，预览与执行使用相同值；描述文件在其间被修改时会拒绝操作，需重新查询和预览。编辑只改精灵 `.yy`，保留 PNG、帧 ID 和未知字段，支持原字节撤销。重排也会改变对应图像的 `image_index`，使用固定索引的游戏代码须自行核对。

`inspect_sprite.frames` 每项对应一张图像，`image_index` 为图像索引，`start` 和 `duration` 描述该图像在动画时间轴的位置。时间轴总长与图像帧数量不同：三张图像的时长为 3、1、2 时，IDE 时间轴总长显示 6；本次 Runtime 验收中 `sprite_get_number` 仍返回 3。

首轮仅支持 `GMSprite v2` 普通位图：以帧为时间单位、单一帧轨道、图像帧与关键帧一一对应、连续且无重叠。暂不支持自动延伸、禁用关键帧、子轨道或修饰器；含广播消息、时刻或事件时允许配置速度与原点，但拒绝重排或调整时长。复杂结构可用 `inspect_resource` 查看原始描述。

格式语义参考 GameMaker 官方[精灵编辑器](https://manual.gamemaker.io/monthly/en/The_Asset_Editors/Sprites.htm)和[关键帧结构](https://manual.gamemaker.io/lts/en/GameMaker_Language/GML_Reference/Asset_Management/Sequences/Sequence_Structs/The_Keyframe_Struct.htm)；结构检查与自动测试不替代 IDE 验收。

## 兼容与限制

| 验证项 | 已观察结果 |
| --- | --- |
| 自动测试 | 开发版提供 39 项测试；0.1.1 的 25 项测试在 Windows / Python 3.10、3.12 的 [CI 已通过](https://github.com/MKTLMORX/morx-gms2-mcp/actions/runs/38952755796)；新增编辑功能尚无公开 CI 结果 |
| 打包 | wheel、sdist 和公开源码 ZIP 构建通过 |
| MCP | 真实 stdio 握手、工具发现、预览、写入、错误及恢复测试通过 |
| IDE | GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23：原资源创建及新增精灵编辑的 Windows VM 隔离场景编译运行通过 |

<details>
<summary>查看 IDE 验收细节</summary>

- Windows、Python 3.10.9；MCP SDK 1.11.0 和 1.30.0 的自动测试与真实 stdio 握手通过。
- Steam GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23：隔离项目中脚本、物体、房间、中文分组、双帧 PNG、WAV、GLSL ES 均被识别，保存后结构校验通过，Windows VM 编译运行通过。
- 运行画面验证双帧精灵、着色器编译状态、声音资源存在、Alarm 和 DrawGUI。声音示例是静音文件，不代表听觉验证。
- IDE 打开期间新增脚本自动出现；其他资源类型与所有编辑冲突场景的热加载尚未逐一验证。
- 0.2.0.dev1：IDE 正确显示蓝/绿/红重排、3/1/2 时间轴长度、6 FPS 和自定义原点 12,12；IDE 保存后的精灵数据语义保持一致，PNG 哈希未改变。
- 新动画在 Windows VM 实际运行；每组连续采样 180 个绘制步，读取精灵内部像素。按红/绿/蓝统计，基准 1/1/1 得到 59/60/61，重排并设为 3/1/2 得到 60/30/90，小数时长 1.5/1/0.5 得到 30/61/89，按游戏帧速度 0.1 的 3/1/2 得到 60/30/90，与预期相符（最大偏差 1 个绘制步）。此结果不扩展为其他平台、复杂轨道或任意参数的完整兼容保证。

</details>

**当前不包含的能力：**

- 不控制 IDE、不自动编译、不创建新项目；不提供资源删除/重命名、已有事件覆盖、字体/路径/时间线/序列/扩展创建、OGG/MP3 导入。
- 暂不提供精灵增删帧、缩放、裁剪或调色，以及项目窗口/全屏/标题等设置接口；已有精灵动画编辑仅覆盖上文限定的时间轴结构。
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

MORX GMS2 MCP is an independent local stdio server for GameMaker resource workflows. It exposes 21 tools for scripts, objects, events, rooms, PNG sprites, PCM WAV audio, GLSL ES shaders, virtual groups and transaction rollback. The current source is the unreleased 0.2.0.dev1 development version. Sprite editing passed isolated IDE save and Windows VM runtime checks on IDE 2026.0.0.16 / Runtime 2026.0.0.23, including pixel samples for fractional durations and both speed units.

1. Clone this repository, create a Python 3.10+ virtual environment, and install with `python -m pip install .` inside that environment. No official PyPI release is available yet.
2. Create and save a test project in GameMaker. Run `python -m morx_gms2_mcp bind --project <project>` and then `check --project <project>`.
3. Generate configuration with `client-config --project <project> --client codex` or `--client claude`. Add `--write` only when resource writes are intended. Merge the result into the client configuration and reconnect.
4. Verify the target with `server_info`. Mutation tools default to `dry_run=true`; execution requires both a writable server and explicit `dry_run=false`.

Use `inspect_sprite` before `configure_sprite` or `set_sprite_animation`. Reordering requires every existing frame ID exactly once; durations are positive animation timeline units in the resulting order, not seconds. Omitted durations stay attached to their frames. Pass the returned `sha256` as `expected_sha256` to reject stale edits. Only ordinary continuous bitmap timelines are supported; events and moments prevent retiming. PNG files are preserved. Reordering changes image indices.

Existing GML and events are not overwritten. Do not save in the IDE while the server is writing. Multi-file transactions are not fully atomic. This server does not control or compile the IDE, add/remove sprite frames, edit image pixels, or change window/fullscreen project settings. See the compatibility table above for tested versions.

Licensed under [MIT](LICENSE). Contributions and sanitized bug reports are welcome.

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、[Claude Code MCP 配置](https://code.claude.com/docs/en/mcp)。
