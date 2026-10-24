# MORX GMS2 MCP

[![CI](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml)
[![PyPI 0.5.0](https://img.shields.io/badge/PyPI-0.5.0-3775A9?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/morx-gms2-mcp/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Windows](https://img.shields.io/badge/Platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)](#适用范围)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

**使用 AI 客户端制作、运行和检查 GameMaker 项目。**

MORX GMS2 MCP 是一个本地 stdio MCP 服务。AI 客户端可以读取项目结构、制作资源和修改 GML，也可以通过隔离的 Windows VM 副本编译运行，并读取画面、实例状态、日志和运行时异常。修改默认先预览，写入保留事务备份。

*Local stdio MCP server for GameMaker project authoring, isolated runs, and runtime inspection.*

当前提供 58 个工具；服务使用本地 stdio，默认只读，运行能力默认关闭，MIT 许可。

本文对应 `0.6.0`。使用本页全部功能请[从源码安装](#1-安装)；PyPI 安装包版本见上方徽章。

[快速开始](#快速开始) · [能做什么](#能做什么) · [连接客户端](#连接客户端) · [使用示例](#使用示例) · [运行与观察](#运行与观察) · [工具清单](#工具清单) · [English](#english-quick-start)

## 能做什么

| 资源与操作 | 能力 |
| --- | --- |
| 脚本与物体 | 创建脚本、物体和常用事件（含碰撞、键盘、鼠标），配置精灵引用、父物体及常驻状态 |
| 房间与实例 | 创建房间和实例图层，放置物体，设置启动房间 |
| 美术与音频 | 导入 PNG、WAV/OGG 和字体数据，创建着色器；查询和配置声音的压缩、流式、质量及音频组 |
| 精灵动画 | 查询已有位图精灵，调整速度、原点、帧顺序和逐帧时长；按双哈希替换 PNG，可显式调整尺寸 |
| 显示设置 | Windows 启动选项，以及独立的房间、相机和视口配置 |
| GML 与运行 | 读取及哈希保护修改 GML；安装测试桥接；隔离 Windows VM 构建运行 |
| 运行观察与控制 | 游戏截图、实例与全局变量读写、实例生成/销毁、运行日志、运行时异常、数据事件触发、窗口消息鼠标、批量动作序列 |
| 项目结构 | 创建多级中文分组，移动虚拟分组，清理空分组，删除未引用资源，重命名并更新引用，校验资源引用 |
| 修改与恢复 | 默认预览、固定项目绑定、冲突检测、事务备份与撤销 |

## 开发工作流

将需求交给助手，按“理解项目 → 制作与配置 → 运行观察 → 调整并验证”推进。AI 客户端负责理解需求和组织工具调用，MCP 负责执行项目操作。

| 环节 | 使用方式 |
| --- | --- |
| 理解项目 | 查询资源、分组、引用、动画、房间和相机配置，确定修改目标 |
| 制作与配置 | 创建资源和 GML 事件，读取及修改已有 GML，编辑动画及显示设置，先预览再写入 |
| 运行观察 | 安装测试桥接后用 `run_game` 构建隔离副本；用 `runtime_status` 等待就绪，再截图、读写实例/全局变量、增删实例并读取日志 |
| 调整并验证 | 用 `read_code`/`update_code` 修改 GML，`stop_game` 后重新运行；用数据事件、窗口消息鼠标和等待帧复现交互 |

本仓库提供的接口覆盖项目文件、资源、GML 和隔离运行。运行能力默认关闭，需要 `--allow-run` 和本机 Runtime 配置；截图和状态来自游戏内的测试桥接，不抓取桌面。IDE 图形界面、原生断点和逐行调试仍需使用 GameMaker 本身。

## 快速开始

需要 Windows、Python 3.10+、一个由 GameMaker 创建并保存的项目，以及支持本地 stdio 的 MCP 客户端。建议先用独立测试项目熟悉操作；以下命令在 Windows PowerShell 中运行。

### 1. 安装

推荐用 [pipx](https://pipx.pypa.io/) 安装为独立命令：

```powershell
pipx install morx-gms2-mcp
morx-gms2-mcp --version
```

也可以装进 venv：

```powershell
python -m venv .venv
$mcpPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $mcpPython -m pip install morx-gms2-mcp
& $mcpPython -m morx_gms2_mcp --version
```

<details>
<summary>从源码安装</summary>

```powershell
git clone https://github.com/MKTLMORX/morx-gms2-mcp.git
cd morx-gms2-mcp
python -m venv .venv
$mcpPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $mcpPython -m pip install .
& $mcpPython -m morx_gms2_mcp --version
```

</details>

依赖版本见 [pyproject.toml](pyproject.toml)。

### 2. 绑定并检查项目

```powershell
$gameProject = 'D:\Games\MyGame' # 替换为你的测试项目
morx-gms2-mcp bind --project $gameProject
morx-gms2-mcp check --project $gameProject
```

用 venv 安装时，把 `morx-gms2-mcp` 换成 `& $mcpPython -m morx_gms2_mcp`。`--project` 也接受 `.yyp` 文件路径。目录须恰有一个现代 `GMProject v1` 的 `.yyp`；服务不创建空项目。

## 连接客户端

### 3. 生成配置

```powershell
# 按你使用的客户端选择一条
morx-gms2-mcp client-config --project $gameProject --client codex --write
morx-gms2-mcp client-config --project $gameProject --client claude --write
```

| 客户端 | 将生成内容合并到 | 示例 |
| --- | --- | --- |
| Codex | 游戏项目的 `.codex/config.toml` | [TOML 示例](examples/codex.config.toml) |
| Claude Code | 游戏项目的 `.mcp.json` | [JSON 示例](examples/claude.mcp.json) |

`client-config` 只输出配置，不会自动合并；保留文件中已有服务，然后重新加载客户端。Claude Code 可能要求确认项目信任。去掉 `--write` 可保持资源只读，允许查询和预览，拒绝实际修改；运行能力由 `--allow-run` 单独控制。使用 venv 时把 `morx-gms2-mcp` 换成 `& $mcpPython -m morx_gms2_mcp`。

生成的配置把单次工具调用上限设为 180 秒（Claude Code 的 `timeout` 以毫秒计），因为 `wait_game_frames` 等待较多帧时可能合法地阻塞超过默认的 60 秒。较早的 Claude Code 版本若不支持 `timeout` 字段，可在启动前设置 `MCP_TOOL_TIMEOUT=180000`。

需要运行能力时，在生成配置时同时传入运行参数：

```powershell
morx-gms2-mcp client-config --project $gameProject --client claude --write `
  --allow-run --runtime-root '<Runtime 目录>' --user-directory '<已登录用户目录>'
```

<details>
<summary>通过 Claude Code 命令行注册</summary>

也可以在游戏项目目录使用 Claude Code 的本地作用域注册：

```powershell
claude mcp add --scope local --transport stdio morx-gms2-mcp -- morx-gms2-mcp serve --project "D:\Games\MyGame" --write
claude mcp get morx-gms2-mcp
```

使用 venv 时，把命令中的 `morx-gms2-mcp serve` 换成 `"D:\Tools\gms2-mcp\.venv\Scripts\python.exe" -m morx_gms2_mcp serve`。若 PowerShell 的 `claude.ps1` 包装器未正确传递参数，可直接调用 `claude.exe`，或使用上文生成的 JSON 配置。

</details>

### 4. 连接并验证

重新加载客户端后，先调用 `server_info` 核对项目路径、写入模式和运行能力。一个服务固定绑定一个项目；管理多个项目时，分别配置服务。迁移项目后需要重新绑定，绑定文件位于 `.codex/gms2-resource-binding.json`。

## 使用示例

连接后，可以向 AI 助手提出这样的请求：

> 检查当前绑定项目，并列出资源引用错误，先不要修改。

> 在“脚本/game”分组创建 scr_demo_init，先给我看修改预览。

> 将我提供的两张等尺寸 PNG 导入为 spr_demo，设置播放速度和原点。

> 查询 spr_demo 的动画，将帧倒序，让第一帧停留更久，先预览修改。

> 查询启动房间和 Windows 显示设置，预览将相机设为 320×180、视口设为 960×540，并允许调整窗口大小。

> 列出备份，预览撤销最近一次资源创建会影响哪些文件。

> 将 spr_player 重命名为 spr_hero，先检查并列出会被更新的资源引用。

> 删除不再使用的 scr_old_helper，先确认没有其他资源引用它，并给我看将删除的文件。

> 安装测试桥接并运行当前游戏，等桥接就绪后截图。

> 查询运行中 obj_player 的 x、y、hp 和 image_index，并读取全局变量 score 和 inventory。

> 向右移动 60 帧，再等待 30 帧后截图；然后停止运行并清理副本。

> 用数据触发 obj_player 的 KeyPress 事件（E 键），不要发送物理键盘输入。

> 用窗口消息按住 E 键 4 帧再释放，确认 KeyPress 和按住状态都发生变化；不要改动我的系统键盘。

> 在游戏窗口客户区 (480,270) 点击一次；默认走窗口消息，不要移动我的真实光标。

> 依次等待 10 帧、触发 obj_enemy 的碰撞事件并截图，把序列结果一起返回。

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

```text
install_runtime_bridge → run_game → runtime_status → capture_game / inspect_game / set_game_variables / spawn_game_instance / trigger_game_event → stop_game
```

**启用条件**

- 服务启动时额外传入 `--allow-run`、`--runtime-root <runtime 目录>`、`--user-directory <已登录用户目录>`；用户目录下应直接有 `licence.plist`，服务只检查存在性，不读取或复制许可证内容。
- 本机安装 Windows x64 Runtime；绑定项目必须可编译，并已有非继承 `GMRoom v1` 启动房间。
- 安装测试桥接和修改 GML 仍需要 `--write` 与 `dry_run=false`；`run_game` 只会运行绑定项目的独立副本。

**推荐流程**

1. 保存 IDE 中尚未落盘的内容；调用 `inspect_project` 确认结构。
2. `install_runtime_bridge` 先预览再执行。它在启动房间安装常驻测试物体 `sys_morx_runtime` 和实例图层；没有会话文件时物体自行销毁。安装是一次普通事务，可用 `restore_backup` 撤销。
3. `run_game` 返回 `session_id`；轮询 `runtime_status` 直到 `bridge_ready=true`。不能把进程启动当作编译成功。
4. `capture_game` 获取游戏画面，`inspect_game` 读取房间、窗口、GUI、帧号、FPS 和最多 100 个实例的指定变量；用 `scope='globals'` 读取全局变量，数组和结构体会按限制展开。`get_runtime_output` 读取构建和游戏日志，`get_runtime_errors` 读取未处理异常和桥接错误。
5. `set_game_variables` 按名称写入实例或全局变量，返回旧值；实例写入须给出 `object_name` 或 `instance_id`，默认拒绝不存在的变量，`create_missing=true` 才允许新建。支持布尔、数值、字符串、数组和结构体，不支持 null 或表达式。
6. `spawn_game_instance` 在当前房间按物体名创建实例，可指定图层或 depth；`destroy_game_instance` 按实例 ID 或物体名销毁最多 `limit` 个实例。两者都会正常触发 Create/Destroy/CleanUp 事件，且拒绝操作测试桥接。
7. `wait_game_frames` 等待 1～600 个自然运行帧（不是暂停或单步）；`trigger_game_event` 通过桥接数据执行 KeyPress、Mouse、Collision 等事件代码，不需要物理键鼠或前台焦点；`send_game_input` 默认通过窗口消息按住按键，可产生按下/释放事件；`send_game_mouse` 默认用窗口消息点击，不移动真实光标。
8. `run_game_sequence` 可把 wait、input、state、set、spawn、destroy、capture、event 组合成最多 32 步的序列，减少往返并一次返回多张截图。
9. `stop_game` 结束会话并清理构建副本和存档目录；再次运行前必须先停止并读取旧日志。

### 只编译、保留存档重启与截图导出

`compile_game` 只调用 Igor 的 Windows VM Compile，不启动游戏，也不要求测试桥接；仍需启用 `--allow-run` 和配置 Runtime。返回会话后查询 `runtime_status`：`build_status=succeeded` 和退出码 0 才表示编译成功，不能凭日志中的完成标记判断。`compile_error_count` 是已识别诊断行的数量，无法识别的失败仍会由退出码标记为 failed；它与桥接的 `error_count` 分开。结束后用 `stop_game` 清理副本。

`restart_game` 先结束本次受管进程，再用同一隔离副本重新 Igor Run，保留该会话存档。它会重新编译副本，不复制源项目最新改动，也不接管外部 Runner；返回后重新等待桥接就绪。最终 `stop_game` 仍会清理副本和存档。

`save_game_capture` 将与内嵌截图相同的预览 PNG 保存到绑定项目 `.codex/morx-captures/<filename>.png`。它需要 `--write` 和 `dry_run=false`，只接受安全文件名，不覆盖已有文件，不开放任意写入目录。

### 鼠标坐标与拖放

`send_game_mouse` 默认 `coordinate_space='client'`，对应服务的 Win32 客户区读数。可选择 `physical`（独立查询的物理客户区）、`window`（游戏引擎窗口）或 `gui`；工具按所选尺寸转换为输入客户区坐标，并返回各轴缩放系数。无法取得所选尺寸时拒绝输入，不假定机器固定为某个 DPI。

GUI 模式按整个客户区比例换算；项目若自行设置 GUI 偏移、留黑边或非全幅输出，须使用明确的 client 坐标。截图预览缩放与输入坐标是不同概念。

`action='drag'` 需要起点 `x/y` 和终点 `end_x/end_y`，支持 50～3000 毫秒、2～60 步，只使用窗口消息；按住期间的移动消息携带按钮标记，结束或异常时释放按钮。移动按单调时钟安排，系统调度仍可能延长实际耗时。click/drag 默认先移动并等待 2 帧，可用 `settle_frames` 调整到 0～30。

消息模式的 click/drag 返回 `mouse_trace`，按 `trace_id` 关联本次消息投递顺序和游戏 BeginStep 的逐帧记录，包含按下、释放时的窗口/GUI 坐标及按钮状态。最多保留 128 帧，截断时保留首帧、最近帧和独立的按下/释放样本；记录在操作结束或 10 秒后停止。`dispatch_complete` 只表示消息投递完成，`game_effect='unknown'` 表示工具不判断项目逻辑是否成功。

`observed_mouse` 是操作后的较晚采样，可能已回到其他位置；核对释放位置应看本次记录的 `engine.release`。引擎未采到释放时该字段为空，不能用最近按下位置替代。BeginStep 记录也不能证明项目 GUI 事件执行成功；仍需检查实际游戏状态，不要自动重复输入。消息投递途中出错会保留已投递记录和错误，按钮仍会尝试释放。

### 心跳与迟到回执

`runtime_status.bridge_diagnostics` 返回就绪原因、最近心跳年龄及是否使用缓存。心跳文件短暂不可读时会重试，缓存也遵守 10 秒有效期。待确认请求返回年龄；如果完整回执留在临时文件，会按会话和请求 ID 识别为完成，不重发命令。响应不完整或身份不符时继续保留待确认状态。

`bridge_io` 区分文件重试与实际命令结果：

| 字段 | 含义 |
| --- | --- |
| `retry_attempts` | 本次游戏进程中，文件提升未成功的累计尝试数；旧字段 `write_failures` 是其兼容别名 |
| `retry_recoveries` | 曾需要重试、后来成功提升的写入周期数，不是命令数量 |
| `pending_writes` | 当前尚未提升的文件数量 |
| `stalled_writes` | 持续等待至少 10 秒的文件数量；诊断阈值不代表命令已失败 |
| `files` | 固定桥接文件各自的计数、等待时长、最近失败/恢复帧和请求 ID |

`state` 为 `healthy`、`retrying`、`stalled` 或 `recovered`。已恢复后的累计重试数不会清零；不能因它大于 0 就认定输入丢失或截图失败。以工具的 `ok`/错误结果和实际游戏状态判断。`response_source='completed_temporary_file'` 只说明使用了完整临时回执，并不表示截图失败或需要重新执行。

`inspect_game` 返回请求处理时的采样；`runtime_status` 从心跳读取，带 `sample_age_seconds`；停止结果的 `current=false` 表示最近历史采样。未提供诊断或字段不合法时返回未知/不可用，不当作零错误。文件诊断只能说明写入阶段和恢复情况，不推断 Windows 占用的具体原因。

### 升级后的桥接同步

先查询 `server_info.runtime_bridge`。`compatible=false` 时，预览并显式执行 `install_runtime_bridge` 同步项目桥接，再预览运行；服务不会自动修改项目中的桥接。该提示发生在启动 Runner 之前，不是编译失败。`compile_game` 不要求安装或同步桥接。

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

- 仅支持 Windows VM 目标；不控制 IDE 图形界面，不提供原生断点、暂停/逐行调试或任意表达式求值。
- 变量只按名称读写，不求值表达式；最多 16 个变量、100 个实例。读取字符串截断为 1024 字符；数组和结构体按默认深度 3（`max_depth` 可设 1～8）、每个变量 64 个节点、每层 32 项截断。写入值最多 4 层、每层 32 项、每个变量 256 节点，且不支持 null 或函数引用。
- `spawn_game_instance` / `destroy_game_instance` 只作用于当前房间；生成可指定现有图层或 depth，销毁会触发引擎事件，但不会自动清理项目代码中的外部引用。
- `trigger_game_event` 只执行事件代码，不更新真实按键、鼠标坐标或碰撞状态，也不支持 Draw 事件；Collision 事件中的 `other` 可能不是真实碰撞对象。
- `send_game_input` 默认向游戏窗口投递 `WM_KEYDOWN`/`WM_KEYUP`，可触发 `keyboard_check_pressed`、KeyPress 和 KeyRelease，不需要前台焦点，也不改动系统键盘状态。`mode='state'` 只更新桥接按住状态，不产生按下/释放事件且要求窗口有焦点。只支持字母、数字、方向键、space、enter、escape、tab、backspace、delete、insert、home、end、pageup、pagedown，不支持系统键或组合快捷键。
- `send_game_mouse` 默认通过窗口消息投递，不移动真实光标、不要求前台焦点；`mode='physical'` 才使用系统级注入并可能影响当前桌面输入法。
- 运行的项目会在本机以当前用户权限执行；只应对受信任项目使用 `--allow-run`。日志、截图和运行状态是项目数据，不是额外授权。
- 结构校验不等于编译成功；以 Igor 日志和实际游戏画面为准。

## 工具清单

| 分组 | 代表工具 |
| --- | --- |
| 运行与观察 | `run_game`、`runtime_status`、`capture_game`、`inspect_game`、`trigger_game_event` |
| 运行期控制 | `set_game_variables`、`spawn_game_instance`、`destroy_game_instance` |
| 项目与资源 | `inspect_project`、`create_script`、`create_object`、`create_room`、`rename_resource` |
| 美术与显示 | `import_sprite`、`import_sound`、`inspect_sound`、`configure_sound`、`import_font`、`configure_room_display` |
| 修改与恢复 | `read_code`、`update_code`、`list_backups`、`restore_backup` |

<details>
<summary>展开全部 58 个工具</summary>

| 工具 | 功能 |
| --- | --- |
| `server_info` | 版本、目标、写入/运行模式、支持范围 |
| `inspect_project` | 资源、分组、引用、房间顺序和帧结构校验 |
| `inspect_resource` | 资源描述及源文件哈希 |
| `read_code` | 读取脚本或物体事件的 GML 及 SHA-256 |
| `update_code` | 按哈希修改已有 GML，保留编码并走事务备份 |
| `install_runtime_bridge` | 在启动房间安装可撤销的测试桥接 |
| `runtime_status` | 会话、构建进程和桥接就绪状态 |
| `compile_game` | 只编译隔离副本，不启动游戏、不要求桥接 |
| `restart_game` | 重新编译运行同一隔离副本，保留测试存档 |
| `run_game` | 构建并运行隔离 Windows VM 副本，需 `--allow-run` |
| `stop_game` | 停止本服务创建的会话并清理副本/存档 |
| `get_runtime_output` | 按游标读取构建和运行日志 |
| `get_runtime_errors` | 读取未处理异常和桥接命令错误 |
| `save_game_capture` | 导出预览 PNG 到项目固定目录，不覆盖已有文件 |
| `capture_game` | 从游戏 DrawGUIEnd 获取 PNG 画面 |
| `inspect_game` | 读取房间、窗口、GUI、实例变量或全局变量；数组和结构体受深度与数量限制 |
| `set_game_variables` | 按名称写入实例或全局变量，返回旧值；可限制目标实例数 |
| `spawn_game_instance` | 在当前房间按物体名创建实例，可指定图层或 depth |
| `destroy_game_instance` | 按实例 ID 或物体名销毁最多 limit 个实例 |
| `send_game_input` | 默认用窗口消息按住按键并释放，可产生 KeyPress；`state` 模式只改按住状态 |
| `trigger_game_event` | 通过桥接数据执行事件代码，不需要物理键鼠 |
| `send_game_mouse` | 默认用窗口消息发送 move/click/wheel/drag，明确坐标空间，可选系统级注入 |
| `wait_game_frames` | 等待自然运行帧，不是暂停或单步 |
| `run_game_sequence` | 组合 wait/input/state/set/spawn/destroy/capture/event 动作并批量返回结果 |
| `inspect_display_settings` | Windows 选项及指定房间/首房间的尺寸、相机和视口 |
| `configure_windows_display` | 显示名称、启动全屏、边框、大小调整许可等 Windows 选项 |
| `configure_room_display` | 独立调整房间大小、一个相机及其视口 |
| `remove_empty_group` | 项目哈希保护清理空分组及排序项，拒绝非空组 |
| `ensure_group` | 创建多级虚拟分组 |
| `create_script` | 创建脚本与初始 GML |
| `create_object` | 创建物体 |
| `add_object_event` | 新增事件与初始 GML |
| `remove_object_event` | 删除已有事件及其 GML 文件，保留物体 |
| `configure_object` | 常驻、可见、精灵及父物体引用 |
| `move_resource` | 调整虚拟分组，保持磁盘原生目录 |
| `delete_resource` | 删除未被引用的资源及文件，走事务备份 |
| `rename_resource` | 重命名资源、移动文件并更新资源引用 |
| `create_room` | 创建房间与 Instances 图层 |
| `add_instance_layer` | 新增实例图层 |
| `place_object` | 放入实例并登记创建顺序 |
| `set_start_room` | 调整启动房间 |
| `import_sprite` | 复制等尺寸 PNG 帧并创建可编辑精灵 |
| `replace_sprite_images` | 双哈希保护替换同帧数、单图像图层 PNG；可显式调整尺寸、原点及碰撞框 |
| `inspect_sprite` | 查询帧 ID、顺序、时长、速度、原点及描述文件哈希 |
| `configure_sprite` | 修改已有位图精灵的速度、速度单位及自定义原点 |
| `set_sprite_animation` | 重排现有帧、设置逐帧时长，不修改 PNG |
| `import_sound` | 复制 WAV/OGG Vorbis 并创建声音，可指定压缩、流式、质量等导出设置 |
| `inspect_sound` | 查询声音设置、描述和源音频哈希、已有音频组 |
| `configure_sound` | 按描述哈希配置已有声音，默认预览并支持事务撤销 |
| `import_font` | 将 TTF/TTE/OTF/TTC 作为 IncludedFile 导入 datafiles/fonts |
| `create_shader` | 创建 GLSL ES 顶点/片段源文件 |
| `create_tileset` | 从精灵创建瓦片集 |
| `inspect_tileset` | 查询瓦片尺寸、列数、瓦片数和引用 |
| `create_tile_layer` | 在房间中创建瓦片图层 |
| `set_tilemap_cells` | 写入瓦片单元格、翻转和旋转标记 |
| `inspect_tilemap` | 读取瓦片图层尺寸、非空单元格和指定区域 |
| `list_backups` | 查看事务及状态 |
| `restore_backup` | 撤销最新事务 |

</details>

清理分组前调用 `inspect_project`，将 `project_sha256` 传给 `remove_empty_group.expected_sha256`。预览会列出直接资源、子组和排序引用；非空组执行时拒绝，不递归删除。

替换精灵图像前调用 `inspect_sprite`，传入 `sha256` 和 `image_sha256`，将 PNG 按动画顺序交给 `replace_sprite_images`。默认要求同尺寸，所有新帧仍须等尺寸、数量不变、单图像图层；资源、帧/图层身份、分组和时间轴保留，外部改图后旧哈希失效。

需要改变尺寸时，显式设置 `allow_resize=true`，先核对预览中的 `geometry.before/after`：

| 参数 | 尺寸变化时的行为 |
| --- | --- |
| `origin_policy='keep'`（默认） | 保留原点像素坐标，允许位于图像外，切换为自定义原点 |
| `origin_policy='scale'` | 按宽高比例调整原点并取最近整数，切换为自定义原点 |
| `bbox_policy='keep'`（默认） | 保留碰撞框模式；整图框更新到新尺寸，自动框按全部新帧 Alpha 与原透明度阈值重算，手动框坐标保持且须落在新尺寸内 |
| `bbox_policy='scale'` | 按比例调整旧框，切换为手动碰撞框 |
| `bbox_policy='full_image'` | 使用新图整幅边界，切换为整图碰撞框 |

尺寸变化拒绝九宫格和配置覆盖精灵。自动框在同尺寸替换时也会重新计算；全部帧都没有超过透明度阈值的像素时拒绝猜测空框。描述与所有帧 PNG 在同一事务内更新，失败回滚，撤销恢复原字节；不会先删除再重新注册资源。不增删帧或自动改写使用该精灵的 GML；尺寸和碰撞效果需要重新编译运行检查。

默认分组：脚本、物体/object、物体/system（`sys_`）、物体/ui（`ui_`）、房间、精灵、声音、着色器、瓦片集；均可通过参数指定其他分组。

事件支持 Create、Destroy、Step/BeginStep/EndStep、Draw/DrawGUI/DrawBegin/DrawEnd/DrawGUIBegin/DrawGUIEnd、CleanUp、RoomStart/RoomEnd，以及 Alarm 0～11、User 0～15。Alarm/User 通过 `event` 与 `number` 分别指定。

PNG 支持 1～256 帧，每帧尺寸相同、单文件最多 64 MiB、单边最多 8192。声音最多 128 MiB：WAV 为单/双声道 8/16/24 位 PCM；OGG 为单/双声道 Vorbis，服务读取采样率、声道和时长。字体支持 TTF/TTE/OTF/TTC，最多 64 MiB，作为 `datafiles/fonts` 下的 IncludedFile 导入，不烘焙 GMFont 资源。原始导入文件不修改。`configure_object` 中 null 表示保留，sprite/parent 空字符串表示清空。

### 声音设置

新声音可在 `import_sound` 时指定设置；已有声音先 `inspect_sound`，再把描述文件的 `sha256` 传给 `configure_sound.expected_sha256`。默认只预览，执行需 `--write` 和 `dry_run=false`；null 保留原值，未知字段、源音频、资源身份和资源树分组不改。配置覆盖的声音可查询，暂不修改。

| 参数 | 支持值 |
| --- | --- |
| `compression` | `uncompressed` 未压缩；`compressed` 压缩；`decompress_on_load` 加载时解压；`streamed` 从磁盘流式播放 |
| `volume` / `preload` | 资源音量 0～1；布尔预加载标志 |
| `compression_quality` | 0～10，导入默认 4；只影响需要转换的压缩导出 |
| `conversion_mode` | `automatic`（默认）或 `required`；OGG automatic 可直接复制源文件，required 强制按目标设置转换 |
| `sample_rate` / `channel_format` | 8000～192000 Hz；`mono`、`stereo`、`3d`，导入默认沿用源采样率/声道 |
| `bit_depth` | 8 或 16，导入默认 16；只影响未压缩导出 |
| `audio_group` | 项目已有音频组，导入默认 `audiogroup_default`；与资源树文件夹 `group` 不同 |

流式模式只接受默认音频组。将已有非默认组声音改成流式时，必须同时显式指定 `audio_group='audiogroup_default'`，工具不会自动移组。`preload` 是保存到资源的标志，实际加载受平台、压缩模式和音频组控制：默认组的非流式声音通常随启动加载，自定义组仍须运行期 `audio_group_load`；它不保证整段流式音乐驻留内存。参见 [官方声音编辑器说明](https://manual.gamemaker.io/lts/en/The_Asset_Editors/Sounds.htm)和[音频组说明](https://manual.gamemaker.io/lts/en/Settings/Audio_Groups.htm)。

例如，先查询背景音乐，再预览改为流式：

```json
{"name":"bgm_demo"}
```

```json
{"name":"bgm_demo","expected_sha256":"<inspect_sound 返回的 sha256>","compression":"streamed","audio_group":"audiogroup_default","compression_quality":6,"conversion_mode":"required","preload":false,"dry_run":true}
```

修改只改变资源导出设置；源 WAV/OGG 不转码、不覆盖。保存后通过 `compile_game` 检查构建，再在新运行会话核对播放；已有运行副本不会随源资源修改而更新，`restart_game` 仍使用原快照。

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
| 输入缩放 | 已实测 125% DPI 下多组窗口/GUI 尺寸；其他 DPI 尚未实测，自定义 GUI 偏移需显式使用 client 坐标 |


其他 IDE 版本、平台及复杂项目建议先用副本验证。这是独立社区工具，与 GameMaker 官方无隶属关系。

### 使用前需要了解

- `inspect_*` 读取磁盘中的项目资源和配置；运行工具通过 Igor 构建并运行隔离副本。服务不控制 IDE 图形界面，不提供原生断点、暂停/逐行调试或任意表达式求值。鼠标默认通过窗口消息投递；只有显式 `mode='physical'` 才会注入系统级鼠标事件。
- 新建项目仍在 GameMaker 中完成；`run_game` 负责编译和运行绑定项目的副本。资源热加载行为取决于 IDE，修改后应核对实际加载结果。
- 安装测试桥接会修改源项目的启动房间和 `.yyp`；这是一次可撤销事务。运行前先停止旧会话，运行副本和存档会在停止后删除。
- 运行的项目以当前用户权限执行；只应对受信任项目启用 `--allow-run`。
- 资源删除要求没有被其他资源引用；重命名会更新 `.yyp`、排序文件和资源引用，但不会改写 GML 代码中的资源名。不提供覆盖已有 GML/事件、路径/时间线/序列/扩展创建，以及 MP3 导入。字体以 IncludedFile 导入，不创建烘焙 GMFont 资源。已有 GML 用 `read_code`/`update_code` 修改。
- 精灵编辑不增删帧、不缩放、裁剪或调色；显示配置不包含其他平台、GUI 尺寸或运行时窗口切换代码。
- 结构校验只检查工具支持的格式和引用关系，不能代替 Igor 编译或实际游戏运行测试。
- 拒绝越界路径、符号链接及目录联接；名为 `参考项目` 的目录及其子目录不能绑定。

使用问题和功能建议可提交到 [Issues](https://github.com/MKTLMORX/morx-gms2-mcp/issues)。测试与构建见[贡献指南](CONTRIBUTING.md)，数据边界见[安全说明](SECURITY.md)，版本变化见[更新记录](CHANGELOG.md)。

## English quick start

MORX GMS2 MCP is a local stdio server for AI-assisted GameMaker development. An assistant can inspect the project, create, rename and remove resources, edit code, build and run an isolated Windows copy, observe the game, and refine the result from runtime output. It exposes 58 tools with previews, backups and rollback.

This README describes `0.6.0`. Install from source for all features documented here; the badge above shows the PyPI package version.

Use an inspect → author → run and observe → adjust workflow. The AI client coordinates the work; this server handles project files, resources, GML and isolated runs. Runtime observation uses the server's own bridge inside the game, not desktop capture. IDE GUI control and native breakpoint/step debugging remain outside its scope.

1. Install from PyPI with `python -m pip install morx-gms2-mcp` in a Python 3.10+ virtual environment, or use `pipx install morx-gms2-mcp` / `uvx morx-gms2-mcp`. To install from source, clone this repository and run `python -m pip install .` inside a virtual environment.
2. Create and save a test project in GameMaker. Run `python -m morx_gms2_mcp bind --project <project>` and then `check --project <project>`.
3. Generate configuration with `client-config --project <project> --client codex` or `--client claude`. Add `--write` only when resource writes are intended. Merge the result into the client configuration and reconnect.
4. Verify the target with `server_info`. Mutation tools default to `dry_run=true`; execution requires both a writable server and explicit `dry_run=false`.
5. For runtime observation, start with `--allow-run` and the Runtime/user-folder options, run `install_runtime_bridge` (preview first), then `run_game`. Poll `runtime_status` until `bridge_ready=true` before calling `capture_game`, `inspect_game`, `get_runtime_errors`, `wait_game_frames`, `trigger_game_event` or `send_game_mouse`. Use `set_game_variables` to write instance or global variables by name (booleans, numbers, strings, arrays and structs; no null or expressions), and `spawn_game_instance` / `destroy_game_instance` to add or remove instances in the current room. Use `run_game_sequence` to batch wait/input/state/set/spawn/destroy/capture/event steps. Finish with `stop_game`; the isolated build copy and save directory are removed.

Use `inspect_sprite` before `configure_sprite` or `set_sprite_animation`. Reordering requires every existing frame ID exactly once; durations are positive animation timeline units in the resulting order, not seconds. Omitted durations stay attached to their frames. Pass the returned `sha256` as `expected_sha256` to reject stale edits. Only ordinary continuous bitmap timelines are supported; events and moments prevent retiming. PNG files are preserved. Reordering changes image indices.

Use `delete_resource` only after checking that no other resource references the target; `rename_resource` moves files and updates resource references, but does not rewrite names used in GML code. Use `inspect_game` with `scope='globals'` to read global variables; arrays and structs are bounded by depth and node count. Values are read by name only, not evaluated as expressions.

Use `inspect_display_settings` before `configure_windows_display` or `configure_room_display`. Windows edits require an existing GMWindowsOptions v2 file. Room, camera and viewport dimensions are independent; enabling views requires at least one visible viewport. Inherited room display settings are not edited. Use the corresponding Windows or room hash to reject stale writes. Borderless windows and borderless fullscreen are distinct; runtime code and the OS may override startup behavior. GUI sizes and runtime GML are not edited.

Runtime tools are disabled by default. Start the server with `--allow-run --runtime-root <runtime> --user-directory <logged-in user folder>` in addition to `--write` when GML edits are needed. `install_runtime_bridge` adds a managed persistent test object and room instance; preview it first. `run_game` builds a Windows VM copy under the user's local app data, then returns a session ID. Poll `runtime_status` until `bridge_ready` is true. Use `capture_game`, `inspect_game`, `get_runtime_output` and `get_runtime_errors` to observe. `inspect_game` reads instance variables by name; pass `scope='globals'` with a variable list to read globals, and expect bounded arrays and structs. `set_game_variables` writes instance or global variables and returns previous values; target instances by `object_name` or `instance_id`, and set `create_missing=true` only when a new variable is intended. `spawn_game_instance` and `destroy_game_instance` create or remove instances in the current room and trigger normal engine events. `trigger_game_event` executes event code from bridge data without physical input; `send_game_mouse` uses window messages by default and only moves the real cursor with `mode='physical'`. `send_game_input` posts `WM_KEYDOWN`/`WM_KEYUP` by default, so it can trigger press/release checks without changing the system keyboard state or requiring focus; `mode='state'` only updates held key state. Use `run_game_sequence` to batch wait/input/state/set/spawn/destroy/capture/event steps. `stop_game` ends the session and removes the build copy and save directory. Only one session is active; the maximum lifetime is 15 minutes. The game runs with your user privileges, so enable `--allow-run` only for trusted projects.

The verified environment is Windows with GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23, targeting Windows VM. Mouse input was checked at 125% DPI with multiple window/GUI sizes. Other DPI settings and IDE/runtime versions should be checked on an isolated project before use.

Existing GML and events are not overwritten by creation tools; use `read_code`/`update_code` for existing code. `delete_resource` requires that no other resource references the target. `rename_resource` updates resource references and collision event filenames, but names inside GML code are not rewritten. Do not save in the IDE while the server is writing. Multi-file transactions are not fully atomic. This server does not control the IDE, set breakpoints, pause/step the debugger or evaluate arbitrary expressions. `trigger_game_event` runs existing event code but does not update real key, mouse or collision state; Draw events are not supported by it. Mouse input uses window messages by default and only moves the real cursor in explicit physical mode. Font import adds TTF/OTF/TTC files as IncludedFiles under `datafiles/fonts`; it does not bake GMFont resources. Sprite frame creation/removal, pixel painting, MP3 import and display options for other platforms are outside its scope.

Additional tools: `compile_game` builds without launching Runner; check `runtime_status.build_status` and the exit code, then clean up with `stop_game`. `restart_game` rebuilds the same isolated snapshot while retaining its save data; final stop removes both. `save_game_capture` exports the preview to `.codex/morx-captures/` with write permission and no overwrite. `remove_empty_group` uses `inspect_project.project_sha256` and rejects nonempty groups.

`inspect_sound` reports GMSound v2 settings and descriptor/source hashes. `configure_sound` requires the descriptor hash and supports volume, compression/streaming, a preload flag, quality, conversion policy, sample rate, channel format, bit depth and existing audio groups; `import_sound` accepts the same settings. Streamed sounds require the default audio group. Preload does not replace runtime group loading. OGG automatic conversion can copy the original file; use required to enforce target conversion. Source audio is preserved, configuration overrides are not edited, and settings take effect on a new build.

`replace_sprite_images` preserves resource/frame/layer IDs, grouping and timing, with both descriptor and image hashes from `inspect_sprite`. Equal-count single-layer PNG frames are required. Size changes require `allow_resize=true`; origin keep/scale and bounding-box keep/scale/full_image policies are previewed and applied in one reversible transaction. Nine-slice and configuration overrides are rejected for resizing.

Mouse `coordinate_space` can be client, physical, window or gui. GUI mapping assumes full-client proportional scaling; custom offsets require client coordinates. Message-only drag has bounded duration/steps and releases the button. Message click/drag returns a correlated `mouse_trace` with dispatch order and up to 128 BeginStep frame samples, including press/release coordinates and button state; tracing stops after the operation or 10 seconds. The later `observed_mouse` sample does not establish release coordinates or gameplay success. Check game state and never replay inputs automatically. `inspect_game.max_depth` accepts 1–8 with the same 64-node budget. Readiness diagnostics report heartbeat age and cached reads; complete temporary responses are correlated to the request without replaying it.

Bridge I/O `retry_attempts` (legacy alias `write_failures`) counts unsuccessful promotion attempts, not final command failures. `retry_recoveries` counts recovered write episodes; `pending_writes` and `stalled_writes` distinguish current work from waits of at least 10 seconds. Per-file records identify recent retry/recovery frames and request IDs. Heartbeat samples include age; stop results are historical. A complete temporary response is correlated without replaying the command; inspect its actual success/error result. Check `server_info.runtime_bridge` after upgrades, then preview and explicitly apply `install_runtime_bridge` when incompatible. Compile-only does not require the bridge.

Licensed under [MIT](LICENSE). Contributions and sanitized bug reports are welcome.

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、[Claude Code MCP 配置](https://code.claude.com/docs/en/mcp)。
