import ctypes
import os
from ctypes import wintypes
from pathlib import Path

gdiplus = ctypes.windll.gdiplus

class GDIPlusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_int),
        ("SuppressExternalCodecs", ctypes.c_int),
    ]

def load_hicon_from_image(image_path: str | Path) -> int | None:
    path_str = str(Path(image_path).resolve())
    if not os.path.exists(path_str):
        return None

    token = ctypes.c_ulong()
    input_struct = GDIPlusStartupInput(1, None, 0, 0)
    if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(input_struct), None) != 0:
        return None

    bitmap = ctypes.c_void_p()
    hicon = ctypes.c_void_p()
    try:
        if gdiplus.GdipCreateBitmapFromFile(ctypes.c_wchar_p(path_str), ctypes.byref(bitmap)) == 0:
            if gdiplus.GdipCreateHICONFromBitmap(bitmap, ctypes.byref(hicon)) == 0:
                return hicon.value
            gdiplus.GdipDisposeImage(bitmap)
    finally:
        pass
    return None

if __name__ == "__main__":
    h = load_hicon_from_image("icon.jpg")
    print("Loaded hicon from icon.jpg:", h)
