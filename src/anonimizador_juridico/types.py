from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntitySpan:
    """Entidade detectada em offsets absolutos do texto original."""

    start: int
    end: int
    entity_type: str
    score: float
    recognizer: str
    canonical_key: str = ""
    alvo: str = ""

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Span invalido")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("Score deve estar entre 0 e 1")

    def value_from(self, text: str) -> str:
        return text[self.start : self.end]
