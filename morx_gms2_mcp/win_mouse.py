"""受控游戏窗口鼠标消息；默认只向本次运行的窗口投递消息，不移动真实光标。"""
import ctypes
from ctypes import wintypes
import os
import time

MOUSE_BUTTONS = {'left': (0x0002, 0x0004), 'right': (0x0008, 0x0010),
                 'middle': (0x0020, 0x0040)}
MOUSEEVENTF_WHEEL = 0x0800
TH32CS_SNAPPROCESS = 0x00000002
SW_RESTORE = 9
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL = 0x020A
BUTTON_MESSAGES = {'left': (WM_LBUTTONDOWN, WM_LBUTTONUP, 0x0001),
                   'right': (WM_RBUTTONDOWN, WM_RBUTTONUP, 0x0002),
                   'middle': (WM_MBUTTONDOWN, WM_MBUTTONUP, 0x0010)}


class Window:
    __slots__ = ('handle', 'pid', 'title', 'width', 'height', 'physical_width', 'physical_height')

    def __init__(self, handle, pid, title, width, height):
        self.handle, self.pid, self.title = handle, pid, title
        self.width, self.height = width, height
        self.physical_width = self.physical_height = None


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]


def _windows():
    if os.name != 'nt':
        raise ValueError('鼠标输入仅支持 Windows')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
                                   wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return user32, kernel32


def _processes(kernel32):
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    result = {}
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            result[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def find_game_window(root_pid, run_name):
    """按进程树/可执行文件名和窗口标题定位本次运行的顶层窗口。"""
    user32, kernel32 = _windows()
    processes = _processes(kernel32)
    related = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in processes.items():
            if pid not in related and parent in related:
                related.add(pid)
                changed = True
    executable = (run_name + '.exe').casefold()
    for pid, (_, name) in processes.items():
        if name.casefold() == executable:
            related.add(pid)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in related or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return True
        title_match = 0 if run_name.casefold() in title.value.casefold() else 1
        found.append((title_match, -(width * height), int(hwnd), pid.value,
                      title.value, width, height))
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        raise ValueError('未找到本次运行的可见游戏窗口；确认游戏已启动且未最小化')
    _, _, handle, pid, title, width, height = sorted(found)[0]
    window = Window(handle, pid, title, width, height)
    # 只改变当前工作线程的 DPI 上下文，查询后立即恢复；不改变客户端进程的 DPI 设置。
    if hasattr(user32, 'SetThreadDpiAwarenessContext'):
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if previous:
            try:
                rect = wintypes.RECT()
                if user32.GetClientRect(handle, ctypes.byref(rect)):
                    window.physical_width, window.physical_height = rect.right, rect.bottom
            finally:
                user32.SetThreadDpiAwarenessContext(previous)
    return window


def coordinate_metrics(window, engine=None):
    """client 是 MCP 调用线程看到的 Win32 客户区；physical 独立查询，不能假定固定 DPI。"""
    engine = engine or {}
    sizes = {'client': {'width': window.width, 'height': window.height}}
    for space, width, height in (
            ('physical', getattr(window, 'physical_width', None), getattr(window, 'physical_height', None)),
            ('window', engine.get('window_width'), engine.get('window_height')),
            ('gui', engine.get('gui_width'), engine.get('gui_height'))):
        if type(width) in (int, float) and type(height) in (int, float) and width > 0 and height > 0:
            sizes[space] = {'width': width, 'height': height,
                            'to_client_x': window.width / width, 'to_client_y': window.height / height}
    return {'spaces': sizes, 'default_space': 'client',
            'logical_client': sizes['client'],
            'gui_mapping': 'proportional_full_client; custom GUI offsets require client coordinates'}


def convert_point(x, y, space, metrics):
    if space not in metrics['spaces']:
        raise ValueError('当前无法取得该坐标空间的尺寸：' + space)
    size = metrics['spaces'][space]
    if not (0 <= x < size['width'] and 0 <= y < size['height']):
        raise ValueError(f"坐标超出 {space} 范围 {size['width']}×{size['height']}")
    client = metrics['spaces']['client']
    return (min(client['width'] - 1, int(x * client['width'] / size['width'])),
            min(client['height'] - 1, int(y * client['height'] / size['height'])))


def _focus(user32, kernel32, handle):
    user32.ShowWindow(handle, SW_RESTORE)
    if user32.GetForegroundWindow() == handle:
        return
    user32.SetForegroundWindow(handle)
    time.sleep(0.15)
    if user32.GetForegroundWindow() == handle:
        return
    foreground = user32.GetForegroundWindow()
    current = kernel32.GetCurrentThreadId()
    attached = []
    for thread in (user32.GetWindowThreadProcessId(foreground, None) if foreground else 0,
                   user32.GetWindowThreadProcessId(handle, None)):
        if thread and thread != current and user32.AttachThreadInput(current, thread, True):
            attached.append(thread)
    try:
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
        time.sleep(0.1)
    finally:
        for thread in attached:
            user32.AttachThreadInput(current, thread, False)
    if user32.GetForegroundWindow() != handle:
        raise ValueError('游戏窗口必须在前台；请先切换到游戏窗口再重试')


def send_mouse(window, action, x=None, y=None, button='left', delta=0, hold_ms=50):
    """把真实光标移到窗口客户区坐标并注入点击/滚轮；失焦时失败。"""
    user32, kernel32 = _windows()
    _focus(user32, kernel32, window.handle)
    point = wintypes.POINT()
    if x is not None and y is not None:
        point.x, point.y = x, y
        if not user32.ClientToScreen(window.handle, ctypes.byref(point)):
            raise ValueError('无法换算游戏窗口客户区坐标')
        if user32.GetForegroundWindow() != window.handle:
            raise ValueError('换算坐标前游戏窗口失去焦点')
        if not user32.SetCursorPos(point.x, point.y):
            raise ValueError('无法移动鼠标光标')
        time.sleep(0.03)
    if action == 'move':
        return {'screen_x': point.x, 'screen_y': point.y}
    if action == 'wheel':
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta * 120 & 0xFFFFFFFF, None)
        return {'screen_x': point.x, 'screen_y': point.y}
    down, up = MOUSE_BUTTONS[button]
    user32.mouse_event(down, 0, 0, 0, None)
    try:
        time.sleep(hold_ms / 1000)
    finally:
        user32.mouse_event(up, 0, 0, 0, None)
    if user32.GetForegroundWindow() != window.handle:
        raise ValueError('鼠标按下期间游戏窗口失去焦点；按键已释放')
    return {'screen_x': point.x, 'screen_y': point.y}


def _post(user32, handle, message, wparam=0, lparam=0, on_dispatch=None):
    if not user32.PostMessageW(handle, message, wparam, lparam):
        code = ctypes.get_last_error()
        detail = f'（WinError {code}）' if code else ''
        raise ValueError('无法向游戏窗口投递鼠标消息' + detail)
    if on_dispatch is not None:
        on_dispatch(message, wparam, lparam)


def _lparam(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def send_mouse_message(window, action, x=None, y=None, button='left', delta=0, hold_ms=50,
                       on_dispatch=None):
    """向游戏窗口投递 WM_MOUSE* 消息；不移动真实光标、不切换前台窗口。
    x/y 为窗口客户区像素；滚轮省略坐标时使用客户区中心。
    """
    user32, _ = _windows()
    if action == 'wheel' and (x is None or y is None):
        x, y = window.width // 2, window.height // 2
    if action in {'move', 'click', 'wheel'} and (x is None or y is None):
        raise ValueError('鼠标消息动作需要客户区坐标')
    if not (0 <= x < window.width and 0 <= y < window.height):
        raise ValueError('坐标超出游戏窗口客户区')
    screen = wintypes.POINT(x, y)
    if not user32.ClientToScreen(window.handle, ctypes.byref(screen)):
        raise ValueError('无法换算游戏窗口客户区坐标')
    _post(user32, window.handle, WM_MOUSEMOVE, 0, _lparam(x, y), on_dispatch)
    if action == 'move':
        return {'screen_x': screen.x, 'screen_y': screen.y}
    if action == 'wheel':
        if not -5 <= delta <= 5 or delta == 0:
            raise ValueError('滚轮 delta 须为 -5～5 的非零整数')
        wparam = ((delta * 120) & 0xFFFF) << 16
        _post(user32, window.handle, WM_MOUSEWHEEL, wparam,
              _lparam(screen.x, screen.y))
        return {'screen_x': screen.x, 'screen_y': screen.y}
    down, up, flag = BUTTON_MESSAGES[button]
    _post(user32, window.handle, down, flag, _lparam(x, y), on_dispatch)
    try:
        time.sleep(hold_ms / 1000)
    finally:
        _post(user32, window.handle, up, 0, _lparam(x, y), on_dispatch)
    return {'screen_x': screen.x, 'screen_y': screen.y}


def drag_message(window, x, y, end_x, end_y, button='left', duration_ms=500, steps=20,
                 on_dispatch=None):
    """一次完整拖动；每个移动消息带按住标记，异常时仍尝试释放。"""
    if not (type(steps) is int and 2 <= steps <= 60 and type(duration_ms) is int and 50 <= duration_ms <= 3000):
        raise ValueError('拖动须为 2～60 步、50～3000 毫秒')
    if button not in BUTTON_MESSAGES or any(not (0 <= a < window.width and 0 <= b < window.height)
                                           for a, b in ((x, y), (end_x, end_y))):
        raise ValueError('拖动按钮或客户区坐标无效')
    user32, _ = _windows()
    down, up, flag = BUTTON_MESSAGES[button]
    _post(user32, window.handle, WM_MOUSEMOVE, 0, _lparam(x, y), on_dispatch)
    release_x, release_y = x, y
    _post(user32, window.handle, down, flag, _lparam(x, y), on_dispatch)
    try:
        started = time.monotonic()
        for index in range(1, steps + 1):
            # Windows 短时 sleep 可能向上取整；按绝对期限追赶，避免逐步累计误差。
            delay = started + duration_ms * index / steps / 1000 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            release_x = round(x + (end_x - x) * index / steps)
            release_y = round(y + (end_y - y) * index / steps)
            _post(user32, window.handle, WM_MOUSEMOVE, flag, _lparam(release_x, release_y), on_dispatch)
    finally:
        _post(user32, window.handle, up, 0, _lparam(release_x, release_y), on_dispatch)
    return {'client_x': end_x, 'client_y': end_y}
