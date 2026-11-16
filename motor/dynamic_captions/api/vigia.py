"""O motor fecha junto com o Premiere.

O painel sobe o motor como processo separado (``pythonw``), que antes continuava rodando depois
que o Premiere fechava. Agora, ao iniciar, o motor procura o Premiere entre os processos que o
criaram (motor ← launcher do venv ← CEPHtmlEngine ← Adobe Premiere Pro.exe) e fica vigiando:
quando ele termina — fechado normalmente, travado e encerrado, ou derrubado — o motor sai também.

- ``DYNAMIC_CAPTIONS_HOST_PID``: PID a vigiar, se quem inicia o motor já souber.
- ``DYNAMIC_CAPTIONS_PANEL_PID``: PID do painel (o host.js passa ``process.pid``); a busca do Premiere
  começa por ele se a cadeia do próprio motor não chegar lá.
- Iniciado à mão (terminal, testes), sem Premiere acima: não vigia nada.

No Windows o vigia segura um handle do processo (``OpenProcess`` + ``WaitForSingleObject``), então
um PID reaproveitado pelo sistema não engana o vigia.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

HOST_NAMES = ("premiere",)  # "Adobe Premiere Pro.exe", "Adobe Premiere Pro (Beta).exe"
CEP_NAMES = ("cephtmlengine",)
MAX_DEPTH = 16


def _table() -> dict[int, tuple[int, str]]:
    """pid -> (pid do pai, nome do executável em minúsculas)."""
    if os.name == "nt":
        return _table_windows()
    try:
        import psutil

        out = {}
        for p in psutil.process_iter(["pid", "ppid", "name", "exe"]):
            name = p.info.get("exe") or p.info.get("name") or ""
            out[p.info["pid"]] = (p.info.get("ppid") or 0, os.path.basename(name).lower())
        return out
    except Exception:
        return _table_proc()


def _table_proc() -> dict[int, tuple[int, str]]:
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", encoding="utf-8", errors="replace") as f:
                stat = f.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            try:
                name = os.path.basename(os.readlink(f"/proc/{d}/exe"))
            except OSError:
                name = stat.split("(", 1)[1].rsplit(")", 1)[0]
            out[int(d)] = (ppid, name.lower())
        except (OSError, ValueError, IndexError):
            continue
    return out


def _table_windows() -> dict[int, tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def find_host(start: int | None = None, table: dict[int, tuple[int, str]] | None = None) -> int | None:
    """Sobe a partir de ``start`` (padrão: o próprio motor) até achar o Premiere.

    Sem Premiere na cadeia, usa o CEPHtmlEngine mais alto (ele termina junto com o Premiere)."""
    table = _table() if table is None else table
    pid, seen, cep = (start or os.getpid()), set(), None
    for _ in range(MAX_DEPTH):
        if pid in seen or pid not in table:
            break
        seen.add(pid)
        ppid, name = table[pid]
        if pid != os.getpid():
            if any(n in name for n in HOST_NAMES):
                return pid
            if any(n in name for n in CEP_NAMES):
                cep = pid
        if not ppid or ppid == pid:
            break
        pid = ppid
    return cep


def host_pid() -> int | None:
    """Quem vigiar: variável explícita, senão o Premiere acima do motor ou do painel."""
    explicit = os.environ.get("DYNAMIC_CAPTIONS_HOST_PID", "").strip()
    if explicit.isdigit():
        return int(explicit)
    try:
        table = _table()
    except Exception:
        return None
    found = find_host(None, table)
    panel = os.environ.get("DYNAMIC_CAPTIONS_PANEL_PID", "").strip()
    if found is None and panel.isdigit():
        found = find_host(int(panel), table) or (int(panel) if int(panel) in table else None)
    return found


def _wait_windows(pid: int, stop: threading.Event, interval: float) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return True  # já não existe
    try:
        while not stop.is_set():
            if k32.WaitForSingleObject(h, int(interval * 1000)) == 0:  # WAIT_OBJECT_0: terminou
                return True
        return False
    finally:
        k32.CloseHandle(h)


def _alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # processo morto ainda não recolhido pelo pai ("zumbi") conta como fechado
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def watch(pid: int, on_gone: Callable[[], None], interval: float = 2.0) -> threading.Event:
    """Chama ``on_gone`` (uma vez) quando o processo ``pid`` terminar. Devolve um Event para parar."""
    stop = threading.Event()

    def run() -> None:
        if os.name == "nt":
            gone = _wait_windows(pid, stop, interval)
        else:
            gone = False
            while not stop.wait(interval):
                if not _alive_posix(pid):
                    gone = True
                    break
        if gone and not stop.is_set():
            on_gone()

    threading.Thread(target=run, name="dynamic-captions-vigia", daemon=True).start()
    return stop


def exit_with_host(server, grace_s: float = 8.0) -> Callable[[], None]:
    """Encerra o uvicorn com calma (cancela trabalhos no shutdown) e garante a saída depois de ``grace_s``."""

    def on_gone() -> None:
        print("O Premiere fechou: encerrando o motor.", flush=True)
        server.should_exit = True

        def force() -> None:
            time.sleep(grace_s)
            os._exit(0)

        threading.Thread(target=force, name="dynamic-captions-saida", daemon=True).start()

    return on_gone
