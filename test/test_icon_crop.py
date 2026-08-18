import ctypes
import os
from ctypes import wintypes
from pathlib import Path

gdiplus = ctypes.windll.gdiplus
user32 = ctypes.windll.user32

class GDIPlusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_int),
        ("SuppressExternalCodecs", ctypes.c_int),
    ]

# GDI+ Quality Constants
InterpolationModeHighQualityBicubic = 7
SmoothingModeAntiAlias = 4
PixelOffsetModeHighQuality = 4

def load_custom_icon_cropped(image_path: str | Path, target_size: int = 32) -> int | None:
    path_str = str(Path(image_path).resolve())
    if not os.path.exists(path_str):
        return None

    token = ctypes.c_ulong()
    input_struct = GDIPlusStartupInput(1, None, 0, 0)
    if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(input_struct), None) != 0:
        return None

    src_bitmap = ctypes.c_void_p()
    if gdiplus.GdipCreateBitmapFromFile(ctypes.c_wchar_p(path_str), ctypes.byref(src_bitmap)) != 0:
        return None

    w = ctypes.c_uint()
    h = ctypes.c_uint()
    gdiplus.GdipGetImageWidth(src_bitmap, ctypes.byref(w))
    gdiplus.GdipGetImageHeight(src_bitmap, ctypes.byref(h))
    orig_w, orig_h = w.value, h.value

    # Compute center crop square
    crop_size = min(orig_w, orig_h)
    src_x = (orig_w - crop_size) // 2
    src_y = (orig_h - crop_size) // 2

    # Get system Small Icon metrics for DPI scaling
    icon_w = user32.GetSystemMetrics(49) or target_size  # SM_CXSMICON
    icon_h = user32.GetSystemMetrics(50) or target_size  # SM_CYSMICON
    icon_size = max(icon_w, icon_h, target_size)

    # Create target high-DPI square bitmap (Format32bppArgb = 0x26200A)
    dst_bitmap = ctypes.c_void_p()
    if gdiplus.GdipCreateBitmapFromScan0(icon_size, icon_size, 0, 0x26200A, None, ctypes.byref(dst_bitmap)) != 0:
        gdiplus.GdipDisposeImage(src_bitmap)
        return None

    graphics = ctypes.c_void_p()
    gdiplus.GdipGetImageGraphicsContext(dst_bitmap, ctypes.byref(graphics))

    # Set high quality render modes
    gdiplus.GdipSetInterpolationMode(graphics, InterpolationModeHighQualityBicubic)
    gdiplus.GdipSetSmoothingMode(graphics, SmoothingModeAntiAlias)
    gdiplus.GdipSetPixelOffsetMode(graphics, PixelOffsetModeHighQuality)

    # Draw centered cropped square to dst_bitmap
    # GdipDrawImageRectRect(graphics, image, dstx, dsty, dstw, dsth, srcx, srcy, srcw, srch, unit=2, attrs=0, cb=0, data=0)
    gdiplus.GdipDrawImageRectRectI(
        graphics, src_bitmap,
        0, 0, icon_size, icon_size,
        src_x, src_y, crop_size, crop_size,
        2, None, None, None
    )

    hicon = ctypes.c_void_p()
    res = gdiplus.GdipCreateHICONFromBitmap(dst_bitmap, ctypes.byref(hicon))

    # Clean up GDI+ resources
    gdiplus.GdipDeleteGraphics(graphics)
    gdiplus.GdipDisposeImage(dst_bitmap)
    gdiplus.GdipDisposeImage(src_bitmap)

    if res == 0 and hicon.value:
        return hicon.value
    return None

if __name__ == "__main__":
    h = load_custom_icon_cropped("icon.png")
    print("Successfully created high-quality cropped HICON:", h)
