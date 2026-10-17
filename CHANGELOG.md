# 更新记录

## 0.5.0.dev0 — 未发布

- 修复运行桥接文件在 Windows 上被同时读取时，原子替换偶发抛出 `PermissionError` 的问题；替换失败会短暂重试。
- `inspect_game` 新增 `scope='globals'` 和 `instance_id`，可读取全局变量及单个实例；`instance_id` 可直接使用返回的 `@ref instance(...)` 标识。
- 实例和全局变量中的数组、结构体会按深度 3、每个变量 64 个节点、每层 32 项展开，超出部分标记截断；仍不执行表达式。
- `send_game_input` 默认改为窗口消息模式：投递 `WM_KEYDOWN`/`WM_KEYUP`，可触发 `keyboard_check_pressed`、KeyPress 和 KeyRelease，不需要前台焦点；`mode='state'` 保留原桥接按住状态行为。
- `run_game_sequence` 的 input 动作支持 `mode` 参数，默认使用窗口消息。
- 新增 `delete_resource`：拒绝删除仍被其他资源引用的资源，删除前列出文件，走事务备份。
- 新增 `rename_resource`：移动资源文件，更新 `.yyp`、排序文件和资源引用，同步碰撞事件文件名；不重写 GML 中的资源名。
- 新增 `set_game_variables`：按名称写入实例或全局变量，返回旧值；支持布尔、数值、字符串、数组和结构体，限制深度、数量和序列化大小；默认拒绝不存在的变量，`create_missing=true` 才允许新建。
- 新增 `spawn_game_instance` 和 `destroy_game_instance`：在当前运行房间创建实例或按实例 ID/物体名销毁实例；正常触发引擎事件，拒绝操作受管理的测试桥接。
- `run_game_sequence` 新增 `set`、`spawn`、`destroy` 动作，可与等待、输入、状态、截图和事件组合。
- 新增 `remove_object_event`：删除物体已有事件及其 GML 文件，支持预览、哈希保护和事务恢复；拒绝 GML Visual 事件。
- `import_sound` 支持 OGG Vorbis，读取采样率、声道和时长；声音描述改为 GameMaker 2026 的 `channelFormat`/`compressionQuality` 字段，不再写入已移除的 `bitRate`/`type`。
- 新增 `import_font`：将 TTF/TTE/OTF/TTC 作为 IncludedFile 导入 `datafiles/fonts`，返回运行时路径和 `font_add` 示例；不烘焙 GMFont 资源。
- 真实验收通过：实例/全局变量写入、缺失变量拒绝、实例生成与按 ID/物体名销毁、组合序列、事件删除、真实 OGG/WAV 资源编译与 `audio_exists`、真实 TTF 通过 `font_add` 加载；停止后副本和存档已清理。
- 工具数增至 51，自动测试增至 109 项。

## 0.4.1 — 2026-10-17

- 重新整理 README：PyPI/pipx 安装优先、步骤化快速开始、亮点和运行链路；PyPI 徽章改为静态版本，避免图片代理缓存显示 not found。
- 发布工作流增加 PyPI 同版本检查和 GitHub Release 幂等处理，重跑时不会重复上传或重复创建 Release。
- 无运行时代码行为变更；工具数 44，自动测试 88 项。

## 0.4.0 — 2026-10-17

- 运行桥接新增未处理异常捕获；`get_runtime_errors` 按游标返回最多 20 条异常或桥接命令错误，并在 `runtime_status`/`stop_game` 中给出数量。
- 新增 `trigger_game_event`：通过桥接数据执行 Keyboard/KeyPress/KeyRelease、Mouse、Collision、Alarm、User、Step/Other 等事件代码，不需要物理键鼠或前台焦点；Draw 事件不支持。它只执行事件代码，不更新真实按键、鼠标坐标或碰撞状态。
- 新增 `send_game_mouse`：默认 `mode='message'` 通过窗口消息投递 move/click/wheel，不移动真实光标、不要求前台焦点；`mode='physical'` 才使用系统级注入。
- 新增 `run_game_sequence`：组合最多 32 个 wait/input/state/capture/event 动作，总帧数上限 1800，截图最多 4 张，失败时返回已完成步骤。
- 事件制作支持碰撞目标 `collision_object`、Keyboard/KeyPress/KeyRelease、鼠标事件编号及 DrawResize、GameStart、GameEnd 等 Other 事件。
- 新增瓦片集与瓦片图层：`create_tileset`、`inspect_tileset`、`create_tile_layer`、`set_tilemap_cells`、`inspect_tilemap`；支持空瓦片及翻转/旋转标记。
- 桥接新增 `event` 命令，按 `object_name` 和可选 `instance_id` 选择目标；省略 `instance_id` 时最多触发 `limit` 个实例。
- 修正模拟键盘的语义说明：`keyboard_key_press` 只更新按住状态，不产生 KeyPress/KeyRelease 事件；事件测试改用 `trigger_game_event`。
- 真实验收通过：瓦片值、碰撞事件、KeyPress 数据事件、窗口消息鼠标、批量序列和未处理异常捕获；默认路径不再移动真实光标或发送系统级按键。
- 自动测试增至 88 项并全部通过；工具数增至 44。

## 0.3.0.dev1 — 未发布

- 新增运行观察：读取及哈希保护修改 GML、安装测试桥接、隔离 Windows VM 构建运行、游戏截图、实例状态、运行日志和按键测试，工具数增至 35。
- 桥接控制文件改为单行 JSON，GML 侧完整读取整个文件，修复多行 JSON 无法解析的问题。
- 桥接安装会校验并修复启动房间实例、事件列表、常驻标记及行尾/BOM 差异；启动房间变化后自动迁移桥接实例；同名普通物体给出明确错误。
- 运行副本和存档移至用户目录，停止或过期后清理；再次运行前必须先停止并读取旧会话日志。
- 运行响应异常会清理挂起请求，`runtime_status` 返回诊断而不是抛出内部错误；等待帧数按帧数计算超时。
- 日志脱敏改为大小写不敏感，并覆盖存档目录和用户目录；截图读取后删除临时 PNG。
- `inspect_game` 不再用源项目校验运行副本，桥接返回的错误按失败处理。
- 移除测试用 `.mcp-probe-project` 绑定绕过；统一事件编号校验和实例结构；`client-config` 的 Codex 环境变量改为从同一份配置生成。
- `client-config` 生成的单次工具调用上限改为 180 秒（Codex `tool_timeout_sec`、Claude Code `timeout`），避免 `wait_game_frames` 等待较多帧时被默认 60 秒截断。
- 修正 Igor `Windows Run` 命令：不传 `/of`，由 Igor 在运行目录生成输出；传 `/of` 会导致 Runner 加载错误文件并退出。
- 运行的链接检查改为 Windows reparse point 判断，覆盖控制文件、项目复制和清理，避免 GitHub Actions 临时目录的短路径被 `resolve()` 误判。
- 新增运行、桥接修复、脱敏、清理和绑定测试，共 80 项通过。
- 在 IDE 2026.0.0.16 / Runtime 2026.0.0.23 完成真实验收：MCP 创建控制器和桥接，Igor 编译运行，桥接就绪，截图包含 DrawGUI 内容，实例状态可读，等待 30 帧和发送方向键生效，停止后构建副本与存档均已清理。IDE 图形界面未参与本轮验收。

## 0.2.0.dev2 — 未发布

- 新增显示配置查询、Windows 启动显示选项、房间/相机/视口配置，工具数增至 24。
- 支持显示名称、启动全屏、无边框、窗口大小调整许可、全屏切换许可、光标、垂直同步和像素插值。
- 房间尺寸、相机范围和视口位置/尺寸独立设置，保留其他视口、实例和未知字段；继承显示配置拒绝修改。
- 仅将现有 Windows 配置文件加入固定写入白名单，沿用预览、备份撤销和可选 SHA-256 检查。
- 新增 15 项显示设置测试，扩展真实 stdio 验证，共 54 项测试通过。
- 在 IDE 2026.0.0.16 / Runtime 2026.0.0.23 完成显示配置保存比对，以及窗口、启动全屏和无边框三种 Windows VM 场景的编译运行验收。

## 0.2.0.dev1 — 未发布

- 新增精灵动画查询、已有精灵速度与原点配置、帧重排和逐帧时长设置，工具数增至 21。
- 编辑保留帧 ID、图像与未知字段，沿用预览、事务备份和撤销；可用描述文件 SHA-256 拒绝过期操作。
- 首轮支持普通位图连续时间轴；含广播消息或时刻时不重排、不调整时长，复杂布局拒绝修改。
- 新增精灵编辑测试，并扩展真实 stdio 协议测试。
- 在 IDE 2026.0.0.16 / Runtime 2026.0.0.23 完成隔离项目保存及 Windows VM 编译运行；普通、小数时长及两种速度单位的实际像素采样通过。

## 0.1.1 — 2026-10-17

- 绑定前统一解析项目路径，修复 Windows 短路径/路径别名被误判为目录联接的问题。
- 新增路径规范化回归测试；CI 保留两种 Python 版本的独立结果。
- 打包日志兼容英文 Windows 的 cp1252 输出编码，避免中文日志导致构建失败。

## 0.1.0 — 2026-10-17

- 首个可安装版本，采用 MIT 许可证。
- 首次公开名称确定为 MORX GMS2 MCP；安装包、命令和服务标识为 `morx-gms2-mcp`。
- 18 个 stdio MCP 工具，支持脚本、物体、事件、房间、PNG 精灵、PCM WAV、GLSL ES 及中文分组。
- 固定项目绑定、默认只读服务、默认预览、备份撤销、冲突检查和跨进程锁。
- 提供 Codex / Claude Code 配置生成、独立验收场景及协议测试。
- 已完成指定 GameMaker 2026 版本的隔离项目编译运行验收；适用边界见 README。
