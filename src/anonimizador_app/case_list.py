"""Regras do caso (H9-i, lote B1): processos, CDAs e contatos da aba Caso trocados em todo o texto, sem rotulo.

O numero que o Operador listou (ou que veio da planilha do Nexus) ja foi provado: nao precisa de rotulo nem de
contexto, vale ate dentro de ementa (o processo do caso citado no precedente continua sendo o processo do caso).
Nao e regex faminta: so se procura o valor listado, com a pontuacao que for e partido por quebra de linha.

- Processos: 20 digitos -> processo judicial (CNJ, ``[PROCESSO_n]``); outro numero com 8+ digitos -> processo
  administrativo (``[PROCESSO_ADMINISTRATIVO_n]``).
- CDAs: o numero da inscricao (``[CDA_n]``).
- Contatos: e-mail (``[EMAIL_n]``), telefone (``[TELEFONE_n]``, com ou sem +55, com ou sem parenteses no DDD) e
  endereco (``[ENDERECO_n]``, as palavras do endereco na ordem, sem caixa/acento, com a pontuacao que for; com
  numero predial, o trecho ate o numero tambem casa); veiculo do alvo com rotulo: ``placa: ABC1D23``
  (``[PLACA_n]``) e ``renavam: 00345678901`` (``[RENAVAM_n]``).

Grafias adicionais: ``nome principal = grafia1; grafia2`` (uma entrada por linha). Todas as grafias saem com o
token da entrada principal (``target_list``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import canonicalize, fold_lexical
from anonimizador_juridico.validators import only_digits

CASE_RULE_RECOGNIZER = "ListaDoCaso"
_SCORE = 0.99
_INDEXED = frozenset({"BR_PROCESSO", "BR_PROCESSO_ADMINISTRATIVO", "BR_CDA", "BR_RENAVAM"})
# Sequencia de digitos com ate 2 separadores entre dois digitos, sem letra nem digito colado nas pontas.
_DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z])\d(?:[\s.\-/]{0,2}\d)*(?![0-9A-Za-z])")
_ALIAS_SPLIT = re.compile(r"\s*;\s*")
_LINE_PREFIX = re.compile(
    r"(?i)^\s*(?P<label>e-?mail|email|telefone|tel\.?|fone|celular|cel\.?|endere[cç]o|end\.?|"
    r"processo\s+administrativo|proc\.?\s+adm\.?|pa|paf|processo|cda|nire|placa|renavam)\s*[:\-–—]\s*"
    r"|^\s*(?P<vlabel>placa|renavam)\b\s*"
)
_PHONE_ONLY = re.compile(r"^[\d\s()+.\-]+$")
_EMAIL = re.compile(r"^[^\s@]+\s*@\s*[^\s@]+\.[^\s@]+$")
_WORD = re.compile(r"[^\W_]+")
_ADDRESS_SEP = r"[\s,.;:\-–—/º°ª()]+"
_VIA_ABBREV = {
    "rua": r"(?:rua|r)",
    "avenida": r"(?:avenida|av|avda)",
    "travessa": r"(?:travessa|tv|trav)",
    "rodovia": r"(?:rodovia|rod)",
    "alameda": r"(?:alameda|al)",
    "estrada": r"(?:estrada|estr|est)",
    "praca": r"(?:praca|pca|pc)",
}
_ACCENT = {
    "a": "aáàâãä",
    "e": "eéèêë",
    "i": "iíìîï",
    "o": "oóòôõö",
    "u": "uúùûü",
    "c": "cç",
    "n": "nñ",
}


def parse_alias_lines(lines: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]:
    """``nome principal = grafia1; grafia2`` -> (principal, (grafia1, grafia2)). Linha sem ``=`` e ignorada."""

    found: list[tuple[str, tuple[str, ...]]] = []
    for raw in lines:
        line = str(raw or "").strip()
        if "=" not in line:
            continue
        principal, _sep, rest = line.partition("=")
        principal = " ".join(principal.split()).strip(" -,;:|")
        if len(principal) < 3:
            continue
        aliases: list[str] = []
        seen = {fold_lexical(principal)}
        for part in _ALIAS_SPLIT.split(rest):
            alias = " ".join(part.split()).strip(" -,;:|")
            key = fold_lexical(alias)
            if len(alias) < 3 or key in seen:
                continue
            seen.add(key)
            aliases.append(alias)
        if aliases:
            found.append((principal, tuple(aliases)))
    return found


def format_alias_line(principal: str, aliases: Sequence[str]) -> str:
    return f"{principal} = " + "; ".join(aliases)


def classify_contact(line: str) -> tuple[str, str] | None:
    """(tipo, valor) de uma linha da caixa de contatos: ``email``, ``telefone`` ou ``endereco``."""

    value = str(line or "").strip()
    if not value:
        return None
    label = ""
    match = _LINE_PREFIX.match(value)
    if match is not None:
        label = fold_lexical((match.group("label") or match.group("vlabel"))).replace("-", "")
        value = value[match.end() :].strip()
    if not value:
        return None
    if label == "placa":
        compact = re.sub(r"[^A-Z0-9]", "", value.upper())
        return ("placa", compact) if re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", compact) else None
    if label == "renavam":
        digits = only_digits(value)
        return ("renavam", digits) if 9 <= len(digits) <= 11 else None
    if label.startswith("email") or (not label and "@" in value):
        compact = re.sub(r"\s*@\s*", "@", value)
        return ("email", compact) if _EMAIL.match(compact) else None
    digits = only_digits(value)
    if label.startswith(("tel", "fone", "cel")) or (not label and _PHONE_ONLY.match(value)):
        return ("telefone", value) if 8 <= len(digits) <= 13 else None
    if len(_WORD.findall(value)) < 2:
        return None
    return ("endereco", value)


def classify_process(line: str) -> tuple[str, str] | None:
    """(tipo de entidade, valor): CNJ (20 digitos) ou processo administrativo (8+ digitos)."""

    value = str(line or "").strip()
    match = _LINE_PREFIX.match(value)
    administrative = False
    if match is not None:
        label = fold_lexical((match.group("label") or match.group("vlabel")))
        administrative = label not in {"processo"}
        value = value[match.end() :].strip()
    digits = only_digits(value)
    if len(digits) == 20 and not administrative:
        return "BR_PROCESSO", value
    if len(digits) >= 8:
        return "BR_PROCESSO_ADMINISTRATIVO", value
    return None


def classify_cda(line: str) -> str | None:
    value = str(line or "").strip()
    match = _LINE_PREFIX.match(value)
    if match is not None:
        value = value[match.end() :].strip()
    compact = canonicalize("BR_CDA", value)
    if len(compact) < 6 or sum(char.isdigit() for char in compact) < 5:
        return None
    return value


@dataclass(frozen=True, slots=True)
class _Rule:
    entity_type: str
    canonical: str
    pattern: re.Pattern[str]


class CaseRules:
    """Processos, CDAs e contatos do caso: acha o valor listado em todo o texto (sem rotulo)."""

    def __init__(
        self,
        *,
        processes: Iterable[str] = (),
        cdas: Iterable[str] = (),
        contacts: Iterable[str] = (),
    ) -> None:
        rules: list[_Rule] = []
        seen: set[tuple[str, str]] = set()
        # Numero so de digitos (processo, CDA, RENAVAM): indice, nao uma regex por valor. A carteira inteira do Nexus
        # traz milhares de processos e CDAs; uma varredura por valor levava ~1 min a cada 400 mil caracteres.
        self._numbers: dict[str, str] = {}

        def add(entity_type: str, canonical: str, patterns) -> None:
            """``patterns``: as regexes do valor, ou uma funcao que as monta (so chamada se o valor nao for indexado)."""

            key = (entity_type, canonical)
            if not canonical or key in seen:
                return
            seen.add(key)
            if canonical.isdigit() and entity_type in _INDEXED:
                self._numbers.setdefault(canonical, entity_type)
                return
            for pattern in patterns() if callable(patterns) else patterns:
                rules.append(_Rule(entity_type, canonical, pattern))

        for line in processes:
            parsed = classify_process(line)
            if parsed is None:
                continue
            entity_type, value = parsed
            digits = only_digits(value)
            add(entity_type, digits, lambda digits=digits: [_compact_pattern(digits)])
        for line in cdas:
            value = classify_cda(line)
            if value is None:
                continue
            compact = canonicalize("BR_CDA", value)
            add("BR_CDA", compact, lambda compact=compact: [_compact_pattern(compact)])
        for line in contacts:
            parsed = classify_contact(line)
            if parsed is None:
                continue
            kind, value = parsed
            if kind == "email":
                canonical = value.casefold()
                add("EMAIL_ADDRESS", canonical, [_email_pattern(canonical)])
            elif kind == "placa":
                body = rf"{value[:3]}[-\s]?{value[3:]}"
                add("BR_PLACA", value, [re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)])
            elif kind == "renavam":
                add("BR_RENAVAM", value, lambda value=value: [_compact_pattern(value)])
            elif kind == "telefone":
                canonical = canonicalize("BR_PHONE", value)
                add("BR_PHONE", canonical, _phone_patterns(canonical))
            else:
                patterns = _address_patterns(value)
                if patterns:
                    add("LOCATION", canonicalize("LOCATION", value), patterns)
        self._rules = tuple(rules)
        self.counts = {
            "processos": sum(1 for item in seen if item[0] in {"BR_PROCESSO", "BR_PROCESSO_ADMINISTRATIVO"}),
            "cdas": sum(1 for item in seen if item[0] == "BR_CDA"),
            "contatos": sum(1 for item in seen if item[0] in {"EMAIL_ADDRESS", "BR_PHONE", "LOCATION"}),
            "veiculos": sum(1 for item in seen if item[0] in {"BR_PLACA", "BR_RENAVAM"}),
        }
        self.values = tuple(sorted(seen))

    def __bool__(self) -> bool:
        return bool(self._rules) or bool(self._numbers)

    def _number_hits(self, text: str) -> list[EntitySpan]:
        """Os numeros listados, numa varredura so: cada sequencia de digitos com ate 2 separadores (ponto, traco,
        barra, espaco, quebra) entre eles e conferida no indice por todo trecho que comeca e termina numa fronteira
        (inicio da sequencia ou separador), como faria a regex do valor."""

        if not self._numbers:
            return []
        lengths = sorted({len(value) for value in self._numbers})
        found: list[EntitySpan] = []
        for run in _DIGIT_RUN.finditer(text):
            positions = [run.start() + index for index, char in enumerate(run.group()) if char.isdigit()]
            digits = "".join(text[position] for position in positions)
            count = len(digits)
            for first in range(count):
                if first and positions[first] == positions[first - 1] + 1:
                    continue  # comeca no meio de um bloco de digitos colados
                for size in lengths:
                    last = first + size - 1
                    if last >= count:
                        break
                    if last + 1 < count and positions[last + 1] == positions[last] + 1:
                        continue  # termina no meio de um bloco de digitos colados
                    entity_type = self._numbers.get(digits[first : last + 1])
                    if entity_type is None:
                        continue
                    found.append(
                        EntitySpan(
                            start=positions[first],
                            end=positions[last] + 1,
                            entity_type=entity_type,
                            score=_SCORE,
                            recognizer=CASE_RULE_RECOGNIZER,
                            canonical_key=digits[first : last + 1],
                        )
                    )
        return found

    def detect(self, text: str) -> list[EntitySpan]:
        """Ocorrencias dos valores listados; entre si, o trecho mais longo vence."""

        found: list[EntitySpan] = self._number_hits(text)
        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                start, end = match.span()
                if rule.entity_type == "LOCATION":
                    start, end = _trim(text, start, end)
                if end <= start:
                    continue
                found.append(
                    EntitySpan(
                        start=start,
                        end=end,
                        entity_type=rule.entity_type,
                        score=_SCORE,
                        recognizer=CASE_RULE_RECOGNIZER,
                        canonical_key=rule.canonical,
                    )
                )
        chosen: list[EntitySpan] = []
        for span in sorted(found, key=lambda item: (-(item.end - item.start), item.start)):
            if any(span.start < other.end and other.start < span.end for other in chosen):
                continue
            chosen.append(span)
        return sorted(chosen, key=lambda item: (item.start, item.end))


def _compact_pattern(canonical: str) -> re.Pattern[str]:
    """Os caracteres do valor, na ordem, com ate 2 separadores (ponto, traco, barra, espaco, quebra) entre eles."""

    body = r"[\s.\-/]{0,2}".join(re.escape(char) for char in canonical)
    return re.compile(rf"(?<![0-9A-Za-z])(?:{body})(?![0-9A-Za-z])", re.IGNORECASE)


def _email_pattern(canonical: str) -> re.Pattern[str]:
    local, _sep, domain = canonical.partition("@")
    return re.compile(
        rf"(?<![\w.+-]){re.escape(local)}\s*@\s*{re.escape(domain)}(?![\w-])", re.IGNORECASE
    )


def _phone_patterns(canonical: str) -> list[re.Pattern[str]]:
    if len(canonical) < 8:
        return []
    sep = r"[\s.\-]{0,2}"
    patterns: list[re.Pattern[str]] = []
    if len(canonical) >= 10:
        ddd, local = canonical[:2], canonical[2:]
        local_body = sep.join(re.escape(char) for char in local)
        patterns.append(
            re.compile(
                rf"(?<![\d])(?:\+?\s*55{sep})?(?:\(\s*{ddd}\s*\)|{ddd[0]}{sep}{ddd[1]}){sep}{local_body}(?!\d)"
            )
        )
        # So o numero local, escrito como telefone (com separador no meio): "99123-4567".
        cut = len(local) - 4
        patterns.append(re.compile(rf"(?<![\d\-.)]){re.escape(local[:cut])}[\s.\-]{re.escape(local[cut:])}(?![\d])"))
    else:
        cut = len(canonical) - 4
        patterns.append(
            re.compile(rf"(?<![\d]){re.escape(canonical[:cut])}{sep}{re.escape(canonical[cut:])}(?!\d)")
        )
    return patterns


def _word_regex(word: str) -> str:
    out: list[str] = []
    for char in word:
        folded = fold_lexical(char) or char.casefold()
        variants = _ACCENT.get(folded)
        if variants:
            out.append("[" + variants + variants.upper() + "]")
        elif char.isalpha():
            out.append(f"[{re.escape(char.lower())}{re.escape(char.upper())}]")
        else:
            out.append(re.escape(char))
    return "".join(out)


def _address_patterns(value: str) -> list[re.Pattern[str]]:
    words = _WORD.findall(value)
    if len(words) < 2:
        return []

    def build(items: Sequence[str]) -> re.Pattern[str]:
        parts: list[str] = []
        for index, word in enumerate(items):
            folded = fold_lexical(word)
            if index == 0 and folded in _VIA_ABBREV:
                parts.append("(?i:" + _VIA_ABBREV[folded] + r")\.?")
            elif word.isdigit():
                # "123", "nº 123", "n. 123", "numero 123": o rotulo do numero predial e opcional.
                parts.append(r"(?:(?i:n[º°o]?|nro|num|numero|número)\.?\s*)?" + re.escape(word))
            else:
                parts.append(_word_regex(word))
        body = _ADDRESS_SEP.join(parts)
        return re.compile(rf"(?<![^\W_])(?:{body})(?![^\W_])")

    patterns = [build(words)]
    number_at = next((index for index, word in enumerate(words) if word.isdigit()), None)
    if number_at is not None and 2 <= number_at < len(words) - 1:
        patterns.append(build(words[: number_at + 1]))
    return patterns


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in " \t\n,;:.-":
        start += 1
    while end > start and text[end - 1] in " \t\n,;:-":
        end -= 1
    return start, end
