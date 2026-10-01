"""Codigos eproc de procurador na capa e em linhas Procurador(es)/Advogado(s) (Ciclo 4c).

Apos um PERSON da lista de alvos, ``PR900001`` / ``P9000001`` viram [EPROC_n].
``PFN`` + digitos ao lado de PFN permanece em claro. ``REsp 1.259.018`` nao casa.
Uma vez visto, o mesmo codigo e propagado no restante do documento.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from anonimizador_juridico import EntitySpan, resolve_overlaps

from .header_heuristic import _cover_window
from .settings import default_enabled_tags
from .target_list import phrase_pattern

RECOGNIZER = "EprocCodes"
_SCORE = 0.90
_CODE = re.compile(r"\b([A-Z]{1,2}\d{6,7})\b")
_AFTER_PERSON = re.compile(r"^\s+([A-Z]{1,2}\d{6,7})\b")
_LAWYER_LINE = re.compile(r"(?i)procurador(?:a|es|\(\s*es\s*\))?|advogad[oa]s?")
_PRECEDENT = re.compile(r"(?i)\b(?:REsp|AgRg|AgInt|EREsp|AREsp|RE|ARE|HC|MS)\b")
_PFN_PREFIX = re.compile(r"^PFN\d")


class EprocCodesEngine:
    """Identificador eproc colado ao procurador da lista; propaga a mesma string."""

    provider = "eproc_codes"

    def __init__(
        self,
        *,
        people: Iterable[str] = (),
        extra_keep: Iterable[str] = (),
        enabled_tags: dict[str, bool] | None = None,
    ) -> None:
        self.people = tuple(item for item in people if str(item).strip())
        self.extra_keep = tuple(extra_keep)
        self.enabled_tags = enabled_tags if enabled_tags is not None else default_enabled_tags()

    def detect(self, text: str) -> list[EntitySpan]:
        if not self.enabled_tags.get("BR_EPROC", True):
            return []
        seeds = list(_codes_after_targets(text, self.people)) if self.people else []
        seeds.extend(_codes_on_capa_or_lawyer_line(text, seeds))
        if not seeds:
            return []
        known = {text[span.start : span.end] for span in seeds}
        spans = list(seeds)
        spans.extend(_propagate(text, known, seeds))
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


def _person_hits(text: str, people: Sequence[str]) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for name in sorted(people, key=len, reverse=True):
        try:
            pattern = phrase_pattern(name)
        except re.error:
            continue
        for match in pattern.finditer(text):
            key = (match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            hits.append(key)
    return hits


def _line_ok(text: str, start: int, cover_end: int) -> bool:
    if start < cover_end:
        return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    return bool(_LAWYER_LINE.search(text[line_start:line_end]))


def _codes_after_targets(text: str, people: Sequence[str]) -> list[EntitySpan]:
    cover_end = len(_cover_window(text))
    spans: list[EntitySpan] = []
    seen: set[tuple[int, int]] = set()
    for start, end in _person_hits(text, people):
        if not _line_ok(text, start, cover_end):
            continue
        match = _AFTER_PERSON.match(text[end:])
        if match is None:
            continue
        code = match.group(1)
        if _PFN_PREFIX.match(code):
            continue
        code_start = end + match.start(1)
        code_end = end + match.end(1)
        if _PRECEDENT.search(text[max(0, code_start - 12) : code_start]):
            continue
        key = (code_start, code_end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            EntitySpan(
                start=code_start,
                end=code_end,
                entity_type="BR_EPROC",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=code,
            )
        )
    return spans


def _codes_on_capa_or_lawyer_line(
    text: str, existing: Sequence[EntitySpan]
) -> list[EntitySpan]:
    """PR/RS/SC+dígitos na capa ou linha de procurador, mesmo sem lista de alvos."""

    cover_end = len(_cover_window(text))
    seen = {(span.start, span.end) for span in existing}
    spans: list[EntitySpan] = []
    for match in _CODE.finditer(text):
        code = match.group(1)
        if _PFN_PREFIX.match(code):
            continue
        start, end = match.start(1), match.end(1)
        if (start, end) in seen:
            continue
        if not _line_ok(text, start, cover_end):
            continue
        if _PRECEDENT.search(text[max(0, start - 12) : start]):
            continue
        seen.add((start, end))
        spans.append(
            EntitySpan(
                start=start,
                end=end,
                entity_type="BR_EPROC",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=code,
            )
        )
    return spans


def _propagate(
    text: str,
    known: set[str],
    existing: list[EntitySpan],
) -> list[EntitySpan]:
    occupied = [(span.start, span.end) for span in existing]
    extras: list[EntitySpan] = []
    for match in _CODE.finditer(text):
        code = match.group(1)
        if code not in known:
            continue
        if _PFN_PREFIX.match(code):
            continue
        start, end = match.start(1), match.end(1)
        if any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        if _PRECEDENT.search(text[max(0, start - 12) : start]):
            continue
        occupied.append((start, end))
        extras.append(
            EntitySpan(
                start=start,
                end=end,
                entity_type="BR_EPROC",
                score=_SCORE,
                recognizer=RECOGNIZER,
                canonical_key=code,
            )
        )
    return extras
