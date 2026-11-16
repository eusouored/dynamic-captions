"""Instala o painel Dynamic Captions no Premiere (extensão CEP) — preparação única.

Uso, no mesmo ambiente Python onde o motor foi instalado:
    python -m dynamic_captions.instalar_painel                     # copia e configura; NÃO mexe no registro
    python -m dynamic_captions.instalar_painel --verificar         # só mostra o estado (nada muda)
    python -m dynamic_captions.instalar_painel --ligar-depuracao   # opcional: liga o PlayerDebugMode
    python -m dynamic_captions.instalar_painel --remover           # desinstala

O que faz por padrão (sem administrador e sem mudar configurações de segurança):
1. Copia a extensão para %APPDATA%\\Adobe\\CEP\\extensions\\com.dynamic.captions.
2. Grava %LOCALAPPDATA%\\DynamicCaptions\\launch.json com este Python, para o painel iniciar o
   motor sozinho quando ele não estiver rodando.
3. Lê (sem alterar) o PlayerDebugMode e diz se ele está ligado.

Requisito separado: o Premiere só carrega extensões não assinadas pela Adobe com o
PlayerDebugMode=1 (HKCU\\Software\\Adobe\\CSXS.<versão>). Essa é uma decisão de segurança do
usuário: ligue manualmente ou com --ligar-depuracao. Depois, salve o projeto, reinicie o
Premiere e abra Janela › Extensões › Dynamic Captions.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .paths import config_dir

EXT_ID = "com.dynamic.captions"
CSXS_VERSIONS = ("10", "11", "12")


def extensions_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Adobe" / "CEP" / "extensions"
    return Path.home() / "Library" / "Application Support" / "Adobe" / "CEP" / "extensions"


def set_debug_mode(enabled: bool = True) -> list[str]:
    done = []
    if sys.platform == "win32":
        import winreg  # type: ignore

        for v in CSXS_VERSIONS:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Adobe\CSXS.{v}")
            winreg.SetValueEx(key, "PlayerDebugMode", 0, winreg.REG_SZ, "1" if enabled else "0")
            winreg.CloseKey(key)
            done.append(f"CSXS.{v}")
    else:
        import subprocess

        for v in CSXS_VERSIONS:
            subprocess.run(["defaults", "write", f"com.adobe.CSXS.{v}", "PlayerDebugMode", "1" if enabled else "0"], check=False)
            done.append(f"CSXS.{v}")
    return done


def debug_mode_status() -> dict[str, str | None]:
    """Valor atual do PlayerDebugMode por versão do CSXS, sem alterar nada."""
    status: dict[str, str | None] = {}
    if sys.platform != "win32":
        return status
    import winreg  # type: ignore

    for v in CSXS_VERSIONS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Adobe\CSXS.{v}") as key:
                status[f"CSXS.{v}"] = str(winreg.QueryValueEx(key, "PlayerDebugMode")[0])
        except OSError:
            status[f"CSXS.{v}"] = None
    return status


def install(target_root: Path | None = None, *, registry: bool = False) -> Path:
    pkg = Path(__file__).resolve().parent
    target = (target_root or extensions_dir()) / EXT_ID
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(pkg / "cep", target)
    shutil.copytree(pkg / "web", target / "web")
    (target / "VERSION").write_text(__version__, encoding="utf-8")
    cfg = config_dir()
    launch = {"python": sys.executable.replace("python.exe", "pythonw.exe") if sys.platform == "win32" and Path(sys.executable.replace("python.exe", "pythonw.exe")).exists() else sys.executable,
              "cwd": str(cfg)}
    # Reinstalar não pode apagar variáveis de ambiente configuradas à mão (ex.: PYTHONPATH de
    # uma cópia de desenvolvimento): sem elas o painel passaria a iniciar outro motor.
    try:
        old = json.loads((cfg / "launch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if isinstance(old, dict) and isinstance(old.get("env"), dict):
        launch["env"] = old["env"]
    (cfg / "launch.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2), encoding="utf-8")
    if registry:
        set_debug_mode(True)
    return target


def uninstall(target_root: Path | None = None) -> None:
    target = (target_root or extensions_dir()) / EXT_ID
    if target.exists():
        shutil.rmtree(target)


def main(argv: list[str]) -> int:
    if "--remover" in argv:
        uninstall()
        print("Painel removido. O PlayerDebugMode não foi alterado.")
        return 0
    if "--verificar" in argv:
        target = extensions_dir() / EXT_ID
        print(f"Extensão: {'instalada em ' + str(target) if target.exists() else 'não instalada'}")
        print(f"PlayerDebugMode: {debug_mode_status() or 'não se aplica neste sistema'}")
        return 0
    enable = "--ligar-depuracao" in argv
    target = install(registry=enable)
    print(f"Painel Dynamic Captions {__version__} copiado para {target}")
    status = debug_mode_status()
    if enable:
        print("PlayerDebugMode ligado a pedido (--ligar-depuracao).")
    elif status and not any(v == "1" for v in status.values()):
        print("ATENÇÃO: o PlayerDebugMode está desligado. O Premiere não carrega extensões não assinadas")
        print("sem ele. Essa mudança de segurança é sua: ligue manualmente (HKCU\\Software\\Adobe\\CSXS.12,")
        print("valor de texto PlayerDebugMode = 1) ou rode de novo com --ligar-depuracao.")
    else:
        print(f"PlayerDebugMode atual: {status or 'não se aplica neste sistema'} (não alterado).")
    print("Depois: salve seu projeto, reinicie o Premiere e abra Janela › Extensões › Dynamic Captions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
