from __future__ import annotations

import re

from presidio_analyzer import Pattern, PatternRecognizer

from anonimizador_juridico.recognizers import build_structured_recognizers

from .settings import AppSettings, CustomRule

_BROAD_PATTERNS = {
    ".*",
    ".+",
    ".?",
    ".*?",
    ".+?",
    r"\w+",
    r"\w*",
    r"\W+",
    r"\S+",
    r"\s+",
    r".{1,}",
    r"[\s\S]+",
    r"[\s\S]*",
    r"(.+)",
    r"(.*)",
    r"^(.*)$",
    r"^(.+)$",
}


class InvalidRuleError(ValueError):
    """Regex ou campos de regra personalizada invalidos."""


def validate_regex(pattern: str) -> None:
    compact = pattern.strip()
    if not compact:
        raise InvalidRuleError("A expressao regular nao pode ficar vazia.")
    if compact in _BROAD_PATTERNS:
        raise InvalidRuleError(
            "Essa expressao e larga demais e descaracterizaria a peca. "
            "Use um padrao estreito, com um rotulo (ex.: PIS) ou digitos."
        )
    if not re.search(r"(\\d|[A-Za-z]{3,}|\d)", compact):
        raise InvalidRuleError(
            "Inclua um texto fixo de pelo menos tres letras ou uma classe de "
            "digitos. Padroes vagos nao sao aceitos."
        )
    try:
        re.compile(compact)
    except re.error as exc:
        raise InvalidRuleError(f"Expressao regular invalida: {exc}") from exc


def validate_custom_rule(name: str, regex: str, token_label: str) -> None:
    if not name.strip():
        raise InvalidRuleError("Informe um nome para a regra.")
    label = token_label.strip()
    if not label:
        raise InvalidRuleError("Informe o rotulo do token, por exemplo PIS.")
    if re.search(r"[^A-Za-z0-9_]", label):
        raise InvalidRuleError("O rotulo do token so pode ter letras, numeros e sublinhado.")
    validate_regex(regex)


def slug_entity_type(name: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper()
    if not compact:
        raise InvalidRuleError("Informe um nome ou um codigo de tag.")
    if not compact.startswith("BR_"):
        compact = f"BR_{compact}"
    return compact


def build_recognizers(settings: AppSettings | None = None) -> list[PatternRecognizer]:
    active = settings or AppSettings()
    selected: list[PatternRecognizer] = []
    for recognizer in build_structured_recognizers():
        entity = recognizer.supported_entities[0]
        if active.enabled_tags.get(entity, True):
            selected.append(recognizer)
    from . import features

    if features.enabled("ocr_fuzzy_numbers"):
        # v0.4.12: NIRE, RG, CPF, CNPJ e matricula com O/o->0, l/I->1, S->5 (CPF/CNPJ so com DV conferindo).
        from .ocr_numbers import build_ocr_number_recognizers

        selected.extend(build_ocr_number_recognizers(active.enabled_tags))
    for rule in active.custom_rules:
        selected.append(_custom_recognizer(rule))
    return selected


def _custom_recognizer(rule: CustomRule) -> PatternRecognizer:
    validate_custom_rule(rule.name, rule.regex, rule.token_label)
    entity = rule.entity_type or slug_entity_type(rule.name)
    score = rule.score if 0.0 < rule.score <= 1.0 else 0.65
    return PatternRecognizer(
        supported_entity=entity,
        supported_language="pt",
        name=rule.name or f"Custom{entity}",
        patterns=[
            Pattern(
                name=rule.name or entity.lower(),
                regex=rule.regex,
                score=score,
            )
        ],
    )
