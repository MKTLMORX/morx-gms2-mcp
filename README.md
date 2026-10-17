# MORX GMS2 MCP

[![CI](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**让 AI 助手参与 GameMaker 项目的制作、运行、检查与迭代。**

A local MCP foundation for AI-assisted GameMaker development: understand projects, create and edit resources, build and run an isolated Windows copy, observe the game, and iterate using real feedback. Works with Codex and Claude Code.

[开发工作流](#开发工作流) · [快速开始](#快速开始) · [连接客户端](#连接客户端) · [使用示例](#使用示例) · [运行与观察](#运行与观察) · [工具清单](#工具清单) · [适用范围](#适用范围) · [English](#english-quick-start)

MORX GMS2 MCP 为 AI 助手提供操作 GameMaker 项目的工具接口。让助手先理解项目结构，再制作资源、调整动画与显示设置，通过隔离的 Windows VM 副本编译运行游戏，读取画面、实例状态和日志，并根据实际运行结果继续修改。修改可以先预览，写入后保留备份，方便检查和撤销。

## 能做什么

| 资源与操作 | 能力 |
| --- | --- |
| 脚本与物体 | 创建脚本、物体和常用事件，配置精灵引用、父物体及常驻状态 |
| 房间与实例 | 创建房间和实例图层，放置物体，设置启动房间 |
| 美术与音频 | 导入多帧 PNG、PCM WAV，创建 GLSL ES 着色器 |
| 精灵动画 | 查询已有位图精灵，调整速度、原点、帧顺序和逐帧时长 |
| 显示设置 | Windows 启动选项，以及独立的房间、相机和视口配置 |
| GML 与运行 | 读取及哈希保护修改 GML；安装测试桥接；隔离 Windows VM 构建运行 |
| 运行观察 | 游戏截图、实例变量、运行日志、等待自然帧、受限制的键盘输入 |
| 项目结构 | 创建多级中文分组，移动虚拟分组，校验资源引用 |
| 修改与恢复 | 默认预览、固定项目绑定、冲突检测、事务备份与撤销 |

**35 个工具 · 本地 stdio · 默认只读 · 运行默认关闭 · MIT**

## 开发工作流

将需求交给助手，按“理解项目 → 制作与配置 → 运行观察 → 调整并验证”推进。AI 客户端负责理解需求和组织工具调用，MCP 负责执行项目操作。

| 环节 | 使用方式 |
| --- | --- |
| 理解项目 | 查询资源、分组、引用、动画、房间和相机配置，确定修改目标 |
| 制作与配置 | 创建资源和 GML 事件，读取及修改已有 GML，编辑动画及显示设置，先预览再写入 |
| 运行观察 | 安装测试桥接后用 `run_game` 构建隔离副本；用 `runtime_status` 等待就绪，再截图、查询实例变量和日志 |
| 调整并验证 | 用 `read_code`/`update_code` 修改 GML，`stop_game` 后重新运行；用按键输入和等待帧复现交互 |

本仓库提供的接口覆盖项目文件、资源、GML 和隔离运行。运行能力默认关闭，需要 `--allow-run` 和本机 Runtime 配置；截图和状态来自游戏内的测试桥接，不抓取桌面。IDE 图形界面、原生断点和逐行调试仍需使用 GameMaker 本身。

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

复制命令输出，合并到对应配置文件，再重新加载客户端。`client-config` 不会自动写入配置文件；合并时保留已有服务。Claude Code 可能要求确认项目信任。去掉 `--write` 可保持资源只读，允许查询和预览，拒绝实际修改；运行能力由 `--allow-run` 单独控制。

生成的配置把单次工具调用上限设为 180 秒（Claude Code 的 `timeout` 以毫秒计），因为 `wait_game_frames` 等待较多帧时可能合法地阻塞超过默认的 60 秒。较早的 Claude Code 版本若不支持 `timeout` 字段，可在启动前设置 `MCP_TOOL_TIMEOUT=180000`。

需要运行能力时，在生成配置时同时传入运行参数：

```powershell
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client claude --write `
  --allow-run --runtime-root '<Runtime 目录>' --user-directory '<已登录用户目录>'
```

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

> 安装测试桥接并运行当前游戏，等桥接就绪后截图。

> 查询运行中 obj_player 的 x、y、hp 和 image_index。

> 向右移动 60 帧，再等待 30 帧后截图；然后停止运行并清理副本。

## 使用流程与写入保护

1. 保存 IDE 中尚未落盘的内容，调用 `server_info` 和 `inspect_project`。
2. 调用修改工具，默认 `dry_run=true`，查看拟改文件。
3. 确认目标和请求后以相同参数及 `dry_run=false` 执行；服务还必须带 `--write` 启动。没有 `--write` 时实际写入被拒绝。
4. 再次检查项目；需要时用 `run_game` 编译运行验证。结构校验不等于编译成功。

例如 `create_script` 参数：

```json
{"name":"scr_demo_init","group":"脚本/game","code":"function scr_demo_init() { return 1; }","dry_run":true}
```

预览不会改动游戏资源，但读取/预览可能创建项目锁文件。已有资源和事件拒绝覆盖；已有 GML 用 `read_code`/`update_code` 修改，按哈希拒绝过期写入。

每次写入都会在 `.codex/gms2-resource-backups/` 保存备份。用 `list_backups` 查询事务，再将 `transaction_id` 传给 `restore_backup`，预览或执行撤销。恢复须从最新未撤销事务开始；发现后续外部改动会拒绝覆盖。撤销新建操作会移除其新建文件。备份包含项目代码、素材和本机路径，不应上传到公共仓库。

跨进程锁只协调本工具，IDE 不遵守此锁。单文件使用原子替换，多文件整体不是原子事务；请勿同时在 IDE 保存和执行 MCP 修改。失败会尝试恢复，有未完成恢复的事务时阻止新写入。

## 运行与观察

运行能力通过 GameMaker 自带的 Igor 命令行构建 Windows VM 目标，并在用户目录中运行独立副本。它与资源写入开关分开，默认关闭。

**启用条件**

- 服务启动时额外传入 `--allow-run`、`--runtime-root <runtime 目录>`、`--user-directory <已登录用户目录>`；用户目录下应直接有 `licence.plist`，服务只检查存在性，不读取或复制许可证内容。
- 本机安装 Windows x64 Runtime；绑定项目必须可编译，并已有非继承 `GMRoom v1` 启动房间。
- 安装测试桥接和修改 GML 仍需要 `--write` 与 `dry_run=false`；`run_game` 只会运行绑定项目的独立副本。

**推荐流程**

1. 保存 IDE 中尚未落盘的内容；调用 `inspect_project` 确认结构。
2. `install_runtime_bridge` 先预览再执行。它在启动房间安装常驻测试物体 `sys_morx_runtime` 和实例图层；没有会话文件时物体自行销毁。安装是一次普通事务，可用 `restore_backup` 撤销。
3. `run_game` 返回 `session_id`；轮询 `runtime_status` 直到 `bridge_ready=true`。不能把进程启动当作编译成功。
4. `capture_game` 获取游戏画面，`inspect_game` 读取房间、窗口、GUI、帧号、FPS 和最多 100 个实例的指定标量变量，`get_runtime_output` 读取构建和游戏日志。
5. `wait_game_frames` 等待 1～600 个自然运行帧（不是暂停或单步）；`send_game_input` 向有焦点的游戏发送按键，保持 1～300 帧后释放。
6. `stop_game` 结束会话并清理构建副本和存档目录；再次运行前必须先停止并读取旧日志。

**隔离与边界**

| 项目 | 行为 |
| --- | --- |
| 项目副本 | 复制到用户本地数据目录（默认 `%LOCALAPPDATA%`）的 `_morx_runs/<session>`，跳过 `.git/.codex/.venv/dist/build/node_modules`，拒绝链接和目录联接，限制 100000 文件或 2 GiB |
| 存档 | 使用 `MORX_Run_<session>` 独立存档目录；源项目的显示设置和存档不受运行影响 |
| 通信 | 通过存档目录中的 JSON 文件请求/应答，无 HTTP/TCP 监听；旧会话和旧请求 ID 会被拒绝 |
| 画面 | `capture_game` 从游戏 `DrawGUIEnd` 保存 PNG，预览最长边界 1280×720；不抓取桌面 |
| 日志 | 异步收集、有限环形缓冲，隐藏凭据相关行并脱敏本机路径 |
| 生命周期 | 单个活动会话；最长 15 分钟；停止只针对本服务创建的 Igor 和子进程 |
| 清理 | 停止、过期或启动失败都会删除构建副本与存档目录 |

**限制**

- 仅支持 Windows VM 目标；不控制 IDE 图形界面，不提供原生断点、暂停/逐行调试、鼠标输入或任意表达式求值。
- 实例变量只返回指定的标量；字符串截断为 1024 字符；最多 16 个变量、100 个实例。
- 按键输入需要游戏窗口有焦点；只支持字母、数字、方向键、space/enter/escape/tab/backspace，不支持系统键或组合快捷键。
- 运行的项目会在本机以当前用户权限执行；只应对受信任项目使用 `--allow-run`。日志、截图和运行状态是项目数据，不是额外授权。
- 结构校验不等于编译成功；以 Igor 日志和实际游戏画面为准。

## 工具清单

<details>
<summary>展开全部 35 个工具</summary>

| 工具 | 功能 |
| --- | --- |
| `server_info` | 版本、目标、写入/运行模式、支持范围 |
| `inspect_project` | 资源、分组、引用、房间顺序和帧结构校验 |
| `inspect_resource` | 资源描述及源文件哈希 |
| `read_code` | 读取脚本或物体事件的 GML 及 SHA-256 |
| `update_code` | 按哈希修改已有 GML，保留编码并走事务备份 |
| `install_runtime_bridge` | 在启动房间安装可撤销的测试桥接 |
| `runtime_status` | 会话、构建进程和桥接就绪状态 |
| `run_game` | 构建并运行隔离 Windows VM 副本，需 `--allow-run` |
| `stop_game` | 停止本服务创建的会话并清理副本/存档 |
| `get_runtime_output` | 按游标读取构建和运行日志 |
| `capture_game` | 从游戏 DrawGUIEnd 获取 PNG 画面 |
| `inspect_game` | 读取房间、窗口、GUI 和实例标量变量 |
| `send_game_input` | 向有焦点的游戏发送受限键盘输入 |
| `wait_game_frames` | 等待自然运行帧，不是暂停或单步 |
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
| 运行观察 | Windows x64 VM、已登录的本机 Runtime（Igor）、Windows 项目、单个活动会话 |
| 已验证环境 | Windows，GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23，Windows VM 目标 |

运行链路已在上述环境完成真实验收：MCP 创建控制器和桥接，Igor 编译运行，桥接就绪，截图包含 DrawGUI，实例状态可读，等待 30 帧和方向键输入生效，停止后副本与存档已清理。IDE 图形界面未参与该轮验收。

其他 IDE 版本、平台及复杂项目建议先用副本验证。这是独立社区工具，与 GameMaker 官方无隶属关系。

### 使用前需要了解

- `inspect_*` 读取磁盘中的项目资源和配置；运行工具通过 Igor 构建并运行隔离副本。服务不控制 IDE 图形界面，不提供原生断点、暂停/逐行调试、鼠标输入或任意表达式求值。
- 新建项目仍在 GameMaker 中完成；`run_game` 负责编译和运行绑定项目的副本。资源热加载行为取决于 IDE，修改后应核对实际加载结果。
- 安装测试桥接会修改源项目的启动房间和 `.yyp`；这是一次可撤销事务。运行前先停止旧会话，运行副本和存档会在停止后删除。
- 运行的项目以当前用户权限执行；只应对受信任项目启用 `--allow-run`。
- 不提供资源删除或重命名、覆盖已有 GML/事件、字体/路径/时间线/序列/扩展创建，以及 OGG/MP3 导入。已有 GML 用 `read_code`/`update_code` 修改。
- 精灵编辑不增删帧、不缩放、裁剪或调色；显示配置不包含其他平台、GUI 尺寸或运行时窗口切换代码。
- 结构校验只检查工具支持的格式和引用关系，不能代替 Igor 编译或实际游戏运行测试。
- 拒绝越界路径、符号链接及目录联接；名为 `参考项目` 的目录及其子目录不能绑定。

使用问题和功能建议可提交到 [Issues](https://github.com/MKTLMORX/morx-gms2-mcp/issues)。测试与构建见[贡献指南](CONTRIBUTING.md)，数据边界见[安全说明](SECURITY.md)，版本变化见[更新记录](CHANGELOG.md)。

## English quick start

MORX GMS2 MCP provides a local tool foundation for AI-assisted GameMaker development. Let an assistant inspect your project, create and edit resources, build and run an isolated Windows copy, observe the game, and refine the result using real feedback. It exposes 35 stdio tools with previews, backups and rollback.

Use an inspect → author → run and observe → adjust workflow. The AI client coordinates the work; this server handles project files, resources, GML and isolated runs. Runtime observation uses the server's own bridge inside the game, not desktop capture. IDE GUI control and native breakpoint/step debugging remain outside its scope.

1. Clone this repository, create a Python 3.10+ virtual environment, and install with `python -m pip install .` inside that environment. Use this repository as the installation source; there is no official PyPI package.
2. Create and save a test project in GameMaker. Run `python -m morx_gms2_mcp bind --project <project>` and then `check --project <project>`.
3. Generate configuration with `client-config --project <project> --client codex` or `--client claude`. Add `--write` only when resource writes are intended. Merge the result into the client configuration and reconnect.
4. Verify the target with `server_info`. Mutation tools default to `dry_run=true`; execution requires both a writable server and explicit `dry_run=false`.
5. For runtime observation, start with `--allow-run` and the Runtime/user-folder options, run `install_runtime_bridge` (preview first), then `run_game`. Poll `runtime_status` until `bridge_ready=true` before calling `capture_game`, `inspect_game`, `wait_game_frames` or `send_game_input`. Finish with `stop_game`; the isolated build copy and save directory are removed.

Use `inspect_sprite` before `configure_sprite` or `set_sprite_animation`. Reordering requires every existing frame ID exactly once; durations are positive animation timeline units in the resulting order, not seconds. Omitted durations stay attached to their frames. Pass the returned `sha256` as `expected_sha256` to reject stale edits. Only ordinary continuous bitmap timelines are supported; events and moments prevent retiming. PNG files are preserved. Reordering changes image indices.

Use `inspect_display_settings` before `configure_windows_display` or `configure_room_display`. Windows edits require an existing GMWindowsOptions v2 file. Room, camera and viewport dimensions are independent; enabling views requires at least one visible viewport. Inherited room display settings are not edited. Use the corresponding Windows or room hash to reject stale writes. Borderless windows and borderless fullscreen are distinct; runtime code and the OS may override startup behavior. GUI sizes and runtime GML are not edited.

Runtime tools are disabled by default. Start the server with `--allow-run --runtime-root <runtime> --user-directory <logged-in user folder>` in addition to `--write` when GML edits are needed. `install_runtime_bridge` adds a managed persistent test object and room instance; preview it first. `run_game` builds a Windows VM copy under the user's local app data, then returns a session ID. Poll `runtime_status` until `bridge_ready` is true. Use `capture_game`, `inspect_game` and `get_runtime_output` to observe; use `wait_game_frames` and `send_game_input` for playback and input. `stop_game` ends the session and removes the build copy and save directory. Only one session is active; the maximum lifetime is 15 minutes. The game runs with your user privileges, so enable `--allow-run` only for trusted projects.

The verified environment is Windows with GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23, targeting Windows VM. The run chain was verified end to end: bridge readiness, capture including DrawGUI content, live instance values, frame waits, keyboard input, stop and cleanup. IDE GUI interaction was not part of that verification. Test a copy of your project when using other versions or platforms.

Existing GML and events are not overwritten by creation tools; use `read_code`/`update_code` for existing code. Do not save in the IDE while the server is writing. Multi-file transactions are not fully atomic. This server does not control the IDE, set breakpoints, pause/step the debugger, send mouse input or evaluate arbitrary expressions. Sprite frame creation/removal, image editing and display options for other platforms are outside its scope.

Licensed under [MIT](LICENSE). Contributions and sanitized bug reports are welcome.

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、[Claude Code MCP 配置](https://code.claude.com/docs/en/mcp)。
