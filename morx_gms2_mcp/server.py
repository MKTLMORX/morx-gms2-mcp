"""标准 stdio MCP 服务。stdout 只由 SDK 输出协议消息。"""
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from . import __version__
from .resources import Project, EVENTS


def build_server(project_root: Path, writable=False):
    project = Project(project_root, writable=writable)
    mcp = FastMCP('morx-gms2-mcp', instructions=(
        '先调用 server_info 确认绑定项目。修改工具默认 dry_run=true，仅预览；'
        '执行时显式传 dry_run=false。操作前保存 IDE 内容，避免与 IDE 保存并发。'
        '工具不操控界面，不编译；不得把校验成功当作 IDE 或编译成功。'
        '既有 GML 由客户端文件编辑工具按项目编码约定修改。'))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    add = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    change = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @mcp.tool(annotations=read)
    def server_info() -> dict:
        """查询服务版本、固定目标、写入模式、能力和局限。"""
        return {'version': __version__, 'project': str(project.yyp), 'writable': project.writable,
            'transport': 'stdio', 'events': list(EVENTS) + ['Alarm (0..11)', 'User (0..15)'],
            'resources': ['script', 'object', 'room', 'PNG sprite', 'PCM WAV sound', 'GLSL ES shader'],
            'limitations': ['不执行 IDE 操作或编译', '不支持删除或重命名资源', '不覆盖已有 GML 或事件',
                '不创建字体、路径、时间线、序列或扩展资源', '多个文件整体非操作系统级原子事务'],
            'verified_ide': '2026.0.0.16', 'verified_runtime': '2026.0.0.23'}

    @mcp.tool(annotations=read)
    def inspect_project() -> dict:
        """检查资源、分组、房间顺序、精灵帧及引用；不表示编译通过。"""
        return project.validate()

    @mcp.tool(annotations=read)
    def inspect_resource(name: str) -> dict:
        """读取资源描述和代码文件哈希，供核对目标与外部编辑状态。"""
        return project.inspect_resource(name)

    @mcp.tool(annotations=add)
    def ensure_group(group: str, dry_run: bool = True) -> dict:
        """按需建立虚拟分组及父组，例如 脚本/battle。已有同名分组不重复。"""
        return project.ensure_group(group, dry_run)

    @mcp.tool(annotations=add)
    def create_script(name: str, code: str = '', group: str = '脚本', dry_run: bool = True) -> dict:
        """创建并注册脚本；同名拒绝覆盖，保存前保留备份。"""
        return project.create('scripts', name, code, group, dry_run)

    @mcp.tool(annotations=add)
    def create_object(name: str, group: str | None = None, dry_run: bool = True) -> dict:
        """创建物体；默认按 sys_、ui_、其他名称分入 system、ui、object 子组。"""
        return project.create('objects', name, group=group, dry_run=dry_run)

    @mcp.tool(annotations=add)
    def add_object_event(object_name: str, event: str, code: str = '', number: int = 0, dry_run: bool = True) -> dict:
        """新增事件，不覆盖；常用命名见 server_info，Alarm/User 使用 number。"""
        return project.add_event(object_name, event, code, number, dry_run)

    @mcp.tool(annotations=change)
    def configure_object(name: str, persistent: bool | None = None, visible: bool | None = None,
                         sprite: str | None = None, parent: str | None = None, dry_run: bool = True) -> dict:
        """调整常驻、可见、精灵和父物体。null 保留，sprite/parent 空字符串清空。拒绝继承循环。"""
        return project.configure_object(name, persistent, visible, sprite, parent, dry_run)

    @mcp.tool(annotations=change)
    def move_resource(name: str, group: str, dry_run: bool = True) -> dict:
        """调整已有资源的 IDE 虚拟分组；磁盘资源目录保持原生结构。"""
        return project.move_resource(name, group, dry_run)

    @mcp.tool(annotations=add)
    def create_room(name: str, width: int = 1366, height: int = 768, group: str = '房间', dry_run: bool = True) -> dict:
        """创建带 Instances 图层的空房间，追加到房间顺序末尾。"""
        return project.create_room(name, width, height, group, dry_run)

    @mcp.tool(annotations=add)
    def add_instance_layer(room_name: str, layer_name: str, depth: int = 0, dry_run: bool = True) -> dict:
        """为已有房间增加实例图层，拒绝同名图层。"""
        return project.add_layer(room_name, layer_name, depth, dry_run)

    @mcp.tool(annotations=add)
    def place_object(object_name: str, room_name: str, layer_name: str, instance_name: str,
                     x: float = 0, y: float = 0, dry_run: bool = True) -> dict:
        """放置物体并登记创建顺序。支持嵌套图层，拒绝同名实例。"""
        return project.place(object_name, room_name, layer_name, instance_name, x, y, dry_run)

    @mcp.tool(annotations=change)
    def set_start_room(room_name: str, dry_run: bool = True) -> dict:
        """将已有房间移至启动顺序首位，保留其余房间的相对顺序。"""
        return project.set_start_room(room_name, dry_run)

    @mcp.tool(annotations=add)
    def import_sprite(name: str, png_paths: list[str], group: str = '精灵', speed: float = 10.0,
                      origin_x: int = 0, origin_y: int = 0, dry_run: bool = True) -> dict:
        """从明确给出的本地 PNG 路径复制等尺寸帧，建立可编辑的帧/图层和动画。原图不修改。"""
        return project.import_sprite(name, png_paths, group, speed, origin_x, origin_y, dry_run)

    @mcp.tool(annotations=add)
    def import_sound(name: str, wav_path: str, group: str = '声音', volume: float = 1.0, dry_run: bool = True) -> dict:
        """从明确给出的本地 PCM WAV 路径导入音效；验证声道、位深和数据完整性。"""
        return project.import_sound(name, wav_path, group, volume, dry_run)

    @mcp.tool(annotations=add)
    def create_shader(name: str, vertex_code: str, fragment_code: str,
                      group: str = '着色器', dry_run: bool = True) -> dict:
        """创建 GLSL ES 着色器及顶点、片段源文件，拒绝同名资源。"""
        return project.create_shader(name, vertex_code, fragment_code, group, dry_run)

    @mcp.tool(annotations=read)
    def list_backups() -> dict:
        """按时间列出备份事务和状态；恢复须从最新未撤销事务开始。"""
        with project.lock:
            return {'project': str(project.yyp), 'transactions': project.transactions()}

    @mcp.tool(annotations=change)
    def restore_backup(transaction_id: str, dry_run: bool = True) -> dict:
        """预览或撤销最新事务；会移除该事务新建的文件，拒绝覆盖后来发生的外部修改。"""
        return project.restore(transaction_id, dry_run)

    return mcp
