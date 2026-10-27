from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from anonimizador_juridico import EntitySpan
from anonimizador_juridico.validators import normalize_cnpj, only_digits

from .gemini_detector import is_kept_term, is_legal_nature, is_registry_office, is_system_term, is_too_generic
from .protected_patterns import inside_protected_region, touches_legislation

_TARGET_RECOGNIZER = "ListaDeAlvos"
_CASE_RULE_RECOGNIZER = "ListaDoCaso"
# Propagacao do lote (deteccao 2.1): a entidade ja foi julgada no elenco (cargo colado
# ao nome em qualquer arquivo tira a entidade da propagacao); a ocorrencia propagada nao
# repete as regras amplas de contexto (juiz/PFN por perto, parte de precedente, nome banal).
_PROPAGATION_RECOGNIZER = "PropagacaoLote"
_ENTITY_LEVEL_RECOGNIZERS = frozenset({_TARGET_RECOGNIZER, _PROPAGATION_RECOGNIZER})
_ANCHORED_PERSON_RECOGNIZERS = frozenset(
    # H9-o: NerAncoradoLista = nome de pessoa do NER que venceu o pedaco de um listado (a lista o ancorou).
    {"ListaDeAlvos", "HeaderHeuristic", "FormFields", "NerAncoradoLista", _PROPAGATION_RECOGNIZER}
)
_CONTEXTUAL_TYPES = frozenset({"PERSON", "ORGANIZATION", "LOCATION"})
CAPA_CHARS = 4500

# Raízes de CNPJ de órgãos públicos. Só entra raiz confirmável ou lista
# explícita do Ciclo 2; fácil de estender.
PUBLIC_CNPJ_ROOTS: dict[str, str] = {
    "00394460": (
        "União Federal (raiz do CNPJ da União; neste lote: "
        "00.394.460/0216-53 no polo ativo)"
    ),
    "00394411": "Administração federal / RFB — lista Ciclo 2 (GOVERNANCA §2.4)",
    "00394494": "PGFN — lista Ciclo 2 (GOVERNANCA §2.4)",
    "00360305": "Tesouro Nacional / STN — lista Ciclo 2 (GOVERNANCA §2.4)",
    "33683111": "DATAPREV (CNPJ público 33.683.111/0001-07)",
    "29979036": "INSS (CNPJ público 29.979.036/0001-40)",
}

_TITLE_IN_VALUE = re.compile(
    r"(?i)^\s*(?:minist(?:ro|ra)|desembargador(?:a)?|relator(?:a)?|"
    r"juiz(?:a)?(?:\s+federal)?|diretor(?:a)?\s+de\s+secretaria|"
    r"oficial(?:a)?(?:\s+de\s+justi[cç]a)?)\b"
)

_CUE_BEFORE = re.compile(
    r"(?is)"
    r"(?:"
    r"juiz(?:a)?(?:\s+federal)?\s*(?:\([^)]{0,12}\))?\s*[:|]?"
    r"|magistrad[oa]\s*[:|]?"
    r"|desembargador(?:a)?(?:\s+federal)?"
    r"|diretor(?:a)?\s+de\s+secretaria"
    r"|oficial(?:a)?(?:\s+de\s+(?:justi[cç]a|registro))?"
    r"|signat[aá]rio(?:\s*\(\s*a\s*\))?\s*[:|]?"
    r"|usu[aá]rio\s*:"
    r"|chefe\s+d[ae]\s+(?:pfn|procuradoria|pgfn)"
    r"|documento\s+eletr[oô]nico\s+assinado\s+por"
    r"|relator(?:a)?\b"
    r"|rel\.\s*(?:minist(?:ro|ra)|min\.?)?"
    r"|minist(?:ro|ra)s?\b"
    r"|(?<![A-Za-zÁ-ÿ])min\."
    r"|estudo\s+de"
    r"|li[cç][aã]o\s+de"
    r"|nas\s+palavras\s+de"
    r"|no\s+dizer\s+d[aoa]"
    r"|apud\b"
    r")\s*$"
)

_CUE_AFTER = re.compile(
    r"(?i)^\s*[,–—\-]?\s*"
    r"(?:juiz(?:a)?(?:\s+federal)?|magistrad[oa]|desembargador(?:a)?|"
    r"diretor(?:a)?\s+de\s+secretaria|oficial(?:a)?)\b"
)

_PFN_CUE = re.compile(
    r"(?is)"
    r"(?:"
    r"procurador(?:a|es)?\s+da\s+fazenda\s+nacional"
    r"|procurador(?:a|es)?\s+da\s+uni[aã]o"
    r"|procurador(?:a|es)?\s+federal"
    r"|procurador(?:a|es)?\s+d[oa]\s+estado"
    r"|procurador(?:a|es)?\s+d[oa]\s+munic[ií]pio"
    r"|procuradoria-geral\s+da\s+fazenda\s+nacional"
    r"|procuradoria-geral\s+federal"
    r"|procuradoria-geral\s+d[oa]\s+estado"
    r"|procuradoria-geral\s+d[oa]\s+munic[ií]pio"
    r"|procuradoria\s+da\s+fazenda"
    r"|advocacia-geral\s+da\s+uni[aã]o"
    r"|chefe\s+d[ae]\s+(?:pfn|procuradoria|pgfn|pgf|pge|pgm|agu)"
    r"|\bpgfn\b"
    r"|\bpfn(?:\b|\d)"
    r")"
)

_ID_CONTEXT_MARKERS = re.compile(
    r"(?i)(?:"
    r"\bnire\b"
    r"|sob\s+o?\s*n[º°oª.]?"
    r"|c[oó]digo\s+de\s+verifica"
    r"|protocolo"
    r"|registro\s+n"
    r"|registro\s+em"
    r"|n[º°oª]?\s*de\s+registro"
    r"|arquivamento"
    r"|autentica[cç][aã]o"
    r"|n[uú]mero\s*:"
    r"|\btvp\b"
    r"|\bhash\b"
    r"|t[íi]tulo\s+(?:de\s+)?eleitor"
    r")"
)

_ID_CONTEXT_COMPACT = re.compile(
    r"(?i)(?:"
    r"nire"
    r"|sobo?n"
    r"|codigodeverifica"
    r"|protocolo"
    r"|registron"
    r"|registroem"
    r"|nde?registro"
    r"|arquivamento"
    r"|autenticacao"
    r"|numero:"
    r"|tvp"
    r"|hash"
    r"|titulodeeleitor"
    r"|tituloeeleitor"
    r")"
)

_ID_KEEP_MARKERS = re.compile(
    r"(?i)\b(?:cpf|cnpj|tel\.?|fone|telefone|cel\.?|whatsapp)\b"
)

_PRECEDENT_CUE = re.compile(
    r"(?i)(?:"
    r"\b(?:AG|AC|AI|AR|AgRg|AgInt|REsp|EREsp|RE|ARE|EDcl|HC|MS|RMS|AREsp)\b"
    r"|\bTema\b"
    r"|\bS[úu]mula\b"
    r"|\bTRF4\b"
    r"|\bTRF\b"
    r"|\bSTJ\b"
    r"|\bSTF\b"
    r"|\bRel\."
    r"|\bRelator(?:a)?\b"
    r"|\bjulgado\b"
    r"|\bDJe\b"
    # H9-i: ministro, desembargador, turma e camara tambem marcam citacao de precedente.
    r"|\bMin\."
    r"|\bMinistr[oa]\b"
    r"|\bDes\."
    r"|\bDesembargador(?:a)?\b"
    r"|\bTurma\b"
    r"|\bC[âa]mara\b"
    r"|\bSe[cç][aã]o\b"
    r")"
)

# O que vem logo DEPOIS do numero numa citacao: "5001234-56.2019.4.04.7200, Rel. Min. X, Segunda Turma, DJe ...".
_PRECEDENT_AFTER = re.compile(
    r"(?i)^[\s,;)\]–—\-]*(?:"
    r"Rel\.|Relator(?:a)?\b|Min\.|Ministr[oa]\b|Des\.|Desembargador(?:a)?\b|DJe?\b|julgad[oa]\b"
    r"|(?:\d+\s*[ªaº]?\s*|\w+\s+)?(?:Turma|C[âa]mara|Se[cç][aã]o)\b"
    r"|(?:STJ|STF|TRF\s?\d?)\b"
    r")"
)

_PRECEDENT_ORG_CUE = re.compile(
    r"(?is)"
    r"(?:"
    r"\bEMENTA\b"
    r"|\bRel\."
    r"|\bRelator(?:a)?\b"
    r"|\bMin\."
    r"|\bMinistro\b"
    r"|\bMinistra\b"
    r"|\bDes\."
    r"|julgado\s+em"
    r"|\bDJe\b"
    r"|DJ\s"
    r"|\bTurma\b"
    r"|un[aâ]nime"
    r"|\bREsp\b"
    r"|\bAgRg\b"
    r"|\bAgInt\b"
    r"|\bEREsp\b"
    r"|\bTema\b"
    r"|\bS[úu]mula\b"
    r"|\svs\.\s"
    r"|\sx\s"
    r")"
)

_PUBLIC_CNPJ_LINE = re.compile(
    r"(?i)(?:"
    r"uni[aã]o"
    r"|fazenda\s+nacional"
    r"|procuradoria"
    r"|minist[eé]rio"
    r"|secretaria"
    r"|prefeitura"
    r"|estado\s+do"
    r"|\binss\b"
    r"|receita\s+federal"
    r"|tesouro"
    r"|tribunal"
    r"|justi[cç]a"
    r")"
)

_PHONE_LABEL = re.compile(
    r"(?i)(?:\b(?:tel\.?|fone|telefone|cel\.?|whatsapp|contato)\b|\([1-9]\d\))"
)

_FILENAME_CNJ = re.compile(r"(?i)(?:^|[\\/_-])PR-(\d{20})")


@dataclass(slots=True)
class FidelityDropStats:
    """Contagens de spans descartados por regra de fidelidade (sem valores)."""

    cnpj_publico: int = 0
    nire_protocolo: int = 0
    telefone_sem_rotulo: int = 0
    natureza_juridica: int = 0
    parte_precedente: int = 0
    processo_precedente: int = 0
    higiene_html: int = 0
    higiene_rotulo: int = 0
    higiene_fusao: int = 0
    higiene_tamanho: int = 0
    # Endereco cortado no limite: celula/coluna, parentese nao fechado, numero cortado ao meio.
    higiene_limite: int = 0


# H9-j: contato institucional de orgao publico (decisao do Operador de 17/10: fica em claro). No teste de 16/10 eram 19
# ocorrencias trocadas (e-mail, telefone, CEP e endereco de vara e procuradoria no cabecalho das pecas).
_PUBLIC_EMAIL = re.compile(r"(?i)@(?:[a-z0-9-]+\.)*(?:jus|gov|mp|leg|def|mil)\.br$")
_CONTACT_TYPES = frozenset({"BR_PHONE", "BR_CEP", "LOCATION"})
_ORGAN_LINE = re.compile(
    r"(?i)\b(?:vara\s+(?:federal|do\s+trabalho|c[ií]vel|criminal)|f[oó]rum|tribunal|se[cç][aã]o\s+judici[aá]ria"
    r"|subse[cç][aã]o\s+judici[aá]ria|justi[cç]a\s+federal|procuradoria|pgfn|receita\s+federal|advocacia-geral"
    r"|minist[ée]rio\s+p[úu]blico|defensoria|secretaria\s+da\s+vara|cartório\s+eleitoral"
    # H9-k (lote de 19/10/2026, ~1.000 ocorrencias): serventia extrajudicial e vara numerada tambem sao orgao.
    r"|cart[óo]rio|serventia|tabelionato|tabeli[ãa]o|of[íi]cio\s+(?:de\s+)?registro|registro\s+de\s+im[óo]veis"
    r"|registro\s+civil|registro\s+de\s+t[íi]tulos|registro\s+geral\s+de\s+im[óo]veis|\d{1,2}\s*[ºª°o]?\s*vara"
    r"|vara\s+de\s+execu[cç][õo]es)\b"
)
# H9-k: pista de que o endereco da linha e de particular (qualificacao), nunca do orgao.
_PRIVATE_ADDRESS_CUE = re.compile(r"(?i)\b(?:residente|domiciliad[oa]|reside|resid[êe]ncia|propriet[áa]ri[oa]|portador)")
# H9-k: e-mail de serventia em dominio privado ("@cartorio...", "@registrodeimoveis...", "@1ri....com.br").
_REGISTRY_EMAIL = re.compile(
    r"(?i)@[^@\s]*(?:cartorio|registro|tabelionato|oficio|serventia|\dri[a-z]{0,12}\.(?:com|org|not)\.br)"
)


def is_institutional_contact(text: str, start: int, end: int, entity_type: str) -> bool:
    """E-mail de dominio publico (.jus.br, .gov.br, .mp.br...) ou telefone/CEP/endereco na mesma linha do nome de um
    orgao publico (vara, foro, tribunal, procuradoria, Receita): contato institucional, fica em claro."""

    value = text[start:end].strip()
    if entity_type == "EMAIL_ADDRESS":
        compact = value.replace(" ", "")
        if _PUBLIC_EMAIL.search(compact) or _REGISTRY_EMAIL.search(compact):
            return True
    elif entity_type not in _CONTACT_TYPES:
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : line_end if line_end >= 0 else len(text)]
    # So linha curta de cabecalho/rodape: numa frase longa ("requer ao tribunal a citacao na Rua X") o endereco e do
    # particular.
    if len(line.strip()) > 160 or _PRIVATE_ADDRESS_CUE.search(line):
        return False
    if _ORGAN_LINE.search(line):
        return True
    # H9-k: cabecalho de certidao em linhas curtas ("REGISTRO DE IMOVEIS DA 1a CIRCUNSCRICAO" / "Rua X, 10 - Fone").
    # So a linha de cima, e as duas curtas (ate 100 caracteres): no corpo da matricula as linhas sao longas.
    if line_start > 0 and len(line.strip()) <= 100:
        previous_start = text.rfind("\n", 0, line_start - 1) + 1
        previous = text[previous_start : line_start - 1]
        return len(previous.strip()) <= 100 and bool(_ORGAN_LINE.search(previous))
    return False


def _sense_guard_drops(text: str, span: EntitySpan) -> bool:
    """v0.4.12 (chaves ``case2_false_positives`` e ``notary_public_agents`` do config.json)."""

    from . import features
    from .public_agents import public_agent_context

    if features.enabled("case2_false_positives"):
        from .sense_guard import misread

        if misread(text, span.start, span.end, span.entity_type):
            return True
    if (
        span.entity_type == "PERSON"
        and span.recognizer not in _ENTITY_LEVEL_RECOGNIZERS
        and (features.enabled("case2_false_positives") or features.enabled("notary_public_agents"))
        and public_agent_context(text, span.start, span.end)
    ):
        # O motor local (sem LLM) tambem segue a decisao da v0.4.11: agente publico com o cargo colado, sem prova de
        # particular na frase, fica em claro. Antes so o pipeline com LLM aplicava (guarda final).
        return True
    return False


def drop_protected_spans(
    text: str,
    spans: Sequence[EntitySpan],
    extra_keep: Iterable[str] = (),
    *,
    source_name: str = "",
    stats: FidelityDropStats | None = None,
) -> list[EntitySpan]:
    """Remove spans institucionais, de exclusao, de magistrado, PFN e de ementa/doutrina."""

    kept: list[EntitySpan] = []
    for span in spans:
        value = span.value_from(text)
        # Trava geral (H9-i): a lista do caso (aba Caso) vence a lista protegida de sistemas, orgaos e padroes.
        is_target = span.recognizer == _TARGET_RECOGNIZER
        if span.recognizer == _CASE_RULE_RECOGNIZER:
            # H9-i lote B1: processo, CDA e contato listados na aba Caso: so a lista de exclusoes do usuario os tira
            # (nem a regra do precedente, nem a do telefone sem rotulo; o valor ja foi provado na lista).
            if not is_kept_term(value, extra_keep):
                kept.append(span)
            continue
        if is_kept_term(value, extra_keep) and not (is_target and is_system_term(value)):
            continue
        if not is_target and touches_legislation(text, span.start, span.end):
            continue  # "Lei 8.112", "Tema 1.209": nunca entidade, nunca numero trocado
        if not is_target and is_institutional_contact(text, span.start, span.end, span.entity_type):
            continue  # e-mail, telefone, CEP ou endereco de vara, procuradoria, orgao publico (H9-j)
        if not is_target and _sense_guard_drops(text, span):
            continue  # v0.4.12: expressao generica, julgador com o titulo no trecho, contato do orgao, cartorario
        if not is_target and span.entity_type in _CONTEXTUAL_TYPES and inside_protected_region(text, span.start, span.end):
            continue  # orgao fracionado, sigla com UF, cooperativa (padroes 6a a 6c)
        if (
            span.entity_type in {"ORGANIZATION", "LOCATION"}
            and span.recognizer != _TARGET_RECOGNIZER
            and is_registry_office(value)
        ):
            continue  # nome de cartorio/oficio/tabeliao: ente do registro publico, fica em claro
        if (
            span.entity_type == "PERSON"
            and span.recognizer not in _ENTITY_LEVEL_RECOGNIZERS
            and (
                is_magistrate_or_citation(text, span.start, span.end)
                or is_pfn_officer(text, span.start, span.end)
            )
        ):
            continue
        if (
            span.entity_type == "PERSON"
            and span.recognizer not in _ANCHORED_PERSON_RECOGNIZERS
            and _is_unanchored_common_person(value)
        ):
            continue
        if (
            span.entity_type == "ORGANIZATION"
            and span.recognizer not in _ENTITY_LEVEL_RECOGNIZERS
            and is_precedent_party(text, span.start, span.end)
        ):
            if stats is not None:
                stats.parte_precedente += 1
            continue
        if (
            span.entity_type in _CONTEXTUAL_TYPES
            and span.recognizer != _TARGET_RECOGNIZER
            and is_too_generic(span.entity_type, value)
        ):
            if stats is not None and span.entity_type == "ORGANIZATION" and is_legal_nature(value):
                stats.natureza_juridica += 1
            continue
        if span.recognizer != _TARGET_RECOGNIZER and is_masked_cpf_overlap(text, span.start, span.end):
            if stats is not None:
                stats.nire_protocolo += 1
            continue  # CPF mascarado do gov.br ("***.123.456-**"): ja esta oculto, fica em claro (H9-i)
        if span.entity_type not in _CONTEXTUAL_TYPES and span.recognizer != _TARGET_RECOGNIZER and is_ip_address(
            text, span.start, span.end
        ):
            if stats is not None:
                stats.nire_protocolo += 1
            continue  # endereco IP: nunca e CPF, telefone nem outro ID (H9-i)
        if span.entity_type in _CLEAR_REGISTRY_TYPES and is_clear_registry_number(
            text, span.start, span.end, span.entity_type
        ):
            if stats is not None:
                stats.nire_protocolo += 1
            continue  # CCIR, CAR, CIB, NIRF; protocolo, arquivamento e chancela da JUCESC: em claro (H9-i)
        if span.entity_type in {"BR_CPF", "BR_PHONE"} and is_blocked_numeric_context(
            text, span.start, span.end
        ):
            if stats is not None:
                stats.nire_protocolo += 1
            continue
        if span.entity_type == "BR_PHONE" and is_code_fragment(text, span.start, span.end):
            if stats is not None:
                stats.nire_protocolo += 1
            continue
        if span.entity_type == "BR_PHONE" and is_unlabeled_compact_phone(
            text, span.start, span.end
        ):
            if stats is not None:
                stats.telefone_sem_rotulo += 1
            continue
        if span.entity_type == "BR_CNPJ" and is_public_cnpj(
            text, span.start, span.end, value
        ):
            if stats is not None:
                stats.cnpj_publico += 1
            continue
        if span.entity_type == "BR_PROCESSO" and is_precedent_process(
            text, span.start, span.end, source_name=source_name
        ):
            if stats is not None:
                stats.processo_precedente += 1
            continue
        kept.append(span)
    return kept


def _is_unanchored_common_person(value: str) -> bool:
    """Evita import circular com target_list (que ja importa este modulo)."""

    from .target_list import is_common_person_name_span

    return is_common_person_name_span(value)


def is_magistrate_or_citation(text: str, start: int, end: int) -> bool:
    """Juiz da capa, assinatura de magistrado, relator de ementa ou autor de doutrina."""

    if _TITLE_IN_VALUE.match(text[start:end]):
        return True
    before = text[max(0, start - 160) : start]
    if _CUE_BEFORE.search(before):
        return True
    after = text[end : min(len(text), end + 48)]
    return bool(_CUE_AFTER.match(after)) or doctrine_reference(text, start, end)


# v0.4.15: referencia bibliografica ("12 SOBRENOME, Prenome Nome. Titulo da obra. 9 ed. Cidade: Editora, 2001, p. 281."):
# o prenome do autor citado, depois do SOBRENOME em caixa alta e da virgula, com edicao, editora ou ano na mesma linha.
_DOCTRINE_SURNAME_BEFORE = re.compile(
    r"(?:^|\n)[ \t]*(?:\d{1,3}[ \t]+)?[A-ZÀ-Ý][A-ZÀ-Ý'’\-]+(?:[ \t]+[A-ZÀ-Ý][A-ZÀ-Ý'’\-]+){0,3}[ \t]*,[ \t]*$"
)
_DOCTRINE_TAIL = re.compile(r"(?i)\b\d{1,2}[ªa]?\s*ed\.|\beditora\b|\bp\.\s*\d|\b(?:19|20)\d{2}\b")


def doctrine_reference(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start)
    line_end = text.find("\n", end)
    tail = text[end : line_end if line_end >= 0 else len(text)]
    head = text[max(0, line_start) : start]
    return bool(_DOCTRINE_SURNAME_BEFORE.search(head) and tail.lstrip().startswith(".") and _DOCTRINE_TAIL.search(tail))


def is_pfn_officer(text: str, start: int, end: int) -> bool:
    """Procurador da PFN/PGFN/PGF/PGE/PGM/AGU no contexto proximo (mesma linha ou ~80 chars apos).

    So se aplica a PERSON de NER/heuristica de capa. ListaDeAlvos continua
    tokenizando (caso do nome que tambem e alvo do processo). Sigla PGE/AGU
    sozinha na linha nao basta — evitaria tokenizar a parte privada.
    """

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    same_line = text[line_start:line_end]
    after = text[end : min(len(text), end + 80)]
    if not (_PFN_CUE.search(same_line) or _PFN_CUE.search(after)):
        return False
    # v0.4.11: o socio com CPF na linha de cima do "Procurador da Fazenda Nacional" nao e procurador.
    from .public_agents import private_evidence

    return not private_evidence(text, start, end)


def _fold_compact(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", "", stripped)


def is_blocked_numeric_context(text: str, start: int, end: int) -> bool:
    """NIRE, registro da Junta, protocolo, hash: nao sao CPF nem telefone."""

    value = text[start:end]
    if re.search(r"[.\-()]", value):
        return False
    raw = text[max(0, start - 30) : start]
    if _ID_KEEP_MARKERS.search(raw):
        return False
    compact = _fold_compact(raw)
    return bool(_ID_CONTEXT_MARKERS.search(raw) or _ID_CONTEXT_COMPACT.search(compact))


# Opcoes (decisao do Operador de 16/10: estes quatro sao TROCADOS, por reconhecedor proprio): True = fica em claro.
CADASTROS_RURAIS_EM_CLARO = False  # CCIR, CAR, CIB, NIRF, ITR
CARIMBO_JUCESC_EM_CLARO = False  # protocolo, arquivamento e chancela da Junta Comercial
CPF_MASCARADO_EM_CLARO = False  # CPF do gov.br "***.123.456-**"
IP_EM_CLARO = False  # endereco IPv4
# --- numeros que ficam SEMPRE em claro (H9-i) -----------------------------------------------------------------------
# CPF mascarado pelo gov.br: tres marcas, os seis digitos do meio, duas marcas ("***.123.456-**", "xxx.123.456-xx").
_MASKED_CPF = re.compile(r"(?<![\w*])[*xX•●·]{3}[.\s]?\d{3}[.\s]?\d{3}[\-\s]?[*xX•●·]{2}(?![\w*])")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
# Rotulo de cadastro rural / imobiliario (CCIR, CAR, CIB, NIRF, ITR) logo antes do numero, na mesma linha.
_REGISTRY_LABEL_BEFORE = re.compile(r"(?:(?i:\b(?:CCIR|CIB|NIRF|ITR)\b)|\bCAR\b)[^\n\d]{0,30}$")
# Protocolo, arquivamento e chancela da Junta Comercial (JUCESC) logo antes do numero.
_STAMP_BEFORE = re.compile(r"(?i)\b(protocolo|arquivamento|chancela)\b[^\n\d]{0,40}$")
_JUNTA_CONTEXT = re.compile(r"(?i)\b(?:jucesc|junta\s+comercial|chancela)\b")
_CLEAR_REGISTRY_TYPES = frozenset(
    {
        "BR_CPF", "BR_PHONE", "BR_NIRE", "BR_CNH", "BR_PIS", "BR_RG", "BR_CEP", "BR_RENAVAM",
        "BR_CONTA_BANCARIA", "BR_CDA", "BR_PROCESSO_ADMINISTRATIVO", "BR_PROCESSO",
    }
)
_STAMP_TYPES = frozenset({"BR_CPF", "BR_PHONE", "BR_NIRE", "BR_CNH", "BR_PIS"})


def is_masked_cpf_overlap(text: str, start: int, end: int) -> bool:
    """O trecho toca um CPF mascarado do gov.br (digitos do meio entre marcas): nao e CPF nem nome, fica em claro."""

    if not CPF_MASCARADO_EM_CLARO:
        return False
    for match in _MASKED_CPF.finditer(text, max(0, start - 16), min(len(text), end + 16)):
        if match.start() < end and start < match.end():
            return True
    return False


def is_ip_address(text: str, start: int, end: int) -> bool:
    """O trecho toca um endereco IPv4 ("177.126.012.34", "192.168.0.10"): em claro, nunca CPF ou telefone."""

    if not IP_EM_CLARO:
        return False
    for match in _IPV4.finditer(text, max(0, start - 16), min(len(text), end + 16)):
        if match.start() < end and start < match.end():
            if all(int(part) <= 255 for part in match.group().split(".")):
                return True
    return False


def is_clear_registry_number(text: str, start: int, end: int, entity_type: str) -> bool:
    """Numero de CCIR, CAR, CIB ou NIRF (rotulo logo antes) e protocolo/arquivamento/chancela da Junta Comercial: o
    numero fica em claro por decisao do Operador (vazamento controlado). CPF e CNPJ rotulados no meio do caminho
    mantem o numero como ID."""

    line_start = text.rfind("\n", 0, start) + 1
    before = text[max(line_start, start - 90) : start]
    label = _REGISTRY_LABEL_BEFORE.search(before)
    if CADASTROS_RURAIS_EM_CLARO and label is not None and not _ID_KEEP_MARKERS.search(label.group()):
        return True
    if CARIMBO_JUCESC_EM_CLARO and entity_type in _STAMP_TYPES:
        stamp = _STAMP_BEFORE.search(before)
        if stamp is not None and not _ID_KEEP_MARKERS.search(stamp.group()):
            if stamp.group(1).casefold() != "protocolo" or _JUNTA_CONTEXT.search(text[max(0, start - 220) : start]):
                return True
    return False


_CODE_BEFORE = re.compile(r"\d[.,/-]?\Z")
_CODE_AFTER = re.compile(r"[.,/-]?\d")


def is_code_fragment(text: str, start: int, end: int) -> bool:
    """Numero sem DV cujos digitos continuam, por [.,/-], em outro digito: e pedaco de
    codigo mais longo (protocolo CNIB "dddddd.dddd.dddddddd-IA-nnn"), nao telefone."""

    value = text[start:end]
    before = text[max(0, start - 2) : start]
    return bool(
        (value[:1].isdigit() and _CODE_BEFORE.search(before))
        or (value[-1:].isdigit() and _CODE_AFTER.match(text, end))
    )


def is_unlabeled_compact_phone(text: str, start: int, end: int) -> bool:
    """10–11 dígitos corridos só viram TELEFONE com rótulo (P10)."""

    value = text[start:end]
    if re.search(r"[.\-()]", value):
        return False
    digits = only_digits(value)
    if len(digits) not in (10, 11):
        return False
    if re.sub(r"\s+", "", value.strip()) != digits:
        return False
    window = text[max(0, start - 30) : start]
    return not _PHONE_LABEL.search(window)


def is_public_cnpj(text: str, start: int, end: int, value: str) -> bool:
    """CNPJ de órgão público: raiz conhecida ou marcador na mesma linha."""

    compact = normalize_cnpj(value)
    compact = re.sub(r"^(?:CNPJ|MF)+", "", compact)
    digits = only_digits(value)
    roots = tuple(PUBLIC_CNPJ_ROOTS)
    if any(compact.startswith(root) or digits.startswith(root) for root in roots):
        return True
    line_start = text.rfind("\n", 0, start) + 1
    before = text[line_start:start]
    return bool(_PUBLIC_CNPJ_LINE.search(before[-60:]))


def is_uniao_cnpj(text: str, start: int, end: int, value: str) -> bool:
    """Alias de is_public_cnpj (nome histórico dos testes)."""

    return is_public_cnpj(text, start, end, value)


def cnj_from_filename(source_name: str) -> str:
    match = _FILENAME_CNJ.search(source_name.replace("\\", "/"))
    return match.group(1) if match else ""


def is_own_case_process(text: str, start: int, end: int, source_name: str = "") -> bool:
    """Processo da capa (doc longo) ou o CNJ do próprio arquivo."""

    if len(text) >= CAPA_CHARS and start < CAPA_CHARS:
        return True
    own = cnj_from_filename(source_name)
    if not own:
        return False
    return only_digits(text[start:end]) == own


def _before_window(text: str, start: int, size: int = 40) -> str:
    """Janela à esquerda sem fragmento de palavra no corte (evita \\bAR\\b em 'comprar')."""

    begin = max(0, start - size)
    raw = text[begin:start]
    if begin > 0 and raw[:1].isalpha() and text[begin - 1].isalpha():
        return re.sub(r"^[A-Za-zÀ-ÿ]+", "", raw, count=1)
    return raw


def is_precedent_process(
    text: str,
    start: int,
    end: int | None = None,
    source_name: str = "",
) -> bool:
    """Numero CNJ de ementa/precedente, salvo o processo do próprio documento."""

    stop = end if end is not None else min(len(text), start + 25)
    if is_own_case_process(text, start, stop, source_name):
        return False
    if _PRECEDENT_CUE.search(_before_window(text, start, 60)):
        return True
    return bool(_PRECEDENT_AFTER.match(text[stop : stop + 60].split("\n", 1)[0]))


# H9-i lote B5: o titulo do julgador COLADO antes do nome, na mesma linha ("Rel. Min.", "Relatora", "Des. Federal").
# H9-k: o OCR e a ementa trazem "Rel . Des. Fed . Nome" (espaco antes do ponto, "Fed." abreviado) e o token pode pegar
# so o nome do meio: entre o titulo e o span cabem ate dois nomes proprios.
_JUDGE_TITLE_BEFORE = re.compile(
    r"(?i)(?:\bRel\s*\.(?:\s*p/\s*ac[oó]rd[aã]o)?(?:\s*(?:Min\s*\.|Minist(?:ro|ra)|Des\s*\.|Desembargador(?:a)?))?"
    r"|\bRelator(?:a)?(?:\s*p/\s*ac[oó]rd[aã]o)?(?:\s*(?:Min\s*\.|Minist(?:ro|ra)|Des\s*\.|Desembargador(?:a)?))?"
    r"|\bMin\s*\.|\bMinist(?:ro|ra)\b|\bDes\s*\.|\bDesembargador(?:a)?"
    r"|\bJu[ií]z(?:a)?\s+Federal\s+Convocad[oa])"
    r"(?:\s+Fed(?:eral|\s*\.))?(?:\s*\(a\))?\s*[:\-–]?"
    r"(?-i:(?:\s*[A-ZÀ-Þ][\wÀ-ÿ'’]+){0,2})\s*$"
)


def is_precedent_judge(text: str, start: int, end: int) -> bool:
    """Nome do julgador numa citacao de precedente (ementa, "Rel. Min. X, Segunda Turma, DJe ...").

    Estreito de proposito (vale para o nome que o LLM ou o elenco trouxe, que ja passou pelo juizo): o titulo tem de
    estar colado antes do nome, na mesma linha, e a linha (ou os 200 caracteres em volta) tem de ter sinal de citacao
    (REsp, Turma, DJe, julgado, STJ...). O magistrado da capa ou da assinatura segue as regras de sempre."""

    head = max(text.rfind("\n", 0, start) + 1, start - 40)
    title = _JUDGE_TITLE_BEFORE.search(text, head, start)
    if title is None:
        return False
    # O sinal de citacao tem de vir de fora do proprio titulo (o "Rel." sozinho nao prova ementa).
    around = text[max(0, start - 200) : title.start()] + " " + text[end : min(len(text), end + 200)]
    return bool(
        _PRECEDENT_ORG_CUE.search(around) or _PRECEDENT_AFTER.match(text[end : end + 60].split("\n", 1)[0])
    )


def is_precedent_party(text: str, start: int, end: int) -> bool:
    """ORGANIZATION de NER em contexto de citação jurisprudencial."""

    if is_magistrate_or_citation(text, start, end):
        return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    window = text[max(0, start - 200) : min(len(text), end + 200)]
    same_line = text[line_start:line_end]
    return bool(_PRECEDENT_ORG_CUE.search(window) or _PRECEDENT_ORG_CUE.search(same_line))
