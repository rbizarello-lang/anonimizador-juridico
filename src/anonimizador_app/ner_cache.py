"""Cache do NER local: spans brutos do GLiNER/spaCy, antes do pos-processamento do Ciclo 1.

A chave cobre o texto de entrada do ``LocalNerEngine.detect`` e a identidade do
motor (modelo, limiar, rotulos, sombra de caixa, fatias, exclusoes). O valor e a
lista devolvida por ``detect`` (offsets no texto original). Nao cacheia
``resolve_overlaps``, lista de alvos, canonical nem fidelidade.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from anonimizador_juridico import EntitySpan

CACHE_FORMAT = 1


def default_ner_cache_dir(private_root: Path) -> Path:
    return Path(private_root) / "cache_ner"


def cache_key(
    text: str,
    *,
    model: str,
    threshold: float,
    labels: Sequence[str],
    shadow_case: bool,
    chunk_chars: int,
    chunk_overlap: int,
    extra_keep: Sequence[str] = (),
) -> str:
    digest = hashlib.sha256()
    digest.update(text.encode("utf-8"))
    digest.update(b"\0")
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(f"{threshold:.6f}".encode("ascii"))
    digest.update(b"\0")
    digest.update("\n".join(labels).encode("utf-8"))
    digest.update(b"\0")
    digest.update(b"1" if shadow_case else b"0")
    digest.update(b"\0")
    digest.update(f"{chunk_chars},{chunk_overlap}".encode("ascii"))
    digest.update(b"\0")
    digest.update("\n".join(extra_keep).encode("utf-8"))
    return digest.hexdigest()


def load_spans(cache_dir: Path, key: str) -> list[EntitySpan] | None:
    path = Path(cache_dir) / f"{key}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw_spans = payload.get("spans")
    if not isinstance(raw_spans, list):
        return None
    spans: list[EntitySpan] = []
    for item in raw_spans:
        if not isinstance(item, dict):
            return None
        try:
            spans.append(
                EntitySpan(
                    start=int(item["start"]),
                    end=int(item["end"]),
                    entity_type=str(item["entity_type"]),
                    score=float(item["score"]),
                    recognizer=str(item.get("recognizer") or "NerLocal"),
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    return spans


def save_spans(
    cache_dir: Path,
    key: str,
    spans: Sequence[EntitySpan],
    *,
    text: str,
    model: str,
    threshold: float,
    labels: Sequence[str],
    shadow_case: bool,
    chunk_chars: int,
    chunk_overlap: int,
) -> None:
    destination = Path(cache_dir)
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "formato": CACHE_FORMAT,
        "modelo": model,
        "limiar": threshold,
        "rotulos": list(labels),
        "case_shadow": shadow_case,
        "fatia": {"chars": chunk_chars, "overlap": chunk_overlap},
        "data": datetime.now().isoformat(timespec="seconds"),
        "spans": [
            {
                "start": span.start,
                "end": span.end,
                "entity_type": span.entity_type,
                "score": span.score,
                "text": text[span.start : span.end],
                "recognizer": span.recognizer,
            }
            for span in spans
        ],
    }
    path = destination / f"{key}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_residual(cache_dir: Path, key: str) -> list[str] | None:
    path = Path(cache_dir) / f"{key}.residual.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    leftovers = payload.get("leftovers") if isinstance(payload, dict) else None
    if not isinstance(leftovers, list):
        return None
    values: list[str] = []
    for item in leftovers:
        if not isinstance(item, str):
            return None
        values.append(item)
    return values


def save_residual(
    cache_dir: Path,
    key: str,
    leftovers: Sequence[str],
    *,
    model: str,
    threshold: float,
) -> None:
    destination = Path(cache_dir)
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "formato": CACHE_FORMAT,
        "modelo": model,
        "limiar": threshold,
        "data": datetime.now().isoformat(timespec="seconds"),
        "leftovers": list(leftovers),
    }
    path = destination / f"{key}.residual.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
