"""Prenome solto de pessoa do lote (v0.4.15, motor local).

Leitura folha a folha do sintetico 3 (v0.4.14): o prenome escrito sozinho ("a administracao fica a cargo de Fulano",
"as socias Fulana e Beltrana", "Sr. Sicrano") ficava em claro, embora a pessoa tivesse token no lote inteiro.

Regra: o prenome (primeira palavra de conteudo, 4+ letras) de uma pessoa do elenco do lote com nome de 2+ palavras,
escrito como nome proprio e SOZINHO (nem palavra de nome colada antes nem depois, fora "e"/virgula), recebe:

- o token da pessoa, quando so UMA pessoa do lote tem esse prenome;
- o token de prenome ``[PRENOME_n]`` (explicado no ELENCO), quando 2+ pessoas do lote o tem: nunca o de uma so.

Fica em claro: palavra do dicionario de uso comum, nome de municipio ou estado, termo protegido, prenome de agente
publico do lote (juiz, procurador), lugar ("Colonia Dona Fulana", "Rua Fulano"), julgador e citacao (fidelidade),
prenome seguido de "Filho"/"Neto" e o prenome em minuscula.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.pseudonymizer import fold_lexical

FIRST_NAME_TYPE = "FIRST_NAME"
RECOGNIZER = "PrenomeLote"
_SCORE = 0.88
_WORD = re.compile(r"[^\W\d_]+")
_CONNECTIVES = frozenset({"de", "da", "do", "das", "dos", "e"})
_MIN_LETTERS = 4
# Palavra de nome colada (antes ou depois, na mesma linha, so com espaco): o prenome e de um nome mais longo.
_NAME_BEFORE = re.compile(r"(?:[A-ZÀ-Ý][^\W\d_]+|(?i:d[aeo]s?))[ \t]+$")
_NAME_AFTER = re.compile(r"^[ \t]+(?:[A-ZÀ-Ý][^\W\d_]*|(?i:d[aeo]s?)[ \t]+[A-ZÀ-Ý])")
# Palavra capitalizada antes que NAO e nome (titulo, artigo, inicio de frase): o prenome continua sozinho.
_NOT_NAME_BEFORE = frozenset(
    "sr sra srta dona dom senhor senhora o a os as ao aos e com de da do por para pela pelo socio socia socios socias "
    "administrador administradora titular sucessor herdeiro herdeira conjuge esposa marido filho filha ex em".split()
)
_STREET_BEFORE = re.compile(
    r"(?i)\b(?:rua|r\.|avenida|av\.|travessa|alameda|pra[cç]a|rodovia|estrada|servid[aã]o|largo|beco)[ \t]+"
    r"(?:(?:dona|dom|santa|santo|s[ãa]o|doutor|dr\.?|professor|prof\.?|coronel|cel\.?|padre|pe\.?)[ \t]+)?$"
)


def first_word(name: str) -> str:
    words = [word for word in _WORD.findall(fold_lexical(name)) if word not in _CONNECTIVES]
    return words[0] if len(words) >= 2 else ""


def owners_by_first_name(people: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """(chave, nome) -> {prenome dobrado: chaves das pessoas}."""

    owners: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for key, name in people:
        word = first_word(name)
        if len(word) >= _MIN_LETTERS:
            owners[word].add(key)
            names[key] = name
    for word, keys in owners.items():
        # A mesma pessoa em duas grafias do lote ("Fulano Beltrano Silva" e "Fulano B. Silva": mesmo prenome e mesmo
        # ultimo sobrenome) conta como uma: o prenome vai para a grafia mais longa.
        if len(keys) < 2:
            continue
        longest = max(keys, key=lambda item: (len(names[item]), item))
        if all(_same_person(names[key], names[longest]) for key in keys):
            owners[word] = {longest}
    return owners


def _same_person(short: str, long: str) -> bool:
    """Mesmo prenome e mesmo ultimo sobrenome, e as palavras do meio do nome curto (ou as iniciais) no nome longo."""

    left = [word for word in _WORD.findall(fold_lexical(short)) if word not in _CONNECTIVES]
    right = [word for word in _WORD.findall(fold_lexical(long)) if word not in _CONNECTIVES]
    if len(left) < 2 or left[0] != right[0] or left[-1] != right[-1]:
        return False
    middle = right[1:-1]
    return all(any(word == other or (len(word) == 1 and other.startswith(word)) for other in middle) for word in left[1:-1])


def _blocked_word(word: str) -> bool:
    from .geo_names import is_geo_word
    from .target_list import is_blocked_particle
    from .variant_propagation import common_words

    return word in common_words() or is_geo_word(word) or is_blocked_particle(word)


def _alone(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    before = text[max(line_start, start - 40) : start]
    match = _NAME_BEFORE.search(before)
    if match is not None and fold_lexical(match.group(0).strip()).strip(".") not in _NOT_NAME_BEFORE:
        return False
    line_end = text.find("\n", end)
    after = text[end : line_end if line_end >= 0 else len(text)]
    return _NAME_AFTER.match(after) is None


def first_name_spans(
    text: str,
    owners: dict[str, set[str]],
    taken: Sequence[EntitySpan],
    protected: frozenset[str] = frozenset(),
) -> list[EntitySpan]:
    """Os prenomes soltos do texto, fora do que ``taken`` ja cobre."""

    from .fidelity import is_magistrate_or_citation, is_pfn_officer
    from .name_generation import generation_tail
    from .propagation import is_protected
    from .public_agents import public_agent_context
    from .target_list import PLACE_NAME_BEFORE

    occupied = sorted((span.start, span.end) for span in taken)
    found: list[EntitySpan] = []
    usable = {word: keys for word, keys in owners.items() if not _blocked_word(word)}
    if not usable:
        return found
    for match in _WORD.finditer(text):
        value = match.group(0)
        if not value[:1].isupper() or len(value) < _MIN_LETTERS:
            continue
        word = fold_lexical(value)
        keys = usable.get(word)
        if not keys:
            continue
        start, end = match.span()
        if any(start < other_end and other_start < end for other_start, other_end in occupied):
            continue
        if is_protected(value, protected) or not _alone(text, start, end):
            continue
        window = text[max(0, start - 60) : start]
        if _STREET_BEFORE.search(window) or PLACE_NAME_BEFORE.search(window):
            continue
        if generation_tail(text, end) is not None:
            continue
        if is_magistrate_or_citation(text, start, end) or is_pfn_officer(text, start, end):
            continue
        if public_agent_context(text, start, end):
            continue
        if len(keys) == 1:
            found.append(
                EntitySpan(start=start, end=end, entity_type="PERSON", score=_SCORE, recognizer=RECOGNIZER,
                           canonical_key=next(iter(keys)))
            )
        else:
            found.append(
                EntitySpan(start=start, end=end, entity_type=FIRST_NAME_TYPE, score=_SCORE, recognizer=RECOGNIZER,
                           canonical_key="prenome:" + word, alvo=value)
            )
    return found
