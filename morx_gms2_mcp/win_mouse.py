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
    __slots__ = ('handle', 'pid', 'title', 'width', 'height')

    def __init__(self, handle, pid, title, width, height):
        self.handle, self.pid, self.title = handle, pid, title
        self.width, self.height = width, height


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
    return Window(handle, pid, title, width, height)


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


def _post(user32, handle, message, wparam=0, lparam=0):
    if not user32.PostMessageW(handle, message, wparam, lparam):
        code = ctypes.get_last_error()
        detail = f'（WinError {code}）' if code else ''
        raise ValueError('无法向游戏窗口投递鼠标消息' + detail)


def _lparam(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def send_mouse_message(window, action, x=None, y=None, button='left', delta=0, hold_ms=50):
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
    _post(user32, window.handle, WM_MOUSEMOVE, 0, _lparam(x, y))
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
    _post(user32, window.handle, down, flag, _lparam(x, y))
    try:
        time.sleep(hold_ms / 1000)
    finally:
        _post(user32, window.handle, up, 0, _lparam(x, y))
    return {'screen_x': screen.x, 'screen_y': screen.y}
