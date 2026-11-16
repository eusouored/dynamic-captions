"""Inicia o motor: ``python -m smartcaption.api``.

Escolhe a primeira porta livre entre 47310 e 47314 (a mesma lista que o painel percorre em web/host.js)
e grava porta e token em engine.json (o token é colado uma vez no painel).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys

from .. import __version__
from ..paths import config_dir
from .app import PORT_RANGE, create_app


def _bind_port() -> socket.socket:
    """Reserva a primeira porta livre e a mantém presa até o uvicorn servir nela."""
    for port in PORT_RANGE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return s
        except OSError:
            s.close()
    raise SystemExit(f"Nenhuma porta livre entre {PORT_RANGE.start} e {PORT_RANGE.stop - 1}")


def _single_instance(cfg, wait_s: float = 10.0):
    """Um motor por usuário: dois painéis abrindo juntos (ou "Tentar de novo" durante uma partida
    lenta) não podem subir dois motores carregando modelos na GPU. Devolve o arquivo travado
    (mantido aberto enquanto o processo vive) ou None se outro motor já está rodando."""
    import time

    f = open(cfg / "engine.lock", "a+b")
    deadline = time.monotonic() + wait_s  # o motor anterior pode estar saindo (troca de versão)
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.monotonic() >= deadline:
                f.close()
                return None
            time.sleep(0.25)


def main() -> None:
    import uvicorn  # dependência de execução, não da biblioteca

    cfg = config_dir()
    if sys.stdout is None or sys.stderr is None:  # iniciado pelo painel com pythonw: registra em arquivo
        log = open(cfg / "motor.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    lock = _single_instance(cfg)
    if lock is None:
        print("Outro motor do Dynamic Captions já está rodando; este sai sem mexer no engine.json.")
        return
    token = os.environ.get("SMARTCAPTION_TOKEN") or secrets.token_urlsafe(32)
    sock = _bind_port()
    port = sock.getsockname()[1]
    # Grava num temporário e troca de uma vez: o painel nunca lê um engine.json pela metade.
    tmp = cfg / f"engine.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid(), "version": __version__}), encoding="utf-8")
    os.replace(tmp, cfg / "engine.json")
    # Uso pessoal: modelos não comerciais (SUPlime) liberados. Defina SMARTCAPTION_USO_COMERCIAL=1
    # se a ferramenta for usada em trabalho remunerado, para usar só licenças comerciais.
    commercial = os.environ.get("SMARTCAPTION_USO_COMERCIAL", "0") == "1"
    from ..rede import acelerar
    acelerar()  # modelo que faltar e for baixado no primeiro uso também vem em várias conexões
    app = create_app(cfg / "projects", token=token, allow_noncommercial=not commercial)
    app.state.warmup()  # modelos, fontes e presets carregam já, sem esperar o primeiro "Legendar"
    print(f"Dynamic Captions em http://127.0.0.1:{port} — token salvo em {cfg / 'engine.json'}")
    if not os.environ.get("SMARTCAPTION_TOKEN"):
        print("Dica: defina SMARTCAPTION_TOKEN para o token não mudar a cada início (o painel guarda o token).")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    # Fecha junto com o Premiere (antes ficava rodando sozinho depois que ele fechava).
    from . import vigia

    host = vigia.host_pid()
    if host:
        vigia.watch(host, vigia.exit_with_host(server))
        print(f"Vigiando o Premiere (PID {host}): o motor fecha quando ele fechar.")
    try:
        server.run(sockets=[sock])
    finally:
        lock.close()


if __name__ == "__main__":
    main()
