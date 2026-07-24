from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import traceback
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEALTH_URL = "http://127.0.0.1:8080/health"
LOG_PATH = ROOT / "myclaw.log"
WM_TRAY = 0x8001
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
ID_STATUS, ID_LOG, ID_EXIT = 1001, 1002, 1003
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = wintypes.LPARAM
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]

class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
    ]

WNDPROC = ctypes.WINFUNCTYPE(
    wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]

server: subprocess.Popen | None = None
job_handle = None
nid = NOTIFYICONDATA()

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def healthy() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(HEALTH_URL)
        with opener.open(req, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def get_health_detail() -> tuple[bool, str]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(HEALTH_URL)
        with opener.open(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                ws = "🟢 已连接" if data.get("ws_connected") else "🔴 未连接"
                return True, f"✅ MyClaw 后端服务运行正常 (端口 8080)\n飞书长连接: {ws}"
    except Exception as e:
        return False, f"❌ 后端服务未响应 (8080 端口): {e}\n详情请查看 myclaw.log 日志。"
    return False, "❌ 后端服务未响应，请查看日志。"


def start_server() -> None:
    global server, job_handle
    if healthy():
        return
    env = os.environ.copy()
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(name, None)
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    try:
        job_handle = kernel32.CreateJobObjectW(None, None)
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        kernel32.SetInformationJobObject(
            job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
    except Exception:
        pass
    server = subprocess.Popen(
        [str(python), "-m", "app.main"], cwd=ROOT, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if job_handle:
        try:
            kernel32.AssignProcessToJobObject(job_handle, int(server._handle))
        except Exception:
            pass
    for _ in range(30):
        if healthy():
            return
        if server.poll() is not None:
            break
        time.sleep(0.5)
    raise RuntimeError(f"myclaw failed to start. Check {LOG_PATH}")


def stop_server() -> None:
    global job_handle
    if job_handle:
        kernel32.CloseHandle(job_handle)
        job_handle = None


def message(text: str, title: str = "myclaw") -> None:
    user32.MessageBoxW(None, text, title, 0x40)


def show_menu(hwnd: int) -> None:
    menu = user32.CreatePopupMenu()
    user32.AppendMenuW(menu, 0, ID_STATUS, "查看状态")
    user32.AppendMenuW(menu, 0, ID_LOG, "打开日志")
    user32.AppendMenuW(menu, 0x800, 0, None)
    user32.AppendMenuW(menu, 0, ID_EXIT, "退出 myclaw")
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.SetForegroundWindow(hwnd)
    user32.TrackPopupMenu(menu, 0, point.x, point.y, 0, hwnd, None)
    user32.DestroyMenu(menu)


def window_proc(hwnd, msg, wparam, lparam):
    if msg == WM_TRAY:
        if lparam == WM_RBUTTONUP:
            show_menu(hwnd)
        elif lparam == WM_LBUTTONDBLCLK:
            is_ok, detail = get_health_detail()
            message(detail, "myclaw 状态确认" if is_ok else "myclaw 异常提醒")
        return 0
    if msg == WM_COMMAND:
        command = wparam & 0xFFFF
        if command == ID_STATUS:
            is_ok, detail = get_health_detail()
            message(detail, "myclaw 状态确认" if is_ok else "myclaw 异常提醒")
        elif command == ID_LOG and LOG_PATH.exists():
            os.startfile(LOG_PATH)
        elif command == ID_EXIT:
            user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        stop_server()
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def main() -> None:
    mutex = kernel32.CreateMutexW(None, True, "Global\\MyClawTray")
    if kernel32.GetLastError() == 183:
        if healthy():
            return
        time.sleep(1)
    start_server()
    callback = WNDPROC(window_proc)
    cls = WNDCLASS()
    cls.lpfnWndProc = callback
    cls.lpszClassName = "MyClawTrayWindow"
    cls.hInstance = kernel32.GetModuleHandleW(None)
    user32.RegisterClassW(ctypes.byref(cls))
    hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "myclaw", 0, 0, 0, 0, 0, None, None, cls.hInstance, None)
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd, nid.uID = hwnd, 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = user32.LoadIconW(None, 32512)
    nid.szTip = "myclaw"
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        (ROOT / "myclaw-tray-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        message(str(exc), "myclaw 启动失败")