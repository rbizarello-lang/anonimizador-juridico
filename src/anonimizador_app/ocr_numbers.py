"""v0.4.12: numero deturpado pelo OCR nas regras de NIRE, RG, CPF, CNPJ e matricula de imovel (chave ``ocr_fuzzy_numbers``).

Dentro de uma sequencia que ja e majoritariamente de digitos (2/3 ou mais), aceita O/o->0, l/I->1 e S->5 quando o padrao
fecha: CPF e CNPJ so com o digito verificador conferindo DEPOIS da correcao; NIRE, RG e matricula so com o rotulo antes.
O trecho mascarado e o original, como aparece no texto; o token e o do numero corrigido (o mesmo do numero limpo).
So entra o que tem ao menos uma letra: o numero limpo continua com as regras de sempre.

Dado generico; nenhum dado de caso.
"""

from __future__ import annotations

import re

from presidio_analyzer import Pattern, PatternRecognizer, RecognizerResult

from anonimizador_juridico.pseudonymizer import canonicalize
from anonimizador_juridico.validators import is_valid_cnpj, is_valid_cpf

RECOGNIZER_PREFIX = "OcrNumero"
_MAP = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"})
_D = r"[0-9OoIlS]"
_SPECS = {
    "BR_CPF": (rf"(?<![0-9A-Za-z]){_D}{{3}}\.{_D}{{3}}\.{_D}{{3}}-{_D}{{2}}(?![0-9A-Za-z])", None),
    "BR_CNPJ": (rf"(?<![0-9A-Za-z]){_D}{{2}}\.{_D}{{3}}\.{_D}{{3}}/{_D}{{4}}-{_D}{{2}}(?![0-9A-Za-z])", None),
    "BR_NIRE": (rf"(?<![0-9A-Za-z]){_D}(?:[ .\-]?{_D}){{10}}(?![0-9A-Za-z])", r"(?i)\bnire\b[^\n\d]{0,12}$"),
    "BR_RG": (
        rf"(?<![0-9A-Za-z]){_D}{{1,4}}(?:[.\s]{_D}{{3}}){{1,2}}(?:-[0-9Xx])?(?![0-9A-Za-z])",
        r"(?i)(?:\bR\.?\s*G\.?|\bidentidade|\bC\.?\s*I\.?)\s*(?:n[º°o.]*\s*)?[:\-]?\s*$",
    ),
    "BR_MATRICULA_IMOVEL": (
        rf"(?<![0-9A-Za-z]){_D}{{1,7}}(?:\.{_D}{{1,3}})?(?![0-9A-Za-z])",
        r"(?i)\bmatr[íi]cula\s*(?:n[.\s]*[oº°]?\s*)?$",
    ),
}


def corrected(value: str) -> str | None:
    """O valor com O/o/l/I/S trocados por digito, se a sequencia ja e majoritariamente de digitos e tem letra."""

    chars = [char for char in value if char.isalnum()]
    letters = sum(1 for char in chars if not char.isdigit())
    if not letters or not chars or (len(chars) - letters) * 3 < len(chars) * 2:
        return None
    return value.translate(_MAP)


class OcrNumberRecognizer(PatternRecognizer):
    def __init__(self, entity_type: str) -> None:
        regex, label = _SPECS[entity_type]
        self._value = re.compile(regex)
        self._label = re.compile(label) if label else None
        self._entity = entity_type
        super().__init__(
            supported_entity=entity_type,
            supported_language="pt",
            name=f"{RECOGNIZER_PREFIX}{entity_type}",
            patterns=[Pattern(name="ocr_numero", regex=regex, score=0.85)],
        )

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        found: list[RecognizerResult] = []
        for match in self._value.finditer(text):
            value = match.group()
            fixed = corrected(value)
            if fixed is None:
                continue
            if self._label is not None and not self._label.search(text[max(0, match.start() - 40) : match.start()]):
                continue
            digits = re.sub(r"\D", "", fixed)
            if self._entity == "BR_CPF" and not is_valid_cpf(digits):
                continue
            if self._entity == "BR_CNPJ" and not is_valid_cnpj(digits):
                continue
            if self._entity == "BR_NIRE" and len(digits) != 11:
                continue
            found.append(
                RecognizerResult(
                    entity_type=self._entity,
                    start=match.start(),
                    end=match.end(),
                    score=0.85,
                    recognition_metadata={"canonical_key": canonicalize(self._entity, fixed)},
                )
            )
        return found


def build_ocr_number_recognizers(enabled_tags: dict[str, bool]) -> list[PatternRecognizer]:
    return [OcrNumberRecognizer(entity) for entity in _SPECS if enabled_tags.get(entity, True)]
