"""向受管游戏窗口投递键盘消息；不改变系统键盘状态，也不需要前台焦点。"""
import ctypes
from ctypes import wintypes
import os

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
                 0x2D, 0x2E, 0x5B, 0x5C, 0x90}


def _user32():
    if os.name != 'nt':
        raise ValueError('键盘消息仅支持 Windows')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    return user32


def _lparam(user32, code, up=False):
    scan = user32.MapVirtualKeyW(code, 0) & 0xFF
    value = 1 | (scan << 16)
    if code in EXTENDED_KEYS:
        value |= 0x01000000
    if up:
        value |= 0xC0000000
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def _post(user32, handle, message, code):
    if not user32.PostMessageW(handle, message, code, _lparam(user32, code, message == WM_KEYUP)):
        error = ctypes.get_last_error()
        detail = f'（WinError {error}）' if error else ''
        raise ValueError('无法向游戏窗口投递键盘消息' + detail)


def key_down(window, codes):
    """按顺序投递 WM_KEYDOWN；中途失败时释放已按下的键。"""
    user32 = _user32()
    sent = []
    try:
        for code in codes:
            _post(user32, window.handle, WM_KEYDOWN, code)
            sent.append(code)
    except Exception:
        for code in sent:
            try:
                _post(user32, window.handle, WM_KEYUP, code)
            except Exception:
                pass
        raise


def key_up(window, codes):
    """按相反顺序投递 WM_KEYUP。"""
    user32 = _user32()
    error = None
    for code in reversed(codes):
        try:
            _post(user32, window.handle, WM_KEYUP, code)
        except Exception as exc:
            error = error or exc
    if error:
        raise error
