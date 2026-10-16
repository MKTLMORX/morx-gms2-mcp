# MORX GMS2 MCP

[![CI](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**通过 AI 助手创建和管理 GameMaker 资源，编辑精灵动画、配置显示，并在写入前预览修改。**

Local MCP tools for GameMaker resources, sprite animation and display settings. Preview changes before writing, with backups and rollback. Works with Codex and Claude Code.

[快速开始](#快速开始) · [连接客户端](#连接客户端) · [使用示例](#使用示例) · [工具清单](#工具清单) · [适用范围](#适用范围) · [English](#english-quick-start)

## 能做什么

| 资源与操作 | 能力 |
| --- | --- |
| 脚本与物体 | 创建脚本、物体和常用事件，配置精灵引用、父物体及常驻状态 |
| 房间与实例 | 创建房间和实例图层，放置物体，设置启动房间 |
| 美术与音频 | 导入多帧 PNG、PCM WAV，创建 GLSL ES 着色器 |
| 精灵动画 | 查询已有位图精灵，调整速度、原点、帧顺序和逐帧时长 |
| 显示设置 | Windows 启动选项，以及独立的房间、相机和视口配置 |
| 项目结构 | 创建多级中文分组，移动虚拟分组，校验资源引用 |
| 修改与恢复 | 默认预览、固定项目绑定、冲突检测、事务备份与撤销 |

**24 个工具 · 本地 stdio · 默认只读 · MIT**

服务直接读写绑定项目的资源文件，无需保持 GameMaker 运行。资源修改后，在 IDE 中打开项目并运行游戏，检查实际效果。

## 快速开始

需要 Python 3.10+、支持本地 stdio MCP 的客户端，以及由 GameMaker 创建并保存的项目。建议先用独立测试项目熟悉操作。以下命令在 Windows PowerShell 中运行。

**1. 安装到独立环境**

```powershell
git clone https://github.com/MKTLMORX/morx-gms2-mcp.git
cd morx-gms2-mcp
python -m venv .venv
$mcpPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $mcpPython -m pip install .
& $mcpPython -m morx_gms2_mcp --version
```

请使用本仓库安装；本项目未提供官方 PyPI 包。依赖版本见 [pyproject.toml](pyproject.toml)。

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

复制命令输出，合并到对应配置文件，再重新加载客户端。`client-config` 不会自动写入配置文件；合并时保留已有服务。Claude Code 可能要求确认项目信任。去掉 `--write` 可保持服务只读，允许查询和预览，拒绝实际修改。

<details>
<summary>通过 Claude Code 命令行注册</summary>

也可以在游戏项目目录使用 Claude Code 的本地作用域注册：

```powershell
claude mcp add --scope local --transport stdio morx-gms2-mcp -- "D:\Tools\gms2-mcp\.venv\Scripts\python.exe" -m morx_gms2_mcp serve --project "D:\Games\MyGame" --write
claude mcp get morx-gms2-mcp
```

若 PowerShell 的 `claude.ps1` 包装器未正确传递参数，可直接调用 `claude.exe`，或使用上文生成的 JSON 配置。

</details>

连接后，先调用 `server_info` 核对项目路径和写入模式。一个服务固定绑定一个项目；管理多个项目时，分别配置服务。迁移项目后须核对并重新绑定，绑定文件位于 `.codex/gms2-resource-binding.json`。

## 使用示例

连接后，可以向 AI 助手提出这样的请求：

> 检查当前绑定项目，并列出资源引用错误，先不要修改。

> 在“脚本/game”分组创建 scr_demo_init，先给我看修改预览。

> 将我提供的两张等尺寸 PNG 导入为 spr_demo，设置播放速度和原点。

> 查询 spr_demo 的动画，将帧倒序，让第一帧停留更久，先预览修改。

> 查询启动房间和 Windows 显示设置，预览将相机设为 320×180、视口设为 960×540，并允许调整窗口大小。

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

每次写入都会在 `.codex/gms2-resource-backups/` 保存备份。用 `list_backups` 查询事务，再将 `transaction_id` 传给 `restore_backup`，预览或执行撤销。恢复须从最新未撤销事务开始；发现后续外部改动会拒绝覆盖。撤销新建操作会移除其新建文件。备份包含项目代码、素材和本机路径，不应上传到公共仓库。

跨进程锁只协调本工具，IDE 不遵守此锁。单文件使用原子替换，多文件整体不是原子事务；请勿同时在 IDE 保存和执行 MCP 修改。失败会尝试恢复，有未完成恢复的事务时阻止新写入。

## 工具清单

<details>
<summary>展开全部 24 个工具</summary>

| 工具 | 功能 |
| --- | --- |
| `server_info` | 版本、目标、写入模式、支持范围 |
| `inspect_project` | 资源、分组、引用、房间顺序和帧结构校验 |
| `inspect_resource` | 资源描述及源文件哈希 |
| `inspect_display_settings` | Windows 选项及指定房间/首房间的尺寸、相机和视口 |
| `configure_windows_display` | 显示名称、启动全屏、边框、大小调整许可等 Windows 选项 |
| `configure_room_display` | 独立调整房间大小、一个相机及其视口 |
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

`inspect_sprite.frames` 每项对应一张图像，`image_index` 为图像索引，`start` 和 `duration` 描述该图像在动画时间轴的位置。时间轴总长与图像数量不同：三张图像的时长为 3、1、2 时，时间轴总长为 6，图像数量仍为 3。

支持 `GMSprite v2` 普通位图：以帧为时间单位、单一帧轨道、图像帧与关键帧一一对应、连续且无重叠。自动延伸、禁用关键帧、子轨道或修饰器不在支持范围内；含广播消息、时刻或事件时允许配置速度与原点，但拒绝重排或调整时长。复杂结构可用 `inspect_resource` 查看原始描述。

相关概念见 GameMaker 官方[精灵编辑器](https://manual.gamemaker.io/monthly/en/The_Asset_Editors/Sprites.htm)和[关键帧结构](https://manual.gamemaker.io/lts/en/GameMaker_Language/GML_Reference/Asset_Management/Sequences/Sequence_Structs/The_Keyframe_Struct.htm)。

### 配置项目显示

`inspect_display_settings` 分别返回 `windows` 和 `room` 的文件路径、支持状态及 `sha256`。省略 `room_name` 时读取启动房间；缺少 Windows 平台文件时标记不可用，不自动创建。继承房间的查询值仅是本地记录，不解析继承结果。

`configure_windows_display` 仅修改现有 `GMWindowsOptions v2` 的以下字段，null 保留：`title`、`start_fullscreen`、`borderless`、`resizable`、`allow_fullscreen_switching`、`display_cursor`、`vsync`、`interpolate_pixels`。`title` 是 Windows 平台显示名称；运行时代码可能覆盖窗口标题或其他启动设置。`borderless` 只去除边框，不等于铺满显示器的无边框全屏；无边框下窗口手动拉伸不会生效，两个开关同时为 true 时返回提示。缩放策略、安装器、图标、文件系统沙箱及其他平台配置不修改。

`configure_room_display` 针对非继承 `GMRoom v1`：

| 参数 | 含义 |
| --- | --- |
| `room_width` / `room_height` | 房间世界尺寸，不联动改变相机或视口 |
| `camera_x` / `camera_y` / `camera_width` / `camera_height` | 相机在房间中的位置和可见范围 |
| `viewport_x` / `viewport_y` / `viewport_width` / `viewport_height` | 视口在输出画布上的位置和尺寸 |
| `view_index` / `visible` / `enable_views` | 操作 0～7 中的一个视口、是否可见，以及房间是否启用视口 |

尺寸须为 1～32768 的整数；视口位置为非负整数，可见视口的右/下边界不得超过 32768。null 保留原值，不关闭其他视口；启用后至少一个视口必须可见。继承房间、继承房间设置、继承视口设置或选中的继承视口均拒绝写入，不自动解除继承。

例如预览将 `rm_demo` 的相机设为 320×180、视口设为 960×540：

```json
{"room_name":"rm_demo","enable_views":true,"view_index":0,"visible":true,"camera_width":320,"camera_height":180,"viewport_width":960,"viewport_height":540,"expected_sha256":"<查询返回的 room.sha256>","dry_run":true}
```

修改 Windows 选项时传入 `windows.sha256`，修改房间时使用 `room.sha256`；预览和执行保留同一哈希。初始窗口大小由首房间及其可见视口决定，实际结果可能受操作系统和运行时代码影响；仅修改后续房间不会自动重设已运行的窗口。GUI 尺寸、运行时窗口切换、无边框全屏模式和应用表面手动调整仍由 GML 管理，本工具不注入代码。参考官方 [Windows 选项](https://manual.gamemaker.io/lts/en/Settings/Game_Options/Windows.htm)、[房间和视口](https://manual.gamemaker.io/lts/en/The_Asset_Editors/Room_Properties/Room_Properties.htm)。

## 适用范围

| 项目 | 支持范围 |
| --- | --- |
| 客户端 | 支持本地 stdio MCP；提供 Codex、Claude Code 配置生成 |
| 项目格式 | 现代 `GMProject v1`；项目目录中恰有一个 `.yyp` |
| 精灵编辑 | 普通 `GMSprite v2` 位图连续时间轴，详见上文 |
| 显示配置 | 已有 `GMWindowsOptions v2`，以及非继承 `GMRoom v1` 的房间、相机和视口 |
| 已验证环境 | Windows，GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23，Windows VM 目标 |

其他 IDE 版本、平台及复杂项目建议先用副本验证。这是独立社区工具，与 GameMaker 官方无隶属关系。

### 使用前需要了解

- `inspect_*` 读取磁盘中的项目资源和配置。服务不提供游戏截图、运行日志、断点、单步执行或运行时变量检查，也不启动游戏或控制 IDE。
- 创建和编译项目仍在 GameMaker 中完成；资源热加载行为取决于 IDE，修改后应核对实际加载结果。
- 不提供资源删除或重命名、已有 GML/事件覆盖、字体/路径/时间线/序列/扩展创建，以及 OGG/MP3 导入。
- 精灵编辑不增删帧、不缩放、裁剪或调色；显示配置不包含其他平台、GUI 尺寸或运行时窗口切换代码。
- 结构校验只检查工具支持的格式和引用关系，不能代替 IDE 编译或游戏运行测试。
- 拒绝越界路径、符号链接及目录联接；名为 `参考项目` 的目录及其子目录不能绑定。

使用问题和功能建议可提交到 [Issues](https://github.com/MKTLMORX/morx-gms2-mcp/issues)。测试与构建见[贡献指南](CONTRIBUTING.md)，数据边界见[安全说明](SECURITY.md)，版本变化见[更新记录](CHANGELOG.md)。

## English quick start

MORX GMS2 MCP connects AI assistants to GameMaker project files through 24 local stdio tools. Create resources, edit sprite animation, configure Windows display settings and room cameras/viewports, and preview changes before writing. Each write includes a backup for rollback.

1. Clone this repository, create a Python 3.10+ virtual environment, and install with `python -m pip install .` inside that environment. Use this repository as the installation source; there is no official PyPI package.
2. Create and save a test project in GameMaker. Run `python -m morx_gms2_mcp bind --project <project>` and then `check --project <project>`.
3. Generate configuration with `client-config --project <project> --client codex` or `--client claude`. Add `--write` only when resource writes are intended. Merge the result into the client configuration and reconnect.
4. Verify the target with `server_info`. Mutation tools default to `dry_run=true`; execution requires both a writable server and explicit `dry_run=false`.

Use `inspect_sprite` before `configure_sprite` or `set_sprite_animation`. Reordering requires every existing frame ID exactly once; durations are positive animation timeline units in the resulting order, not seconds. Omitted durations stay attached to their frames. Pass the returned `sha256` as `expected_sha256` to reject stale edits. Only ordinary continuous bitmap timelines are supported; events and moments prevent retiming. PNG files are preserved. Reordering changes image indices.

Use `inspect_display_settings` before `configure_windows_display` or `configure_room_display`. Windows edits require an existing GMWindowsOptions v2 file. Room, camera and viewport dimensions are independent; enabling views requires at least one visible viewport. Inherited room display settings are not edited. Use the corresponding Windows or room hash to reject stale writes. Borderless windows and borderless fullscreen are distinct; runtime code and the OS may override startup behavior. GUI sizes and runtime GML are not edited.

The verified environment is Windows with GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23, targeting Windows VM. Test a copy of your project when using other versions or platforms.

Existing GML and events are not overwritten. Do not save in the IDE while the server is writing. Multi-file transactions are not fully atomic. This server reads project files; it does not capture the running game, read runtime variables, set breakpoints, launch or compile projects, or control the IDE. Sprite frame creation/removal, image editing and display options for other platforms are outside its scope.

Licensed under [MIT](LICENSE). Contributions and sanitized bug reports are welcome.

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、[Claude Code MCP 配置](https://code.claude.com/docs/en/mcp)。
