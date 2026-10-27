"""Fusao de identidade no elenco (H9-h, restrita): varias entradas da MESMA pessoa ou empresa viram uma so.

Politica "na duvida, separado" (decisao do condutor, 16/10/2026). A primeira versao (prefixo, sufixo, iniciais, palavra
do meio omitida, similaridade global) produziu no lote real fusoes indevidas que cada correcao pontual reabria: nomes
justapostos numa celula ou num bloco de assinaturas faziam de ponte entre duas pessoas, razoes sociais que diferem so
na sigla inicial foram fundidas, o nome do meio de uma magistrada casou com a ponta da variante de uma parte, um
prenome fundiu com uma firma individual. A fusao agora so aceita IDENTIDADE da forma inteira:

- pessoa: formas IGUAIS depois de normalizar (caixa, acentos, espacos, hifen de quebra de linha, pontuacao, titulo); ou
  grafia de OCR com o MESMO numero de palavras (2+), cada palavra igual ou a mesma salvo uma letra de OCR (nunca o par
  Adriano/Adriana), com ao menos uma palavra exata; ou o mesmo nome canonico do LLM (o nome de uma entrada e variante
  da outra). Todo elo passa pelo NOME de uma das entradas: a mesma variante repetida em duas entradas nao liga nenhuma. Nada de prefixo, sufixo, iniciais, palavra do meio omitida nem forma "forte" contida em outra;
- empresa: razoes sociais iguais depois de normalizar caixa, acentos, pontuacao, sufixo societario e as abreviaturas
  imp/exp/ind/com/cia; ou OCR com o mesmo numero de palavras, cada uma igual ou >= 0,9 e com ao menos uma exata; ou o
  mesmo nome canonico do LLM. A marca curta segue ligada pela H9-g (``VariantPropagator``), fora daqui;
- pessoa e empresa nunca se fundem. A MESMA forma escrita como pessoa e como empresa vira uma entrada EMPRESA;
- forma que junta duas entidades nunca serve para fundir: pessoa com "e", "/", "&", virgula ou ponto e virgula, ou toda
  em minuscula; empresa com "/", "&" ou ";" (fora o "S/A"), com " e " entre duas razoes sociais, e qualquer forma que
  contenha, inteiros e sem sobreposicao, os nomes de duas entradas do elenco (nomes justapostos);
- a decisao de unir dois grupos compara as formas ORIGINAIS de cada membro (as da entrada antes de qualquer fusao
  desta chamada), e todo membro de um grupo tem de casar com todo membro do outro: a fusao nao e transitiva por forma
  acumulada, nem por corrente de grafias (A~B, B~C, A distinto de C).

O caminho da "fusao fraca" (prefixo de 2 palavras com prova no texto e veto de parentesco) saiu: sem prefixo, nao ha
mais o que provar. Entrada fixada pelo usuario ou da lista de alvos nao entra.

Acao da entrada fundida (``merged_action``): "manter" vence se algum membro e cargo publico confirmado (juizo do lote ou
papel de magistrado, servidor, procurador, orgao) ou se todos ja eram "manter"; senao, tokenizar.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from difflib import SequenceMatcher

from anonimizador_juridico.pseudonymizer import fold_lexical

from .batch_propagation import _CONNECTIVES, _close
from .geo_names import UF_SIGLAS, is_geo_word
from .name_generation import conflicting_generation, generation_of
from .roster import KEEP, TOKENIZE, Roster, RosterEntry, entity_core, has_token

_WORD = re.compile(r"[^\W\d_]+")
_LINE_HYPHEN = re.compile(r"(?<=\w)-\s+(?=\w)")
_PUBLIC_ROLES = frozenset({"magistrado", "servidor_publico", "procurador_publico", "orgao_publico"})
_PRIVATE_ROLES = frozenset({"socio", "parte", "advogado"})
_UF_WORDS = frozenset(sigla.lower() for sigla in UF_SIGLAS.split("|"))
# Palavras de ramo que nao distinguem uma razao social de outra (ajuste 2).
_GENERIC_BRANCH = frozenset(
    "importacao exportacao comercio industria industrias servicos transportes participacoes".split()
) | _CONNECTIVES

_ABBREVIATIONS = {"imp": "importacao", "exp": "exportacao", "ind": "industria", "com": "comercio", "cia": "companhia"}
_RATIO_PERSON = 0.85
_RATIO_COMPANY = 0.9


def _words(kind: str, value: str) -> tuple[str, ...]:
    """Palavras dobradas da forma inteira (sem titulo nem sufixo societario; conectivo de dentro fica, o do fim sai).
    Razao social: "Imp. e Exp." e "Importacao e Exportacao" sao a mesma (R3)."""

    joined = _LINE_HYPHEN.sub("", value)
    words = _WORD.findall(fold_lexical(entity_core(kind, joined)))
    if kind == "empresa":
        words = [_ABBREVIATIONS.get(word, word) for word in words]
    while words and words[-1] in _CONNECTIVES:
        words.pop()
    return tuple(words)


def content_words(kind: str, value: str) -> list[str]:
    """Palavras de conteudo dobradas (sem titulo, sufixo societario nem conectivo)."""

    return [word for word in _words(kind, value) if word not in _CONNECTIVES]


def _written(value: str) -> tuple[str, ...]:
    """Forma como esta escrita (com sufixo societario), so dobrada: compara pessoa com empresa (item 3)."""

    return tuple(_WORD.findall(fold_lexical(_LINE_HYPHEN.sub("", value))))


def _word_close(left: str, right: str, kind: str, first: bool) -> bool:
    if left == right:
        return True
    person = kind == "pessoa"
    if not _close(left, right, person=person, first=first and person):
        return False
    return SequenceMatcher(None, left, right).ratio() >= (_RATIO_PERSON if person else _RATIO_COMPANY)


def _ocr_match(left: tuple[str, ...], right: tuple[str, ...], kind: str) -> bool:
    """Mesmo numero de palavras (2+), cada uma igual ou a mesma salvo OCR, com ao menos uma palavra exata."""

    if len(left) < 2 or len(left) != len(right) or left == right:
        return False
    if not any(a == b for a, b in zip(left, right)):
        return False
    return all(_word_close(a, b, kind, number == 0) for number, (a, b) in enumerate(zip(left, right)))


_BRIDGE = re.compile(r"\s[eE&]\s|[/&,;]")
_COMPANY_BRIDGE_CHARS = re.compile(r"[/&;]")
_SA = re.compile(r"\bs\s*/\s*a\b", re.IGNORECASE)
_SOCIETARY = re.compile(r"(?i)\b(?:ltda|eireli|epp|me|s\.?\s*a)\b\.?")
_CORPORATE_TAIL = re.compile(r"(?i)[\s,.\-]+(?:ltda|eireli|epp)\.?\s*$|\bs\s*/\s*a\b|\bs\.a\.?\s*$")
_CORPORATE_UPPER = re.compile(r"[\s,]+(?:ME|EPP)\.?\s*$")
_CORPORATE_WORDS = frozenset(
    "agroflorestal agropecuaria agroindustrial comercio industria industrias importacao exportacao participacoes holding "
    "transportes construtora incorporadora distribuidora laticinios cooperativa associacao fundacao empreendimentos "
    "madeireira siderurgica".split()
)


def is_bridge_form(kind: str, value: str) -> bool:
    """Variante que nao pode ligar identidades (R1): pessoa com "e", "/", "&", virgula ou ponto e virgula no meio
    (duas pessoas numa grafia) ou escrita toda em minuscula ("fatica": palavra de texto, nao nome). Empresa: "/", "&"
    ou ";" (o "S/A" do sufixo nao conta) e " e " entre duas razoes sociais ("Alfa Ltda e Beta Ltda")."""

    if kind == "empresa":
        bare = _SA.sub(" ", value)
        if _COMPANY_BRIDGE_CHARS.search(bare):
            return True
        parts = re.split(r"\s[eE]\s", bare)
        return len(parts) > 1 and sum(1 for part in parts if _SOCIETARY.search(part)) >= 2
    if kind != "pessoa":
        return False
    return bool(_BRIDGE.search(value)) or not any(char.isupper() for char in value)


def is_corporate(value: str) -> bool:
    """Nome com sufixo societario (Ltda, S/A, EIRELI, ME, EPP) ou palavra de razao social (R2): e empresa."""

    if _CORPORATE_TAIL.search(value) or _CORPORATE_UPPER.search(value):
        return True
    return any(word in _CORPORATE_WORDS for word in _WORD.findall(fold_lexical(value)))


def _distinctive(words: tuple[str, ...]) -> list[str]:
    """Palavras que sobram da razao social sem municipio/UF, conectivo nem palavra generica de ramo (ajuste 2)."""

    return [
        word for word in words if word not in _GENERIC_BRANCH and word not in _UF_WORDS and not is_geo_word(word)
    ]


def _contains_two_names(words: tuple[str, ...], names: Sequence[tuple[str, ...]]) -> bool:
    """A forma contem, inteiros e sem sobreposicao, os nomes (2+ palavras) de duas entradas do elenco?"""

    spans: set[tuple[int, int]] = set()
    for name in names:
        size = len(name)
        if size < 2 or name == words:
            continue
        for start in range(len(words) - size + 1):
            if words[start : start + size] == name:
                spans.add((start, start + size))
    ordered = sorted(spans)
    return any(
        first[1] <= second[0] for position, first in enumerate(ordered) for second in ordered[position + 1 :]
    )


class _Cluster:
    """Uma entrada do elenco com as formas ORIGINAIS dela (nome e variantes) e o tipo (R2: sufixo societario)."""

    __slots__ = ("key", "kind", "forms", "name_form", "retyped", "written", "values")

    def __init__(self, entry: RosterEntry, names: dict[str, list[tuple[str, ...]]]) -> None:
        self.key = entry.key
        self.kind = entry.kind
        self.retyped = False
        if entry.kind == "pessoa" and any(is_corporate(value) for value in (entry.name, *entry.variants)):
            self.kind = "empresa"
            self.retyped = True
        self.values = [
            value for value in (entry.name, *entry.variants) if not has_token(value) and not is_bridge_form(self.kind, value)
        ]
        self.forms: set[tuple[str, ...]] = set()
        self.written: set[tuple[str, ...]] = set()
        self.name_form: tuple[str, ...] = ()
        self._build(entry, names)

    def _build(self, entry: RosterEntry, names: dict[str, list[tuple[str, ...]]]) -> None:
        pool = names.get(self.kind, [])
        self.forms = set()
        self.written = set()
        for value in self.values:
            words = _words(self.kind, value)
            if not words or _contains_two_names(words, pool):
                continue
            self.forms.add(words)
            self.written.add(_written(value))
            if value == entry.name:
                self.name_form = words

    def retype_company(self, entry: RosterEntry, names: dict[str, list[tuple[str, ...]]]) -> None:
        self.kind = "empresa"
        self.retyped = True
        self.values = [value for value in self.values if not is_bridge_form("empresa", value)]
        self._build(entry, names)


def _may_retype(cluster: _Cluster, clusters: Sequence[_Cluster], roster: Roster, by_key: dict[str, RosterEntry]) -> bool:
    """Ajuste 1: a forma escrita como pessoa e como empresa so vira EMPRESA com sufixo societario ou palavra de razao
    social E sem papel de pessoa (socio, parte, advogado) em nenhum membro; senao as duas entradas ficam separadas."""

    shared = cluster.written & {
        _written(value)
        for other in clusters
        if other.kind == "empresa"
        for value in other.values
    } | cluster.written & {
        _written(value)
        for entry in roster.entries
        if entry.kind == "empresa" and not _eligible(entry)
        for value in (entry.name, *entry.variants)
    }
    holders = [by_key[cluster.key]]
    for other in clusters:
        if other is not cluster and other.kind == "empresa" and other.written & shared:
            holders.append(by_key[other.key])
    holders += [
        entry for entry in roster.entries
        if entry.kind == "empresa" and not _eligible(entry)
        and any(_written(value) in shared for value in (entry.name, *entry.variants))
    ]
    forms = [
        value for entry in holders for value in (entry.name, *entry.variants) if _written(value) in shared
    ]
    if not any(is_corporate(value) for value in forms):
        return False
    # Pessoa: qualquer papel privado veta. Empresa: "parte" e o papel normal dela; so socio e advogado vetam.
    if set(holders[0].roles) & _PRIVATE_ROLES:
        return False
    return not any(set(entry.roles) & {"socio", "advogado"} for entry in holders[1:])


def _eligible(entry: RosterEntry) -> bool:
    return (
        entry.kind in {"pessoa", "empresa"}
        and not entry.pinned
        and entry.source != "lista"
        and bool(entry.name.strip())
    )


def _relation(left: _Cluster, right: _Cluster) -> str:
    """"igual" (os dois nomes iguais depois de normalizar), "canonico" (o nome de uma e variante da outra: o LLM deu o
    mesmo nome canonico), "ocr" (grafia de OCR de um nome contra uma forma da outra) ou "" (separadas). Todo elo passa
    pelo NOME de uma das entradas: a mesma variante repetida em duas entradas nao liga nenhuma das duas."""

    if left.kind != right.kind or not left.name_form or not right.name_form:
        return ""
    if left.kind == "pessoa" and generation_of(" ".join(left.name_form)) != generation_of(" ".join(right.name_form)):
        return ""  # v0.4.10: pai e filho, avo e neto nunca se fundem
    if left.name_form == right.name_form:
        # Ajuste 2: razao social sem palavra distintiva (so municipio, ramo e conectivo) nao funde por "igual".
        if left.kind == "empresa" and not _distinctive(left.name_form):
            return ""
        return "igual"
    if left.name_form in right.forms or right.name_form in left.forms:
        # Ajuste 3: o nome canonico so liga formas com 2+ palavras de conteudo (nunca um sobrenome sozinho).
        if min(len(content_words(left.kind, " ".join(left.name_form))), len(content_words(right.kind, " ".join(right.name_form)))) < 2:
            return ""
        return "canonico"
    if any(_ocr_match(left.name_form, form, left.kind) for form in right.forms) or any(
        _ocr_match(form, right.name_form, left.kind) for form in left.forms
    ):
        return "ocr"
    return ""


# H9-j: o mesmo CNPJ colado a duas razoes sociais prova a mesma pessoa juridica (prova, nao aproximacao). No teste de
# 16/10 o nome antigo da requerida aparecia no RENAJUD com o CNPJ dela e virou outro token. Matriz e filiais (mesma raiz
# de 8 digitos) sao a mesma pessoa juridica.
_CNPJ_IN_TEXT = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}\s?/?\s?\d{4}\s?-?\s?\d{2}(?!\d)")
_CNPJ_GAP = re.compile(
    r"(?i)^[\s|,;:()\[\]\-–—]*(?:(?:inscrit[ao]\s+no\s+|sob\s+o\s+|com\s+)?(?:cnpj|c\.n\.p\.j\.?)(?:\s*/\s*mf)?"
    r"\s*(?:n[º°o.]{1,2})?)?[\s|,;:()\-–—]*$"
)
_CNPJ_WINDOW = 160
_CNPJ_TOKEN = re.compile(r"\[CNPJ_\d+\]")
# Depois do CNPJ e antes do nome: so pontuacao, separador de tabela e rotulo ("| [CNPJ_1]: NOME").
_CNPJ_LEAD = re.compile(r"(?i)^[\s|,;:()\[\]\-–—]*(?:cnpj\s*[:\-]?\s*)?[\s|,;:()\-–—]*$")
_CNPJ_LEAD_START = re.compile(r"(?i)^[\s|,;:()\[\]\-–—]*(?:cnpj\s*[:\-]?\s*)?[\s|,;:()\-–—]*$")


def _cnpj_roots(text: str, tokens: dict[str, str] | None = None) -> list[tuple[int, int, str]]:
    """(inicio, fim, raiz de 8 digitos) de cada CNPJ valido do texto: escrito, ou o token ``[CNPJ_n]`` (no pipeline o
    texto ja chega com o CNPJ mascarado; ``tokens`` traz token -> 14 digitos, vindo do mapa)."""

    from anonimizador_juridico.validators import is_valid_cnpj, only_digits

    found: list[tuple[int, int, str]] = []
    for match in _CNPJ_IN_TEXT.finditer(text):
        digits = only_digits(match.group(0))
        if len(digits) == 14 and is_valid_cnpj(digits):
            found.append((match.start(), match.end(), digits[:8]))
    if tokens:
        for match in _CNPJ_TOKEN.finditer(text):
            digits = tokens.get(match.group(0), "")
            if len(digits) == 14:
                found.append((match.start(), match.end(), digits[:8]))
    return found


def _cnpj_links(
    clusters: Sequence["_Cluster"],
    by_key: dict[str, RosterEntry],
    texts: Sequence[str],
    tokens: dict[str, str] | None = None,
) -> list[tuple[int, int]]:
    """Pares de grupos de empresa cujo nome aparece colado ao mesmo CNPJ (mesma raiz), em qualquer texto do lote: o
    nome antes do CNPJ ("NOME (CNPJ 11.222...)") ou depois dele ("| [CNPJ_1]: NOME |", extrato RENAJUD/SISBAJUD).
    v0.4.11: a forma de uma palavra distintiva tambem liga, so quando esta sozinha (sem outra palavra de nome colada);
    entre formas que casam no mesmo ponto vence a mais longa."""

    from . import features

    owners: dict[str, set[int]] = defaultdict(set)
    for number, roots in _cluster_roots(clusters, by_key, texts, tokens).items():
        if len(roots) >= 2 and features.enabled("shared_company_names"):
            # v0.4.13: nome colado a dois CNPJ de raizes diferentes e denominacao usada por duas empresas: nao liga a
            # nenhuma delas (fundir com uma juntaria as ocorrencias da outra; ``shared_names`` separa por trecho).
            continue
        for root in roots:
            owners[root].add(number)
    pairs: set[tuple[int, int]] = set()
    for members in owners.values():
        ordered = sorted(members)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                pairs.add((left, right))
    return sorted(pairs)


# --- v0.4.11: unificacao de empresa (forma curta, CNPJ, denominacao declarada) ---------------------------------------
#
# Leitura cega do caso real (v0.4.10): a requerida ficou em 2 tokens alternados (razao social x forma curta) e as
# empresas do grupo em 2-3 tokens; o avaliador mostrou tambem o nome ANTIGO de uma empresa e a forma curta de OUTRA
# (mesmo radical) no mesmo token. Regras, todas "na duvida, separado":
#
# - forma de UMA palavra so liga por CNPJ ou declaracao se for distintiva (4+ letras, nao ramo generico, nao lugar);
# - declaracao no texto: "X (doravante Y)", "X, nome fantasia Y", "X, atual/antiga denominacao Y", "X, antiga Y",
#   "X, anteriormente denominada Y" (nunca "sucessora": sucessao e outra pessoa juridica);
# - forma curta sem declaracao: as palavras dela sao o COMECO das formas de UM grupo so do elenco, nenhuma outra
#   empresa do elenco nem outra razao social escrita no lote comeca por elas, e nenhum CNPJ diferente esta colado a
#   cada uma;
# - a forma curta que ficou numa entrada mas tambem abre o nome de OUTRA empresa (radical comum) sai da entrada.

_BRAND_MIN = 4
_DECLARED_CUE = re.compile(
    r"(?i)(?<![^\W\d_])(?:doravante(?:\s+(?:denominad[ao]|designad[ao]|chamad[ao]|referid[ao]))?(?:\s+(?:apenas|simplesmente))?"
    r"(?:\s+(?:como|de))?"
    r"|(?:com\s+(?:o\s+)?|sob\s+o\s+|de\s+)?nome\s+(?:de\s+)?fantasia(?:\s+de)?"
    r"|atual(?:mente)?\s+denominad[ao]|atual\s+denomina[cç][aã]o(?:\s+de)?|antiga(?:mente)?\s+denominad[ao]"
    r"|anteriormente\s+denominad[ao]|antes\s+denominad[ao]|(?:antiga|anterior|nova)\s+denomina[cç][aã]o(?:\s+de)?"
    r"|denomina[cç][aã]o\s+(?:anterior|antiga|atual)(?:\s+de)?|antiga|atual|ex-)(?![^\W\d_])"
)
_DECLARED_GAP_BEFORE = re.compile(r"^[\s,;(\[\-–—]*$")
_DECLARED_GAP_AFTER = re.compile(r"^[\s:,(\[“”\"'‘’]*$")
_COMPANY_IN_TEXT = re.compile(
    r"(?<![^\W\d_])([A-ZÀ-Ý][^\W\d_]{2,}(?:[ \t]+(?:[A-ZÀ-Ý&][^\W\d_]*|d[aeo]s?|e)){0,7}?)[ \t,.\-]+"
    r"(?:LTDA|Ltda|S/A|S\.A\.?|SA|EIRELI|Eireli|EPP|ME)\b"
)


def _distinctive_word(word: str) -> bool:
    return len(word) >= _BRAND_MIN and word not in _GENERIC_BRANCH and word not in _UF_WORDS and not is_geo_word(word)


def _linkable_form(value: str) -> bool:
    """Forma que pode ligar por CNPJ ou declaracao: 2+ palavras de conteudo, ou uma palavra distintiva."""

    words = content_words("empresa", value)
    return len(words) >= 2 or (len(words) == 1 and _distinctive_word(words[0]))


_NAME_WORD_BEFORE = re.compile(r"[A-ZÀ-Ý][^\W\d_]*[ \t]+$")
_NAME_WORD_AFTER = re.compile(r"^[ \t]+(?:[A-ZÀ-Ý][^\W\d_]|d[aeo]s?[ \t]+[A-ZÀ-Ý])")


def _isolated(text: str, start: int, end: int) -> bool:
    """A forma de uma palavra esta sozinha: nem palavra de nome logo antes, nem logo depois (senao e o comeco ou o fim
    de OUTRO nome, ex. o nome antigo de outra empresa com o mesmo radical)."""

    return not _NAME_WORD_BEFORE.search(text[max(0, start - 40) : start]) and not _NAME_WORD_AFTER.match(
        text[end : end + 40]
    )


def _search(pattern: re.Pattern[str], single: bool, text: str, pos: int = 0):
    for match in pattern.finditer(text, pos):
        if not single or _isolated(text, match.start(), match.end()):
            yield match


def _form_patterns(
    clusters: Sequence["_Cluster"], by_key: dict[str, RosterEntry]
) -> list[tuple[int, re.Pattern[str], bool]]:
    forms: list[tuple[int, re.Pattern[str], bool]] = []
    for number, cluster in enumerate(clusters):
        if cluster.kind != "empresa":
            continue
        entry = by_key.get(cluster.key)
        if entry is None:
            continue
        for value in (entry.name, *entry.variants):
            if has_token(value) or not _linkable_form(value) or is_bridge_form("empresa", value):
                continue
            words = re.findall(r"[^\W_]+", _SOCIETARY.sub(" ", value))
            if words:
                forms.append(
                    (
                        number,
                        re.compile(
                            r"(?i)(?<![^\W_])" + r"[\s.,/&-]+".join(map(re.escape, words))
                            + r"(?:[\s,.\-]+(?:ltda|eireli|epp|me|s\.?\s*/?\s*a)\.?)?(?![^\W_])"
                        ),
                        len(content_words("empresa", value)) < 2,
                    )
                )
    return forms


def _declared_links(
    clusters: Sequence["_Cluster"], by_key: dict[str, RosterEntry], texts: Sequence[str]
) -> list[tuple[int, int]]:
    """Pares de empresas que o texto declara serem a mesma: o nome logo antes da expressao e o nome logo depois."""

    forms = _form_patterns(clusters, by_key)
    if len(forms) < 2:
        return []
    pairs: set[tuple[int, int]] = set()
    for text in texts:
        for cue in _DECLARED_CUE.finditer(text):
            line_start = max(text.rfind("\n", 0, cue.start()) + 1, cue.start() - _CNPJ_WINDOW)
            before = text[line_start : cue.start()]
            after = text[cue.end() : cue.end() + _CNPJ_WINDOW]
            left = _best_before(forms, before, _DECLARED_GAP_BEFORE)
            if left is None:
                continue
            right = _best_after(forms, after, _DECLARED_GAP_AFTER)
            if right is None or right == left:
                continue
            pairs.add((min(left, right), max(left, right)))
    return sorted(pairs)


def _best_before(forms, before: str, gap: re.Pattern[str]) -> int | None:
    """O grupo cuja forma termina logo antes do ponto (so o ``gap`` no meio); a que termina mais perto e, no empate, a
    mais longa."""

    best: tuple[int, int, int] | None = None
    for number, pattern, single in forms:
        for match in _search(pattern, single, before):
            if gap.match(before[match.end() :]):
                rank = (match.end(), match.end() - match.start())
                if best is None or rank > best[:2]:
                    best = (*rank, number)
    return best[2] if best is not None else None


def _best_after(forms, after: str, gap: re.Pattern[str]) -> int | None:
    """O grupo cuja forma comeca logo depois do ponto; no empate de inicio, a mais longa."""

    best: tuple[int, int, int] | None = None
    for number, pattern, single in forms:
        for match in _search(pattern, single, after):
            if not gap.match(after[: match.start()]):
                continue
            rank = (-match.start(), match.end() - match.start())
            if best is None or rank > best[:2]:
                best = (*rank, number)
            break
    return best[2] if best is not None else None


def _cluster_roots(
    clusters: Sequence["_Cluster"], by_key: dict[str, RosterEntry], texts: Sequence[str], tokens: dict[str, str] | None
) -> dict[int, set[str]]:
    """Raizes de CNPJ coladas a cada grupo (indice do cluster -> raizes de 8 digitos)."""

    forms = _form_patterns(clusters, by_key)
    roots: dict[int, set[str]] = defaultdict(set)
    for text in texts:
        for start, end, root in _cnpj_roots(text, tokens):
            line_start = max(text.rfind("\n", 0, start) + 1, start - _CNPJ_WINDOW)
            before = text[line_start:start]
            line_end = text.find("\n", end)
            after = text[end : min(line_end if line_end >= 0 else len(text), end + _CNPJ_WINDOW)]
            best = _best_before(forms, before, _CNPJ_GAP)
            if best is None:
                best = _best_after(forms, after, _CNPJ_LEAD_START)
            if best is not None:
                roots[best].add(root)
    return roots


def _company_forms_of(entry: RosterEntry) -> list[tuple[str, ...]]:
    return [
        tuple(content_words("empresa", value))
        for value in (entry.name, *entry.variants)
        if not has_token(value) and not is_bridge_form("empresa", value) and content_words("empresa", value)
    ]


def _starts_with(form: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(form) > len(prefix) and form[: len(prefix)] == prefix


def _written_companies(texts: Sequence[str]) -> set[tuple[str, ...]]:
    """Razoes sociais escritas no lote (nome com sufixo societario), em palavras de conteudo."""

    found: set[tuple[str, ...]] = set()
    for text in texts:
        for match in _COMPANY_IN_TEXT.finditer(text):
            words = tuple(content_words("empresa", match.group(1)))
            if len(words) >= 2:
                found.add(words)
    return found


def _same_entity_forms(forms: Sequence[tuple[str, ...]], other: tuple[str, ...]) -> bool:
    return any(form == other or _ocr_match(form, other, "empresa") for form in forms)


def fuse_short_forms(roster: Roster, texts: Sequence[str] = (), cnpj_tokens: dict[str, str] | None = None) -> dict[str, str]:
    """A entrada de empresa cujo nome e a forma curta (o comeco, em palavras inteiras) das formas de UMA outra entrada
    vira essa outra, quando nada no lote abre com as mesmas palavras e o CNPJ nao as separa. Devolve {absorvida: que
    ficou}."""

    companies = [
        entry
        for entry in roster.entries
        if entry.kind == "empresa" and not entry.pinned and entry.name.strip() and entry.action == TOKENIZE
    ]
    if len(companies) < 2:
        return {}
    all_forms = {entry.key: _company_forms_of(entry) for entry in roster.entries if entry.kind == "empresa"}
    written = _written_companies(texts)
    clusters = [_Cluster(entry, {}) for entry in companies]
    by_key = {entry.key: entry for entry in companies}
    roots = _cluster_roots(clusters, by_key, texts, cnpj_tokens)
    root_of = {clusters[number].key: found for number, found in roots.items()}
    removed: dict[str, str] = {}
    for short in companies:
        if short.key in removed or short.source == "lista":
            continue
        if roster.contested("empresa", short.name):
            continue  # v0.4.15: o LLM afirmou esta forma de duas empresas diferentes
        prefix = tuple(content_words("empresa", short.name))
        if not prefix or len(prefix) > 2 or not _distinctive_word(prefix[0]):
            continue
        if any(len(form) > len(prefix) for form in all_forms.get(short.key, [])):
            continue  # a entrada ja tem forma longa propria: nao e so a forma curta
        owners = [
            key for key, forms in all_forms.items()
            if key != short.key and key not in removed and any(_starts_with(form, prefix) for form in forms)
        ]
        if len(owners) != 1:
            continue  # nenhuma ou 2+ empresas abrem com estas palavras (radical comum): separado
        target = roster.get(owners[0])
        if target is None or target.action != TOKENIZE:
            continue
        target_forms = all_forms[target.key]
        foreign = [
            form for form in written
            if (_starts_with(form, prefix) or form == prefix) and not _same_entity_forms(target_forms, form)
        ]
        if foreign:
            continue  # outra razao social escrita no lote abre com as mesmas palavras
        mine, theirs = root_of.get(short.key, set()), root_of.get(target.key, set())
        if mine and theirs and not mine & theirs:
            continue
        roster.merge(target.key, [short.key])
        roster.identity_reasons["forma_curta"] = roster.identity_reasons.get("forma_curta", 0) + 1
        removed[short.key] = target.key
        all_forms[target.key] = _company_forms_of(target)
        all_forms.pop(short.key, None)
    return removed


def drop_ambiguous_short_variants(roster: Roster, texts: Sequence[str] = ()) -> int:
    """A variante curta (1-2 palavras de conteudo) de uma empresa que tambem abre o nome de OUTRA empresa do elenco ou
    outra razao social escrita no lote sai da entrada: o radical comum nao diz de quem e (colisao e o pior erro)."""

    entries = [entry for entry in roster.entries if entry.kind == "empresa"]
    forms = {entry.key: _company_forms_of(entry) for entry in entries}
    written = _written_companies(texts)
    dropped = 0
    for entry in entries:
        if entry.pinned:
            continue
        for value in list(entry.variants):
            short = tuple(content_words("empresa", value))
            if not short or len(short) > 2 or tuple(content_words("empresa", entry.name)) == short:
                continue
            others = [
                key for key, items in forms.items()
                if key != entry.key and any(_starts_with(form, short) or form == short for form in items)
            ]
            foreign = [
                form for form in written
                if _starts_with(form, short) and not _same_entity_forms(forms[entry.key], form)
            ]
            if others or foreign:
                roster.drop_variant(entry.key, value)
                dropped += 1
    if dropped:
        roster.identity_reasons["forma_curta_ambigua"] = roster.identity_reasons.get("forma_curta_ambigua", 0) + dropped
    return dropped


# --- v0.4.11: pessoa com e sem nome do meio -----------------------------------------------------------------------------
#
# Leitura cega (variante qwen do caso real): uma socia em 2 tokens; filho e filha em 2 tokens cada. A fusao restrita
# (H9-h) so aceitava a forma inteira igual. Aqui, so entre os NOMES das entradas (nunca variante como ponte):
#
# - mesmo prenome e mesmo ultimo sobrenome, o mesmo sufixo de geracao (Filho/Junior/Neto: pai e filho nunca), o nome
#   mais curto com 2+ palavras e as palavras do meio dele presentes, na ordem, no mais longo (iguais ou a inicial:
#   "M." = "Maria"); o mais longo com no maximo 2 palavras a mais;
# - o nome curto cabe em UMA entrada so (dois candidatos: "Joao Silva" de "Joao Carlos Silva" ou de "Joao Pedro Silva"
#   fica separado);
# - CPF diferente colado a cada um separa;
# - mae e filha ("Maria Clara X Y" x "Maria Clara X Y Z") e a casada com o sobrenome do marido terminam em sobrenomes
#   diferentes e nao se unem (protecao da v0.4.10 mantida).

_CPF_NEAR = re.compile(
    r"(?i)[\s,;:(\-–—|]*(?:(?:portador(?:a)?\s+do\s+|inscrit[oa]\s+no\s+)?cpf(?:\s*/\s*mf)?(?:\s+sob\s+o)?"
    r"\s*(?:n[º°o.]{0,2})?)?[\s:,\-–—|]*(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\[CPF_\d+\])"
)


def _person_words(value: str) -> tuple[str, ...]:
    from .name_generation import strip_generation

    return tuple(content_words("pessoa", strip_generation(value)))


def _middle_compatible(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    if len(short) < 2 or len(long) < len(short) or len(long) > len(short) + 2 or short == long:
        return False
    if len(long) == len(short) and not any(len(word) == 1 for word in short[1:-1]):
        return False  # mesmo tamanho: so a forma com o nome do meio abreviado ("M." = "Maria")
    if short[0] != long[0] or short[-1] != long[-1]:
        return False
    rest = iter(long[1:-1])
    for word in short[1:-1]:
        for candidate in rest:
            if candidate == word or (len(word) == 1 and candidate.startswith(word)):
                break
        else:
            return False
    return True


def _person_cpfs(entry: RosterEntry, texts: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for value in (entry.name, *entry.variants):
        words = re.findall(r"[^\W_]+", value)
        if len(words) < 2:
            continue
        pattern = re.compile(r"(?i)(?<![^\W_])" + r"[\s.,-]+".join(map(re.escape, words)) + r"(?![^\W_])")
        for text in texts:
            for match in pattern.finditer(text):
                cpf = _CPF_NEAR.match(text, match.end(), min(len(text), match.end() + 60))
                if cpf is not None:
                    found.add(re.sub(r"\D", "", cpf.group(1)) or cpf.group(1))
    return found


def fuse_middle_names(
    roster: Roster, texts: Sequence[str] = (), cnpj_tokens: dict[str, str] | None = None
) -> dict[str, str]:
    """Funde a entrada de pessoa cujo nome e o de outra sem (parte do) nome do meio. Devolve {absorvida: que ficou}."""

    people = [
        entry for entry in roster.entries
        if entry.kind == "pessoa" and entry.name.strip() and not has_token(entry.name)
        and not is_bridge_form("pessoa", entry.name)
    ]
    words = {entry.key: _person_words(entry.name) for entry in people}
    removed: dict[str, str] = {}
    for short in sorted(people, key=lambda entry: len(words[entry.key])):
        if short.key in removed or short.pinned or short.action != TOKENIZE or short.source == "lista":
            continue
        mine = words[short.key]
        if len(mine) < 2:
            continue
        candidates = [
            other for other in people
            if other.key != short.key and other.key not in removed
            and generation_of(other.name) == generation_of(short.name)
            and _middle_compatible(mine, words[other.key])
        ]
        if len(candidates) != 1:
            continue
        target = candidates[0]
        if target.action != TOKENIZE or target.pinned:
            continue
        # outra pessoa do elenco com o mesmo prenome e ultimo sobrenome que nao e compativel com o alvo: ambiguo
        rivals = [
            other for other in people
            if other.key not in {short.key, target.key} and other.key not in removed
            and words[other.key][:1] == mine[:1] and words[other.key][-1:] == mine[-1:]
            and not _middle_compatible(words[other.key], words[target.key])
            and words[other.key] != words[target.key]
        ]
        if rivals:
            continue
        cpf_short, cpf_target = _person_cpfs(short, texts), _person_cpfs(target, texts)
        if cpf_short and cpf_target and not cpf_short & cpf_target:
            continue
        roster.merge(target.key, [short.key])
        roster.identity_reasons["nome_do_meio"] = roster.identity_reasons.get("nome_do_meio", 0) + 1
        removed[short.key] = target.key
    return removed


# --- v0.4.13: forma parcial achada pelo LLM ligada a UMA entrada do elenco ----------------------------------------------
#
# Rodada 4 (caso real 2): a Gemma deixou 16 entidades fragmentadas sem cobertura no ELENCO (motor: 7). O LLM devolve
# pedacos do nome que o elenco ja tem (sobrenome sozinho, prenome sozinho, prenome + 2o nome, razao social sem o sufixo
# ou com a ultima palavra abreviada) e cada pedaco virava token novo. So entradas que o LLM criou (origem ``llm`` ou
# ``verificacao``), so se a forma cabe em UMA entrada existente do mesmo tipo, e nada se a forma cabe em 2+ (sobrenome de
# familia, radical comum de empresas do grupo): na duvida, separado.
#
# - pessoa, uma palavra (4+ letras, nao municipio): ela e prenome ou sobrenome de UMA pessoa so do elenco (em qualquer
#   posicao do nome, entre todas as pessoas, mantidas ou nao);
# - pessoa, duas palavras: prenome + 2o nome de quem tem 3+ palavras ("Fulano Beltrano" de "Fulano Beltrano Sicrano");
#   prenome + ultimo sobrenome ja e da ``fuse_middle_names``. O mesmo sufixo de geracao (pai/filho nunca: v0.4.10);
# - pessoa com 3+ palavras: nada novo (mae/filha e a casada com o sobrenome do marido seguem separadas, v0.4.10);
# - empresa SEM sufixo societario: as palavras dela sao o comeco das formas de UMA empresa so, nenhuma outra razao social
#   escrita no lote abre com elas e o CNPJ nao separa (a ``fuse_short_forms`` com 3+ palavras, so para o LLM);
# - empresa com a ultima palavra abreviada ("Alfa Beta Ind." de "Alfa Beta Industrial Ltda"): mesmas palavras, a ultima
#   o comeco (3+ letras) da palavra da outra, uma empresa so.

_LLM_SOURCES = frozenset({"llm", "verificacao"})
_LONE_MIN = 4


def _abbreviated(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    if len(short) < 2 or len(short) != len(long) or short[:-1] != long[:-1]:
        return False
    tail, full = short[-1], long[-1]
    return len(tail) >= 3 and len(full) > len(tail) and full.startswith(tail)


def _person_hosts(mine: tuple[str, ...], people: Sequence[RosterEntry], words: dict[str, tuple[str, ...]]) -> list[RosterEntry]:
    if len(mine) == 1:
        word = mine[0]
        if len(word) < _LONE_MIN or is_geo_word(word) or word in _CONNECTIVES:
            return []
        return [other for other in people if word in words[other.key]]
    if len(mine) == 2:
        return [other for other in people if len(words[other.key]) >= 3 and words[other.key][:2] == mine]
    return []


def fuse_llm_subforms(
    roster: Roster, texts: Sequence[str] = (), cnpj_tokens: dict[str, str] | None = None
) -> dict[str, str]:
    """v0.4.13: a entrada do LLM que e forma parcial de UMA entrada do elenco vira essa entrada. Devolve {absorvida: que
    ficou}. Ver o bloco acima."""

    from . import features

    if not features.enabled("llm_subform_link"):
        return {}
    removed: dict[str, str] = {}
    people = [
        entry for entry in roster.entries
        if entry.kind == "pessoa" and entry.name.strip() and not has_token(entry.name)
        and not is_bridge_form("pessoa", entry.name)
    ]
    words = {entry.key: _person_words(entry.name) for entry in people}
    # O pedaco mais longo primeiro: "Fulano Beltrano" entra antes de "Fulano", que entao tem um dono so.
    for short in sorted(people, key=lambda entry: -len(words[entry.key])):
        if short.source not in _LLM_SOURCES or short.pinned or short.action != TOKENIZE or short.key in removed:
            continue
        mine = words[short.key]
        if not mine or any(len(_person_words(value)) > len(mine) for value in short.variants):
            continue  # a entrada ja tem forma mais longa propria: nao e so o pedaco
        hosts = [other for other in _person_hosts(mine, people, words) if other.key != short.key and other.key not in removed]
        if len(hosts) != 1:
            continue  # nenhuma ou 2+ pessoas (sobrenome de familia, prenome comum): separado
        target = hosts[0]
        if target.action != TOKENIZE or generation_of(target.name) != generation_of(short.name):
            continue
        if conflicting_generation(target.name, short.name):
            continue
        cpf_short, cpf_target = _person_cpfs(short, texts), _person_cpfs(target, texts)
        if cpf_short and cpf_target and not cpf_short & cpf_target:
            continue
        roster.merge(target.key, [short.key])
        roster.identity_reasons["forma_parcial_llm"] = roster.identity_reasons.get("forma_parcial_llm", 0) + 1
        removed[short.key] = target.key

    companies = [entry for entry in roster.entries if entry.kind == "empresa" and entry.name.strip()]
    all_forms = {entry.key: _company_forms_of(entry) for entry in companies}
    written = _written_companies(texts)
    clusters = [_Cluster(entry, {}) for entry in companies]
    roots = _cluster_roots(clusters, {entry.key: entry for entry in companies}, texts, cnpj_tokens)
    root_of = {clusters[number].key: found for number, found in roots.items()}
    for short in sorted(companies, key=lambda entry: -len(content_words("empresa", entry.name))):
        if short.source not in _LLM_SOURCES or short.pinned or short.action != TOKENIZE or short.key in removed:
            continue
        if is_bridge_form("empresa", short.name) or roster.contested("empresa", short.name):
            continue  # v0.4.15: forma afirmada pelo LLM como de duas empresas diferentes fica com token proprio
        mine = tuple(content_words("empresa", short.name))
        if not mine or (len(mine) == 1 and (not _distinctive_word(mine[0]) or len(mine[0]) < 5)):
            continue
        if any(len(form) > len(mine) for form in all_forms.get(short.key, [])):
            continue
        suffixed = any(_SOCIETARY.search(value) for value in (short.name, *short.variants))
        owners = []
        for key, forms in all_forms.items():
            if key == short.key or key in removed:
                continue
            if any(_abbreviated(mine, form) for form in forms) or (
                not suffixed and any(_starts_with(form, mine) for form in forms)
            ):
                owners.append(key)
        if len(owners) != 1:
            continue
        target = roster.get(owners[0])
        if target is None or target.action != TOKENIZE:
            continue
        rivals = [
            key for key, forms in all_forms.items()
            if key not in {short.key, target.key} and any(form == mine or _starts_with(form, mine) for form in forms)
        ]
        if rivals:
            continue  # outra empresa do elenco abre com as mesmas palavras (radical comum)
        foreign = [
            form for form in written
            if (_starts_with(form, mine) or form == mine) and not _same_entity_forms(all_forms[target.key], form)
            and not _same_entity_forms(all_forms[short.key], form)
        ]
        if foreign:
            continue  # outra razao social escrita no lote abre com as mesmas palavras
        mine_roots, theirs = root_of.get(short.key, set()), root_of.get(target.key, set())
        if mine_roots and theirs and not mine_roots & theirs:
            continue
        roster.merge(target.key, [short.key])
        roster.identity_reasons["forma_parcial_llm"] = roster.identity_reasons.get("forma_parcial_llm", 0) + 1
        removed[short.key] = target.key
        all_forms[target.key] = _company_forms_of(target)
        all_forms.pop(short.key, None)
    return removed


def fuse_identities(
    roster: Roster, texts: Sequence[str] | None = None, cnpj_tokens: dict[str, str] | None = None
) -> dict[str, str]:
    """Funde no ``roster`` as entradas da mesma entidade; devolve ``{chave removida: chave que ficou}``.

    ``texts`` (os textos do lote): H9-j, a empresa cujo nome aparece colado ao mesmo CNPJ que outra e a mesma entrada.
    ``cnpj_tokens``: token ``[CNPJ_n]`` -> 14 digitos (o texto do pipeline ja vem com o CNPJ mascarado)."""

    entries = [entry for entry in roster.entries if _eligible(entry)]
    # v0.4.10: a empresa da lista do caso entra so para o elo de CNPJ (fica sendo a entrada que sobrevive).
    entries += [entry for entry in roster.entries if _listed_company(entry)]
    names: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for entry in roster.entries:
        if entry.kind in {"pessoa", "empresa"} and not has_token(entry.name):
            kind = "empresa" if entry.kind == "pessoa" and is_corporate(entry.name) else entry.kind
            names[kind].append(_words(kind, entry.name))
    clusters = [_Cluster(entry, names) for entry in entries]
    by_key = {entry.key: entry for entry in entries}
    # Item 3: a mesma forma escrita como pessoa e como empresa vira uma entrada EMPRESA.
    company_written = {form for cluster in clusters if cluster.kind == "empresa" for form in cluster.written}
    company_written |= {
        _written(value)
        for entry in roster.entries
        if entry.kind == "empresa" and not _eligible(entry)
        for value in (entry.name, *entry.variants)
    }
    for cluster in clusters:
        if cluster.kind == "pessoa" and cluster.written & company_written:
            if _may_retype(cluster, clusters, roster, by_key):
                cluster.retype_company(by_key[cluster.key], names)
    clusters = [cluster for cluster in clusters if cluster.forms]
    # Arestas: nome canonico e igualdade primeiro, OCR depois (grafia de OCR nao disputa com a identidade exata).
    matches: dict[tuple[int, int], str] = {}
    edges: dict[str, list[tuple[int, int]]] = {"canonico": [], "igual": [], "ocr": [], "cnpj": []}
    listed_keys = {entry.key for entry in entries if _listed_company(entry)}
    for left in range(len(clusters)):
        for right in range(left + 1, len(clusters)):
            if clusters[left].key in listed_keys or clusters[right].key in listed_keys:
                continue
            relation = _relation(clusters[left], clusters[right])
            if relation:
                matches[(left, right)] = relation
                edges[relation].append((left, right))
    # v0.4.15: raiz de CNPJ diferente colada a cada uma veta QUALQUER elo (nome igual, canonico do LLM, OCR, declaracao).
    roots = _cluster_roots(clusters, by_key, texts or (), cnpj_tokens)

    def other_company(pair: tuple[int, int]) -> bool:
        # Raizes conhecidas dos dois lados e diferentes; ou a forma colada a duas raizes (denominacao de duas empresas)
        # contra a outra: nao prova que sao a mesma pessoa juridica.
        first, second = roots.get(pair[0], set()), roots.get(pair[1], set())
        if len(first) > 1 or len(second) > 1:
            return True  # denominacao colada a duas raizes (v0.4.13): nao liga a nenhuma outra entrada
        return bool(first and second and first != second)

    for pair in [pair for pair in matches if other_company(pair)]:
        relation = matches.pop(pair)
        edges[relation].remove(pair)
        roster.identity_reasons["veto_cnpj"] = roster.identity_reasons.get("veto_cnpj", 0) + 1
    for pair in _cnpj_links(clusters, by_key, texts or (), cnpj_tokens):
        if clusters[pair[0]].key in listed_keys and clusters[pair[1]].key in listed_keys:
            continue  # duas entradas da lista nunca se fundem
        if pair not in matches:
            matches[pair] = "cnpj"
            edges["cnpj"].append(pair)
    # v0.4.11: o texto declara que e a mesma empresa ("doravante X", "nome fantasia X", "atual/antiga denominacao").
    edges["declarado"] = []
    for pair in _declared_links(clusters, by_key, texts or ()):
        if clusters[pair[0]].key in listed_keys and clusters[pair[1]].key in listed_keys:
            continue
        if other_company(pair):
            continue  # CNPJ diferente colado a cada uma: nao e a mesma pessoa juridica
        if pair not in matches:
            matches[pair] = "declarado"
            edges["declarado"].append(pair)
    parent = list(range(len(clusters)))
    members: dict[int, list[int]] = {number: [number] for number in range(len(clusters))}

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def join(first: int, second: int, relation: str) -> None:
        root_a, root_b = find(first), find(second)
        if root_a == root_b:
            return
        for a in members[root_a]:
            for b in members[root_b]:
                if (min(a, b), max(a, b)) not in matches:
                    return
        keep, drop = min(root_a, root_b), max(root_a, root_b)
        parent[drop] = keep
        members[keep].extend(members.pop(drop))
        roster.identity_reasons[relation] = roster.identity_reasons.get(relation, 0) + 1

    for relation in ("canonico", "igual", "cnpj", "declarado"):
        for left, right in edges[relation]:
            join(left, right, relation)
    # OCR: so quando a grafia nao e ambigua. Os vizinhos de OCR de um grupo (fora o outro) tem de ser os mesmos do outro
    # grupo (o triangulo de grafias entra; a corrente A~B, B~C com A distinto de C fica toda separada).
    root0 = {number: find(number) for number in range(len(clusters))}
    neighbours: dict[int, set[int]] = defaultdict(set)
    for left, right in edges["ocr"]:
        if root0[left] != root0[right]:
            neighbours[root0[left]].add(root0[right])
            neighbours[root0[right]].add(root0[left])
    for left, right in edges["ocr"]:
        root_a, root_b = root0[left], root0[right]
        if root_a != root_b and neighbours[root_a] - {root_b} == neighbours[root_b] - {root_a}:
            join(left, right, "ocr")
    grouped: dict[int, list[int]] = defaultdict(list)
    for number in range(len(clusters)):
        grouped[find(number)].append(number)
    removed: dict[str, str] = {}
    for group in grouped.values():
        group_clusters = [clusters[number] for number in group]
        keys = [cluster.key for cluster in group_clusters]
        retyped = any(cluster.retyped for cluster in group_clusters)
        kind = group_clusters[0].kind
        if len(keys) < 2:
            if retyped:
                roster.retype(keys[0], kind)
            continue
        survivor = _survivor(roster, keys, kind)
        absorbed = [key for key in keys if key != survivor]
        roster.merge(survivor, absorbed)
        if retyped:
            roster.retype(survivor, kind)
        removed.update({key: survivor for key in absorbed})
    # v0.4.11: forma curta de UMA razao social do lote (sem outra empresa com o mesmo comeco) e a mesma empresa.
    for key, survivor in fuse_short_forms(roster, texts or (), cnpj_tokens).items():
        for old_key, target in list(removed.items()):
            if target == key:
                removed[old_key] = survivor
        removed[key] = survivor
    drop_ambiguous_short_variants(roster, texts or ())
    # v0.4.11: a mesma pessoa com e sem nome do meio (ou com o nome do meio abreviado) vira uma entrada so.
    for key, survivor in fuse_middle_names(roster, texts or (), cnpj_tokens).items():
        for old_key, target in list(removed.items()):
            if target == key:
                removed[old_key] = survivor
        removed[key] = survivor
    # v0.4.13: forma parcial que o LLM achou (sobrenome, prenome, razao social sem sufixo) de UMA entrada do elenco.
    for key, survivor in fuse_llm_subforms(roster, texts or (), cnpj_tokens).items():
        for old_key, target in list(removed.items()):
            if target == key:
                removed[old_key] = survivor
        removed[key] = survivor
    return removed


def _listed_company(entry: RosterEntry) -> bool:
    return entry.kind == "empresa" and entry.source == "lista" and not entry.pinned and bool(entry.name.strip())


def _survivor(roster: Roster, keys: Sequence[str], kind: str = "") -> str:
    """A entrada de nome mais completo (mais palavras de conteudo, depois mais variantes) fica."""

    def rank(key: str) -> tuple[int, int, int, int, str]:
        entry = roster.get(key)
        assert entry is not None
        listed = 0 if entry.source == "lista" else 1  # v0.4.10: a entrada da lista do caso sobrevive
        return (0 if not kind or entry.kind == kind else 1, listed, -len(content_words(entry.kind, entry.name)), -len(entry.variants), -len(entry.name), key)

    return min(keys, key=rank)


def merged_action(entries: Sequence[RosterEntry], locked: Sequence[bool]) -> str:
    """"manter" so com (a) membro travado pelo juizo do lote, (b) todos ja "manter", ou (c) papel publico em algum
    membro e nenhum papel privado especifico (socio, parte, advogado) em nenhum; "terceiro" e "desconhecido" nao
    vetam. Um socio com uma grafia rotulada de procurador fica tokenizado, como a ``policy_action`` faria."""

    if any(locked):
        return KEEP
    if all(entry.action == KEEP for entry in entries):
        return KEEP
    roles = {role for entry in entries for role in entry.roles}
    if roles & _PUBLIC_ROLES and not roles & _PRIVATE_ROLES:
        if any(entry.action == KEEP and _PUBLIC_ROLES & set(entry.roles) for entry in entries):
            return KEEP
    return TOKENIZE
