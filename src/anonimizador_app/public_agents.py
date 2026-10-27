"""Agente publico em claro (v0.4.11, decisao do Operador de 22/10/2026): NENHUM agente publico vira token.

Ficam em claro juiz(a), desembargador(a), relator(a), ministro(a), procurador(a) publico (da Fazenda Nacional, PGFN,
federal, da Uniao/AGU, do Estado, do Municipio, da Republica/MP), promotor(a), defensor(a) publico, servidor(a), diretor(a)
de secretaria, oficial de justica, analista e tecnico judiciario, escrivao, assessor(a), e os cargos de cartorio que o
motor ja tratava assim (tabeliao, escrevente, registrador). O advogado das partes NAO e agente publico (segue trocado):
"Procurador(es):" da capa do eproc e "procurador da parte" sao advogados, por isso o "procurador" sozinho nao conta.

A decisao e por OCORRENCIA (``public_agent_context``): o cargo colado ao nome (antes: "Juiza Federal X", "Relator:\\nX",
"Procuradora da Fazenda Nacional X"; depois: "X, Procurador da Fazenda Nacional", "X\\nJuiz Federal Substituto",
"Documento assinado eletronicamente por X, Juiza Federal"), e nenhuma prova de particular na mesma frase (CPF,
qualificacao "brasileiro, casado...", OAB, ou rotulo de parte logo antes: "o socio X", "executado X"). Assim o juiz
homonimo de um socio fica em claro onde assina e o socio segue trocado onde e socio.

Para o LOTE (todas as ocorrencias do mesmo nome, ``lot_public_agent``): o nome so fica em claro em toda parte quando
alguma ocorrencia tem o cargo colado e NENHUMA ocorrencia tem prova de particular. Com prova de particular em alguma
(homonimo), vale so a regra por ocorrencia.

v0.4.13: assinatura do carimbo da Junta Comercial ("CERTIFICO O REGISTRO ... / NOME / Protocolo: N / SECRETARIO GERAL"):
o cargo pode vir uma a tres linhas abaixo do nome (com o protocolo, o numero ou a empresa no meio) e deturpado pelo OCR
("BECRETARO GERAL"). Na rodada do Qwen de 24/10 o LLM apontou esses nomes e o filtro so olhava o cargo colado.

Dado generico; nenhum dado de caso.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from . import features

# Cargo publico ANTES do nome (o fim do trecho de ate 90 caracteres antes do nome).
_TITLE = (
    r"(?:ju[ií]z(?:a)?(?:\s*\(a\))?(?:\s+(?:federal|de\s+direito|do\s+trabalho|estadual|eleitoral))?"
    r"(?:\s+substitut[oa])?(?:\s+(?:titular|convocad[oa]|respons[aá]vel|plantonista))?"
    r"|magistrad[oa]"
    r"|desembargador(?:a)?(?:\s*\(a\))?(?:\s+federal)?(?:\s+convocad[oa])?(?:\s+relator(?:a)?)?"
    r"|relator(?:a)?(?:\s*\(a\))?(?:\s+(?:p/|para\s+o)\s*ac[oó]rd[aã]o|\s+convocad[oa])?"
    r"|rel\.(?:\s*(?:des\.|min\.|desembargador(?:a)?|minist(?:ro|ra)))?(?:\s*federal)?"
    r"|des\.(?:\s*fed(?:eral|\.))?"
    r"|minist(?:ro|ra)(?:\s+relator(?:a)?)?|(?<![^\W\d_])min\."
    r"|procurador(?:a)?(?:\s*\(a\))?(?:-chefe|\s+chefe)?(?:\s+(?:regional|seccional|geral|adjunt[oa]))?"
    r"(?:\s+(?:da\s+fazenda(?:\s+nacional)?|federal|da\s+uni[aã]o|d[oa]\s+estado|d[oa]\s+munic[ií]pio"
    r"|da\s+rep[uú]blica|do\s+trabalho|de\s+justi[cç]a|regional\s+da\s+(?:fazenda\s+nacional|rep[uú]blica|uni[aã]o)))"
    r"|promotor(?:a)?(?:\s+de\s+justi[cç]a)?(?:\s+substitut[oa])?"
    r"|membro\s+do\s+minist[eé]rio\s+p[uú]blico"
    r"|advogad[oa]\s+da\s+uni[aã]o|defensor(?:a)?\s+p[uú]blic[oa](?:\s+da\s+uni[aã]o|\s+federal)?"
    r"|servidor(?:a)?(?:\s+p[uú]blic[oa])?|diretor(?:a)?\s+de\s+secretaria|chefe\s+de\s+(?:secretaria|gabinete|cart[oó]rio)"
    r"|escriv[aã](?:o)?(?:\s+judicial)?|oficial(?:a)?\s+de\s+justi[cç]a(?:\s+avaliador(?:a)?)?"
    r"|analista\s+judici[aá]ri[oa]|t[eé]cnic[oa]\s+judici[aá]ri[oa]|assessor(?:a)?(?:\s+(?:de\s+gabinete|jur[ií]dic[oa]))?"
    r"|secret[aá]ri[oa][\s-]*geral"
    r"|tabeli[aã](?:o)?(?:\s+substitut[oa])?|escrevente(?:\s+(?:substitut[oa]|autorizad[oa]))?"
    r"|registrador(?:a)?(?:\s+civil)?|oficial(?:a)?\s+(?:de\s+registro(?:\s+(?:civil|de\s+im[oó]veis))?|substitut[oa]))"
)
# v0.4.12 (decisao do Operador de 22/10, noite; chave ``notary_public_agents``): registradores, oficiais de registro,
# tabeliaes, notarios, escreventes e substitutos de cartorio sao delegatarios de servico publico e ficam em claro, com a
# mesma regra do cargo colado ao nome e a mesma excecao (prova de particular na frase).
_NOTARY_TITLE = (
    r"(?:oficial(?:a)?(?:\s+(?:registrador(?:a)?|maior|interin[oa]|titular|designad[oa]))?(?:\s+substitut[oa])?"
    r"(?:\s+(?:d[oa]s?|de)\s+(?:\d{1,2}\s*[ºª°o]?\s*)?(?:registro|cart[oó]rio|tabelionato|of[ií]cio|servi[cç]o"
    r"|serventia)(?:\s+(?:geral\s+)?de\s+(?:im[oó]veis|t[ií]tulos(?:\s+e\s+documentos)?|pessoas\s+jur[ií]dicas"
    r"|notas|protesto|registro\s+civil))?)?"
    r"|registrador(?:a)?(?:\s+(?:substitut[oa]|interin[oa]|titular|designad[oa]|p[uú]blic[oa]|de\s+im[oó]veis))*"
    r"|tabeli[aã](?:o)?(?:\s+(?:substitut[oa]|interin[oa]|titular|designad[oa]|de\s+notas|de\s+protesto))*"
    r"|not[aá]ri[oa]|escrevente(?:\s+(?:notarial|juramentad[oa]|substitut[oa]|autorizad[oa]))*"
    r"|substitut[oa]\s+d[oa]\s+(?:tabeli[aã]o|tabeli[aã]|oficial|registrador(?:a)?|cart[oó]rio|tabelionato|of[ií]cio))"
)


def _compile_before(title: str) -> re.Pattern[str]:
    return re.compile(
        r"(?i)(?<![^\W\d_])" + title + r"\s*(?:\([^)\n]{0,12}\))?\s*[:|\-–—]?[ \t]*(?:\n[ \t]*)?"
        r"(?:(?:dr|dra|exm[oa]|sr|sra)\.?[ \t]+)?$"
    )


# Cargo publico DEPOIS do nome (virgula, hifen, parentese ou quebra de linha; ate duas palavras de nome no meio, para o
# trecho que parou antes do ultimo sobrenome).
def _compile_after(title: str) -> re.Pattern[str]:
    return re.compile(
        r"(?i)^(?:[ \t]+(?:(?i:d[aeo]s?)[ \t]+)?(?-i:[A-ZÀ-Ý][^\W\d_]*)){0,2}"
        r"[ \t]*(?:[,–—\-(][ \t]*|\n[ \t]*|[ \t]+)(?:\n[ \t]*)?" + title + r"(?![^\W\d_])"
    )


_WITH_NOTARY = r"(?:" + _NOTARY_TITLE + r"|" + _TITLE + r")"
_BEFORE = _compile_before(_TITLE)
_AFTER = _compile_after(_TITLE)
_BEFORE_NOTARY = _compile_before(_WITH_NOTARY)
_AFTER_NOTARY = _compile_after(_WITH_NOTARY)


def _patterns() -> tuple[re.Pattern[str], re.Pattern[str], str]:
    if features.enabled("notary_public_agents"):
        return _BEFORE_NOTARY, _AFTER_NOTARY, _WITH_NOTARY
    return _BEFORE, _AFTER, _TITLE
# Assinatura do eproc com o codigo de servidor ("Usuario: ABC99 - NOME"); o advogado aparece com a OAB.
_EPROC_USER = re.compile(r"(?i)usu[aá]rio\s*:\s*[A-Z]{2,4}\d{2}\s*[-–—]\s*$")
# v0.4.15: usuario do sistema antes do nome: "USUARIO: NOME" (tela da RFB, caixa alta sem acento) e o do eproc com o codigo
# mascarado ("Usuario: P013*******2 - NOME - PROCURADOR -"). E quem consultou o sistema: agente publico.
_SYSTEM_USER = re.compile(r"(?:\bUSUARIO\s*:\s*|(?i:\busu[aá]rio\s*:\s*[A-Z]\w*\*+\w*\s*[-–—]\s*))$")
_EPROC_ROLE_AFTER = re.compile(r"^\s*[-–—]\s*PROCURADOR(?:A)?\s*[-–—]")
_NAME_WORD_BACK = re.compile(r"(?:[A-ZÀ-Ý][^\W\d_]*(?:['’-][^\W\d_]+)?|(?i:d[aeo]s?|e))[ \t]+$")
_NAME_WORD_AHEAD = re.compile(r"^[ \t]+(?:[A-ZÀ-Ý][^\W\d_]*(?:['’-][^\W\d_]+)?|(?i:d[aeo]s?|e)(?=[ \t]+[A-ZÀ-Ý]))")
_WALK = 5
# Palavra capitalizada que nao e do nome (artigo, cargo, rotulo): a volta para nela.
_STOP_WORD = re.compile(
    r"(?i)(?:o|a|os|as|e|sr|sra|dr|dra|exm[oa]|eminente|federal|nacional|fazenda|uni[aã]o|estado|munic[ií]pio"
    r"|rep[uú]blica|substitut[oa]|titular|relator(?:a)?|ju[ií]z(?:a)?|desembargador(?:a)?|minist(?:ro|ra)"
    r"|procurador(?:a)?|promotor(?:a)?|servidor(?:a)?|diretor(?:a)?|oficial|analista|t[eé]cnic[oa]|escriv[aã]o?"
    r"|s[oó]ci[oa]|executad[oa]|requerid[oa]|advogad[oa]|secretaria|justi[cç]a|judici[aá]ri[oa]|usu[aá]rio"
    r"|tabeli[aã]o?|registrador(?:a)?|escrevente|not[aá]ri[oa]|interin[oa]|designad[oa])"
)

# Prova de particular na mesma frase.
_CPF = re.compile(r"(?i)\bcpf\b|\[CPF_\d+|\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CPF_LABELED = re.compile(r"(?i)\bcpf\b|\[CPF_\d+")
_OAB = re.compile(r"(?i)\boab\b|\[OAB_\d+")
_QUALIFICATION_AFTER = re.compile(
    r"(?i)^[\s,;(–—-]*(?:[^\W\d_]+(?:-[^\W\d_]+)?[\s,;]+)?"
    r"(?:brasileir[oa]|casad[oa]|solteir[oa]|divorciad[oa]|vi[uú]v[oa]|residente|domiciliad[oa]|nascid[oa]|"
    r"portador(?:a)?|empres[aá]ri[oa]|aposentad[oa]|inscrit[oa]\s+no\s+cpf)\b"
)
_PRIVATE_LABEL_BEFORE = re.compile(
    r"(?i)(?<![^\W\d_])(?:s[oó]ci[oa](?:[\s-]+(?:gerente|administrador(?:a)?|quotista|cotista|retirante|ingressante))?"
    r"|executad[oa]|requerid[oa]|r[ée]u|r[ée]|autor(?:a)?|exequente|requerente|agravad[oa]|agravante|embargante"
    r"|embargad[oa]|adquirente|compradora?|comprador|vendedora?|vendedor|outorgad[oa]|outorgante|c[oô]njuge|esposa"
    r"|marido|companheir[oa]|filh[oa]|neto|neta|genro|nora|herdeir[oa]|inventariante|advogad[oa]|administrador(?:a)?"
    r"|propriet[aá]ri[oa]|terceir[oa]|fiador(?:a)?|avalista|devedor(?:a)?|corresponsável|corresponsavel)"
    r"s?[ \t:,]+(?:(?:o|a|os|as|sr|sra|dr|dra)\.?[ \t]+)?$"
)
_ADVOCACY_AFTER = re.compile(r"(?i)^[\s,\-–—(]*(?:advogad[oa]|oab\b|\[OAB_|procurador(?:a)?\s+da\s+parte)")
_SENTENCE_END = re.compile(r"\n\s*\n|[.;]\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])")
_REACH = 60


def _name_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """O nome inteiro que contem o trecho (palavras maiusculas e particulas na mesma linha)."""

    position = start
    for _step in range(_WALK):
        match = _NAME_WORD_BACK.search(text[max(0, position - 40) : position])
        if match is None or _STOP_WORD.fullmatch(match.group().strip()):
            break
        position -= len(match.group())
    head = position
    position = end
    for _step in range(_WALK):
        match = _NAME_WORD_AHEAD.match(text[position : position + 40])
        if match is None:
            break
        position += len(match.group())
    return head, position


def _window(text: str, start: int, end: int) -> tuple[int, int]:
    lo = max(0, start - _REACH)
    cut = None
    for cut in _SENTENCE_END.finditer(text, lo, min(len(text), start + 1)):
        pass
    if cut is not None:
        lo = cut.end()
    hi = min(len(text), end + _REACH)
    after = _SENTENCE_END.search(text, end, hi)
    return lo, after.start() if after is not None else hi


def private_evidence(text: str, start: int, end: int) -> bool:
    """CPF, OAB ou qualificacao de pessoa na mesma frase, ou rotulo de parte logo antes do nome."""

    if not 0 <= start < end <= len(text):
        return False
    head, tail = _name_bounds(text, start, end)
    line_start = text.rfind("\n", 0, head) + 1
    if _SYSTEM_USER.search(text[line_start:head]):
        return False  # v0.4.15: o usuario do sistema (RFB, eproc) que imprimiu a tela; o CPF da linha de baixo e do consultado
    lo, hi = _window(text, start, end)
    lo, hi = min(lo, head), max(hi, tail)
    around = text[lo:head] + " " + text[tail:hi]
    # v0.4.13: no carimbo da Junta o numero do registro (11 digitos, "SOB O NUMERO: ...") nao e CPF: ali so o rotulo conta.
    cpf = _CPF_LABELED if features.enabled("stamp_public_agents") and stamp_signer(text, start, end) else _CPF
    if cpf.search(around) or _OAB.search(text[tail:hi]):
        return True
    if _QUALIFICATION_AFTER.match(text[tail:hi]) or _ADVOCACY_AFTER.match(text[tail : tail + 40]):
        return True
    line_start = text.rfind("\n", 0, head) + 1
    return bool(_PRIVATE_LABEL_BEFORE.search(text[max(line_start, head - 40) : head]))


# v0.4.13: carimbo da Junta Comercial. O cargo da linha de assinatura, com tolerancia a erro de OCR (ate 2 letras), e a
# pista do carimbo antes do nome (so exigida quando o cargo nao esta na linha logo abaixo).
_STAMP_ROLES = ("secretariogeral", "secretariageral")
_STAMP_CUE = re.compile(r"(?i)junta\s+comercial|jucesc|certifico\s*o?\s*registro|certifico\s+que")
_STAMP_LINES = 3
_STAMP_LINE_MAX = 60
_STAMP_CUE_REACH = 500


def _edit_distance(left: str, right: str, limit: int) -> int:
    """Distancia de edicao com teto (``limit + 1`` quando passa)."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, start=1):
        current = [row]
        for column, other in enumerate(right, start=1):
            current.append(min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (char != other)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _stamp_role_line(line: str) -> bool:
    import unicodedata

    folded = unicodedata.normalize("NFKD", line.casefold())
    letters = "".join(char for char in folded if char.isalpha() and char.isascii())
    if not 12 <= len(letters) <= 19:
        return False
    return any(_edit_distance(letters, role, 2) <= 2 for role in _STAMP_ROLES)


def stamp_signer(text: str, start: int, end: int) -> bool:
    """v0.4.13: o nome assina um carimbo de registro: "SECRETARIO GERAL" (com erro de OCR) na linha de baixo, ou ate tres
    linhas curtas abaixo (protocolo, numero, empresa) quando ha "Junta Comercial"/"Certifico o registro" antes."""

    if not 0 <= start < end <= len(text):
        return False
    _head, tail = _name_bounds(text, start, end)
    line_end = text.find("\n", tail)
    rest_of_line = text[tail : line_end if line_end >= 0 else len(text)]
    if rest_of_line.strip(" \t,.;:-–—"):
        return False  # o nome continua na linha: nao e a linha de assinatura
    lines = text[tail : tail + _STAMP_LINES * (_STAMP_LINE_MAX + 2)].split("\n")[1 : 1 + _STAMP_LINES]
    for index, line in enumerate(lines):
        if len(line) > _STAMP_LINE_MAX:
            return False
        if _stamp_role_line(line):
            return index == 0 or bool(_STAMP_CUE.search(text[max(0, start - _STAMP_CUE_REACH) : start]))
    return False


def public_role_attached(text: str, start: int, end: int) -> bool:
    """O cargo publico colado ao nome (antes ou depois), sem olhar a prova de particular."""

    if not 0 <= start < end <= len(text):
        return False
    head, tail = _name_bounds(text, start, end)
    if features.enabled("stamp_public_agents") and stamp_signer(text, start, end):
        return True
    before_rx, after_rx, title = _patterns()
    for position in dict.fromkeys((start, head)):
        before = text[max(0, position - 90) : position]
        if before_rx.search(before) or _EPROC_USER.search(before) or _SYSTEM_USER.search(before):
            return True
    for position in dict.fromkeys((end, tail)):
        if after_rx.match(text[position : position + 90]) or _EPROC_ROLE_AFTER.match(text[position : position + 20]):
            return True
    return bool(re.match(r"(?i)\s*" + title + r"\s", text[start:end] + " "))


def public_agent_context(text: str, start: int, end: int) -> bool:
    """Esta OCORRENCIA e de agente publico: cargo colado e nenhuma prova de particular na frase."""

    return public_role_attached(text, start, end) and not private_evidence(text, start, end)


def lot_public_agent(occurrences: Iterable[tuple[str, int, int]]) -> bool:
    """O NOME e de agente publico no lote: alguma ocorrencia com o cargo colado e nenhuma com prova de particular."""

    seen = False
    for text, start, end in occurrences:
        if private_evidence(text, start, end):
            return False
        seen = seen or public_role_attached(text, start, end)
    return seen
