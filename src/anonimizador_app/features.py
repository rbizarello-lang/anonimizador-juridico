"""v0.4.12: chaves para desligar, uma por uma, as seis mudancas da versao (config.json; todas ligadas por padrao).

A ideia e isolar depois, sem reimplementar, qual mudanca causou uma piora. Cada chave tem o mesmo nome no config.json
e em ``AppSettings``:

- ``ocr_embedded_images``: OCR das imagens embutidas em folha de texto (>= 5% da folha), contadas na AUDITORIA;
- ``ocr_cleanup``: descarte de leitura de baixa confianca e de texto espelhado do verso; releitura sem sublinhado;
- ``ocr_fuzzy_names``: nome da lista do caso casado com tolerancia a erro de OCR;
- ``ocr_fuzzy_numbers``: O/o->0, l/I->1, S->5 dentro de NIRE, RG, CPF, CNPJ e matricula (CPF/CNPJ so com DV);
- ``notary_public_agents``: registrador, tabeliao, escrevente e substituto de cartorio em claro, e o nome do cartorio;
- ``case2_false_positives``: ministro com "Min." no trecho, "empresa executada" e o contato do orgao publico em claro.

v0.4.13 (mesmo esquema, tambem ligadas por padrao):

- ``shared_company_names``: a denominacao usada por duas empresas (CNPJ diferentes) separada por empresa (``shared_names``);
- ``llm_subform_link``: forma parcial achada pelo LLM ligada a UMA entrada existente do elenco (``roster_identity``);
- ``stamp_public_agents``: assinante do carimbo da Junta Comercial (secretario-geral, com erro de OCR) em claro.

O pipeline ativa as chaves da rodada (``activate``); quem roda fora dele (testes, ferramentas) ve todas ligadas.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields

FEATURE_KEYS = (
    "ocr_embedded_images",
    "ocr_cleanup",
    "ocr_fuzzy_names",
    "ocr_fuzzy_numbers",
    "notary_public_agents",
    "case2_false_positives",
    "shared_company_names",
    "llm_subform_link",
    "stamp_public_agents",
)


@dataclass(frozen=True, slots=True)
class Features:
    ocr_embedded_images: bool = True
    ocr_cleanup: bool = True
    ocr_fuzzy_names: bool = True
    ocr_fuzzy_numbers: bool = True
    notary_public_agents: bool = True
    case2_false_positives: bool = True
    shared_company_names: bool = True
    llm_subform_link: bool = True
    stamp_public_agents: bool = True

    @classmethod
    def from_settings(cls, settings: object | None) -> "Features":
        if settings is None:
            return cls()
        return cls(**{item.name: bool(getattr(settings, item.name, True)) for item in fields(cls)})

    def off(self) -> tuple[str, ...]:
        return tuple(item.name for item in fields(self) if not getattr(self, item.name))


_current = Features()


def current() -> Features:
    return _current


def enabled(name: str) -> bool:
    return bool(getattr(_current, name, True))


def set_current(features: Features) -> None:
    global _current
    _current = features


@contextmanager
def activate(settings: object | None):
    """As chaves da rodada valem enquanto ela roda; no fim volta o que havia."""

    global _current
    previous = _current
    _current = Features.from_settings(settings)
    try:
        yield _current
    finally:
        _current = previous


def extractor_suffix(features: Features | None = None) -> str:
    """Sufixo da versao do extrator (chave do cache) quando uma chave do OCR esta desligada: cache separado."""

    features = features or _current
    suffix = ""
    if not features.ocr_embedded_images:
        suffix += "|sem-recortes"
    if not features.ocr_cleanup:
        suffix += "|sem-limpeza"
    return suffix
