"""Nome completo como um token so (H9-i lote B4; regra 7.6 das regras consolidadas).

Depois da extensao por adjacencia (H9-g), ainda sobram dois casos de nome partido no texto final:

- dois spans de pessoa encostados ("[PESSOA_3] [PESSOA_7]" para "Maria Fernanda Souza"), porque a fusao restrita
  deixou "Maria" e "Fernanda Souza" como entradas separadas do elenco (na duvida, separado);
- o sufixo de geracao solto depois do token ("[PESSOA_2] Neto").

Aqui o nome escrito por inteiro vira UM span. Entre os dois pedacos so pode haver espaco, hifen de quebra de linha do
OCR ou particula (de, da, do, dos, das); "e", virgula, ponto, ponto e virgula, barra e quebra de linha sem hifen
separam (sao duas pessoas ou duas mencoes). Nada muda no elenco: e so a borda do span no texto deste arquivo.

Trava (nomes justapostos, fusao restrita de 16/10): dois nomes completos de entradas diferentes, cada um com 2+
palavras de conteudo, colados numa celula de tabela sem conectivo, sao duas pessoas e ficam separados.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace

from anonimizador_juridico import EntitySpan

# O que pode ficar entre dois pedacos do mesmo nome.
_JOINER = re.compile(r"(?:[ \t]+|-[ \t]*\n[ \t]*)(?:(?i:d[aeo]s?)[ \t]+)?")
# Sufixo de geracao depois do nome (com o espaco antes).
_GENERATION = re.compile(
    r"[ \t]+(?:Neto|Neta|Filho|Filha|J[uú]nior|Jr\.?|Sobrinho|Sobrinha|NETO|NETA|FILHO|FILHA|J[UÚ]NIOR|JR\.?|"
    r"SOBRINHO|SOBRINHA)(?![^\W\d_])"
)
_CONTENT = re.compile(r"[^\W\d_]{2,}")
_PARTICLES = frozenset({"de", "da", "do", "dos", "das"})


def _content_words(value: str) -> int:
    return sum(1 for word in _CONTENT.findall(value) if word.casefold() not in _PARTICLES)


def _from_case_list(key: str) -> bool:
    return key.startswith("alvo:")


def _merged_key(
    text: str, left: EntitySpan, right: EntitySpan, is_listed: Callable[[str], bool] = _from_case_list
) -> str:
    """A chave (e portanto o token) do nome inteiro que junta ``left`` e ``right``."""

    # Ordem (decisao do Operador, 16/10): mesma chave; a da lista do caso vence; o pedaco com mais palavras de conteudo
    # (identifica mais a pessoa); empate fica a esquerda, para o resultado ser sempre o mesmo.
    if left.canonical_key == right.canonical_key:
        return left.canonical_key
    left_listed = is_listed(left.canonical_key)
    right_listed = is_listed(right.canonical_key)
    if left_listed != right_listed:
        return left.canonical_key if left_listed else right.canonical_key
    left_words = _content_words(text[left.start : left.end])
    right_words = _content_words(text[right.start : right.end])
    return right.canonical_key if right_words > left_words else left.canonical_key


def join_person_names(
    text: str,
    entities: Sequence[EntitySpan],
    is_listed: Callable[[str], bool] = _from_case_list,
) -> tuple[list[EntitySpan], int]:
    """Junta os pedacos de um mesmo nome escrito por inteiro; devolve (spans, quantas juncoes). ``is_listed``: se a
    chave e de uma entrada da lista do caso (no elenco, ``source == "lista"``)."""

    ordered = sorted(entities, key=lambda span: (span.start, span.end))
    out: list[EntitySpan] = []
    joined = 0
    for span in ordered:
        previous = out[-1] if out else None
        if (
            previous is not None
            and previous.entity_type == "PERSON"
            and span.entity_type == "PERSON"
            and previous.end <= span.start
            and _JOINER.fullmatch(text[previous.end : span.start]) is not None
            and not _two_people(text, previous, span)
        ):
            out[-1] = replace(previous, end=span.end, canonical_key=_merged_key(text, previous, span, is_listed))
            joined += 1
            continue
        out.append(span)
    result: list[EntitySpan] = []
    for index, span in enumerate(out):
        if span.entity_type == "PERSON":
            match = _GENERATION.match(text, span.end)
            following = out[index + 1].start if index + 1 < len(out) else len(text)
            if match is not None and match.end() <= following:
                span = replace(span, end=match.end())
                joined += 1
        result.append(span)
    return result, joined


def _two_people(text: str, left: EntitySpan, right: EntitySpan) -> bool:
    """Dois nomes completos (2+ palavras cada) de entradas diferentes: duas pessoas justapostas."""

    if left.canonical_key == right.canonical_key:
        return False
    return (
        _content_words(text[left.start : left.end]) >= 2 and _content_words(text[right.start : right.end]) >= 2
    )
