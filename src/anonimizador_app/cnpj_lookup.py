"""Motor contextual: CNPJ da peca -> razao social tokenizada no texto inteiro."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from anonimizador_juridico import EntitySpan, resolve_overlaps

from .cnpj_index import CnpjIndex, default_index_path, extract_valid_cnpjs
from .gemini_detector import is_kept_term, is_too_generic, locate_mentions


class CnpjLookupEngine:
    """Consulta o indice local. Sem arquivo, a camada fica inerte."""

    provider = "cnpj"

    def __init__(
        self,
        path: Path | None = None,
        *,
        extra_keep: Iterable[str] = (),
        enabled: bool = True,
    ) -> None:
        self.path = Path(path) if path else default_index_path()
        self.extra_keep = tuple(extra_keep)
        self.enabled = enabled
        self._index = CnpjIndex(self.path)

    def is_ready(self) -> bool:
        return self.enabled and self._index.exists()

    def detect(self, text: str) -> list[EntitySpan]:
        if not self.is_ready():
            return []
        mentions_exact: list[tuple[str, str]] = []
        mentions_filtered: list[tuple[str, str]] = []
        seen: set[str] = set()
        for cnpj in extract_valid_cnpjs(text):
            record = self._index.lookup(cnpj)
            if record is None or record.publica:
                continue
            names = record.private_names()
            if not names:
                continue
            razao, *aliases = names
            if is_kept_term(razao, self.extra_keep):
                continue
            key = razao.casefold()
            if key not in seen:
                seen.add(key)
                mentions_exact.append(("ORGANIZATION", razao))
            for alias in aliases:
                if is_kept_term(alias, self.extra_keep) or is_too_generic(
                    "ORGANIZATION", alias
                ):
                    continue
                alias_key = alias.casefold()
                if alias_key in seen:
                    continue
                seen.add(alias_key)
                mentions_filtered.append(("ORGANIZATION", alias))
        if not mentions_exact and not mentions_filtered:
            return []
        spans = locate_mentions(
            text,
            mentions_exact,
            extra_keep=self.extra_keep,
            recognizer="CnpjLookup",
            score=0.93,
            enforce_generic_filter=False,
        )
        if mentions_filtered:
            spans.extend(
                locate_mentions(
                    text,
                    mentions_filtered,
                    extra_keep=self.extra_keep,
                    recognizer="CnpjLookup",
                    score=0.90,
                    enforce_generic_filter=True,
                )
            )
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []
