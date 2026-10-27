"""H9-o: tempo de cada etapa da rodada, para a AUDITORIA (exportavel e privada).

Um registro unico do processo (o app roda uma rodada por vez): cada etapa soma o seu tempo e as suas contagens
(``stage``/``count``) e ``take_lines`` devolve as linhas e zera o registro, ao gravar a AUDITORIA do lote. O inicio
da rodada e o primeiro registro depois do ultimo ``take_lines`` (ou ``mark_start``). So tempos, contagens e o
dispositivo dos modelos: nada de texto.

Etapas medidas onde acontecem: extracao do PDF (``extraction_cache``), OCR por pagina (``ocr_pages``), NER local
(``local_ner``, com acerto de cache) e chamadas ao LLM (``llm_discovery``). O resto (lista do caso, resolucao,
troca e gravacao) e o total menos as etapas medidas.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

_LOCK = threading.Lock()
_STATE: dict = {"start": None, "seconds": {}, "counts": {}, "notes": {}}

# Etapas que contam no total (o OCR acontece DENTRO da extracao: aparece, mas nao e somado de novo).
_TOP_STAGES = ("extracao", "ner", "llm")


def mark_start() -> None:
    with _LOCK:
        if _STATE["start"] is None:
            _STATE["start"] = time.perf_counter()


def restart_clock() -> None:
    """Inicio da rodada propriamente dita: o relogio passa a contar so o tempo ja medido em etapas (a previa da
    rodada com LLM extrai antes da confirmacao; a espera pelo Operador nao entra no total)."""

    with _LOCK:
        measured = sum(_STATE["seconds"].get(name, 0.0) for name in _TOP_STAGES)
        _STATE["start"] = time.perf_counter() - measured


def reset() -> None:
    """Zera tempos e contagens. As notas (dispositivo do GLiNER, provedor do OCR) sao do processo e ficam: o
    modelo carregado numa rodada segue o mesmo na seguinte."""

    with _LOCK:
        _STATE.update(start=None, seconds={}, counts={})


@contextmanager
def stage(name: str) -> Iterator[None]:
    mark_start()
    begin = time.perf_counter()
    try:
        yield
    finally:
        add(name, time.perf_counter() - begin)


def add(name: str, seconds: float, calls: int = 1) -> None:
    mark_start()
    with _LOCK:
        _STATE["seconds"][name] = _STATE["seconds"].get(name, 0.0) + max(0.0, seconds)
        _STATE["counts"][name] = _STATE["counts"].get(name, 0) + calls


def count(name: str, amount: int = 1) -> None:
    with _LOCK:
        _STATE["counts"][name] = _STATE["counts"].get(name, 0) + amount


def note(key: str, value: str) -> None:
    with _LOCK:
        _STATE["notes"][key] = value


def _fmt(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f} s".replace(".", ",")
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min {rest:02d} s"


def lines(*, now: float | None = None) -> list[str]:
    """As linhas do tempo da rodada (sem zerar). Vazio se nada foi medido."""

    with _LOCK:
        start = _STATE["start"]
        seconds = dict(_STATE["seconds"])
        counts = dict(_STATE["counts"])
        notes = dict(_STATE["notes"])
    if start is None:
        return []
    total = (now if now is not None else time.perf_counter()) - start
    out = [f"Tempo total da rodada: {_fmt(total)} (do inicio da leitura ate a gravacao da AUDITORIA)"]
    hits, extracted = counts.get("extracao_cache", 0), counts.get("extracao", 0) - counts.get("extracao_cache", 0)
    out.append(
        f"Tempo da extracao do PDF: {_fmt(seconds.get('extracao', 0.0))} ({extracted} extraido(s), {hits} do cache)"
    )
    ocr_pages = counts.get("ocr", 0)
    ocr_line = f"Tempo do OCR (dentro da extracao): {_fmt(seconds.get('ocr', 0.0))}, {ocr_pages} pagina(s) por OCR"
    if notes.get("ocr_provider"):
        ocr_line += f"; {notes['ocr_provider']}"
    out.append(ocr_line)
    ner_hits = counts.get("ner_cache", 0)
    ner_runs = counts.get("ner", 0) - ner_hits
    ner_line = (
        f"Tempo do NER local: {_fmt(seconds.get('ner', 0.0))} ({ner_runs} leitura(s) pelo modelo, {ner_hits} do cache)"
    )
    if notes.get("ner_device"):
        ner_line += f"; dispositivo do GLiNER: {notes['ner_device']}"
    out.append(ner_line)
    calls = counts.get("llm", 0)
    if calls or counts.get("llm_cache", 0):
        average = seconds.get("llm", 0.0) / calls if calls else 0.0
        out.append(
            f"Tempo do LLM: {_fmt(seconds.get('llm', 0.0))} em {calls} chamada(s) (media {_fmt(average)} por "
            f"chamada); trechos do cache do LLM: {counts.get('llm_cache', 0)}"
        )
    measured = sum(seconds.get(name, 0.0) for name in _TOP_STAGES)
    out.append(
        f"Tempo do resto (lista do caso, resolucao, troca e gravacao): {_fmt(total - measured)}"
    )
    return out


def append_private(path, timing: list[str], title: str = "Tempo de cada etapa da rodada") -> None:
    """Acrescenta a secao de tempos a AUDITORIA privada do lote (cria o arquivo se ainda nao existe). v0.4.13:
    ``title`` serve tambem a outras secoes so de contagens (tokens gerados por chamada do LLM)."""

    from pathlib import Path

    if not timing:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.read_text(encoding="utf-8") if target.is_file() else ""
    block = "\n".join(["", f"## {title}", *(f"- {line}" for line in timing), ""])
    target.write_text(previous.rstrip("\n") + "\n" + block, encoding="utf-8")


def take_lines() -> list[str]:
    """As linhas da rodada, e o registro zerado para a proxima."""

    result = lines()
    reset()
    return result
