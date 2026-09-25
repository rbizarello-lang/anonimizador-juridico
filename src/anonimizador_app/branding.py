"""Identidade visual: icone do atalho, da janela e da barra de tarefas."""

from __future__ import annotations

import io
import os
import struct
import threading
import time
from pathlib import Path

from PIL import Image

ICON_NAME = "anonimizador.ico"
PNG_NAME = "anonimizador.png"
ICON_ENV = "ANONIMIZADOR_ICON"
APP_ID_ENV = "ANONIMIZADOR_APP_ID"
APP_ID_VALUE = "Roger.AnonimizadorJuridico"


def recursos_dir(project_root: Path) -> Path:
    return project_root / "recursos"


def icon_path(project_root: Path) -> Path:
    return recursos_dir(project_root) / ICON_NAME


def png_path(project_root: Path) -> Path:
    return recursos_dir(project_root) / PNG_NAME


def ensure_icon(project_root: Path) -> Path | None:
    """Garante o .ico multi-tamanho a partir da arte PNG."""

    png = png_path(project_root)
    ico = icon_path(project_root)
    if not png.is_file():
        return ico if ico.is_file() else None
    if ico.is_file() and ico.stat().st_mtime >= png.stat().st_mtime and ico.stat().st_size > 10_000:
        return ico
    write_ico(png, ico)
    return ico if ico.is_file() else None


def ico_image_count(path: Path) -> int:
    header = path.read_bytes()[:6]
    if len(header) < 6:
        return 0
    _reserved, kind, count = struct.unpack("<HHH", header)
    if kind != 1:
        return 0
    return int(count)


def write_ico(png: Path, ico: Path) -> None:
    source = Image.open(png).convert("RGBA")
    width, height = source.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    square = source.crop((left, top, left + side, top + side))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    blobs: list[bytes] = []
    for size in sizes:
        resized = square.resize((size, size), Image.Resampling.LANCZOS)
        if size < 256:
            blobs.append(_bitmap_ico_image(resized))
        else:
            buf = io.BytesIO()
            resized.save(buf, format="PNG")
            blobs.append(buf.getvalue())
    offset = 6 + 16 * len(sizes)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = bytearray()
    payload = bytearray()
    for size, blob in zip(sizes, blobs, strict=True):
        width_byte = 0 if size >= 256 else size
        bitcount = 32
        entries += struct.pack(
            "<BBBBHHII",
            width_byte,
            width_byte,
            0,
            0,
            1,
            bitcount,
            len(blob),
            offset,
        )
        payload += blob
        offset += len(blob)
    ico.parent.mkdir(parents=True, exist_ok=True)
    ico.write_bytes(header + bytes(entries) + bytes(payload))


def install_native_icon_hook(icon: Path) -> None:
    """A janela nativa nasce noutro processo; o gancho corre la, nao aqui."""

    os.environ[ICON_ENV] = str(icon)
    os.environ[APP_ID_ENV] = APP_ID_VALUE
    try:
        from nicegui.native import native_mode
    except Exception:
        return
    if getattr(native_mode, "_aj_original_open_window", None) is None:
        native_mode._aj_original_open_window = native_mode._open_window
    native_mode._open_window = open_native_window


def open_native_window(
    host: str,
    port: int,
    title: str,
    width: int,
    height: int,
    fullscreen: bool,
    frameless: bool,
    method_queue: object,
    response_queue: object,
) -> None:
    """Alvo do multiprocessing: aplica o icone antes do pywebview criar a janela."""

    _set_process_app_id()
    _patch_winforms_icon()
    from nicegui.native import native_mode

    original = getattr(native_mode, "_aj_original_open_window", None)
    if original is None or original is open_native_window:
        original = native_mode._open_window
    if original is open_native_window:
        return
    original(
        host,
        port,
        title,
        width,
        height,
        fullscreen,
        frameless,
        method_queue,
        response_queue,
    )


def apply_taskbar_icon(project_root: Path, *, title: str) -> None:
    """Reforco: se o gancho nativo falhar, ainda tenta o HWND da janela."""

    ico = ensure_icon(project_root)
    if ico is None:
        return
    thread = threading.Thread(
        target=_set_icon_when_window_exists,
        args=(str(ico), title),
        daemon=True,
    )
    thread.start()


def _bitmap_ico_image(image: Image.Image) -> bytes:
    """DIB 32 bits + mascara AND. O WinForms/Explorer leem isto melhor que PNG em 16-48px."""

    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.tobytes()
    header = struct.pack("<IIIHHIIIIII", 40, width, height * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(height - 1, -1, -1):
        start = y * width * 4
        row = pixels[start : start + width * 4]
        for i in range(0, len(row), 4):
            red, green, blue, alpha = row[i : i + 4]
            xor += bytes((blue, green, red, alpha))
    and_stride = ((width + 31) // 32) * 4
    mask = bytearray()
    for y in range(height - 1, -1, -1):
        bits = 0
        count = 0
        packed = bytearray()
        for x in range(width):
            alpha = pixels[(y * width + x) * 4 + 3]
            bits = (bits << 1) | (0 if alpha >= 128 else 1)
            count += 1
            if count == 8:
                packed.append(bits)
                bits = 0
                count = 0
        if count:
            packed.append(bits << (8 - count))
        packed.extend(b"\x00" * (and_stride - len(packed)))
        mask += packed
    return header + bytes(xor) + bytes(mask)


def _set_process_app_id() -> None:
    if os.name != "nt":
        return
    app_id = os.environ.get(APP_ID_ENV, APP_ID_VALUE)
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        return


def _patch_winforms_icon() -> None:
    icon = os.environ.get(ICON_ENV)
    if not icon or not Path(icon).is_file():
        return
    try:
        import webview.platforms.winforms as winforms
    except Exception:
        return
    form_cls = winforms.BrowserView.BrowserForm
    if getattr(form_cls, "_aj_icon_patched", False):
        return
    original_init = form_cls.__init__

    def patched_init(self, window, cache_dir):  # noqa: ANN001
        original_init(self, window, cache_dir)
        _assign_form_icon(self, icon)

    form_cls.__init__ = patched_init  # type: ignore[method-assign]
    form_cls._aj_icon_patched = True


def _assign_form_icon(form: object, icon: str) -> None:
    try:
        from System.Drawing import Icon as WinIcon
    except Exception:
        return
    try:
        loaded = WinIcon(icon)
        form._aj_icon = loaded  # type: ignore[attr-defined]
        form.Icon = loaded  # type: ignore[attr-defined]
    except Exception:
        return


def _set_icon_when_window_exists(ico: str, title: str) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _each(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        text = buffer.value or ""
        if text == title or text.startswith(title):
            found.append(int(hwnd))
        return True

    for _ in range(80):
        found.clear()
        user32.EnumWindows(_each, 0)
        if found:
            for hwnd in found:
                _assign_hwnd_icon(hwnd, ico)
            time.sleep(0.4)
            for hwnd in found:
                _assign_hwnd_icon(hwnd, ico)
            return
        time.sleep(0.15)


def _assign_hwnd_icon(hwnd: int, ico: str) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    load = user32.LoadImageW
    load.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    load.restype = ctypes.c_void_p
    send = user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_void_p
    image_icon = 1
    load_from_file = 0x0010
    wm_seticon = 0x0080
    small = load(None, ico, image_icon, 16, 16, load_from_file)
    big = load(None, ico, image_icon, 32, 32, load_from_file)
    if small:
        send(hwnd, wm_seticon, 0, small)
    if big:
        send(hwnd, wm_seticon, 1, big)
    if hasattr(user32, "SetClassLongPtrW"):
        user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.SetClassLongPtrW.restype = ctypes.c_void_p
        if big:
            user32.SetClassLongPtrW(hwnd, -14, big)
        if small:
            user32.SetClassLongPtrW(hwnd, -34, small)
