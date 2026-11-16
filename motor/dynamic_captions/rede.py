"""Downloads do Hugging Face rápidos e visíveis.

1. Com o pacote hf_transfer instalado, cada arquivo grande vem em várias conexões ao mesmo tempo.
   Numa conexão HTTP só, a velocidade depende do caminho até o servidor: na instalação de 15/11/2026
   o modelo de 4,2 GB veio a 0,46 MB/s (2h30). Com várias conexões, numa medição na mesma máquina,
   veio a ~100 MB/s.
2. O progresso sai em linhas inteiras (MB, %, velocidade). A janela do instalador lê a saída linha a
   linha, e a barra do tqdm (que reescreve a mesma linha com \\r) não aparecia: a tela ficava parada
   em "92%" durante horas.

acelerar() precisa rodar antes do primeiro import do huggingface_hub, que lê as variáveis ao carregar.
Se ele já tiver sido importado, as constantes também são ajustadas.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager


def _tem_hf_transfer() -> bool:
    try:
        import hf_transfer  # noqa: F401
        return True
    except Exception:
        return False


def acelerar() -> bool:
    """Liga o download em várias conexões, se der. Devolve True se ficou ligado."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows sem modo desenvolvedor: aviso inútil
    if os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "0" or not _tem_hf_transfer():
        _ajustar_constantes(False)
        return False
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    # O Xet, se estiver instalado, tem prioridade sobre o hf_transfer e foi mais lento na medição (15 MB/s).
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    _ajustar_constantes(True)
    return True


def _ajustar_constantes(ligado: bool) -> None:
    mod = sys.modules.get("huggingface_hub.constants")
    if mod is not None:
        mod.HF_HUB_ENABLE_HF_TRANSFER = ligado
        if ligado and os.environ.get("HF_HUB_DISABLE_XET") == "1":
            mod.HF_HUB_DISABLE_XET = True


def desligar_aceleracao() -> None:
    """Para a segunda tentativa: o hf_transfer não retoma arquivo pela metade e falha sem detalhes."""
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    _ajustar_constantes(False)


def _num(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _tam(b: float) -> str:
    return _num(b / 1e9) + " GB" if b >= 1e9 else f"{b / 1e6:.0f} MB"


class _Linhas:
    """Imita o pedaço do tqdm que o huggingface_hub usa (update/close) e escreve uma linha a cada poucos segundos."""

    def __init__(self, desc: str, total: int | None, initial: int = 0, intervalo: float = 5.0):
        self.desc, self.total, self.n = desc, total, initial
        self.intervalo = intervalo
        self.t0 = self.ultimo = time.monotonic()
        self.n0 = initial

    def update(self, n: int = 1) -> None:
        self.n += n
        agora = time.monotonic()
        if agora - self.ultimo >= self.intervalo:
            self.ultimo = agora
            self._linha(agora)

    def _linha(self, agora: float, fim: bool = False) -> None:
        vel = _num((self.n - self.n0) / 1e6 / max(agora - self.t0, 1e-6)) + " MB/s"
        if self.total:
            txt = f"  {self.desc}: {_tam(self.n)} de {_tam(self.total)} ({100 * self.n / self.total:.0f}%), {vel}"
        else:
            txt = f"  {self.desc}: {_tam(self.n)}, {vel}"
        if fim:
            txt += f", em {agora - self.t0:.0f} s"
        print(txt, flush=True)

    def close(self) -> None:
        if self.n - self.n0 > 20e6:  # só arquivos que valeram a pena (os pequenos poluiriam o registro)
            self._linha(time.monotonic(), fim=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@contextmanager
def progresso_em_linhas():
    """Durante o bloco, as barras de bytes do huggingface_hub viram linhas de texto."""
    try:
        from huggingface_hub import file_download
        original = file_download._get_progress_bar_context
    except Exception:  # outra versão do huggingface_hub: segue sem progresso, o download funciona igual
        yield
        return

    def contexto(*, desc, total=None, initial=0, _tqdm_bar=None, **_):
        if _tqdm_bar is not None:
            return original(desc=desc, log_level=0, total=total, initial=initial, _tqdm_bar=_tqdm_bar)
        return _Linhas(desc, total, initial)

    file_download._get_progress_bar_context = contexto
    try:
        yield
    finally:
        file_download._get_progress_bar_context = original


def baixar_repo(repo: str) -> None:
    """snapshot_download com várias conexões e progresso em linhas; se o rápido falhar, tenta o comum."""
    rapido = acelerar()
    from huggingface_hub import snapshot_download

    with progresso_em_linhas():
        try:
            snapshot_download(repo)
            return
        except Exception as exc:
            if not rapido:
                raise
            print(f"  o download em várias conexões falhou ({type(exc).__name__}); tentando do jeito comum", flush=True)
        desligar_aceleracao()
        snapshot_download(repo)
