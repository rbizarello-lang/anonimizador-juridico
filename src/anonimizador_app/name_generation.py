"""Sufixo de geracao no nome de pessoa (v0.4.10): "Fulano de Tal" e "Fulano de Tal Filho" sao DUAS pessoas.

Medido na matriz v0.4.9 (sintetico): pai e filho, avo e neto caiam no mesmo token em todos os modelos, porque a lista
do caso, a resolucao de mencao (que ignorava "Filho" como particula), a juncao do sufixo solto ("[PESSOA_2] Neto") e o
elenco uniam o nome curto ao longo. Aqui fica a regra unica: nomes que diferem pelo sufixo de geracao nunca se juntam.
"""

from __future__ import annotations

import re

from anonimizador_juridico.pseudonymizer import fold_lexical

# Forma dobrada -> forma normalizada (Jr. = Junior; F° = Filho).
_FOLDED = {
    "filho": "filho",
    "filha": "filha",
    "junior": "junior",
    "jr": "junior",
    "neto": "neto",
    "neta": "neta",
    "sobrinho": "sobrinho",
    "sobrinha": "sobrinha",
    "segundo": "segundo",
    "segunda": "segunda",
    "terceiro": "terceiro",
    "terceira": "terceira",
    "f°": "filho",
    "fº": "filho",
}
GENERATION_WORDS = frozenset(_FOLDED)
_WORD = re.compile(r"[^\s,;:()\[\]]+")

# Sufixo logo depois de um nome no texto (espaco ou quebra de linha; "Fi-\nlho" do fim de linha). "Segundo" e
# "Terceiro" ficam de fora no texto corrido ("Fulano segundo consta").
GENERATION_TAIL = re.compile(
    r"(?:[ \t]+|[ \t]*\n[ \t]*)(?P<word>(?i:fi-[ \t]*\n?[ \t]*lho|filh[oa]|j[uú]nior|jr\.?|net[oa]|sobrinh[oa]|f[º°]\.?))"
    r"(?![^\W\d_])"
)


def _norm(word: str) -> str:
    folded = fold_lexical(word.replace("-", "").replace("\n", "")).strip(".")
    return _FOLDED.get(folded, "")


def generation_of(value: str) -> frozenset[str]:
    """Os sufixos de geracao do nome (vazio se nenhum). So conta a palavra depois da primeira (o prenome "Neto"
    nao e sufixo)."""

    words = _WORD.findall(value or "")
    return frozenset(item for item in (_norm(word) for word in words[1:]) if item)


def differ_by_generation(left: str, right: str) -> bool:
    """Os dois nomes diferem por sufixo de geracao (pai x filho, avo x neto)?"""

    return generation_of(left) != generation_of(right)


def strip_generation(value: str) -> str:
    """O nome sem os sufixos de geracao ("Fulano de Tal Filho" -> "Fulano de Tal")."""

    words = (value or "").split()
    kept = [words[0], *(word for word in words[1:] if not _norm(word))] if words else []
    return " ".join(kept)


def generation_tail(text: str, end: int) -> re.Match[str] | None:
    """O sufixo de geracao que segue a posicao ``end`` (ou ``None``)."""

    return GENERATION_TAIL.match(text, end)


def suffix_label(match: re.Match[str]) -> str:
    """A forma normalizada do sufixo achado no texto ("filho", "neto"...)."""

    return _norm(match.group("word"))


def _plain(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", fold_lexical(value or "")).split())


def conflicting_generation(name: str, mention: str) -> bool:
    """A ``mention`` nao pode ser da pessoa ``name`` por causa do sufixo de geracao.

    Mencao com sufixo: so do nome com o mesmo sufixo. Mencao sem sufixo: so deixa de ser de "Fulano Filho" quando e
    o nome inteiro do pai (o prenome, o apelido e as iniciais continuam podendo ser dele)."""

    said = generation_of(mention)
    owner = generation_of(name)
    if said:
        return said != owner
    if not owner:
        return False
    return _plain(mention) == _plain(strip_generation(name))
