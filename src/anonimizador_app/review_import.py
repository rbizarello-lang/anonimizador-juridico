"""Importador da revisao externa (H9-m): a resposta do revisor (Gemini) colada na aba Caso vira proposta para as
listas do caso e para os termos protegidos, conferida contra um lote ja feito. Sem UI: a tela so chama.

Formato de entrada (uma linha por achado, separador ``|``; o resto vira "rejeitada: fora do formato")::

    TROCAR | tipo | texto exato | folha        (tipo: pessoa, empresa, endereco, documento, processo, cda, outro)
    MANTER | [MARCA_n] | motivo
    UNIR | [MARCA_A] | [MARCA_B] | motivo

Conferencia (nada muda ainda):

- TROCAR: o texto tem de existir no texto exportado do lote (``*.md`` da pasta exportavel), sem diferenca de
  quebra de linha, espacos, caixa ou acento; texto que ja e marca e rejeitado. Pessoa ou empresa que e parte de
  (ou igual a) um nome ja conhecido (lista do caso ou original de um token do mapa) vira "Outras grafias" desse
  nome; com mais de um nome possivel, "decidir". Palavra unica de pessoa nunca vira entrada nova.
- MANTER: a marca e traduzida pelo ``mapa.json``; proposta: o nome completo nos termos protegidos. Palavra unica
  ou nome da lista do caso: "decidir".
- UNIR: as duas marcas traduzidas; proposta: "original_A = original_B" em "Outras grafias" (A = o da lista do caso
  ou o de mais ocorrencias). Nunca marcada por padrao.

H9-o: a conferencia usa a lista da operacao escolhida na aba Caso e o que se aplica ganha essa operacao; marca no
5o campo e traduzida pelo mapa (nome com colchetes nunca e gravado); trava: pai e filho (sufixo Filho/Junior/Neto/
Sobrinho/Segundo) nunca unidos, MANTER de escritorio de advocacia recusado (OAB ao lado: decidir), data nunca vira
endereco; UNIR so sai marcada com o mesmo CPF/CNPJ citado no motivo e ao lado das duas marcas.

O registro (``<pasta privada>/revisoes/revisao_<lote>_<AAAAMMDD_HHMMSS>.txt``) guarda a resposta colada e o que foi
aplicado; e pasta privada. A AUDITORIA do lote seguinte ganha so a contagem (``review_audit_lines``).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from anonimizador_juridico.pseudonymizer import fold_lexical

from .case_list import (
    classify_cda,
    classify_contact,
    classify_process,
    format_alias_line,
    looks_like_date,
    parse_alias_lines,
)
from .lote_alerts import INSTRUCTIONS_NAME
from .lote_elenco import CAST_NAME
from .lote_index import INDEX_NAME
from .pipeline import resolve_lote_dirs
from .settings import case_item_key, operation_label, settings_for_operation
from .spreadsheet_targets import merge_unique
from .target_ids import parse_target_ids
from .token_syntax import strip_groups

REVIEWS_DIRNAME = "revisoes"

ACCEPTED = "aceita"
REJECTED = "rejeitada"
DECIDE = "decidir"

# Campo de AppSettings -> nome da caixa na tela.
FIELD_LABELS = {
    "target_people": "Pessoas",
    "target_companies": "Empresas",
    "target_processes": "Processos",
    "target_cdas": "CDAs",
    "target_contacts": "Contatos e veículos",
    "target_ids": "CPF e CNPJ da carteira",
    "target_aliases": "Outras grafias",
    "exclusions": "Termos protegidos",
}

_KINDS = {
    "pessoa": "target_people",
    "empresa": "target_companies",
    "processo": "target_processes",
    "cda": "target_cdas",
    "endereco": "target_contacts",
    "documento": "target_contacts",
    "outro": "target_contacts",
}
_KIND_ALIASES = {"pessoas": "pessoa", "empresas": "empresa", "processos": "processo", "cdas": "cda",
                 "enderecos": "endereco", "documentos": "documento", "outros": "outro"}

_MARK = re.compile(r"^\[?\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9_]*?_\d+)\s*(?:·G\d+)?\s*\]?$")
_TOKEN_ANYWHERE = re.compile(r"\[[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9_]+?_\d+(?:·G\d+)?\]")
_LEADER = re.compile(r"^\s*(?:[-*•>]+|\d{1,3}[.)])\s*")
_STAMP = re.compile(r"(\d{8}_\d{6})")
_APPLIED_LINE = re.compile(r"^Aplicadas: (\d+) \(TROCAR (\d+), MANTER (\d+), UNIR (\d+)\)", re.M)
_LEGAL_TAIL = re.compile(r"(?i)[\s,.\-]+(?:ltda\.?|eireli\.?|s/?a\.?|s\.a\.?|me|epp)\s*$")
_GUIDES = {INDEX_NAME, CAST_NAME, INSTRUCTIONS_NAME}
_PLATE = re.compile(r"^[A-Z]{3}-?[0-9][A-Z0-9][0-9]{2}$")
# H9-o: qualquer coisa entre colchetes no 5o campo ("Joana [PESSOA_1]", "[PESSOA_1 ]").
_BRACKETED = re.compile(r"\[[^\[\]]*\]")
# H9-o (trava): sufixo de geracao. Dois nomes que so diferem por ele sao pai e filho (avo e neto...), nunca a mesma
# pessoa por conta do revisor.
_GENERATION = frozenset({"filho", "filha", "junior", "jr", "neto", "neta", "sobrinho", "sobrinha", "segundo", "segunda"})
# H9-o (trava): escritorio de advocacia nunca e "orgao publico" (MANTER). A Ordem dos Advogados (autarquia) e.
_LAW_FIRM = re.compile(r"(?i)\badvogad[oa]s\b|\badvocacia\b|\badvocat[íi]ci[oa]\b")
_BAR_ORDER = re.compile(r"(?i)\bordem\s+dos\s+advogados\b|^\s*oab\b")
_OAB_NEAR = re.compile(r"(?i)\bOAB\b|\[OAB_\d+\]")
# H9-o: o mesmo CPF/CNPJ citado no motivo do UNIR, ao lado das duas marcas no texto exportado.
_ID_MARK = re.compile(r"\[(?:CPF|CNPJ)_\d+\]")
_ID_REACH = 80


# --------------------------------------------------------------------------------------------- lote de referencia


@dataclass(frozen=True, slots=True)
class LoteRef:
    job_id: str
    map_path: Path
    export_dir: Path

    @property
    def has_export(self) -> bool:
        return self.export_dir.is_dir()


def list_lotes(private_root: Path, export_root: Path) -> list[LoteRef]:
    """Lotes da pasta privada que tem ``mapa.json``, do mais recente ao mais antigo."""

    found: dict[str, LoteRef] = {}
    private_root, export_root = Path(private_root), Path(export_root)
    for base in (private_root, private_root / "privados"):
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            if not (child.is_dir() and child.name.startswith("lote_") and (child / "mapa.json").is_file()):
                continue
            if child.name in found:
                continue
            export_dir, map_path, _warning = resolve_lote_dirs(export_root, private_root, child.name)
            if not map_path.is_file():
                map_path = child / "mapa.json"
            found[child.name] = LoteRef(child.name, map_path, export_dir)
    return [found[name] for name in sorted(found, reverse=True)]


@dataclass(slots=True)
class MapEntry:
    token: str
    entity_type: str
    originals: tuple[str, ...]
    preferred: str
    alvo: str = ""

    @property
    def name(self) -> str:
        return " ".join((self.preferred or (self.originals[0] if self.originals else "")).split())


@dataclass(slots=True)
class LoteData:
    job_id: str
    records: dict[str, MapEntry]
    texts: list[str]
    folded_text: str = ""

    def occurrences(self, token: str) -> int:
        return sum(strip_groups(text).count(token) for text in self.texts)


def load_lote(ref: LoteRef) -> LoteData:
    payload = json.loads(Path(ref.map_path).read_text(encoding="utf-8"))
    records: dict[str, MapEntry] = {}
    for item in payload.get("records", []):
        token = str(item.get("token") or "")
        if not token:
            continue
        originals = tuple(str(value) for value in item.get("originals", []) if str(value).strip())
        records[token] = MapEntry(
            token=token,
            entity_type=str(item.get("entity_type") or ""),
            originals=originals,
            preferred=str(item.get("preferida") or ""),
            alvo=str(item.get("alvo") or ""),
        )
    texts: list[str] = []
    if ref.has_export:
        for path in sorted(ref.export_dir.glob("*.md")):
            if path.name in _GUIDES:
                continue
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    data = LoteData(ref.job_id, records, texts)
    data.folded_text = "\n".join(fold_text(text) for text in texts)
    return data


def fold_text(value: str) -> str:
    """Sem acento, sem caixa, qualquer sequencia de espaco/quebra de linha vira um espaco."""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    stripped = stripped.replace("​", " ").replace("⁠", " ").replace("­", "")
    return re.sub(r"\s+", " ", stripped).casefold().strip()


def text_in_lote(text: str, data: LoteData) -> bool:
    needle = fold_text(text)
    if not needle:
        return False
    pattern = re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)")
    return pattern.search(data.folded_text) is not None


# --------------------------------------------------------------------------------------------------- parse


@dataclass(slots=True)
class ParsedLine:
    number: int
    raw: str
    action: str = ""  # TROCAR, MANTER, UNIR ou "" (fora do formato)
    fields: tuple[str, ...] = ()


def parse_review(text: str) -> list[ParsedLine]:
    """Linhas uteis da resposta. Linha em branco, cerca ``` e "NADA" somem; o resto que nao e TROCAR/MANTER/UNIR
    volta com ``action=""``."""

    lines: list[ParsedLine] = []
    for number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        cleaned = _LEADER.sub("", line).strip().strip("`").strip()
        if fold_lexical(cleaned) in {"nada", "nada a apontar", "nenhum", "nenhuma"}:
            continue
        parts = [part.strip().strip("`").strip() for part in cleaned.split("|")]
        head = fold_lexical(parts[0]).replace("*", "").strip() if parts else ""
        action = head.upper() if head in {"trocar", "manter", "unir"} else ""
        if not action:
            lines.append(ParsedLine(number, line))
            continue
        lines.append(ParsedLine(number, line, action, tuple(parts[1:])))
    return lines


# ------------------------------------------------------------------------------------------------ conferencia


@dataclass(slots=True)
class Proposal:
    field_name: str
    value: str = ""
    principal: str = ""  # so para "Outras grafias"
    principal_field: str = ""  # H9-n: lista em que o nome principal entra, se ainda nao estiver (Pessoas/Empresas)

    def describe(self) -> str:
        label = FIELD_LABELS.get(self.field_name, self.field_name)
        if self.field_name == "target_aliases":
            return f"{label}: {self.principal} = {self.value}"
        return f"{label}: {self.value}"


@dataclass(slots=True)
class ReviewItem:
    number: int
    raw: str
    action: str
    subject: str  # texto (TROCAR) ou marca(s) (MANTER/UNIR)
    status: str
    reason: str = ""
    proposal: Proposal | None = None
    candidates: tuple[str, ...] = ()  # nomes principais possiveis (TROCAR ambiguo)
    alias: str = ""  # a grafia a ligar ao candidato escolhido
    checked: bool = False
    choice: str = ""  # candidato escolhido na tela
    # H9-n: TROCAR de pessoa/empresa em "decidir": o Operador digita o nome completo (nome principal).
    kind: str = ""  # pessoa | empresa
    name_field: bool = False
    typed: str = ""
    suggested: str = ""  # nome completo do 5o campo que nao foi achado (ja preenchido no campo)
    evidence: str = ""  # H9-o: UNIR com o mesmo CPF/CNPJ ao lado das duas marcas (marcada por padrao)

    @property
    def checkable(self) -> bool:
        return self.status != REJECTED and (self.proposal is not None or bool(self.candidates) or self.name_field)

    def type_principal(self, value: str) -> None:
        """O nome completo digitado na tela: a linha vira grafia dele e fica marcada (vazio: desmarca)."""

        self.typed = " ".join(str(value or "").split())
        self.checked = bool(self.typed) or (self.checked and self.proposal is not None)

    def destination(self) -> str:
        chosen = self.effective()
        if chosen is not None:
            return chosen.describe()
        if self.candidates:
            return "Outras grafias de: " + " | ".join(self.candidates)
        return "—"

    def effective(self) -> Proposal | None:
        list_field = "target_companies" if self.kind == "empresa" else "target_people"
        if self.name_field and self.typed and _has_bracket(self.typed):
            return None  # H9-o: nome digitado com marca entre colchetes nunca e gravado
        if self.name_field and self.typed:
            if fold_lexical(self.typed) == fold_lexical(self.alias):
                return Proposal(list_field, self.typed)
            return Proposal("target_aliases", self.alias, self.typed, principal_field=list_field)
        if self.proposal is not None:
            return self.proposal
        if self.candidates and self.choice in self.candidates:
            return Proposal("target_aliases", self.alias, self.choice)
        return None


@dataclass(slots=True)
class _Entity:
    kind: str  # pessoa | empresa
    principal: str
    forms: list[str] = field(default_factory=list)
    folded: set[str] = field(default_factory=set)
    words: list[list[str]] = field(default_factory=list)
    listed_forms: set[str] = field(default_factory=set)  # dobradas, ja na lista do caso

    @property
    def listed(self) -> bool:
        return bool(self.listed_forms)


def _company(value: str) -> bool:
    return bool(_LEGAL_TAIL.search(value))


def _body(value: str) -> str:
    return fold_lexical(_LEGAL_TAIL.sub("", value))


class _Known:
    """Nomes conhecidos: lista do caso (pessoas, empresas, grafias) e originais dos tokens do mapa."""

    def __init__(self, settings, data: LoteData) -> None:
        self.entities: list[_Entity] = []
        self._by_fold: dict[tuple[str, str], _Entity] = {}
        self._by_body: dict[str, _Entity] = {}
        self._listed: set[str] = set()
        for kind, names in (("pessoa", settings.target_people), ("empresa", settings.target_companies)):
            for name in names:
                entity = self._find(kind, name) or self._new(kind, name)
                self._add(entity, name, listed=True)
        for principal, aliases in parse_alias_lines(settings.target_aliases):
            kind = "empresa" if self._find("empresa", principal) or _company(principal) else "pessoa"
            entity = self._find(kind, principal) or self._new(kind, principal)
            for form in (principal, *aliases):
                self._add(entity, form, listed=True)
        for record in data.records.values():
            kind = {"PERSON": "pessoa", "ORGANIZATION": "empresa"}.get(record.entity_type)
            if kind is None:
                continue
            forms = [" ".join(value.split()) for value in (*record.originals, record.preferred) if value.strip()]
            entity = self._find(kind, record.alvo) if record.alvo else None
            for form in forms:
                entity = entity or self._find(kind, form)
            entity = entity or self._new(kind, record.name)
            for form in forms:
                self._add(entity, form, listed=False)

    def _new(self, kind: str, principal: str) -> _Entity:
        entity = _Entity(kind, " ".join(principal.split()))
        self.entities.append(entity)
        self._add(entity, entity.principal, listed=False)
        return entity

    def _add(self, entity: _Entity, form: str, *, listed: bool) -> None:
        folded = fold_lexical(form)
        if not folded:
            return
        if listed:
            entity.listed_forms.add(folded)
            self._listed.add(folded)
        if folded in entity.folded:
            return
        entity.forms.append(form)
        entity.folded.add(folded)
        entity.words.append(folded.split())
        self._by_fold.setdefault((entity.kind, folded), entity)
        if entity.kind == "empresa":
            body = _body(form)
            if body:
                self._by_body.setdefault(body, entity)

    def _find(self, kind: str, name: str) -> _Entity | None:
        found = self._by_fold.get((kind, fold_lexical(name)))
        if found is None and kind == "empresa":
            body = _body(name)
            found = self._by_body.get(body) if body else None
        return found

    def matches(self, kind: str, text: str) -> list[_Entity]:
        words = fold_lexical(text).split()
        if not words:
            return []
        return [
            entity
            for entity in self.entities
            if entity.kind == kind and any(_contains(form_words, words) for form_words in entity.words)
        ]

    def listed_name(self, text: str) -> bool:
        return fold_lexical(text) in self._listed

    def word_positions(self) -> tuple[set[str], set[str]]:
        """(palavras que abrem algum nome de pessoa conhecido, palavras do meio/fim) - para ``name_fragment``."""

        first: set[str] = set()
        inner: set[str] = set()
        for entity in self.entities:
            if entity.kind != "pessoa":
                continue
            for words in entity.words:
                if words:
                    first.add(words[0])
                    inner.update(word for word in words[1:] if word not in _PARTICLE_WORDS)
        return first, inner

    def entity_of(self, record: MapEntry) -> _Entity | None:
        kind = {"PERSON": "pessoa", "ORGANIZATION": "empresa"}.get(record.entity_type)
        if kind is None:
            return None
        for form in (record.alvo, *record.originals, record.preferred):
            if form and form.strip():
                found = self._find(kind, form)
                if found is not None:
                    return found
        return None


def _contains(haystack: list[str], needle: list[str]) -> bool:
    size = len(needle)
    return any(haystack[index : index + size] == needle for index in range(len(haystack) - size + 1))


def _in_list(values, text: str) -> bool:
    folded = fold_lexical(text)
    return any(fold_lexical(value) == folded for value in values)


def review(text: str, settings, data: LoteData) -> list[ReviewItem]:
    """Confere cada linha da resposta contra o lote e as listas atuais. Nao altera nada."""

    # H9-o: "esse nome ja e conhecido?" e conferido contra a lista da operacao escolhida na aba Caso (a mesma que a
    # rodada usa), nao contra a carteira inteira: nome de outra operacao entra (e ganha a operacao ao aplicar).
    active = settings_for_operation(settings)
    known = _Known(active, data)
    items: list[ReviewItem] = []
    for line in parse_review(text):
        if line.action == "TROCAR":
            item = _review_swap(line, active, data, known)
        elif line.action == "MANTER":
            item = _review_keep(line, active, data, known)
        elif line.action == "UNIR":
            item = _review_join(line, data, known)
        else:
            item = ReviewItem(line.number, line.raw, "?", line.raw, REJECTED, "fora do formato")
        _safety_lock(item)
        item.checked = (
            item.status == ACCEPTED and (item.action != "UNIR" or bool(item.evidence)) and item.checkable
        )
        items.append(item)
    return items


def _name_words(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", fold_lexical(value))


def generation_clash(left: str, right: str) -> bool:
    """H9-o: os dois nomes so diferem por sufixo de geracao (Filho, Junior/Jr, Neto, Sobrinho, Segundo), ou um e o
    outro mais o sufixo: pai e filho, nunca a mesma pessoa por conta do revisor."""

    words = (_name_words(left), _name_words(right))
    if not all(words) or words[0] == words[1]:
        return False
    bare = [[word for word in item if word not in _GENERATION] for item in words]
    return bool(bare[0]) and bare[0] == bare[1]


def _has_bracket(value: str) -> bool:
    return "[" in value or "]" in value


def _safety_lock(item: ReviewItem) -> None:
    """H9-o (trava): o que o Operador pode aceitar sem conferir. Nome com marca nunca e gravado; pai e filho nunca
    sao unidos (vai para "decidir", desmarcada, com o motivo)."""

    proposal = item.proposal
    if proposal is None or item.status == REJECTED:
        return
    if _has_bracket(proposal.value) or _has_bracket(proposal.principal):
        item.status, item.reason, item.proposal = REJECTED, "nome com marca entre colchetes: nunca é gravado", None
        item.name_field = False
        return
    if proposal.field_name == "target_aliases" and generation_clash(proposal.principal, proposal.value):
        item.status = DECIDE
        item.reason = "pai e filho? os nomes só diferem por Filho/Júnior/Neto/Sobrinho/Segundo: decidir"
        item.evidence = ""


def _review_swap(line: ParsedLine, settings, data: LoteData, known: _Known) -> ReviewItem:
    fields = line.fields
    if len(fields) < 2:
        return ReviewItem(line.number, line.raw, "TROCAR", "|".join(fields), REJECTED, "fora do formato")
    kind = fold_lexical(fields[0]).replace(" ", "")
    kind = _KIND_ALIASES.get(kind, kind)
    # TROCAR | tipo | texto | folha [| nome completo]. Com 5+ campos, o ultimo e o nome completo e o penultimo a folha
    # (o texto pode ter "|"); com 4, o quarto e o nome completo; com 3, sem nome; com 2, so tipo e texto.
    full_name = ""
    if len(fields) >= 4:
        full_name = fields[-1].strip()
        text = " | ".join(fields[1:-2])
    elif len(fields) == 3:
        text = fields[1]
    else:
        text = fields[1]
    text = " ".join(text.split()).strip("\"'“”")
    full_name = " ".join(full_name.split()).strip("\"'“”")
    item = ReviewItem(line.number, line.raw, "TROCAR", text, ACCEPTED)
    if kind not in _KINDS:
        item.status, item.reason = REJECTED, f"tipo desconhecido ({fields[0]})"
        return item
    if not text:
        item.status, item.reason = REJECTED, "fora do formato"
        return item
    if _TOKEN_ANYWHERE.search(text):
        item.status, item.reason = REJECTED, "já trocado" if _TOKEN_ANYWHERE.fullmatch(text) else "já trocado (contém marca)"
        return item
    if not data.texts:
        item.status, item.reason = DECIDE, "sem o texto exportado do lote para conferir"
        return item
    if not text_in_lote(text, data):
        item.status, item.reason = REJECTED, "texto inexistente"
        return item

    if kind in ("pessoa", "empresa"):
        item.kind = kind
        item.alias = text
        if full_name:
            return _swap_with_name(item, kind, text, full_name, settings, data, known)
        return _swap_name(item, kind, text, settings, known)
    field_name = _KINDS[kind]
    value = text
    if kind in ("documento", "outro", "endereco") and looks_like_date(text):
        # H9-o: data nunca vira endereco (nem contato). Datas ficam em claro; a de nascimento rotulada ("nascido em",
        # "data de nascimento", "DN") o motor ja troca como [NASCIMENTO_n].
        item.status = REJECTED
        item.reason = "data não é endereço (datas ficam em claro; nascimento rotulado vira [NASCIMENTO_n] pelo motor)"
        return item
    if kind in ("documento", "outro", "endereco"):
        ids = list(parse_target_ids([text]))
        compact = re.sub(r"[^A-Z0-9]", "", text.upper())
        if ids and len(re.sub(r"\D", "", text)) in (11, 14):
            field_name, value = "target_ids", ids[0]
        elif _PLATE.match(compact):
            value = f"placa: {compact}"
        elif classify_contact(text) is None:
            item.status, item.reason = DECIDE, "formato que a caixa Contatos não reconhece (ponha um rótulo)"
            item.proposal = Proposal(field_name, value)
            return item
    elif kind == "processo" and classify_process(text) is None:
        item.status, item.reason = DECIDE, "não parece número de processo"
        item.proposal = Proposal(field_name, value)
        return item
    elif kind == "cda" and classify_cda(text) is None:
        item.status, item.reason = DECIDE, "não parece número de CDA"
        item.proposal = Proposal(field_name, value)
        return item
    current = getattr(settings, field_name)
    if field_name == "target_ids" and value in set(parse_target_ids(current)):
        item.status, item.reason = REJECTED, "já está na lista do caso"
        return item
    if field_name != "target_ids" and _in_list(current, value):
        item.status, item.reason = REJECTED, "já está na lista do caso"
        return item
    item.proposal = Proposal(field_name, value)
    return item


# H9-n: pedaco de nome nunca vira entrada nova de Pessoas/Empresas. Heuristica simples (na duvida, "decidir"):
# - uma palavra so;
# - so iniciais ("R. C.", "R C"), ou iniciais na frente sem prenome ("R. C. Valadares");
# - comeca por particula ("da Silva Souza", "dos Santos");
# - a primeira palavra e sobrenome brasileiro frequente (lista curta abaixo), ou so aparece como palavra do MEIO ou do
#   FIM nos nomes ja conhecidos (lista do caso e mapa do lote) e nunca como a primeira: sobrenome sem prenome.
_PARTICLE_WORDS = frozenset({"da", "de", "do", "das", "dos", "e", "d"})
_COMMON_SURNAMES = frozenset(
    fold_lexical(word)
    for word in (
        "Silva Santos Souza Sousa Oliveira Pereira Lima Costa Ferreira Rodrigues Almeida Nascimento Carvalho Gomes "
        "Martins Araújo Ribeiro Alves Barbosa Rocha Dias Moreira Mendes Cardoso Teixeira Correia Pinto Moura Ramos "
        "Nunes Lopes Freitas Machado Vieira Monteiro Andrade Batista Castro Fernandes Gonçalves Melo Mello Barros "
        "Azevedo Reis Fonseca Rezende Resende Matos Mattos Cavalcanti Cavalcante Medeiros Siqueira Farias Pires "
        "Campos Borges Coelho Duarte Miranda Moraes Morais Nogueira Peixoto Queiroz Sampaio Tavares Xavier Brito "
        "Bezerra Cunha Leite Macedo Magalhães Marques Pacheco Prado Rangel Sales Salles Soares Valadares Viana"
    ).split()
)
_INITIAL = re.compile(r"^[^\W\d_]{1,2}\.?$")


def name_fragment(text: str, known_first: set[str] | None = None, known_inner: set[str] | None = None) -> str:
    """Motivo se ``text`` e so pedaco de nome de pessoa (vazio se parece nome com prenome)."""

    words = [word for word in re.split(r"\s+", text.strip()) if word]
    if len(words) <= 1:
        return "palavra única"
    if all(_INITIAL.match(word) for word in words):
        return "só iniciais"
    if _INITIAL.match(words[0]):
        return "iniciais sem prenome"
    first = fold_lexical(words[0])
    if first in _PARTICLE_WORDS:
        return "sobrenome sem prenome (começa por partícula)"
    if first in _COMMON_SURNAMES:
        return "sobrenome sem prenome"
    if known_inner and first in known_inner and first not in (known_first or set()):
        return "sobrenome sem prenome"
    return ""


def _swap_name(item: ReviewItem, kind: str, text: str, settings, known: _Known) -> ReviewItem:
    field_name = _KINDS[kind]
    single = len(fold_lexical(text).split()) == 1
    if known.listed_name(text):
        item.status, item.reason = REJECTED, "já está na lista do caso"
        return item
    found = known.matches(kind, text)
    if len(found) > 1:
        item.status, item.reason = DECIDE, f"ambíguo, decidir: casa com {len(found)} nomes"
        item.candidates = tuple(entity.principal for entity in found)
        item.name_field = True
        return item
    if len(found) == 1:
        entity = found[0]
        if fold_lexical(text) == fold_lexical(entity.principal):
            # O proprio nome de um token do mapa, fora da lista: entra como nome novo (nao como grafia de si mesmo).
            fragment = name_fragment(text, *known.word_positions()) if kind == "pessoa" else (
                "palavra única" if single else ""
            )
            if fragment:
                item.status, item.reason, item.name_field = DECIDE, f"pedaço de nome ({fragment}): decidir", True
                return item
            item.proposal = Proposal(field_name, text)
            item.reason = "nome do mapa do lote, fora da lista"
            return item
        item.proposal = Proposal("target_aliases", text, entity.principal)
        item.reason = "grafia de nome já conhecido"
        return item
    fragment = name_fragment(text, *known.word_positions()) if kind == "pessoa" else ("palavra única" if single else "")
    if fragment:
        item.status, item.reason, item.name_field = DECIDE, f"pedaço de nome ({fragment}), sem nome principal: decidir", True
        return item
    item.proposal = Proposal(field_name, text)
    return item


def _swap_with_name(item: ReviewItem, kind: str, text: str, full_name: str, settings, data: LoteData, known: _Known):
    """H9-n: o revisor deu o nome completo (5o campo): texto ou marca do mapa."""

    field_name = _KINDS[kind]
    mark = _mark(full_name) if full_name.lstrip().startswith("[") or _MARK_ONLY.match(full_name) else ""
    if not mark and _has_bracket(full_name):
        # H9-o: o 5o campo TRAZ uma marca ("Joana [PESSOA_1]"): vale a marca, traduzida pelo mapa para o nome real.
        # O texto em volta e a marca nunca viram nome gravado.
        marks = list(dict.fromkeys(filter(None, (_mark(found) for found in _BRACKETED.findall(full_name)))))
        if len(marks) != 1:
            item.status, item.name_field = DECIDE, True
            item.reason = "5º campo com colchetes sem uma marca só do mapa: decidir (digite o nome completo)"
            return item
        mark = marks[0]
    if mark:
        record = data.records.get(mark)
        if record is None:
            item.status, item.reason, item.name_field = DECIDE, f"marca do nome completo {mark} não está no mapa", True
            return item
        entity = known.entity_of(record)
        principal = entity.principal if entity is not None and entity.listed else record.name
        field_name = {"PERSON": "target_people", "ORGANIZATION": "target_companies"}.get(record.entity_type, field_name)
    else:
        found = [entity for entity in known.matches(kind, full_name) if fold_lexical(full_name) in entity.folded]
        listed = [entity for entity in found if entity.listed]
        if listed:
            principal = listed[0].principal
        elif found or text_in_lote(full_name, data):
            principal = found[0].principal if found else full_name
        else:
            item.status, item.reason = DECIDE, "nome completo sugerido não aparece no lote nem na lista: decidir"
            item.name_field, item.suggested, item.typed = True, full_name, full_name
            return item
    if fold_lexical(principal) == fold_lexical(text):
        if known.listed_name(text):
            item.status, item.reason = REJECTED, "já está na lista do caso"
            return item
        item.proposal = Proposal(field_name, principal)
        item.reason = "nome completo dado pelo revisor"
        return item
    if known.listed_name(text) and any(
        fold_lexical(text) in entity.listed_forms and fold_lexical(principal) in entity.folded
        for entity in known.entities
    ):
        item.status, item.reason = REJECTED, "já está na lista do caso"
        return item
    item.proposal = Proposal("target_aliases", text, principal, principal_field=field_name)
    item.reason = "grafia do nome completo dado pelo revisor"
    return item


_MARK_ONLY = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9_]*_\d+(?:·G\d+)?$")


def _mark(value: str) -> str:
    match = _MARK.match(value.strip())
    return f"[{match.group(1)}]" if match else ""


def _review_keep(line: ParsedLine, settings, data: LoteData, known: _Known) -> ReviewItem:
    mark = _mark(line.fields[0]) if line.fields else ""
    item = ReviewItem(line.number, line.raw, "MANTER", mark or (line.fields[0] if line.fields else ""), ACCEPTED)
    if not mark:
        item.status, item.reason = REJECTED, "fora do formato"
        return item
    record = data.records.get(mark)
    if record is None:
        item.status, item.reason = REJECTED, "marca não está no mapa do lote"
        return item
    name = record.name
    item.subject = f"{mark} = {name}"
    # H9-o (trava): escritorio de advocacia nunca e orgao publico; o nome com OAB ao lado e de advogado(a).
    forms = [value for value in (name, *record.originals) if value.strip()]
    if not any(_BAR_ORDER.search(value) for value in forms):
        if any(_LAW_FIRM.search(value) for value in forms):
            item.status, item.reason = REJECTED, "escritório de advocacia não é órgão público (MANTER recusado)"
            return item
        if _oab_near(mark, data):
            item.status, item.reason = DECIDE, "inscrição na OAB ao lado da marca: advogado(a) não é órgão público; decidir"
            item.proposal = Proposal("exclusions", name)
            return item
    if _in_list(settings.exclusions, name):
        item.status, item.reason = REJECTED, "já está nos termos protegidos"
        return item
    item.proposal = Proposal("exclusions", name)
    listed = known.entity_of(record)
    if record.alvo or (listed is not None and listed.listed) or any(known.listed_name(v) for v in record.originals):
        item.status, item.reason = DECIDE, "conflito com a lista do caso"
        return item
    if len(fold_lexical(name).split()) < 2:
        item.status, item.reason = DECIDE, "palavra única: decidir"
        return item
    return item


def _mark_windows(mark: str, data: LoteData, before: int, after: int):
    """Trechos do texto exportado em volta de cada ocorrencia da marca (sem o sufixo de grupo), na mesma linha."""

    for text in data.texts:
        plain = strip_groups(text)
        start = plain.find(mark)
        while start >= 0:
            end = start + len(mark)
            line_start = plain.rfind("\n", 0, start) + 1
            line_end = plain.find("\n", end)
            line_end = len(plain) if line_end < 0 else line_end
            yield plain[max(line_start, start - before) : start], plain[end : min(line_end, end + after)]
            start = plain.find(mark, end)


def _oab_near(mark: str, data: LoteData) -> bool:
    """H9-o: "[PESSOA_3], OAB/SC [OAB_1]" - inscricao na OAB logo depois (ou logo antes) da marca."""

    return any(_OAB_NEAR.search(after) or _OAB_NEAR.search(before) for before, after in _mark_windows(mark, data, 30, 60))


def _same_id_evidence(marks: list[str], reason: str, data: LoteData) -> str:
    """H9-o: o CPF/CNPJ citado no motivo do UNIR aparece ao lado das DUAS marcas no texto exportado."""

    for token in dict.fromkeys(_ID_MARK.findall(reason)):
        if all(
            any(token in after or token in before for before, after in _mark_windows(mark, data, _ID_REACH, _ID_REACH))
            for mark in marks
        ):
            return token
    return ""


def _review_join(line: ParsedLine, data: LoteData, known: _Known) -> ReviewItem:
    marks = [_mark(value) for value in line.fields[:2]] if len(line.fields) >= 2 else []
    subject = " + ".join(marks) if marks and all(marks) else " | ".join(line.fields[:2])
    item = ReviewItem(line.number, line.raw, "UNIR", subject, ACCEPTED, "confirmar (UNIR só com marcação)")
    if len(marks) < 2 or not all(marks):
        item.status, item.reason = REJECTED, "fora do formato"
        return item
    if marks[0] == marks[1]:
        item.status, item.reason = REJECTED, "a mesma marca duas vezes"
        return item
    records = [data.records.get(mark) for mark in marks]
    if any(record is None for record in records):
        item.status, item.reason = REJECTED, "marca não está no mapa do lote"
        return item
    first, second = records  # type: ignore[misc]
    if {first.entity_type, second.entity_type} - {"PERSON", "ORGANIZATION"}:
        item.status, item.reason = REJECTED, "UNIR só vale para pessoa ou empresa"
        return item
    item.subject = f"{marks[0]} = {first.name} + {marks[1]} = {second.name}"
    entities = [known.entity_of(first), known.entity_of(second)]
    listed = [entity is not None and entity.listed for entity in entities]
    names = [
        entities[index].principal if listed[index] else record.name  # type: ignore[union-attr]
        for index, record in enumerate((first, second))
    ]
    if first.entity_type != second.entity_type:
        item.status, item.reason = DECIDE, "tipos diferentes (pessoa e empresa): decidir"
    if all(listed):
        item.status = DECIDE
        item.reason = "as duas estão na lista do caso (o app não funde duas entradas da lista)"
    if listed[1] and not listed[0]:
        order = (1, 0)
    elif listed[0]:
        order = (0, 1)
    else:
        counts = (data.occurrences(marks[0]), data.occurrences(marks[1]))
        order = (1, 0) if counts[1] > counts[0] else (0, 1)
    principal, alias = names[order[0]], names[order[1]]
    if fold_lexical(principal) == fold_lexical(alias):
        item.status, item.reason = REJECTED, "as duas marcas têm o mesmo original"
        return item
    item.proposal = Proposal("target_aliases", alias, principal)
    if item.status == ACCEPTED and first.entity_type == second.entity_type:
        # H9-o: UNIR so sai marcada quando o motivo cita o mesmo CPF/CNPJ e ele esta ao lado das duas marcas no texto.
        item.evidence = _same_id_evidence(marks, " ".join(line.fields[2:]), data)
        if item.evidence:
            item.reason = f"mesmo {item.evidence} ao lado das duas marcas"
    return item


def summary_counts(items: list[ReviewItem]) -> dict[str, int]:
    counts = Counter(item.status for item in items)
    return {"linhas": len(items), ACCEPTED: counts[ACCEPTED], REJECTED: counts[REJECTED], DECIDE: counts[DECIDE]}


# ---------------------------------------------------------------------------------------------------- aplicar


@dataclass(slots=True)
class ApplyResult:
    applied: list[ReviewItem] = field(default_factory=list)
    added: Counter = field(default_factory=Counter)  # campo -> quantos itens novos
    skipped: int = 0  # marcadas sem efeito (decidir sem escolha, repetidas)
    operation: str = ""  # H9-o: a operacao (rotulo) em que as linhas aplicadas valem; vazio = todas

    def by_action(self) -> Counter:
        return Counter(item.action for item in self.applied)

    def message(self) -> str:
        if not self.applied:
            return "Nada aplicado."
        parts = [f"{count} em {FIELD_LABELS.get(name, name)}" for name, count in self.added.items() if count]
        actions = self.by_action()
        text = (
            f"Aplicadas {len(self.applied)} linha(s) (TROCAR {actions['TROCAR']}, MANTER {actions['MANTER']}, "
            f"UNIR {actions['UNIR']}): " + (", ".join(parts) if parts else "nada novo nas listas")
        )
        if self.skipped:
            text += f"; {self.skipped} sem efeito"
        if self.operation:
            text += f"; valem na operação {self.operation}"
        return text + "."


def merge_aliases(
    existing: list[str],
    incoming: list[tuple[str, str]],
    *,
    operations: dict[str, list[str]] | None = None,
    operation: str = "",
) -> list[str]:
    """Acrescenta (principal, grafia) a "Outras grafias" sem repetir, numa linha por nome principal
    ("principal = g1; g2"). Linha sem "=" fica como esta; duas linhas do mesmo principal viram uma.

    H9-o: com ``operations`` (``settings.case_operations``), cada linha guarda a sua operacao: so se juntam linhas do
    mesmo principal com as MESMAS operacoes (a linha sem operacao vale em todas), a grafia nova vai para a linha da
    ``operation`` escolhida (ou para uma linha nova dela) e a chave da operacao acompanha o texto novo da linha.
    """

    scoped = operations is not None
    ops = operations if operations is not None else {}
    order: list[tuple[str, object]] = []  # ("linha", texto cru) ou ("nome", (principal dobrado, escopo))
    groups: dict[tuple[str, tuple[str, ...] | None], tuple[str, list[str]]] = {}
    old_keys: set[str] = set()

    def scope_of(line: str) -> tuple[str, ...] | None:
        if not scoped:
            return None
        listed = ops.get(case_item_key("target_aliases", line))
        return tuple(sorted(listed)) if listed else None

    def bucket(principal: str, scope: tuple[str, ...] | None) -> list[str]:
        principal = " ".join(principal.split())
        key = (fold_lexical(principal), scope)
        if key not in groups:
            groups[key] = (principal, [])
            order.append(("nome", key))
        return groups[key][1]

    def add(principal: str, alias: str, scope: tuple[str, ...] | None) -> None:
        values = bucket(principal, scope)
        folded = {fold_lexical(item) for item in values} | {fold_lexical(principal)}
        alias = " ".join(alias.split())
        if alias and fold_lexical(alias) not in folded:
            values.append(alias)

    def covered(principal: str, alias: str, scope: tuple[str, ...] | None) -> bool:
        """A grafia ja vale onde a nova valeria: na linha sem operacao ou numa linha que tem a operacao."""

        wanted = fold_lexical(alias)
        name = fold_lexical(" ".join(principal.split()))
        for (key, other), (_principal, values) in groups.items():
            if key != name or wanted not in {fold_lexical(item) for item in values}:
                continue
            if other is None or other == scope or (scope is not None and scope[0] in other):
                return True
        return False

    for raw in existing:
        line = str(raw or "").strip()
        if not line:
            continue
        parsed = parse_alias_lines([line])
        if not parsed:
            order.append(("linha", line))
            continue
        scope = scope_of(line)
        if scope is not None:
            old_keys.add(case_item_key("target_aliases", line))
        principal, aliases = parsed[0]
        bucket(principal, scope)
        for alias in aliases:
            add(principal, alias, scope)
    target = (operation,) if scoped and operation else None
    for principal, alias in incoming:
        if scoped and covered(principal, alias, target):
            continue
        add(principal, alias, target)
    lines: list[str] = []
    new_keys: dict[str, tuple[str, ...]] = {}
    for kind, value in order:
        if kind == "linha":
            lines.append(value)  # type: ignore[arg-type]
            continue
        principal, aliases = groups[value]  # type: ignore[index]
        if aliases:
            line = format_alias_line(principal, aliases)
            lines.append(line)
            scope = value[1]  # type: ignore[index]
            if scope is not None:
                new_keys[case_item_key("target_aliases", line)] = scope
    if scoped:
        for key in old_keys - set(new_keys):
            ops.pop(key, None)
        for key, scope in new_keys.items():
            known = ops.setdefault(key, [])
            known.extend(item for item in scope if item not in known)
    return lines


def _tag_operation(settings, field_name: str, values: list[str], before: list[str], operation: str) -> None:
    """H9-o: o que a revisao pos numa lista vale na operacao escolhida. Item novo: so nela; item que ja tinha outras
    operacoes: ganha esta; item sem operacao (vale em todas): fica como esta."""

    if not operation:
        return
    if field_name == "target_ids":
        before_keys = {case_item_key(field_name, value) for value in before}
        keys = [case_item_key(field_name, value) for value in parse_target_ids(values)]
    else:
        before_keys = {case_item_key(field_name, value) for value in before}
        keys = [case_item_key(field_name, value) for value in values]
    for key in dict.fromkeys(keys):
        listed = settings.case_operations.get(key)
        if key in before_keys and listed is None:
            continue  # ja valia em todas as operacoes
        known = settings.case_operations.setdefault(key, [])
        if operation not in known:
            known.append(operation)


def apply_review(settings, items: list[ReviewItem]) -> ApplyResult:
    """Acrescenta as linhas marcadas as listas de ``settings`` (mesclando, sem duplicar). Nao grava."""

    result = ApplyResult()
    # H9-o: as linhas aplicadas valem na operacao escolhida na aba Caso (sem operacao escolhida: em todas).
    operation = str(getattr(settings, "case_operation", "") or "").strip()
    if operation:
        result.operation = operation_label(settings, operation)
    incoming: dict[str, list[str]] = {}
    aliases: list[tuple[str, str]] = []
    for item in items:
        if not item.checked or not item.checkable:
            continue
        proposal = item.effective()
        if proposal is None or _has_bracket(proposal.value) or _has_bracket(proposal.principal):
            result.skipped += 1  # H9-o: nome com marca entre colchetes nunca e gravado
            continue
        result.applied.append(item)
        if proposal.field_name == "target_aliases":
            aliases.append((proposal.principal, proposal.value))
            if proposal.principal_field:
                # H9-n: o nome principal digitado (ou dado pelo revisor) entra na lista se ainda nao estiver.
                incoming.setdefault(proposal.principal_field, []).append(proposal.principal)
        else:
            incoming.setdefault(proposal.field_name, []).append(proposal.value)
    for field_name, values in incoming.items():
        before = getattr(settings, field_name)
        if field_name == "target_ids":
            after = list(parse_target_ids(list(before) + values))
        else:
            after = merge_unique(list(before), values)
        result.added[field_name] += max(0, len(after) - len(before))
        setattr(settings, field_name, after)
        if field_name != "exclusions":
            _tag_operation(settings, field_name, values, list(before), operation)
    if aliases:
        before_pairs = _alias_pairs(settings.target_aliases)
        settings.target_aliases = merge_aliases(
            settings.target_aliases, aliases, operations=settings.case_operations, operation=operation
        )
        result.added["target_aliases"] += len(_alias_pairs(settings.target_aliases) - before_pairs)
    return result


def _alias_pairs(lines: list[str]) -> set[tuple[str, str]]:
    return {
        (fold_lexical(principal), fold_lexical(alias))
        for principal, values in parse_alias_lines(lines)
        for alias in values
    }


# --------------------------------------------------------------------------------------------------- registro


def write_review_record(
    private_root: Path,
    lote_id: str,
    pasted: str,
    items: list[ReviewItem],
    result: ApplyResult,
    now: datetime | None = None,
) -> Path:
    """``<pasta privada>/revisoes/revisao_<lote>_<AAAAMMDD_HHMMSS>.txt``: a resposta colada e o que foi aplicado.
    PASTA PRIVADA: pode ter nomes reais."""

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    folder = Path(private_root) / REVIEWS_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"revisao_{lote_id}_{stamp}.txt"
    index = 2
    while path.exists():
        path = folder / f"revisao_{lote_id}_{stamp}_{index}.txt"
        index += 1
    actions = result.by_action()
    applied = {id(item) for item in result.applied}
    lines = [
        "REVISAO EXTERNA APLICADA (H9-m) - PASTA PRIVADA, NAO ENVIAR",
        f"Lote de referencia: {lote_id}",
        f"Gravado em: {stamp}",
        f"Aplicadas: {len(result.applied)} (TROCAR {actions['TROCAR']}, MANTER {actions['MANTER']}, "
        f"UNIR {actions['UNIR']})",
        "",
        "## Conferencia, linha a linha",
    ]
    for item in items:
        mark = "APLICADA" if id(item) in applied else "nao aplicada"
        reason = f" ({item.reason})" if item.reason else ""
        target = item.effective().describe() if item.effective() is not None else item.destination()
        lines.append(f"- linha {item.number}: {item.action} | {item.subject} -> {target} | {item.status}{reason} | {mark}")
    lines += ["", "## Resposta colada", str(pasted or "").rstrip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def review_audit_lines(lotes_root: Path, job_id: str) -> list[str]:
    """Linha da AUDITORIA (so contagens): revisoes aplicadas depois do lote anterior e antes deste."""

    lotes_root = Path(lotes_root)
    current = _STAMP.search(job_id)
    if current is None:
        return []
    now = current.group(1)
    previous = ""
    try:
        for child in lotes_root.iterdir():
            match = _STAMP.search(child.name)
            if (
                child.is_dir()
                and child.name.startswith("lote_")
                and child.name != job_id
                and match is not None
                and match.group(1) < now
                and (child / "mapa.json").is_file()
            ):
                previous = max(previous, match.group(1))
    except OSError:
        return []
    folders = [lotes_root / REVIEWS_DIRNAME]
    if lotes_root.name == "privados":
        folders.append(lotes_root.parent / REVIEWS_DIRNAME)
    totals = [0, 0, 0, 0]
    seen: set[Path] = set()
    for folder in folders:
        try:
            files = sorted(folder.glob("revisao_*.txt"))
        except OSError:
            continue
        for path in files:
            if path in seen:
                continue
            seen.add(path)
            stamps = _STAMP.findall(path.name)
            if not stamps:
                continue
            stamp = stamps[-1]
            if not (previous < stamp <= now):
                continue
            try:
                match = _APPLIED_LINE.search(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if match is None:
                continue
            for index in range(4):
                totals[index] += int(match.group(index + 1))
    if not totals[0]:
        return []
    return [
        f"Revisão externa aplicada antes desta rodada: {totals[0]} linhas "
        f"(TROCAR {totals[1]}, MANTER {totals[2]}, UNIR {totals[3]})"
    ]
