"""项目显示元数据的限定字段；窗口、房间、相机和视口分别处理。"""

WINDOWS_PATH = 'options/windows/options_windows.yy'
WINDOWS_FLAGS = {
    'start_fullscreen': 'option_windows_start_fullscreen',
    'borderless': 'option_windows_borderless',
    'resizable': 'option_windows_resize_window',
    'allow_fullscreen_switching': 'option_windows_allow_fullscreen_switching',
    'display_cursor': 'option_windows_display_cursor',
    'vsync': 'option_windows_vsync',
    'interpolate_pixels': 'option_windows_interpolate_pixels',
}
VIEW_FIELDS = {'camera_x': 'xview', 'camera_y': 'yview',
    'camera_width': 'wview', 'camera_height': 'hview',
    'viewport_x': 'xport', 'viewport_y': 'yport',
    'viewport_width': 'wport', 'viewport_height': 'hport'}


def integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label}必须为 {minimum}～{maximum} 的整数')


def boolean(value, label):
    if type(value) is not bool:
        raise ValueError(label + '必须为布尔值')


def title_check(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 255
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)):
        raise ValueError('显示名称须为 1～255 个字符，不能为空白或包含控制字符')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError('显示名称包含无效 Unicode 字符') from exc


def windows_settings(data):
    try:
        if data.get('resourceType') != 'GMWindowsOptions' or data.get('$GMWindowsOptions') != 'v2':
            raise ValueError('仅支持 GMWindowsOptions v2；不创建或迁移平台配置')
        title_check(data['option_windows_display_name'])
        for name, field in WINDOWS_FLAGS.items():
            boolean(data[field], name)
        return {'title': data['option_windows_display_name'],
                **{name: data[field] for name, field in WINDOWS_FLAGS.items()}}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Windows 显示配置字段不完整，拒绝修改') from exc


def room_settings(data):
    try:
        if data.get('resourceType') != 'GMRoom' or data.get('$GMRoom') != 'v1':
            raise ValueError('仅支持 GMRoom v1 显示配置')
        room, settings, views = data['roomSettings'], data['viewSettings'], data['views']
        integer(room['Width'], '房间宽度', 1, 32768)
        integer(room['Height'], '房间高度', 1, 32768)
        for field in ('enableViews', 'inheritViewSettings'):
            boolean(settings[field], field)
        boolean(room['inheritRoomSettings'], 'inheritRoomSettings')
        if not isinstance(views, list) or len(views) != 8:
            raise ValueError('房间必须包含原生的 8 个视口，不自动补建')
        for view in views:
            boolean(view['visible'], '视口可见性')
            boolean(view['inherit'], '视口继承')
            for name, field in VIEW_FIELDS.items():
                if name in ('camera_x', 'camera_y'):
                    minimum, maximum = -(2**31), 2**31 - 1
                else:
                    minimum, maximum = 0, 32768
                integer(view[field], name, minimum, maximum)
            if settings['enableViews'] and view['visible']:
                for field in ('wview', 'hview', 'wport', 'hport'):
                    integer(view[field], field, 1, 32768)
                if view['xport'] + view['wport'] > 32768 or view['yport'] + view['hport'] > 32768:
                    raise ValueError('可见视口的右边界或下边界超出 32768')
        return {'room_width': room['Width'], 'room_height': room['Height'],
            'parent_room': data.get('parentRoom'),
            'inherit_room_settings': room['inheritRoomSettings'],
            'inherit_view_settings': settings['inheritViewSettings'],
            'enable_views': settings['enableViews'],
            'views': [{'index': index, 'visible': view['visible'], 'inherit': view['inherit'],
                'camera': {'x': view['xview'], 'y': view['yview'], 'width': view['wview'], 'height': view['hview']},
                'viewport': {'x': view['xport'], 'y': view['yport'], 'width': view['wport'], 'height': view['hport']},
                'follow_object': view.get('objectId')} for index, view in enumerate(views)]}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('房间显示配置字段不完整，拒绝修改') from exc


def check_expected(raw, expected_sha256):
    if expected_sha256 is not None:
        import re
        from .storage import digest
        if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
            raise ValueError('expected_sha256 必须是查询返回的描述文件 SHA-256')
        if digest(raw) != expected_sha256:
            raise ValueError('显示配置已被修改，请重新 inspect_display_settings 并预览')
