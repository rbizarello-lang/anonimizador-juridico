"""Higiene das mencoes do LLM, antes de entrarem no elenco (H9-c).

O motor local passa por ``sanitize_spans`` e pelo juizo do lote; a mencao do LLM entrava no elenco sem
filtro. Medido no lote IDPJ com o ``qwen3:4b``: trocas fora do gabarito 21 -> 580 registros, termo comum,
rotulo com numero, classe processual, cidade e bairro virando PESSOA/EMPRESA/ENDERECO, mencao sem ancora,
classe e papel inventados pelo modelo, token colado em numero.

Esta funcao descarta so mencao DO LLM que nao e nome de pessoa, de empresa nem endereco. Nunca toca no que
o motor local achou: a politica "o LLM so acrescenta protecao, nunca tira" continua valendo.

Motivos de descarte (contados em ``LlmHygieneStats``; a AUDITORIA leva so os numeros):
classe fora de pessoa/empresa/endereco; sem ancora no texto; so minusculas; termo protegido (ou nome que o
contem); rotulo seguido de numero, so numero ou sigla; termo juridico; municipio, UF ou lugar solto; pessoa
de uma palavra so (sem nome completo no elenco que a contenha). Mais: mencao colada a numero e recortada.

Segunda rodada (H9-e), medida na leitura do lote IDPJ real: cidade de duas palavras ou com UF e orgao judicial
("Balneario Camboriu/SC", "19a Vara Federal de Porto Alegre"); rotulo de formulario e cartorio sem numero
("Razao Social", "Registro de Imoveis", lista em ``data/rotulos_formulario.txt``); clausula e paragrafo ordinais
("Clausula Decima", "DECIMA PRIMEIRA"); URL e dominio institucional; mencao que junta duas pessoas ("Fulano e
sua filha"); variante sem nenhuma palavra em comum com o nome canonico; instituicao financeira como endereco.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from anonimizador_juridico.pseudonymizer import fold_lexical

from .address_guard import company_form, not_an_address
from .gemini_detector import KEEP_TERMS, is_registry_office
from .geo_names import UF_SIGLAS, is_geo_name, is_state_name, place_label_before
from .role_labels import is_fixed_label, is_role_label
from .vehicle_names import is_vehicle_span
from .roster import (
    KEEP,
    ROLE_POLICY,
    TOKENIZE,
    UNKNOWN_ROLE,
    Mention,
    Roster,
    entity_core,
    normalize_kind,
    strip_token_tail,
)

_KINDS = frozenset({"pessoa", "empresa", "endereco"})
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_TOKENS = re.compile(r"[^\W_]+")
_VOWEL = re.compile(r"[AEIOUaeiou]")
_COMPANY_SUFFIX = re.compile(r"(?i)\b(?:ltda|me|epp|eireli|s/?a|s\.a|cia|companhia)\b")
_COMPANY_TAIL_WORDS = re.compile(r"(?i)[\s,.-]+(?:ltda|eireli|epp|me|s\.?\s?/?a)\.?\s*$")
_DOMAIN = re.compile(r"(?i)[\w-]+\.[a-z]{2,}|@")
_SIGLA = re.compile(r"^[A-Z]{2,5}$")
_MATRICULA_MENCAO = re.compile(r"(?i)\bmatr[ií]cula")

# Rotulo de peca seguido de numero ("Pagina 12", "Evento 45", "Tema 1.209") ou sozinho ("Despacho",
# "Valor Total"): e o rotulo do documento, nao uma pessoa.
_LABEL_NUMBER = re.compile(
    r"(?i)^(?:p[aá]gina|p[aá]g\.?|fls?\.?|evento|doc\.?|anexo|par[aá]grafo|art\.?|artigo|processo|despacho|"
    r"decis[aã]o|valor(?:\s+total)?|tema|s[uú]mula|lei|cl[aá]usula|item|inciso|certid[aã]o|of[ií]cio|"
    r"mandado|id|seq\.?)"
    r"(?:\s*(?:n[º°o]\.?|n\.)?\s*(?:\d|:)|\s*$)"
)

# Siglas que a higiene reconhece como sigla processual, alem da regra "ate 5 letras maiusculas sem vogal".
_SIGLAS_PROCESSUAIS = frozenset(
    "cda cpc ctn lef idpj cnpj cpf oab cep rg eproc pje dje trf trt tst tse stm cnh ar".split()
)

# Termo juridico/processual de uso comum: nunca e nome de pessoa, empresa nem endereco. Comparado por
# ``fold_lexical`` (sem acento, minusculo): igual ao termo, ou comeca por ele (termo de varias palavras e as
# poucas palavras de rotulo de ``_PREFIXO_SO_ROTULO``): "Codigo de Processo Civil de 2015".
_TERMOS_JURIDICOS = frozenset(
    fold_lexical(item)
    for item in (
        "decisão interlocutória", "decisão", "despacho", "despacho decisório", "sentença", "acórdão", "ementa",
        "execução fiscal", "execuções fiscais", "embargos", "embargos à execução", "embargos de declaração",
        "embargos de terceiro", "desconsideração da personalidade jurídica",
        "desconsideração inversa da personalidade jurídica", "incidente de desconsideração",
        "pessoa jurídica", "pessoa física", "pessoa natural", "código de processo civil", "código civil",
        "código tributário nacional", "código de defesa do consumidor", "código de processo penal",
        "lei", "lei complementar", "lei ordinária", "súmula", "tema", "recurso especial",
        "recurso extraordinário", "recurso de apelação", "agravo", "agravo de instrumento", "agravo interno",
        "apelação", "apelação cível", "contestação", "petição inicial", "petição", "réplica",
        "mandado de segurança", "mandado de penhora", "cumprimento de sentença", "certidão de dívida ativa",
        "dívida ativa", "ação anulatória", "ação declaratória", "ação civil pública", "ação de execução",
        "medida cautelar fiscal", "cautelar fiscal", "tutela de urgência", "tutela provisória", "penhora",
        "arresto", "citação", "intimação", "sucumbência", "honorários advocatícios", "honorários",
        "correção monetária", "juros de mora", "trânsito em julgado", "coisa julgada", "prescrição",
        "prescrição intercorrente", "decadência", "responsabilidade tributária", "grupo econômico",
        "fraude à execução", "exceção de pré-executividade", "impugnação", "impugnação ao cumprimento",
        "valor total", "valor da causa", "valor da dívida", "valor do débito", "valor atualizado",
        "documento eletrônico", "documento", "classe processual", "assunto", "órgão julgador",
        "parte autora", "parte ré", "polo ativo", "polo passivo", "exequente", "executado", "executada",
        "embargante", "embargado", "requerente", "requerido", "agravante", "agravado", "apelante", "apelado",
        "recorrente", "recorrido", "impetrante", "impetrado", "terceiro interessado", "ministério público",
        # v0.4.10 (leitura do caso real): conceito ou funcao, nunca nome.
        "holding", "holding patrimonial", "holding familiar", "holding patrimonial familiar",
        "administradora de bens", "administradora de bens próprios", "administração de bens próprios",
        "titular", "titular da requerida", "titular da empresa", "titular da executada", "atual titular",
        "sócio administrador", "sócio-administrador", "sócia administradora", "sócio", "sócia",
        "blindagem patrimonial", "confusão patrimonial", "desvio de finalidade", "esvaziamento patrimonial",
        "interposta pessoa", "interposição de pessoa", "pessoa interposta", "laranja", "testa de ferro",
        "conta fácil", "conta corrente", "conta poupança", "motor-casa", "motor-casa/ônibus", "motor casa",
        "requerida", "requerente", "adquirente", "cedente", "cessionário", "ex-cônjuge", "cônjuge",
        "relator", "relatora", "desembargador", "desembargadora", "desembargador federal",
        "desembargadora federal", "juiz federal", "juíza federal", "procurador da fazenda nacional",
        "procuradora da fazenda nacional",
    )
)
_PREFIXO_SO_ROTULO = frozenset({"lei", "sumula", "decisao", "despacho", "sentenca", "acordao", "ementa"})

# Termo protegido de uma palavra que, DENTRO de um nome, tambem o descaracteriza (sigla de instituicao).
# As palavras comuns da lista (uniao, tribunal, evento, comarca...) so valem inteiras: "Uniao Comercial de
# Graos Ltda" e empresa e tem de ser trocada.
# Os sistemas da lista ``data/sistemas_protegidos.txt`` valem inteiros em qualquer caso (KEEP_TERMS); aqui entram so as
# siglas que nao servem de nome proprio dentro de um nome ("Simba", "Regularize" e "Sida" podem ser marca ou sobrenome).
_SIGLAS_INSTITUCIONAIS = frozenset(
    "pgfn pfn inss stf stj trf4 cnj sisbajud renajud cnib infojud cpc ctn lef mpf dpu agu pgf pge pgm "
    "sniper censec serasajud pje ecac eproc".split()
)
_UNIAO_FEDERAL = ("uniao", "federal")


@dataclass(slots=True)
class LlmHygieneStats:
    """Contagens da higiene das mencoes do LLM (so numeros: vao para a AUDITORIA)."""

    classe: int = 0
    ancora: int = 0
    minusculas: int = 0
    protegido: int = 0
    rotulo_numero: int = 0
    juridico: int = 0
    geografia: int = 0
    uma_palavra: int = 0
    # H9-e: segunda rodada.
    rotulo_formulario: int = 0
    ordinal: int = 0
    url: int = 0
    duas_pessoas: int = 0
    variante: int = 0
    # H9-f: variante de 2+ palavras capitalizadas sem palavra em comum com o canonico, aceita (nome fantasia).
    variante_aceita: int = 0
    banco_endereco: int = 0
    # H9-f: modelo ou marca de veiculo como empresa; endereco recortado antes da cidade.
    veiculo: int = 0
    endereco_recortado: int = 0
    colado_recortado: int = 0
    colado_descartado: int = 0
    # Juizo do lote sobre as entradas do LLM (``judge_llm_entries``).
    lote_publico: int = 0
    lote_financeiro: int = 0
    lote_rua: int = 0
    # H9-f: "manter" que o juizo NAO aplicou: entrada que o motor local tambem achou e vai trocar; entrada de
    # papel so privado do LLM com CPF ou qualificacao de pessoa.
    lote_motor_local: int = 0
    lote_privado: int = 0
    # v0.4.10: validacao do que o LLM devolve (independente do modelo).
    data: int = 0
    codigo_eproc: int = 0
    cabecalho_folha: int = 0
    familia: int = 0
    agente_publico: int = 0
    borda_aparada: int = 0
    empresa_como_pessoa: int = 0
    # v0.4.11: guarda de tipo ENDERECO (cartorio, protocolo, titulo de peca, sem marcador de endereco) e empresa tipada
    # como endereco; "holding X" sem a palavra "holding".
    endereco_invalido: int = 0
    empresa_como_endereco: int = 0
    holding_aparada: int = 0
    # Mencoes que entraram no elenco depois do filtro.
    aceitas: int = 0

    @property
    def descartadas(self) -> int:
        return (
            self.classe + self.ancora + self.minusculas + self.protegido
            + self.rotulo_numero + self.juridico + self.geografia + self.uma_palavra
            + self.rotulo_formulario + self.ordinal + self.url + self.duas_pessoas
            + self.variante + self.banco_endereco + self.veiculo
            + self.data + self.codigo_eproc + self.cabecalho_folha + self.familia
            + self.endereco_invalido
        )

    def audit_lines(self, kept_public: int = 0, mixed: int = 0) -> list[str]:
        return [
            (
                f"Higiene das mencoes do LLM: descartadas {self.descartadas} "
                f"(classe fora da lista {self.classe}, sem ancora {self.ancora}, minusculas {self.minusculas}, "
                f"termo protegido {self.protegido}, rotulo/numero {self.rotulo_numero}, "
                f"termo juridico {self.juridico}, geografia {self.geografia}, uma palavra {self.uma_palavra}, "
                f"rotulo de formulario {self.rotulo_formulario}, clausula ordinal {self.ordinal}, "
                f"URL ou dominio {self.url}, duas pessoas {self.duas_pessoas}, "
                f"variante sem relacao {self.variante}, banco como endereco {self.banco_endereco}, "
                f"marca ou modelo de veiculo {self.veiculo}); "
                f"entradas mantidas pelo papel publico do LLM: {kept_public}; papel misto: {mixed}"
            ),
            (
                f"Validacao das mencoes do LLM (v0.4.10): data ou periodo {self.data}, codigo eproc "
                f"{self.codigo_eproc}, linha de folha {self.cabecalho_folha}, familia {self.familia}; "
                f"bordas aparadas {self.borda_aparada}; empresa tipada como pessoa retipada {self.empresa_como_pessoa}; "
                f"agente publico mantido {self.agente_publico}"
            ),
            (
                f"Validacao das mencoes do LLM (v0.4.11): endereco que nao e endereco (cartorio, protocolo, titulo de "
                f"peca, sem marcador de endereco) {self.endereco_invalido}; empresa tipada como endereco retipada "
                f"{self.empresa_como_endereco}; \"holding\" fora do nome da empresa {self.holding_aparada}"
            ),
            (
                "Variantes sem palavra em comum com o nome canonico, aceitas por parecerem nome proprio: "
                f"{self.variante_aceita}"
            ),
            (
                f"Mencoes do LLM coladas a numero: recortadas {self.colado_recortado}, "
                f"descartadas {self.colado_descartado}; enderecos recortados antes da cidade "
                f"{self.endereco_recortado}; aceitas no elenco: {self.aceitas}"
            ),
            (
                "Juizo do lote sobre as entradas do LLM: mantidas por agente publico "
                f"{self.lote_publico}, por instituicao financeira {self.lote_financeiro}, "
                f"retipadas como rua com nome de pessoa {self.lote_rua}; "
                f"manter negado por o motor local tambem a ter achado e trocar {self.lote_motor_local}, "
                f"por papel so privado com CPF ou qualificacao {self.lote_privado}"
            ),
        ]


def _words(value: str) -> list[str]:
    return _TOKENS.findall(fold_lexical(value))


def _contains_run(words: Sequence[str], term: Sequence[str]) -> bool:
    size = len(term)
    if not size or size > len(words):
        return False
    return any(list(words[index : index + size]) == list(term) for index in range(len(words) - size + 1))


_MULTIWORD_KEEP = tuple(
    sorted(
        {tuple(_words(term)) for term in KEEP_TERMS if len(_words(term)) >= 2},
        key=len,
        reverse=True,
    )
)


def _protected(value: str, extra: Sequence[str], *, contains: bool) -> bool:
    """Termo protegido inteiro; com ``contains``, tambem o nome que o traz dentro (Receita Federal de X)."""

    if _exact_or_federal(value, extra):
        return True
    if not contains:
        return False
    words = _words(value)
    if any(word in _SIGLAS_INSTITUCIONAIS for word in words):
        return True
    if any(_contains_run(words, term) for term in _MULTIWORD_KEEP):
        return True
    for item in extra:
        term = _words(str(item))
        if term and _contains_run(words, term):
            return True
    return False


def _exact_or_federal(value: str, extra: Sequence[str]) -> bool:
    """``is_kept_term`` tambem aceita "Uniao Comercial ..." (prefixo); aqui so vale o termo inteiro e a
    "Uniao Federal"."""

    words = _words(value)
    if tuple(words[:2]) == _UNIAO_FEDERAL:
        return True
    folded = " ".join(words)
    terms = {" ".join(_words(term)) for term in (*KEEP_TERMS, *extra)}
    return folded in terms


def _is_legal_term(value: str) -> bool:
    folded = fold_lexical(value)
    if folded in _TERMOS_JURIDICOS:
        return True
    for term in _TERMOS_JURIDICOS:
        if folded.startswith(term + " ") and (" " in term or term in _PREFIXO_SO_ROTULO):
            return True
    return False


is_legal_term = _is_legal_term  # H9-k: o guarda final usa a mesma lista


def is_exact_protected(value: str) -> bool:
    """Termo protegido INTEIRO (ou "Uniao Federal"); nunca por prefixo (H9-k, guarda final)."""

    return _exact_or_federal(value, ())


def _has_initial_capital(value: str) -> bool:
    return any(word[0].isupper() for word in _WORD.findall(value))


def _digit_share(value: str) -> float:
    chars = [char for char in value if char.isalnum()]
    if not chars:
        return 1.0
    return sum(char.isdigit() for char in chars) / len(chars)


def _is_label_or_number(value: str) -> bool:
    compact = value.strip()
    if not any(char.isalpha() for char in compact):
        return True  # so numero e pontuacao
    if _digit_share(compact) > 0.6:
        return True
    if _LABEL_NUMBER.match(compact):
        return True
    if _SIGLA.match(compact) and not _VOWEL.search(compact):
        return True  # sigla de ate 5 letras sem vogal ("TRF", "STJ", "PGFN")
    return False


def _person_words(value: str) -> list[str]:
    return _words(entity_core("pessoa", value))


def _known_full_names(mentions: Sequence[Mention], known_people: Iterable[str]) -> list[list[str]]:
    names: list[list[str]] = []
    for item in known_people:
        words = _person_words(str(item))
        if len(words) >= 2:
            names.append(words)
    for mention in mentions:
        for value in (mention.canonical, mention.text):
            if normalize_kind(mention.kind) == "pessoa" and value:
                words = _person_words(strip_token_tail(value))
                if len(words) >= 2:
                    names.append(words)
    return names


def _geo_reason(
    kind: str, value: str, text: str, start: int, end: int = -1, roster_names: Sequence[Sequence[str]] = ()
) -> bool:
    """Municipio, estado, UF ou lugar solto onde devia haver pessoa ou empresa (ou rua)."""

    if kind == "endereco":
        # Cidade/UF sozinha nao e logradouro; rua tem via ou numero.
        return is_geo_name(value) and not any(char.isdigit() for char in value)
    words = _WORD.findall(value)
    if not words:
        return False
    if _SIGLA.match(value.strip()) and is_geo_name(value):
        return True
    if is_geo_name(value) and (kind == "empresa" or len(words) == 1 or is_state_name(value)):
        return True
    # H9-e: cidade com sufixo de UF ("Balneario Camboriu/SC") e cidade de 2+ palavras como pessoa.
    tail = _UF_TAIL.search(value)
    if tail is not None and is_geo_name(value[: tail.start()]):
        return True
    if (
        kind == "pessoa"
        and len(words) >= 2
        and is_geo_name(value)
        and not _person_with_place_name(text, start, end, value, roster_names)
    ):
        return True
    # Rotulo de lugar logo antes ("Comarca de X", "Bairro: X"): nome curto sem sufixo societario.
    if 0 <= start <= len(text) and len(words) <= 3 and not _COMPANY_SUFFIX.search(value) and place_label_before(text, start):
        return True
    return False


# --- segunda rodada (H9-e) ----------------------------------------------------------------------

LABELS_FILE = Path(__file__).with_name("data") / "rotulos_formulario.txt"
# Conectivos que nao tornam um texto "nome": "Uso exclusivo DO Regin", "No Processo Judicial".
_CONNECTIVES = frozenset("de da do das dos e em no na nos nas n a o as os para por ao com".split())


@lru_cache(maxsize=1)
def _label_vocabulary() -> frozenset[str]:
    """Palavras da lista de rotulos de formulario (``data/rotulos_formulario.txt``), dobradas."""

    words: set[str] = set()
    for raw in LABELS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            words.update(_words(line))
    return frozenset(words)


def _is_form_label(value: str) -> bool:
    """Texto formado so por palavras da lista de rotulos de formulario/cartorio (mais conectivos): e o rotulo
    do documento ("Razao Social", "USO EXCLUSIVO DO REGIN"), nao nome proprio. Vale por prefixo tambem: o
    rotulo seguido so de palavras da lista. Nome de verdade traz ao menos uma palavra fora da lista."""

    words = _words(value)
    content = [word for word in words if word not in _CONNECTIVES]
    if not content:
        return False
    vocabulary = _label_vocabulary()
    return all(word in vocabulary for word in content)


# Orgao judicial ou publico generico: "19a Vara Federal de Porto Alegre", "Delegacia da Receita Federal".
_ORGAO = re.compile(
    r"^(?:\d+[a-z]?\s+)?(?:vara|juizo|subsecao|secao\s+judiciaria|turma\s+recursal|tribunal|camara|comarca|"
    r"juizado|delegacia|procuradoria|secretaria|ministerio|prefeitura|forum|cartorio|tabelionato|"
    r"receita\s+federal|justica\s+(?:federal|estadual|do\s+trabalho)|conselho)\b"
)


def _is_organ(value: str) -> bool:
    if _COMPANY_SUFFIX.search(value):
        return False
    return bool(_ORGAO.match(" ".join(fold_lexical(value).split())))


# Clausula, paragrafo, artigo... + ordinal ("Clausula Decima", "Paragrafo Primeiro", "Artigo 5") e ordinal por
# extenso sozinho ("DECIMA PRIMEIRA").
_STRUCT_WORDS = frozenset(
    "clausula paragrafo artigo item capitulo secao titulo inciso alinea livro anexo subclausula".split()
)
_ORDINAL_WORD = re.compile(
    r"^(?:primeir|segund|terceir|quart|quint|sext|setim|oitav|non|decim|vigesim|trigesim|quadragesim|"
    r"quinquagesim|sexagesim|septuagesim|octogesim|nonagesim|centesim|unic|ultim|penultim)[oa]s?$"
)
_NUMBER_WORD = re.compile(r"^\d+[a-z]{0,2}$")
_ROMAN_WORD = re.compile(r"^[ivxlcdm]+$")


def _is_ordinal_label(value: str) -> bool:
    words = [word for word in _words(value) if word not in {"e", "do", "da", "de"}]
    if not words:
        return False
    has_struct = any(word in _STRUCT_WORDS for word in words)
    meaningful = False
    for word in words:
        if word in _STRUCT_WORDS:
            continue
        if _ORDINAL_WORD.match(word):
            meaningful = True
        elif has_struct and (_NUMBER_WORD.match(word) or _ROMAN_WORD.match(word)):
            meaningful = True
        else:
            return False
    return meaningful


# URL e dominio: institucional (.jus.br, .gov.br, .leg.br, .mp.br, .def.br, .sc.gov) ou com parametro de chave
# nunca e nome; dominio de empresa privada sem query segue aceito como empresa.
_URL_MARK = re.compile(r"(?i)http|www\.|\?chave|\.(?:jus|gov|leg|mp|def)\.br\b|\.sc\.gov\b")
_URL_INSTITUTIONAL = re.compile(r"(?i)\.(?:jus|gov|leg|mp|def)\.br\b|\.sc\.gov\b")


def _is_url_mention(kind: str, value: str) -> bool:
    if not _URL_MARK.search(value):
        return False
    # H9-f: so o dominio nu ("www.empresa.com.br") pode ser nome de empresa; URL com esquema, caminho ou
    # parametro nao e entidade, tenha chave ou nao.
    bare = "?" not in value and "://" not in value and "/" not in value.strip().strip("/")
    private_domain = (
        kind == "empresa"
        and _DOMAIN.search(value) is not None
        and _URL_INSTITUTIONAL.search(value) is None
        and bare
    )
    return not private_domain


# Mencao que junta duas pessoas: "Alessandro e sua filha", "Fulano e Beltrana". A propagacao do lote cobre os
# nomes individuais. Nunca vale para empresa ("Comercio e Industria Ltda").
_COUPLE_POSSESSIVE = re.compile(r"(?i)\s+e\s+(?:sua|seu|suas|seus)\s+")
_AND_SPLIT = re.compile(r"\s+[eE]\s+")


# Sobrenomes comuns e sufixos de parentesco: "Joao Pedro de Almeida E SILVA" e UM nome, nao dois.
_COMMON_SURNAMES = frozenset(
    "silva souza santos oliveira pereira costa castro lima ferreira almeida rodrigues alves carvalho gomes "
    "martins araujo ribeiro barbosa melo cardoso rocha dias nunes moreira teixeira correia mendes freitas "
    "filho junior neto sobrinho netto".split()
)


def _capitalized_words(part: str) -> list[str]:
    return [word for word in _WORD.findall(part) if word[:1].isupper() and fold_lexical(word) not in _CONNECTIVES]


def _is_two_people(value: str) -> bool:
    """Duas pessoas numa mencao so: "Fulano e sua filha/seu filho", ou "Fulano Silva e Beltrana Souza" (cada
    lado com 2+ palavras capitalizadas e o segundo comecando por prenome, nao por sobrenome comum). Nome com
    "e" no meio ("Caio Augusto de Almeida e Silva", "LEONOR DE SOUZA E SILVA", "Rosa Helena Bastos e Castro")
    e UM nome e passa. Duas palavras soltas ("Leonor e Caio") so contam quando o segundo nao e sobrenome."""

    if _COUPLE_POSSESSIVE.search(value):
        return True
    parts = _AND_SPLIT.split(value.strip())
    if len(parts) != 2 or not all(part[:1].isupper() for part in parts):
        return False
    left, right = _capitalized_words(parts[0]), _capitalized_words(parts[1])
    if not left or not right:
        return False
    if fold_lexical(right[0]) in _COMMON_SURNAMES:
        return False
    if len(left) >= 2 and len(right) >= 2:
        return True
    return len(left) == 1 and len(right) == 1


# Cidade com sufixo de UF: "Balneario Camboriu/SC", "Porto Alegre - RS", "Itajai, SC", "Joinville (SC)".
_UF_TAIL = re.compile(rf"\s*(?:[/,(–—-])\s*(?:{UF_SIGLAS})\s*\)?\s*$")
# Quem e pessoa de verdade, mesmo com nome igual ao de um municipio ("Pedro Leopoldo", "Maria Helena").
_PERSON_CUE_BEFORE = re.compile(
    r"(?i)\b(?:sr|sra|srta|dr|dra|s[oó]cio|s[oó]cia|administrador|administradora|executad[oa]|requerid[oa]|"
    r"autor|autora|r[eé]u|embargante|advogad[oa]|testemunha|representante|propriet[aá]ri[oa]|c[oô]njuge|"
    r"filh[oa]|pai|m[aã]e)\b\.?\s*:?\s*$"
)
_PERSON_CUE_AFTER = re.compile(
    r"(?i)^\s*,?\s*(?:inscrit[oa]|portador|portadora|brasileir|nascid|casad|solteir|cpf|rg|oab)\b"
)


def _person_cue(text: str, start: int, end: int) -> bool:
    if start < 0:
        return False
    before = text[max(0, start - 40) : start]
    after = text[end : min(len(text), end + 40)]
    return bool(_PERSON_CUE_BEFORE.search(before) or _PERSON_CUE_AFTER.match(after))


# H9-f: rotulo "Nome:" antes, ou virgula e qualificacao de pessoa em ate 80 caracteres depois.
_NAME_LABEL_BEFORE = re.compile(r"(?i)\b(?:nome(?:\s+completo)?|parte|pessoa|titular)\s*:\s*$")
_QUALIFICATION_AFTER = re.compile(
    r"(?i)^\s*,[^\n]{0,80}?\b(?:brasileir|casad|solteir|divorciad|vi[uú]v|portador|inscrit|residente|"
    r"domiciliad|nascid|cpf|rg|empres[aá]ri|aposentad|advogad)"
)
# Contexto de lugar ao redor: "cidade de X", "comarca de X", "municipio de X", "em X," ou "X/UF" colado.
_PLACE_BEFORE = re.compile(r"(?i)\b(?:cidade|comarca|munic[ií]pio|foro|distrito|localidade|natural)\s+(?:d[eao]s?\s+)?$")
_EM_BEFORE = re.compile(r"(?i)\bem\s+$")
_PLACE_AFTER = re.compile(rf"^\s*(?:[/(–—-]\s*(?:{UF_SIGLAS})\b|,\s*(?:{UF_SIGLAS})\b)")


def _place_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 40) : start]
    after = text[end : min(len(text), end + 40)]
    if _PLACE_BEFORE.search(before) or _PLACE_AFTER.match(after):
        return True
    return bool(_EM_BEFORE.search(before) and re.match(r"^\s*[,/(–—-]", after))


def _person_with_place_name(text: str, start: int, end: int, value: str, roster_names: Sequence[Sequence[str]]) -> bool:
    """H9-f: pessoa de nome igual ao de municipio ("Pedro Leopoldo", "Maria Helena", "Antonio Carlos", "Paulo
    Lopes"): fica quando o nome ja e pessoa do elenco, ha pista de pessoa (cargo/papel antes, qualificacao
    depois, rotulo "Nome:"), ou o texto em volta nao traz contexto de lugar nem sufixo /UF. Com /UF ou termo
    de lugar e sem nenhuma pista de pessoa, continua cidade."""

    words = _person_words(value)
    if any(words == list(names) or _contains_run(names, words) for names in roster_names):
        return True
    if start < 0:
        return False
    if _person_cue(text, start, end):
        return True
    before = text[max(0, start - 40) : start]
    after = text[end : min(len(text), end + 80)]
    if _NAME_LABEL_BEFORE.search(before) or _QUALIFICATION_AFTER.match(after):
        return True
    return not _place_context(text, start, end)


# H9-f: marca ou modelo de veiculo ("Fiat Strada 2020", "Honda CG 160") nao e empresa; H9-g: so com modelo conhecido
# ou numero depois da marca; H9-h: a regra e do texto da mencao, venha de onde vier, e mora em ``vehicle_names``
# (marca, modelo ou implemento sozinho tambem, quando ha placa na linha ou ano colado).


# Instituicao financeira onde so cabe endereco (o juizo do lote cobre a posicao de SISBAJUD; aqui o texto).
_BANK_WORDS = re.compile(
    r"(?i)\b(?:banco|caixa\s+econ[oô]mica|cooperativa\s+de\s+cr[eé]dito|sicredi|sicoob|bradesco|ita[uú]|"
    r"santander|nubank|banrisul|cresol|unicred|safra|corretora|distribuidora\s+de\s+t[ií]tulos)\b"
)
_STREET_WORD = re.compile(
    r"(?i)\b(?:rua|r\.|avenida|av\.?|alameda|travessa|rodovia|estrada|pra[cç]a|largo|via|beco|servid[aã]o)\b"
)


def _is_bank_as_address(value: str) -> bool:
    return (
        _BANK_WORDS.search(value) is not None
        and not any(char.isdigit() for char in value)
        and _STREET_WORD.search(value) is None
    )


# Variante que nao partilha nada com o nome canonico: "nome empresarial" ou uma URL virando apelido de empresa.
_NAME_STOP = frozenset(
    "de da do das dos e em no na nos nas a o as os com para por ao ltda me epp eireli sa s cia companhia".split()
)


def _significant(value: str, minimum: int) -> list[str]:
    return [word for word in _words(value) if word not in _NAME_STOP and len(word) >= minimum]


def _initials(value: str, *, with_suffix: bool) -> str:
    stop = _CONNECTIVES if with_suffix else _NAME_STOP
    return "".join(word[0] for word in _words(value) if word not in stop and word[:1].isalpha())


def variant_related(kind: str, canonical: str, variant: str) -> bool:
    """A variante tem algo do nome canonico: uma palavra significativa em comum (4+ letras; 3+ em pessoa, onde
    "Ana" e apelido de "Ana Souza"; fora artigos, preposicoes e LTDA/ME/EIRELI/S.A.), contida no outro texto
    (dominio "betaindustrial" casa "Beta Industrial"), parecida o bastante para ser erro de OCR, ou sigla/iniciais
    dele. Sem nada disso a variante e outra coisa (rotulo, URL) e nao entra na entrada."""

    minimum = 3 if kind == "pessoa" else 4
    left = _significant(canonical, minimum)
    right = _significant(variant, minimum)
    if not left or not right:
        left = left or _words(canonical)
        right = right or _words(variant)
    compact_left = "".join(left)
    compact_right = "".join(right)
    for word in left:
        if word in compact_right:
            return True
    for word in right:
        if word in compact_left:
            return True
    for first in left:
        for second in right:
            if min(len(first), len(second)) >= 5 and SequenceMatcher(None, first, second).ratio() >= 0.8:
                return True
    for short, long in ((variant, canonical), (canonical, variant)):
        letters = "".join(_words(short))
        if 2 <= len(letters) <= 6 and short.strip().replace(".", "").replace(" ", "").isupper():
            if letters == _initials(long, with_suffix=False) or letters == _initials(long, with_suffix=True):
                return True
    return False


def _trim_glued(text: str, mention: Mention, stats: LlmHygieneStats | None) -> Mention | None:
    """Mencao colada a numero ("2024PEDRO SILVA", "ACME123"): o token ficaria colado em digito. Recorta a
    primeira ou a ultima palavra (a que encosta no numero) quando sobram duas ou mais; senao, descarta."""

    start, end = mention.start, mention.end
    changed = False
    for _ in range(2):
        left_glued = start > 0 and text[start - 1].isdigit()
        right_glued = end < len(text) and text[end].isdigit()
        if not left_glued and not right_glued:
            break
        value = text[start:end]
        if left_glued:
            cut = re.search(r"\s", value)
            if cut is None:
                return _glued_drop(stats)
            start += cut.end()
        elif right_glued:
            cuts = list(re.finditer(r"\s", value))
            if not cuts:
                return _glued_drop(stats)
            end = start + cuts[-1].start()
        changed = True
        while end > start and text[end - 1] in " \t,;:.-":
            end -= 1
        while start < end and text[start] in " \t,;:-":
            start += 1
        if end <= start or len(_WORD.findall(text[start:end])) < 2:
            return _glued_drop(stats)
    if not changed:
        return mention
    if stats is not None:
        stats.colado_recortado += 1
    piece = text[start:end]
    return replace(mention, text=piece, start=start, end=end, source_text=piece)


_TAIL_PART = re.compile(r"\s*,\s*([^,]+)$")
_CITY_UF = re.compile(rf"\s*(?:[/(–—-])\s*(?:{UF_SIGLAS})\s*\)?\s*$")


def _city_tail(part: str) -> bool:
    """Ultimo pedaco do endereco que e so UF ("SC"), cidade ("Itajai") ou cidade com UF ("Itajai/SC")."""

    part = part.strip()
    if re.fullmatch(rf"(?:{UF_SIGLAS})", part):
        return True
    name = _CITY_UF.sub("", part).strip()
    return bool(name) and not any(char.isdigit() for char in name) and is_geo_name(name)


def _trim_address_city(text: str, mention: Mention, stats: LlmHygieneStats | None) -> Mention:
    """H9-f: endereco que termina em ", Cidade", ", Cidade/UF" ou ", UF" e recortado antes da cidade: o token
    cobre a rua, o numero e o bairro; a cidade fica em claro (dado publico que o leitor precisa para o foro)."""

    start, end = mention.start, mention.end
    if not 0 <= start < end <= len(text):
        return mention
    value = text[start:end]
    cut, changed = len(value), False
    while True:
        found = _TAIL_PART.search(value[:cut])
        if found is None or not _city_tail(found.group(1)):
            break
        cut, changed = found.start(), True
    if not changed:
        return mention
    piece = value[:cut].rstrip(" ,;-–—")
    if len(piece) < 3 or (is_geo_name(piece) and not any(char.isdigit() for char in piece)):
        return mention
    if stats is not None:
        stats.endereco_recortado += 1
    canonical = strip_token_tail(mention.canonical or "")
    canonical = piece if not canonical or canonical.startswith(piece) else canonical
    return replace(mention, text=piece, end=start + len(piece), source_text=piece, canonical=canonical)


def _glued_drop(stats: LlmHygieneStats | None) -> None:
    if stats is not None:
        stats.colado_descartado += 1
    return None


# --- v0.4.10: validacao do que o LLM devolve ---------------------------------------------------------------------
# Data ou periodo ("05/2011 a 05/2012", "21/08/2019", "agosto de 2019"): nunca nome nem endereco.
_DATE_PIECE = re.compile(
    r"(?i)\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{1,2}[/.-]\d{4}\b|\b\d{4}\b|"
    r"\b(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b"
)
_DATE_FILLER = re.compile(r"(?i)\b(?:a|e|at[eé]|de|entre|em|desde|o|os|ao|d[eo]s?|per[ií]odo)\b|[\s,;:–—()-]")
# Codigo de documento do eproc (PET1, CONTES1, RELVOTO1, DESPADEC12, OUT3, RÉPLICA1) ou evento.
_EPROC_DOC = re.compile(r"^(?:[A-ZÀ-Ý]{2,12}\d{1,3}|EVENTO\s*\d+|EV\.?\s*\d+)$")
# Linha de coordenada que o app grava: "[fl. 12 · Evento 3 · PET1 · p. 2]".
_FOLIO_LINE = re.compile(r"^\s*\[fl\.")
# "familia Fulano": a familia nao e uma pessoa.
_FAMILY = re.compile(
    r"(?i)^(?:a\s+|da\s+|na\s+|o\s+|do\s+)?(?:fam[ií]lia|patron[ií]mico|sobrenome|nome\s+de\s+fam[ií]lia)\b"
)
_EDGE_LEFT = re.compile(
    r"(?i)^(?:(?:e|o|a|os|as|do|da|dos|das|de|ao|à|pelo|pela|eminente|exm[oa]\.?|ilustr[ií]ssim[oa]|"
    r"sr\.?|sra\.?|senhora?|dr\.?|dra\.?|doutora?|des\.?|min\.?|desembargador(?:a)?(?:\s+federal)?|"
    r"ju[ií]z(?:a)?(?:\s+federal)?|relator(?:a)?|procurador(?:a)?(?:\s+da\s+fazenda\s+nacional)?)[\s,:]+)+"
)
_EDGE_RIGHT = re.compile(r"(?i)(?:[\s,;:]+(?:e|ou|o|a|os|as|do|da|dos|das|de))+$")
_COMPANY_FORM = re.compile(r"(?i)\b(?:ltda|eireli|s\.?\s?/?a\.?|epp|me|holding|participa[cç][oõ]es|com[eé]rcio|ind[uú]stria)\b\.?$")


def _is_date_value(value: str) -> bool:
    if not _DATE_PIECE.search(value):
        return False
    rest = _DATE_FILLER.sub("", _DATE_PIECE.sub("", value))
    return not rest.strip()


def _folio_line(text: str, start: int) -> bool:
    if not 0 <= start <= len(text):
        return False
    line_start = text.rfind("\n", 0, start) + 1
    return bool(_FOLIO_LINE.match(text[line_start:start + 1]))


def _trim_edges(text: str, mention: Mention, stats: LlmHygieneStats | None) -> Mention | None:
    """Apara "e", artigos e titulos ("Eminente Desembargador Federal", "Dr.") das bordas da mencao."""

    value = mention.text
    left = _EDGE_LEFT.match(value)
    cut_left = left.end() if left is not None else 0
    right = _EDGE_RIGHT.search(value[cut_left:])
    cut_right = len(value) - (cut_left + right.start()) if right is not None else 0
    if not cut_left and not cut_right:
        return mention
    piece = value[cut_left : len(value) - cut_right] if cut_right else value[cut_left:]
    if len(_WORD.findall(piece)) < 1:
        return None
    if stats is not None:
        stats.borda_aparada += 1
    start = mention.start + cut_left if mention.start >= 0 else mention.start
    end = start + len(piece) if mention.start >= 0 else mention.end
    if 0 <= start < end <= len(text) and text[start:end] != piece:
        start, end = mention.start, mention.end  # posicao que nao bate: so o texto muda
    canonical = mention.canonical
    if canonical and fold_lexical(canonical) == fold_lexical(value):
        canonical = piece
    return replace(mention, text=piece, start=start, end=end, source_text=piece, canonical=canonical)


_PFN_BEFORE = re.compile(
    r"(?i)\bprocurador(?:a)?(?:-chefe)?\s+(?:da\s+fazenda\s+nacional|federal|do\s+estado|do\s+munic[ií]pio)\s*[:,\-–]?\s*$"
)


def public_agent_mention(text: str, start: int, end: int) -> bool:
    """A regra do julgador e do agente publico do motor local, agora tambem para a mencao do LLM: rotulo de julgador
    ou de procurador publico logo antes do nome, ou o cargo logo depois (o contexto solto de 80 caracteres do
    ``is_pfn_officer`` nao basta: o procurador da executada fica trocado)."""

    from .fidelity import is_magistrate_or_citation
    from .target_list import in_public_agent_name

    if not 0 <= start < end <= len(text):
        return False
    return (
        is_magistrate_or_citation(text, start, end)
        or in_public_agent_name(text, start, end)
        or bool(_PFN_BEFORE.search(text[max(0, start - 70) : start]))
    )


def filter_llm_mentions(
    mentions: Iterable[Mention],
    text: str,
    stats: LlmHygieneStats | None = None,
    *,
    extra_keep: Sequence[str] = (),
    known_people: Iterable[str] = (),
    known_companies: Iterable[str] = (),
) -> list[Mention]:
    """Mencoes do LLM que merecem entrar no elenco. ``text`` e o que o LLM leu (as posicoes das mencoes sao
    dele). ``known_people``: nomes que o elenco ja tem; pessoa de uma palavra so passa se algum nome completo
    (do elenco, de outra mencao ou o ``nome_canonico`` dela) a contem."""

    items = list(mentions)
    extra = tuple(str(item) for item in extra_keep if str(item).strip())
    known_people = list(known_people)
    full_names = _known_full_names(items, known_people)
    roster_names = _known_full_names([], known_people)
    known_companies = [str(item) for item in known_companies]
    companies = {fold_lexical(_COMPANY_TAIL_WORDS.sub("", item)).strip() for item in known_companies}
    companies.discard("")
    company_forms = _company_forms(known_companies)
    kept: list[Mention] = []
    for mention in items:
        if mention.text and normalize_kind(mention.kind) in {"pessoa", "empresa"}:
            trimmed = _trim_edges(text, mention, stats)
            if trimmed is None:
                continue
            mention = trimmed
        if mention.text and normalize_kind(mention.kind) in {"pessoa", "empresa"}:
            holding = _trim_holding(text, mention, stats)
            if holding is not mention:
                mention = replace(holding, kind="empresa")
        address_reason = ""
        if mention.text and normalize_kind(mention.kind) == "endereco":
            # v0.4.11: guarda de tipo ENDERECO. A empresa conhecida tipada como endereco vira empresa; o resto que nao
            # e endereco (cartorio, protocolo, titulo de peca, sem marcador) sai depois dos filtros de sempre.
            address_reason = not_an_address(strip_token_tail(mention.text), company_forms)
            if address_reason == "endereco_empresa":
                mention = replace(mention, kind="empresa")
                address_reason = ""
                if stats is not None:
                    stats.empresa_como_endereco += 1
        if normalize_kind(mention.kind) == "pessoa":
            value = strip_token_tail(mention.text)
            folded = fold_lexical(_COMPANY_TAIL_WORDS.sub("", value)).strip()
            if _COMPANY_FORM.search(value) or (folded and folded in companies):
                # v0.4.10: empresa tipada como pessoa (razao social, ou empresa que o elenco ja tem) vira empresa.
                mention = replace(mention, kind="empresa")
                if stats is not None:
                    stats.empresa_como_pessoa += 1
        reason = _reject(mention, text, extra, full_names, roster_names)
        if reason is not None:
            if stats is not None:
                setattr(stats, reason, getattr(stats, reason) + 1)
            continue
        if address_reason:
            if stats is not None:
                stats.endereco_invalido += 1
            continue
        kind = normalize_kind(mention.kind)
        canonical = strip_token_tail(mention.canonical or "")
        value = strip_token_tail(mention.text)
        if canonical and value and canonical != value and not variant_related(kind, canonical, value):
            if stats is not None:
                stats.variante_aceita += 1
        if kind in {"pessoa", "empresa"}:
            trimmed = _trim_glued(text, mention, stats)
            if trimmed is None:
                continue
            mention = trimmed
        elif kind == "endereco":
            mention = _trim_address_city(text, mention, stats)
        kept.append(mention)
    if stats is not None:
        stats.aceitas += len(kept)
    return kept


def _company_forms(names: Iterable[str]) -> set[str]:
    """Formas de comparacao das empresas conhecidas e a marca (primeira palavra de conteudo de 4+ letras)."""

    forms: set[str] = set()
    for name in names:
        form = company_form(name)
        if not form:
            continue
        forms.add(form)
        words = [word for word in form.split() if word not in {"de", "da", "do", "das", "dos", "e"}]
        if len(words) >= 2 and len(words[0]) >= 4 and not is_geo_name(words[0]):
            forms.add(words[0])
    return forms


# v0.4.11: "holding X", "a holding X", "holding patrimonial X": a palavra fica em claro e o nome e o da empresa X.
_HOLDING_LEAD = re.compile(
    r"(?i)^(?:(?:a|da|na|pela|à)\s+)?(?:holding(?:\s+(?:patrimonial|familiar|patrimonial\s+familiar))?"
    r"|administradora\s+de\s+bens|empresa\s+(?:holding|patrimonial))\s+"
)


def _trim_holding(text: str, mention: Mention, stats: LlmHygieneStats | None) -> Mention:
    """Tira da frente da mencao de empresa a palavra "holding" (e afins) quando o resto nao e razao social com sufixo
    societario que comeca por ela ("HOLDING ALFA S.A." fica): a palavra e o conceito juridico, nao o nome."""

    value = mention.text
    match = _HOLDING_LEAD.match(value)
    if match is None or _HOLDING_LEAD.fullmatch(value.rstrip() + " "):
        return mention  # "Holding Patrimonial" sozinha e o conceito (descartado adiante)
    rest = value[match.end() :]
    if not rest.strip() or not _has_initial_capital(rest):
        return mention
    if value[: match.end()].strip()[:1].isupper() and _COMPANY_TAIL_WORDS.search(rest):
        return mention  # "Holding Alfa Ltda": o nome registrado comeca por Holding
    if stats is not None:
        stats.holding_aparada += 1
    start = mention.start + match.end() if mention.start >= 0 else mention.start
    end = mention.end
    if not (0 <= start < end <= len(text) and text[start:end] == rest):
        start, end = mention.start, mention.end
    canonical = mention.canonical
    if not canonical or fold_lexical(canonical) == fold_lexical(value):
        canonical = rest
    else:
        lead = _HOLDING_LEAD.match(canonical)
        if lead is not None and canonical[lead.end() :].strip():
            canonical = canonical[lead.end() :]
    return replace(mention, text=rest, start=start, end=end, source_text=rest, canonical=canonical)


def _reject(
    mention: Mention,
    text: str,
    extra: Sequence[str],
    full_names: Sequence[Sequence[str]],
    roster_names: Sequence[Sequence[str]] = (),
) -> str | None:
    kind = normalize_kind(mention.kind)
    if kind not in _KINDS:
        return "classe"
    if not mention.anchored:
        return "ancora"
    value = strip_token_tail(mention.text)
    if not value:
        return None  # so token: o elenco ja o descarta
    is_street = kind == "endereco"
    # v0.4.10: o que o LLM devolve e validado aqui, qualquer que seja o modelo.
    if _is_date_value(value):
        return "data"
    if _EPROC_DOC.match(value.strip()):
        return "codigo_eproc"
    if _folio_line(text, mention.start):
        return "cabecalho_folha"
    if kind == "pessoa" and _FAMILY.match(value):
        return "familia"

    if _is_url_mention(kind, value):
        return "url"
    if _MATRICULA_MENCAO.search(value):
        return "rotulo_numero"  # matricula de imovel fica sempre em claro, mesmo que o LLM a aponte (H9-i)
    if not is_street:
        letters = any(char.isalpha() for char in value)
        if letters and not _has_initial_capital(value) and not (kind == "empresa" and _DOMAIN.search(value)):
            return "minusculas"
    if _protected(value, extra, contains=not is_street):
        return "protegido"
    if _is_label_or_number(value):
        return "rotulo_numero"
    if not is_street and _is_ordinal_label(value):
        return "ordinal"
    if not is_street and (
        _is_form_label(value)
        or is_role_label(value)
        or is_fixed_label(value)
        or _is_organ(value)
        or is_registry_office(value)
    ):
        return "rotulo_formulario"
    if not is_street and _is_legal_term(value):
        return "juridico"
    if kind == "empresa" and is_vehicle_span(text, mention.start, mention.end):
        return "veiculo"
    if kind == "pessoa" and _is_two_people(value):
        return "duas_pessoas"
    if is_street and _is_bank_as_address(value):
        return "banco_endereco"
    if _geo_reason(kind, value, text, mention.start, mention.end, roster_names):
        return "geografia"
    if kind == "pessoa":
        words = _person_words(value)
        if len(words) <= 1:
            word = words[0] if words else ""
            if not word or not any(word in names for names in full_names):
                return "uma_palavra"
    canonical = strip_token_tail(mention.canonical or "")
    if canonical and canonical != value and not variant_related(kind, canonical, value):
        if not _looks_like_proper_name(kind, value):
            return "variante"
    return None


def _looks_like_proper_name(kind: str, value: str) -> bool:
    """H9-f: variante sem palavra em comum com o canonico ("Padaria Pao Quente" para "Alfa Comercio de Alimentos
    Ltda", apelido de varias palavras) so e descartada se NAO parecer nome proprio. Ja passaram pelos filtros
    de minusculas, rotulo, URL, termo protegido e termo juridico; aqui exige 2+ palavras capitalizadas e,
    se houver digito, sufixo societario."""

    if len(_capitalized_words(value)) < 2:
        return False
    if any(char.isdigit() for char in value) and not _COMPANY_SUFFIX.search(value):
        return False
    return True


# --- papeis do LLM (item 2) ---------------------------------------------------------------------

PUBLIC_ROLES = frozenset(role for role, action in ROLE_POLICY.items() if action == KEEP)
PRIVATE_ROLES = frozenset(role for role, action in ROLE_POLICY.items() if action == TOKENIZE)


def role_counts(roster: Roster) -> tuple[int, int]:
    """(entradas mantidas so pelo papel publico que o LLM atribuiu, entradas de papel misto: publico e
    privado ao mesmo tempo). Papel ``desconhecido`` nao conta. So contagens."""

    kept = mixed = 0
    for entry in roster.entries:
        roles = {role for role in entry.roles if role != UNKNOWN_ROLE}
        public, private = roles & PUBLIC_ROLES, roles & PRIVATE_ROLES
        if public and private:
            mixed += 1
        elif public and not private and entry.action == KEEP:
            kept += 1
    return kept, mixed
