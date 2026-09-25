"""Atalho de area de trabalho e modo janela (app local, nao aba generica)."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

from .branding import ensure_icon

APP_NAME = "Anonimizador Juridico"
APP_ID = "Roger.AnonimizadorJuridico"
APP_HOST = "127.0.0.1"
APP_PORT = 8765
APP_URL = f"http://{APP_HOST}:{APP_PORT}"
MUTEX_NAME = "Local\\Roger.AnonimizadorJuridico"

_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESHOWWINDOW = 0x00000001
_HANDLE_FLAG_INHERIT = 0x00000001
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_MB_SETFOREGROUND = 0x00010000

_mutex_handle = None
_bootstrapped = False


def wants_browser() -> bool:
    args = {item.casefold() for item in sys.argv[1:]}
    return bool(args & {"--navegador", "--browser", "-b"})


def wants_takeover() -> bool:
    args = {item.casefold() for item in sys.argv[1:]}
    return bool(args & {"--forcar", "--force", "-f"})


def is_worker_process() -> bool:
    """O processo da janela nativa (spawn do NiceGUI) nao e o atalho do usuario."""

    if multiprocessing.current_process().name != "MainProcess":
        return True
    joined = " ".join(sys.argv).casefold()
    return "--multiprocessing-fork" in joined or "multiprocessing.spawn" in joined


def launch_plan(
    *,
    worker: bool,
    has_window: bool,
    port_busy: bool,
    owns_mutex: bool,
    force: bool,
) -> str:
    """Decisao pura: continue | focus | wait_focus | reclaim | start."""

    if worker:
        return "continue"
    if force:
        return "reclaim"
    if has_window and port_busy:
        return "focus"
    if not owns_mutex:
        return "wait_focus"
    if port_busy or has_window:
        return "reclaim"
    return "start"


def prepare_process(project_root: Path) -> None:
    """Compat: mesmo que bootstrap_instance (atalho, iniciar.bat, run_app)."""

    bootstrap_instance(project_root)


def bootstrap_instance(project_root: Path) -> None:
    """Antes da UI: foca a janela existente ou libera a porta, sem dialogo e sem flash."""

    global _bootstrapped
    if _bootstrapped or is_worker_process():
        return
    _set_app_id()
    _ensure_pythonpath(project_root)
    _ensure_stdio(project_root)
    _install_excepthook(project_root)
    log_event(project_root, "bootstrap")

    force = wants_takeover()
    owns_mutex = _try_mutex()
    plan = launch_plan(
        worker=False,
        has_window=window_is_open(),
        port_busy=is_app_listening(),
        owns_mutex=owns_mutex,
        force=force,
    )
    log_event(project_root, f"plan={plan} mutex={int(owns_mutex)} listening={int(is_app_listening())}")

    if plan == "focus":
        focus_app_window()
        log_event(project_root, "focus existing window")
        raise SystemExit(0)

    if plan == "wait_focus":
        if _wait_and_focus():
            log_event(project_root, "focus after wait")
            raise SystemExit(0)
        if not _try_mutex():
            log_event(project_root, "still starting; not killing")
            _notify(
                "O Anonimizador ainda esta abrindo.\n"
                "Espere a janela aparecer; um segundo clique so a traz para frente."
            )
            raise SystemExit(0)
        log_event(project_root, "previous starter gone; continuing")
        if is_app_listening():
            _reclaim_port(project_root)
            if is_app_listening():
                leftover, _ = _occupants(project_root)
                _notify(
                    "Nao consegui liberar a porta 8765.\n\n"
                    f"{leftover or 'Processo nao identificado.'}\n\n"
                    "No Gerenciador de Tarefas, aba Detalhes, encerre python.exe / pythonw.exe "
                    "e abra de novo."
                )
                raise SystemExit(1)
        _bootstrapped = True
        return

    if plan == "reclaim":
        _reclaim_port(project_root)
        if is_app_listening():
            leftover, _ = _occupants(project_root)
            _notify(
                "Nao consegui liberar a porta 8765.\n\n"
                f"{leftover or 'Processo nao identificado.'}\n\n"
                "No Gerenciador de Tarefas, aba Detalhes, encerre python.exe / pythonw.exe "
                "e abra de novo."
            )
            raise SystemExit(1)
        if not owns_mutex and not _try_mutex():
            _notify(
                "Nao consegui encerrar a copia antiga.\n"
                "Feche python.exe / pythonw.exe no Gerenciador de Tarefas e abra de novo."
            )
            raise SystemExit(1)

    _bootstrapped = True


def is_app_listening() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((APP_HOST, APP_PORT)) == 0


def window_is_open(title: str = APP_NAME) -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.FindWindowW(None, title))
    except (AttributeError, OSError):
        return False


def close_app_window(title: str = APP_NAME) -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value and pid.value != os.getpid():
            _terminate_pid(int(pid.value))
        return True
    except (AttributeError, OSError):
        return False


def focus_app_window(title: str = APP_NAME) -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        return True
    except (AttributeError, OSError):
        return False


def is_our_command_line(command: str, project_root: Path) -> bool:
    compact = command.casefold().replace("/", "\\")
    root = str(project_root).casefold().replace("/", "\\")
    return "app.py" in compact and root in compact


def is_stray_http_server(command: str, port: int = APP_PORT) -> bool:
    compact = " ".join(command.casefold().split())
    return f"http.server {port}" in compact


def occupants_are_stray_http_servers(project_root: Path) -> bool:
    commands = list(_command_lines(_listener_pids(APP_PORT)).values())
    if not commands:
        return False
    return all(
        is_stray_http_server(command) and not is_our_command_line(command, project_root)
        for command in commands
    )


def merge_pids(*groups: list[int]) -> list[int]:
    merged: list[int] = []
    seen: set[int] = set()
    for group in groups:
        for pid in group:
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            merged.append(pid)
    return merged


def stop_stale_instance(project_root: Path | None = None) -> bool:
    """Libera a porta 8765 e encerra copias deste app.py."""

    pids = merge_pids(_listener_pids(APP_PORT), _our_app_pids(project_root))
    killed = False
    for pid in pids:
        if pid <= 0 or pid == os.getpid():
            continue
        if _terminate_pid(pid):
            killed = True
    return killed or not is_app_listening()


def hidden_run_kwargs() -> dict:
    """Flags para subprocesso sem janela do System32 (pythonw / atalho)."""

    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= _STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": _CREATE_NO_WINDOW,
    }


def log_event(project_root: Path, message: str) -> None:
    log_dir = project_root / "saidas"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n"
        with open(log_dir / "app.log", "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return


def write_url_shortcut(destination: Path, icon: Path | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    extra = ""
    if icon and icon.is_file():
        extra = f"IconFile={icon}\nIconIndex=0\n"
    destination.write_text(
        "[InternetShortcut]\n"
        f"URL={APP_URL}\n"
        f"{extra}",
        encoding="utf-8",
    )
    return destination


def write_launcher_vbs(project_root: Path) -> Path:
    path = project_root / "iniciar_app.vbs"
    path.write_text(
        "\n".join(
            [
                "Set fso = CreateObject(\"Scripting.FileSystemObject\")",
                "root = fso.GetParentFolderName(WScript.ScriptFullName)",
                "pythonw = root & \"\\.venv\\Scripts\\pythonw.exe\"",
                "If Not fso.FileExists(pythonw) Then",
                "  pythonw = root & \"\\.venv\\Scripts\\python.exe\"",
                "End If",
                "If Not fso.FileExists(pythonw) Then",
                "  MsgBox \"Nao achei o Python do app (.venv).\", 16, \"Anonimizador Juridico\"",
                "  WScript.Quit 1",
                "End If",
                "Set sh = CreateObject(\"Wscript.Shell\")",
                "sh.CurrentDirectory = root",
                "cmd = Chr(34) & pythonw & Chr(34) & \" \" & Chr(34) & root & \"\\app.py\" & Chr(34)",
                "sh.Run cmd, 0, False",
                "",
            ]
        ),
        encoding="ascii",
    )
    return path


def install_windows_shortcuts(project_root: Path) -> list[Path]:
    """Cria atalho no Desktop, no Menu Iniciar e um .url na pasta do projeto."""

    created: list[Path] = []
    icon = ensure_icon(project_root)
    url_file = write_url_shortcut(project_root / f"{APP_NAME}.url", icon)
    created.append(url_file)
    launcher = write_launcher_vbs(project_root)
    created.append(launcher)
    if os.name != "nt":
        return created

    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home() / "OneDrive" / "Desktop"
    start_menu = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
    )
    targets = []
    if desktop.is_dir():
        targets.append(desktop / f"{APP_NAME}.lnk")
    if start_menu.is_dir():
        targets.append(start_menu / f"{APP_NAME}.lnk")

    for link in targets:
        if link.is_file():
            created.append(link)
            continue
        if _write_lnk(link, launcher, project_root, icon):
            created.append(link)
    _refresh_explorer_icons()
    return created


def _reclaim_port(project_root: Path) -> None:
    occupant, _ = _occupants(project_root)
    log_event(project_root, "reclaim " + " ".join(occupant.split()))
    if window_is_open() and not is_app_listening():
        close_app_window()
    stop_stale_instance(project_root)
    _wait_port_free()
    for _ in range(20):
        if not window_is_open():
            break
        close_app_window()
        time.sleep(0.15)


def _wait_and_focus(*, attempts: int = 240, delay: float = 0.5) -> bool:
    for _ in range(attempts):
        if window_is_open() and is_app_listening() and focus_app_window():
            return True
        time.sleep(delay)
    return window_is_open() and is_app_listening() and focus_app_window()


def _try_mutex() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True
    if _mutex_handle:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not handle:
            return True
        kernel32.SetHandleInformation(handle, _HANDLE_FLAG_INHERIT, 0)
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except (AttributeError, OSError):
        return True


def _set_app_id() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        return


def _ensure_pythonpath(project_root: Path) -> None:
    src = str(project_root / "src")
    current = os.environ.get("PYTHONPATH", "")
    parts = [item for item in current.split(os.pathsep) if item]
    if src not in parts:
        os.environ["PYTHONPATH"] = src + ((os.pathsep + current) if current else "")


def _ensure_stdio(project_root: Path) -> None:
    log_dir = project_root / "saidas"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    if sys.stdout is None or sys.stderr is None:
        handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = handle
        if sys.stderr is None:
            sys.stderr = handle


def _install_excepthook(project_root: Path) -> None:
    previous = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        if exc_type is SystemExit:
            previous(exc_type, exc, tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        log_event(project_root, "crash\n" + detail)
        _notify(
            "O Anonimizador nao abriu.\n\n"
            f"{exc_type.__name__}: {exc}\n\n"
            "Detalhes em saidas\\app.log"
        )
        previous(exc_type, exc, tb)

    sys.excepthook = hook


def _notify(message: str) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x40 | _MB_SETFOREGROUND)
            return
        except (AttributeError, OSError):
            pass
    print(message, file=sys.stderr)


def _wait_port_free(*, attempts: int = 40, delay: float = 0.25) -> bool:
    for _ in range(attempts):
        if not is_app_listening():
            return True
        time.sleep(delay)
    return not is_app_listening()


def _occupants(project_root: Path) -> tuple[str, bool]:
    pids = _listener_pids(APP_PORT)
    commands = _command_lines(pids)
    lines: list[str] = []
    ours = False
    for pid in pids:
        command = commands.get(pid, "")
        if is_our_command_line(command, project_root):
            ours = True
        lines.append(format_occupant(pid, command, project_root))
    return "\n".join(lines), ours


def format_occupant(pid: int, command: str, project_root: Path | None = None) -> str:
    compact = " ".join(command.split())
    if not compact:
        return f"python.exe  (PID {pid})"
    if project_root and is_our_command_line(compact, project_root):
        return f"Anonimizador  (PID {pid})"
    if len(compact) > 140:
        compact = "..." + compact[-137:]
    return f"{compact}  (PID {pid})"


def _listener_pids(port: int) -> list[int]:
    if os.name != "nt":
        return []
    return merge_pids(
        _parse_pid_lines(_netstat_listener_output(port)),
        _http_server_pids(port),
    )


def _http_server_pids(port: int) -> list[int]:
    needle = f"http.server {port}"
    pids: list[int] = []
    for pid, command in _python_command_lines().items():
        if needle in " ".join(command.casefold().split()):
            pids.append(pid)
    return pids


def _our_app_pids(project_root: Path | None) -> list[int]:
    if project_root is None:
        return []
    pids: list[int] = []
    for pid, command in _python_command_lines().items():
        if is_our_command_line(command, project_root):
            pids.append(pid)
    return pids


def _python_command_lines() -> dict[int, str]:
    found: dict[int, str] = {}
    try:
        import win32com.client

        wmi = win32com.client.GetObject("winmgmts:")
        for name in ("python.exe", "pythonw.exe"):
            query = f"SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='{name}'"
            for proc in wmi.ExecQuery(query):
                pid = int(proc.ProcessId)
                found[pid] = proc.CommandLine or ""
    except Exception:
        return found
    return found


def _netstat_listener_output(port: int) -> str:
    stdout = _run_hidden(["netstat", "-ano", "-p", "TCP"])
    pids: list[str] = []
    needle = f":{port}"
    for line in stdout.splitlines():
        compact = " ".join(line.split())
        if needle not in compact or "LISTENING" not in compact.upper():
            continue
        pid = compact.rsplit(" ", 1)[-1]
        if pid.isdigit():
            pids.append(pid)
    return "\n".join(pids)


def _parse_pid_lines(output: str) -> list[int]:
    pids: list[int] = []
    seen: set[int] = set()
    for line in output.splitlines():
        item = line.strip()
        if not item.isdigit():
            continue
        pid = int(item)
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    return pids


def _command_lines(pids: list[int]) -> dict[int, str]:
    if not pids:
        return {}
    known = _python_command_lines()
    return {pid: known.get(pid, "") for pid in pids}


def _command_line(pid: int) -> str:
    return _command_lines([pid]).get(pid, "")


def _run_hidden(args: list[str], *, timeout: float = 8) -> str:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **hidden_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout or ""


def _terminate_pid(pid: int) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            **hidden_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return not _pid_exists(pid)
    return completed.returncode == 0 or not _pid_exists(pid)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
    except (AttributeError, OSError):
        return True
    return False


def _refresh_explorer_icons() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except (AttributeError, OSError):
        return


def _write_lnk(link: Path, target: Path, project_root: Path, icon: Path | None) -> bool:
    def quote(path: Path) -> str:
        return str(path).replace("'", "''")

    icon_line = ""
    if icon and icon.is_file():
        icon_line = f"$l.IconLocation = '{quote(icon)},0'; "
    script = (
        "$s = New-Object -ComObject WScript.Shell; "
        f"$l = $s.CreateShortcut('{quote(link)}'); "
        f"$l.TargetPath = 'wscript.exe'; "
        f"$l.Arguments = '\"{quote(target)}\"'; "
        f"$l.WorkingDirectory = '{quote(project_root)}'; "
        f"$l.Description = '{APP_NAME} (janela local)'; "
        + icon_line
        + "$l.WindowStyle = 7; "
        "$l.Save()"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            **hidden_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and link.is_file()
