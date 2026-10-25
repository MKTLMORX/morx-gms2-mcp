# 工具与用法

[返回首页](../README.md) · [安装与客户端配置](setup.md)

[工具清单](#工具清单) · [写入与恢复](#使用流程与写入保护) · [运行与观察](#运行与观察) · [声音设置](#声音设置) · [精灵动画](#编辑已有精灵动画) · [显示配置](#配置项目显示) · [支持范围](#适用范围)

## 工具清单

共 58 个工具。参数类型与默认值可由 MCP 客户端查询；以下说明常用流程和限制。

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

## 资源编辑

### 清理空分组

清理分组前调用 `inspect_project`，将 `project_sha256` 传给 `remove_empty_group.expected_sha256`。预览会列出直接资源、子组和排序引用；非空组执行时拒绝，不递归删除。

### 替换精灵图像

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

### 导入与新建资源

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
| 精灵编辑 | 普通 `GMSprite v2` 位图连续时间轴，详见[精灵动画](#编辑已有精灵动画) |
| 显示配置 | 已有 `GMWindowsOptions v2`，以及非继承 `GMRoom v1` 的房间、相机和视口 |
| 运行观察 | Windows x64 VM、已登录的本机 Runtime（Igor）、Windows 项目、单个活动会话 |
| 已验证环境 | Windows，GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23，Windows VM 目标 |
| 输入缩放 | 已实测 125% DPI 下多组窗口/GUI 尺寸；其他 DPI 尚未实测，自定义 GUI 偏移需显式使用 client 坐标 |


其他 IDE 版本、平台及复杂项目建议先用副本验证。这是独立社区工具，与 GameMaker 官方无隶属关系。

- 新建项目仍在 GameMaker 中完成。资源热加载行为取决于 IDE，修改后应核对实际加载结果。
- 资源删除要求没有其他资源引用；重命名更新资源引用和文件名，不改写 GML 中的资源名。创建工具不覆盖已有 GML 或事件，已有代码使用 `read_code` / `update_code` 修改。
- 不提供路径、时间线、序列或扩展创建，以及 MP3 导入。精灵编辑不增删帧，不缩放、裁剪或调色 PNG；显示配置不包含其他平台和运行时 GML。
- 拒绝越界路径、符号链接及目录联接；名为 `参考项目` 的目录及其子目录不能绑定。

更多数据边界见[安全说明](../SECURITY.md)，版本变化见[更新记录](../CHANGELOG.md)。
