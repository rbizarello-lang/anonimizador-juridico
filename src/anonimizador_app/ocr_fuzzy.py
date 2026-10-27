"""v0.4.12: nome da lista do caso casado com tolerancia a erro de OCR (chave ``ocr_fuzzy_names``).

Medido no caso 2 (auditoria do OCR, so contagens): 27% das mencoes a nomes nas folhas lidas por OCR saem com letra
trocada (M/N/K, l/1, rn/m) ou com palavras coladas; nas folhas nativas, 0. A regra exata nao as pega.

Regras (decisao do Operador e revisao do plano, 22/10):

- so a lista do caso (pessoas, empresas e grafias adicionais), nunca dicionario; so nome de 2+ palavras e 10+ letras;
- so nas folhas lidas por OCR (o texto depois de ``<!-- ocr: ... -->`` ate a proxima folha, e o recorte de imagem);
- distancia de edicao pequena e proporcional: ate 1 erro a cada 8 letras (no maximo 3); espacos entre as palavras sao
  opcionais (nome colado ou quebrado pelo OCR);
- o PRENOME nunca e tolerado por inteiro: nele so vale UMA troca entre letras que o OCR confunde (M/N/K/H/X/W, I/L/J/T,
  U/V, G/Q); vogal trocada, letra a mais ou a menos no prenome, nao (Maria nao casa Mario; Marcela nao casa Marcelo);
- se a grafia ficar perto de dois ou mais nomes da lista, nao casa com nenhum (irmaos com o mesmo sobrenome, homonimo de
  empresario individual) e a recusa vai para o AVISOS.md;
- o casamento entra com a chave do nome certo (mesmo token) e tambem vai para o AVISOS.md.

Dado generico; nenhum dado de caso.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MIN_WORDS = 2
MIN_LETTERS = 10
LETTERS_PER_ERROR = 8
MAX_ERRORS = 3

# Normalizacao dos dois lados (texto e lista): digito lido no lugar de letra e pares que o OCR funde.
_DIGIT_AS_LETTER = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "2": "z", "|": "l", "!": "l", "ı": "i", "€": "e"})
_PAIRS = (("rn", "m"), ("vv", "w"), ("ii", "u"))
# Letras que o OCR troca entre si (so estas valem no prenome, uma vez).
_CONFUSABLE = (frozenset("mnkhxw"), frozenset("iljt"), frozenset("uv"), frozenset("gq"))
_OCR_OPEN = re.compile(r"<!--\s*ocr:\s*([a-z-]+)\s*-->")
_OCR_CLOSE = re.compile(r"<!--\s*/ocr:\s*recorte\s*-->")
_PAGE = re.compile(r"<!--\s*pagina-origem:\s*\d+\s*-->")


def _fold_char(char: str) -> str:
    decomposed = unicodedata.normalize("NFKD", char)
    return "".join(item for item in decomposed if not unicodedata.combining(item)).lower()


def normalize(value: str) -> str:
    """So letras, minusculas, sem acento, com o digito-letra e os pares do OCR resolvidos."""

    letters = "".join(_fold_char(char) for char in value.translate(_DIGIT_AS_LETTER))
    letters = "".join(char for char in letters if char.isalpha())
    for pair, single in _PAIRS:
        letters = letters.replace(pair, single)
    return letters


def ocr_regions(text: str) -> list[tuple[int, int]]:
    """Trechos lidos por OCR: depois de ``<!-- ocr: X -->`` ate a proxima folha (ou o fecho do recorte)."""

    regions: list[tuple[int, int]] = []
    for match in _OCR_OPEN.finditer(text):
        start = match.end()
        if match.group(1) == "recorte":
            close = _OCR_CLOSE.search(text, start)
            end = close.start() if close else len(text)
        else:
            following = _PAGE.search(text, start)
            end = following.start() if following else len(text)
        if end > start:
            regions.append((start, end))
    return regions


@dataclass(frozen=True, slots=True)
class Target:
    key: str
    entity_type: str
    name: str
    alvo: str = ""


@dataclass(frozen=True, slots=True)
class Hit:
    start: int
    end: int
    target: Target
    distance: int


@dataclass(frozen=True, slots=True)
class Refusal:
    start: int
    end: int
    targets: tuple[Target, ...]


def eligible(name: str) -> bool:
    words = [word for word in re.findall(r"[^\W\d_]+", name) if len(word) > 1]
    return len(words) >= MIN_WORDS and len(normalize(name)) >= MIN_LETTERS


def tolerance(compact: str) -> int:
    return max(1, min(MAX_ERRORS, len(compact) // LETTERS_PER_ERROR))


def _first_name(name: str) -> str:
    words = re.findall(r"[^\W\d_]+", name)
    return normalize(words[0]) if words else ""


def first_name_ok(read: str, expected: str) -> bool:
    """O comeco lido (mesmo tamanho do prenome) e o prenome, com no maximo UMA troca entre letras confundiveis."""

    if len(read) < len(expected):
        return False
    diffs = [(one, two) for one, two in zip(read[: len(expected)], expected) if one != two]
    if not diffs:
        return True
    if len(diffs) > 1:
        return False
    one, two = diffs[0]
    return any(one in group and two in group for group in _CONFUSABLE)


def _distance_within(left: str, right: str, limit: int) -> int:
    """Levenshtein com corte: devolve limit+1 se passar do limite."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char != other)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1] if previous[-1] <= limit else limit + 1


class _Stream:
    """As letras normalizadas de um trecho, com o offset de cada uma no texto e as fronteiras de palavra."""

    def __init__(self, text: str, start: int, end: int) -> None:
        letters: list[str] = []
        offsets: list[int] = []
        word_start: list[bool] = []
        word_end: list[bool] = []
        previous_alpha = False
        hyphen = False
        position = start
        while position < end:
            char = text[position]
            mapped = _fold_char(char.translate(_DIGIT_AS_LETTER)) if not char.isspace() else ""
            if mapped and mapped.isalpha():
                for item in mapped:
                    letters.append(item)
                    offsets.append(position)
                    word_start.append(not previous_alpha)
                    word_end.append(False)
                    previous_alpha = True
                hyphen = False
            elif char == "-" and previous_alpha:
                hyphen = True  # hifen de quebra de linha ("ALEXAN-\nDRE") nao separa a palavra
            elif char.isspace() and hyphen:
                pass
            else:
                if previous_alpha and word_end:
                    word_end[-1] = True
                previous_alpha = False
                hyphen = False
            position += 1
        if word_end:
            word_end[-1] = True
        # Os pares que o OCR funde ("rn" lido por "m"), como em ``normalize``: a letra fica no offset da primeira.
        index = 0
        merged: list[str] = []
        merged_offsets: list[int] = []
        merged_start: list[bool] = []
        merged_end: list[bool] = []
        ends: list[int] = []
        while index < len(letters):
            pair = "".join(letters[index : index + 2])
            single = next((item for source, item in _PAIRS if source == pair), None)
            if single is not None and not word_end[index] and not word_start[index + 1]:
                merged.append(single)
                merged_offsets.append(offsets[index])
                merged_start.append(word_start[index])
                merged_end.append(word_end[index + 1])
                ends.append(offsets[index + 1] + 1)
                index += 2
                continue
            merged.append(letters[index])
            merged_offsets.append(offsets[index])
            merged_start.append(word_start[index])
            merged_end.append(word_end[index])
            ends.append(offsets[index] + 1)
            index += 1
        letters, offsets, word_start, word_end = merged, merged_offsets, merged_start, merged_end
        self.letters = "".join(letters)
        self.ends = ends
        self.offsets = offsets
        self.word_start = word_start
        self.word_end = word_end


def _pieces(compact: str, count: int) -> list[tuple[int, str]]:
    size = max(1, len(compact) // count)
    pieces = []
    for index in range(count):
        begin = index * size
        stop = len(compact) if index == count - 1 else begin + size
        pieces.append((begin, compact[begin:stop]))
    return pieces


def _matches_in(stream: _Stream, compact: str, first: str, limit: int) -> list[tuple[int, int, int]]:
    """(inicio, fim, distancia) em letras do stream: comeco e fim em fronteira de palavra, prenome conferido."""

    letters = stream.letters
    found: dict[tuple[int, int], int] = {}
    starts: set[int] = set()
    for offset, piece in _pieces(compact, limit + 1):
        if len(piece) < 3:
            continue
        index = letters.find(piece)
        while index >= 0:
            origin = index - offset
            for start in range(max(0, origin - limit), min(len(letters), origin + limit + 1)):
                if stream.word_start[start]:
                    starts.add(start)
            index = letters.find(piece, index + 1)
    for start in sorted(starts):
        if not first_name_ok(letters[start:], first):
            continue
        for stop in range(start + len(compact) - limit, start + len(compact) + limit + 1):
            if stop <= start or stop > len(letters) or not stream.word_end[stop - 1]:
                continue
            distance = _distance_within(letters[start:stop], compact, limit)
            if distance <= limit:
                key = (start, stop)
                if key not in found or distance < found[key]:
                    found[key] = distance
    return [(start, stop, distance) for (start, stop), distance in found.items()]


def find(text: str, targets: Sequence[Target]) -> tuple[list[Hit], list[Refusal]]:
    """Casamentos tolerantes nos trechos de OCR e as recusas por ambiguidade."""

    usable = [target for target in targets if eligible(target.name)]
    if not usable:
        return [], []
    raw: list[Hit] = []
    for region_start, region_end in ocr_regions(text):
        stream = _Stream(text, region_start, region_end)
        if not stream.letters:
            continue
        for target in usable:
            compact = normalize(target.name)
            limit = tolerance(compact)
            for start, stop, distance in _matches_in(stream, compact, _first_name(target.name), limit):
                begin = stream.offsets[start]
                end = stream.ends[stop - 1]
                raw.append(Hit(begin, end, target, distance))
    # Agrupa o que se sobrepoe: alvos diferentes no mesmo ponto = ambiguo (nao casa nenhum).
    raw.sort(key=lambda hit: (hit.start, -(hit.end - hit.start)))
    hits: list[Hit] = []
    refusals: list[Refusal] = []
    group: list[Hit] = []
    group_end = -1

    def flush() -> None:
        if not group:
            return
        exact = [hit for hit in group if hit.distance == 0]
        if exact:
            # Grafia exata de um nome (o mais longo: a firma "Fulano de Tal ME" contem a pessoa "Fulano de Tal"): nao e
            # ambiguo; a regra exata ja decidiu.
            hits.append(max(exact, key=lambda hit: hit.end - hit.start))
            return
        keys = {hit.target.key for hit in group}
        if len(keys) > 1:
            seen: dict[str, Target] = {}
            for hit in group:
                seen.setdefault(hit.target.key, hit.target)
            refusals.append(Refusal(min(h.start for h in group), max(h.end for h in group), tuple(seen.values())))
            return
        best = min(group, key=lambda hit: (hit.distance, -(hit.end - hit.start)))
        hits.append(best)

    for hit in raw:
        if group and hit.start >= group_end:
            flush()
            group = []
        group.append(hit)
        group_end = max(group_end, hit.end) if len(group) > 1 else hit.end
    flush()
    return hits, refusals


def targets_from(entries: Iterable[object]) -> list[Target]:
    """Os nomes (e grafias adicionais) das entradas da lista do caso (``target_list._TargetEntry``)."""

    found: list[Target] = []
    for entry in entries:
        for name in (*getattr(entry, "names", ()), *getattr(entry, "aliases", ())):
            found.append(Target(entry.canonical_key, entry.entity_type, name, entry.original))
    return found
