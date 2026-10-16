# MORX GMS2 MCP

面向 GameMaker 的本地 stdio MCP 服务。让 Codex、Claude Code 等客户端创建并登记游戏资源，支持中文资源树分组、预览、冲突检测和备份撤销。采用 MIT 许可证。

MORX GMS2 MCP provides project-bound GameMaker resource tools over MCP stdio. Python 3.10+, MIT.

项目展示名为 **MORX GMS2 MCP**，仓库、安装包及命令名为 `morx-gms2-mcp`，Python 模块为 `morx_gms2_mcp`。[GitHub 仓库](https://github.com/MKTLMORX/morx-gms2-mcp)。GMS2 是项目名称的一部分，具体兼容范围以本文的实际验收版本为准。

## 安装

在独立 Python 环境中安装下载的 wheel，或进入本仓库执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe -m morx_gms2_mcp --version
```

尚未发布到 PyPI；不要假定同名线上包来自本项目。运行依赖为 MCP Python SDK 1.x 和 Pillow，具体范围见 `pyproject.toml`。

## 绑定项目

先由 GameMaker 创建并保存项目，然后执行（将路径替换为实际路径）：

```powershell
python -m morx_gms2_mcp bind --project "D:\Games\MyGame"
python -m morx_gms2_mcp check --project "D:\Games\MyGame"
python -m morx_gms2_mcp client-config --project "D:\Games\MyGame" --client claude --write
python -m morx_gms2_mcp client-config --project "D:\Games\MyGame" --client codex --write
```

以上 `python` 必须是安装本工具的解释器。`--project` 也接受 `.yyp` 文件路径。绑定文件位于项目 `.codex/gms2-resource-binding.json`，记录项目绝对路径；迁移项目后需人工核对并重新绑定，不要直接复制旧绑定。目录必须恰有一个现代 `GMProject v1` 的 `.yyp`。名为 `参考项目` 的目录及其子目录禁止绑定。

`client-config` 只打印配置，不修改客户端。合并到已有配置，保留其他服务：Codex 的项目 `.codex/config.toml`，或 Claude Code 的项目 `.mcp.json`。Claude Code 对项目配置可能要求信任确认。也可以使用 Claude Code 的本地项目作用域注册：

```powershell
claude mcp add --scope local --transport stdio morx-gms2-mcp -- "D:\Tools\gms2-mcp\.venv\Scripts\python.exe" -m morx_gms2_mcp serve --project "D:\Games\MyGame" --write
claude mcp get morx-gms2-mcp
```

示例配置位于 `examples/`。若 Windows PowerShell 的 `claude.ps1` 包装器吞掉 `--`，请直接调用 Claude Code 的原生 `claude.exe`，或使用生成的 JSON 配置。重新加载客户端后先调用 `server_info` 核对目标。一个服务绑定一个项目；工具调用不能切换根目录。服务不需要 GameMaker 正在运行。

早期内部版本曾名为 `gms2-resource-mcp`；首次公开版本统一采用当前名称。项目中 `.codex/gms2-resource-binding.json`、锁和备份目录保留原有文件名，以兼容已创建的绑定与事务，不需要迁移或删除备份。

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

## 18 个工具

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

默认分组：脚本、物体/object、物体/system（`sys_`）、物体/ui（`ui_`）、房间、精灵、声音、着色器；均可通过参数指定其他分组。

事件支持 Create、Destroy、Step/BeginStep/EndStep、Draw/DrawGUI/DrawBegin/DrawEnd/DrawGUIBegin/DrawGUIEnd、CleanUp、RoomStart/RoomEnd，以及 Alarm 0～11、User 0～15。Alarm/User 通过 `event` 与 `number` 分别指定。

PNG 支持 1～256 帧，每帧尺寸相同、单文件最多 64 MiB、单边最多 8192。WAV 最多 128 MiB，单/双声道，8/16/24 位 PCM。原始导入文件不修改。`configure_object` 中 null 表示保留，sprite/parent 空字符串表示清空。

## 已验证范围与限制

- Windows、Python 3.10.9；MCP SDK 1.11.0 和 1.30.0 的自动测试与真实 stdio 握手通过。
- Steam GameMaker IDE 2026.0.0.16 / Runtime 2026.0.0.23：隔离项目中脚本、物体、房间、中文分组、双帧 PNG、WAV、GLSL ES 均被识别，保存后结构校验通过，Windows VM 编译运行通过。
- 运行画面验证双帧精灵、着色器编译状态、声音资源存在、Alarm 和 DrawGUI。声音示例是静音文件，不代表听觉验证。
- IDE 打开期间新增脚本自动出现；其他资源类型与所有编辑冲突场景的热加载尚未逐一验证。
- 不控制 IDE、不自动编译、不创建新项目；不提供资源删除/重命名、已有事件覆盖、字体/路径/时间线/序列/扩展创建、OGG/MP3 导入。
- 暂不提供已有精灵的逐帧时长/排序/图像编辑，以及项目窗口/全屏/标题等设置接口；整体动画速度和原点目前只在新建精灵时指定。
- 结构校验只覆盖实现支持的关系，不是 GameMaker 官方完整格式验证器。其他 IDE 版本、平台与复杂现有项目需自行验证。

## 开发与发布

```powershell
python -m pip install . build
python -m unittest discover -s tests -t . -v
python -m build
python scripts/build_release.py
```

最后一条命令按白名单生成不含测试项目、缓存、本机配置及游戏素材的 GitHub 源码 ZIP。wheel/sdist 在 `dist/`。`examples/create_demo.py --project <已绑定的空测试项目>` 经真实 MCP 创建可运行验收场景，只应对独立测试项目运行；重复运行会拒绝覆盖。

开发约定见 [CONTRIBUTING.md](CONTRIBUTING.md)，边界见 [SECURITY.md](SECURITY.md)。CI 文件提供 Windows 自动验证流程；本地通过不代表远程 CI 已运行。

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、[Claude Code MCP 配置](https://code.claude.com/docs/en/mcp)。
