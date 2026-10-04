"""Elenco do caso: quem o LLM encontrou, com papel e decisao.

O LLM classifica (parte, socio, advogado, juiz, orgao...); o codigo decide
pela tabela de papeis e faz a troca. Uma entrada por entidade, nao por
grafia: CAIXA ALTA, sem acento, erro de OCR e dominio da empresa viram o
mesmo token quando o LLM aponta o mesmo ``nome_canonico``.

O elenco guarda os originais: e privado, como o ``mapa.json``. A legenda
exportavel (token -> tipo -> papel) nao leva nome.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from anonimizador_juridico.pseudonymizer import fold_lexical

TOKENIZE = "tokenizar"
KEEP = "manter"

CLASS_TO_ENTITY = {
    "pessoa": "PERSON",
    "empresa": "ORGANIZATION",
    "endereco": "LOCATION",
}

# Papel -> acao padrao. Agente publico e autor citado ficam (item H da
# governanca); parte, socio, advogado da parte e terceiro privado saem.
ROLE_POLICY: dict[str, str] = {
    "parte": TOKENIZE,
    "socio": TOKENIZE,
    "advogado": TOKENIZE,
    "terceiro": TOKENIZE,
    "magistrado": KEEP,
    "servidor_publico": KEEP,
    "procurador_publico": KEEP,
    "orgao_publico": KEEP,
    "autor_citado": KEEP,
}
UNKNOWN_ROLE = "desconhecido"

_KIND_ALIASES = {
    "pessoa": "pessoa",
    "pessoa_fisica": "pessoa",
    "nome": "pessoa",
    "person": "pessoa",
    "empresa": "empresa",
    "pessoa_juridica": "empresa",
    "organizacao": "empresa",
    "razao_social": "empresa",
    "organization": "empresa",
    "endereco": "endereco",
    "logradouro": "endereco",
    "location": "endereco",
    "address": "endereco",
}

_ROLE_ALIASES = {
    "parte": "parte",
    "executado": "parte",
    "executada": "parte",
    "reu": "parte",
    "re": "parte",
    "autor": "parte",
    "autora": "parte",
    "embargante": "parte",
    "devedor": "parte",
    "devedora": "parte",
    "contribuinte": "parte",
    "requerido": "parte",
    "requerida": "parte",
    "socio": "socio",
    "socia": "socio",
    "socio_administrador": "socio",
    "socia_administradora": "socio",
    "administrador": "socio",
    "administradora": "socio",
    "representante_legal": "socio",
    "responsavel": "socio",
    "advogado": "advogado",
    "advogada": "advogado",
    "terceiro": "terceiro",
    "terceira": "terceiro",
    "adquirente": "terceiro",
    "conjuge": "terceiro",
    "testemunha": "terceiro",
    "contador": "terceiro",
    "perito": "terceiro",
    "magistrado": "magistrado",
    "magistrada": "magistrado",
    "juiz": "magistrado",
    "juiza": "magistrado",
    "desembargador": "magistrado",
    "desembargadora": "magistrado",
    "ministro": "magistrado",
    "ministra": "magistrado",
    "relator": "magistrado",
    "relatora": "magistrado",
    "servidor_publico": "servidor_publico",
    "servidor": "servidor_publico",
    "servidora": "servidor_publico",
    "oficial_de_justica": "servidor_publico",
    "diretor_de_secretaria": "servidor_publico",
    "procurador_publico": "procurador_publico",
    "procurador_da_fazenda_nacional": "procurador_publico",
    "procuradora_da_fazenda_nacional": "procurador_publico",
    "orgao_publico": "orgao_publico",
    "ente_publico": "orgao_publico",
    "empresa_publica": "orgao_publico",
    "autor_citado": "autor_citado",
    "doutrinador": "autor_citado",
    "doutrina": "autor_citado",
}

_TOKEN_MARK = re.compile(r"\[[A-Z0-9_]+_\d+(?:·G\d+)?\]")
_ID_LABEL_TAIL = re.compile(
    r"(?i)(?:[\s,;:(\-–—]+(?:cep|cpf|cnpj|oab|rg|n[º°o.]?|inscrit[oa]\s+no))?"
    r"[\s,;:(\-–—]*$"
)
_COMPANY_TAIL = re.compile(
    r"(?:[\s,.\-–—]+(?:ltda|me|epp|eireli|s/?a|s\.a|sa|cia|companhia)\.?)+$"
)
_PERSON_TITLE = re.compile(r"^(?:dr|dra|sr|sra|srta|exmo|exma)\.?\s+")


@dataclass(frozen=True, slots=True)
class Mention:
    """Uma mencao devolvida pelo LLM, no texto que ele leu.

    ``start``/``end`` sao o trecho ancorado (-1 se o LLM nao ancorou).
    ``source_text`` e o que de fato esta no texto nessa posicao; difere de
    ``text`` quando o alinhamento foi aproximado (OCR).
    """

    kind: str
    text: str
    role: str = ""
    canonical: str = ""
    start: int = -1
    end: int = -1
    source_text: str = ""

    @property
    def anchored(self) -> bool:
        return 0 <= self.start < self.end


@dataclass(slots=True)
class RosterEntry:
    key: str
    kind: str
    name: str
    roles: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    action: str = TOKENIZE
    source: str = "llm"
    pinned: bool = False

    @property
    def entity_type(self) -> str:
        return CLASS_TO_ENTITY[self.kind]

    def to_dict(self) -> dict:
        return {
            "chave": self.key,
            "tipo": self.kind,
            "nome": self.name,
            "papeis": list(self.roles),
            "variantes": list(self.variants),
            "acao": self.action,
            "origem": self.source,
            "fixo": self.pinned,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "RosterEntry":
        kind = normalize_kind(str(payload.get("tipo") or ""))
        if not kind:
            raise ValueError(f"Tipo invalido no elenco: {payload.get('tipo')!r}")
        name = str(payload.get("nome") or "").strip()
        action = str(payload.get("acao") or TOKENIZE)
        if action not in (TOKENIZE, KEEP):
            raise ValueError(f"Acao invalida no elenco: {action!r}")
        roles = [normalize_role(str(item)) for item in payload.get("papeis") or []]
        # Acao diferente da que a tabela de papeis daria = decisao do usuario.
        pinned = bool(payload.get("fixo", False)) or action != policy_action(roles)
        return cls(
            key=str(payload.get("chave") or entity_key(kind, name)),
            kind=kind,
            name=name,
            roles=roles,
            variants=[str(item) for item in payload.get("variantes") or [] if str(item).strip()],
            action=action,
            source=str(payload.get("origem") or "usuario"),
            pinned=pinned,
        )


def normalize_kind(raw: str) -> str:
    return _KIND_ALIASES.get(_slug(raw), "")


def normalize_role(raw: str) -> str:
    slug = _slug(raw)
    if slug in _ROLE_ALIASES:
        return _ROLE_ALIASES[slug]
    for prefix, role in (
        ("socio", "socio"),
        ("advogad", "advogado"),
        ("procurador_da_fazenda", "procurador_publico"),
        ("juiz", "magistrado"),
        ("desembargador", "magistrado"),
        ("ministr", "magistrado"),
        ("servidor", "servidor_publico"),
    ):
        if slug.startswith(prefix):
            return role
    return UNKNOWN_ROLE


def policy_action(roles: Iterable[str]) -> str:
    """Um papel que tokeniza basta: na duvida entre dois papeis, protege a pessoa."""

    actions = [ROLE_POLICY.get(role, TOKENIZE) for role in roles]
    if not actions:
        return TOKENIZE
    return TOKENIZE if TOKENIZE in actions else KEEP


def entity_core(kind: str, value: str) -> str:
    """Forma dobrada que identifica a entidade (sem sufixo societario / titulo)."""

    folded = fold_lexical(value)
    if kind == "empresa":
        folded = _COMPANY_TAIL.sub("", folded).strip(" ,.-")
    elif kind == "pessoa":
        folded = _PERSON_TITLE.sub("", folded)
    return folded


def entity_key(kind: str, name: str) -> str:
    return f"{kind}:{entity_core(kind, name)}"


def has_token(value: str) -> bool:
    return bool(_TOKEN_MARK.search(value))


def strip_token_tail(value: str) -> str:
    """Corta a mencao no primeiro token ja aplicado ("Rua X, 120, CEP [CEP_1]").

    O LLM le o texto com os IDs mascarados; o que vem depois do token e do
    rotulo dele nao e parte do nome. Mencao que comeca por token e descartada.
    """

    match = _TOKEN_MARK.search(value)
    cut = value if match is None else value[: match.start()]
    cut = _ID_LABEL_TAIL.sub("", cut)
    return cut.strip().strip(",;:(-–—").strip()


class Roster:
    """Elenco acumulado no lote. Decisao do usuario (``fixo``) nao e sobrescrita."""

    def __init__(self, entries: Iterable[RosterEntry] = ()) -> None:
        self._entries: dict[str, RosterEntry] = {}
        self._by_variant: dict[tuple[str, str], str] = {}
        for entry in entries:
            self._put(entry)

    def _put(self, entry: RosterEntry) -> None:
        self._entries[entry.key] = entry
        self._index_variant(entry, entry.name)
        for variant in entry.variants:
            self._index_variant(entry, variant)

    def _index_variant(self, entry: RosterEntry, value: str) -> None:
        core = entity_core(entry.kind, value)
        if core:
            self._by_variant.setdefault((entry.kind, core), entry.key)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def get(self, key: str) -> RosterEntry | None:
        return self._entries.get(key)

    @property
    def entries(self) -> tuple[RosterEntry, ...]:
        return tuple(self._entries.values())

    def lookup(self, kind: str, text: str) -> RosterEntry | None:
        """A entrada do elenco a que uma grafia pertence (por nome ou variante), ou ``None``."""

        key = self._by_variant.get((kind, entity_core(kind, text)))
        return self._entries.get(key) if key is not None else None

    def to_tokenize(self) -> tuple[RosterEntry, ...]:
        return tuple(item for item in self._entries.values() if item.action == TOKENIZE)

    def add_mention(self, mention: Mention, *, source: str = "llm") -> RosterEntry | None:
        kind = normalize_kind(mention.kind)
        text = strip_token_tail(mention.text)
        if not kind or not text:
            return None
        if not entity_core(kind, text):
            return None
        canonical = mention.canonical.strip()
        if not canonical or has_token(canonical):
            canonical = text
        role = normalize_role(mention.role) if mention.role else UNKNOWN_ROLE
        entry = self._find(kind, canonical, text)
        if entry is None:
            entry = RosterEntry(
                key=entity_key(kind, canonical),
                kind=kind,
                name=canonical,
                source=source,
            )
            self._put(entry)
        self._absorb(entry, text, role)
        grounded = mention.source_text.strip().strip(",;:")
        if grounded and grounded != text and _similar_length(grounded, text):
            self._absorb(entry, grounded, "")
        return entry

    def add_listed(self, kind: str, name: str) -> RosterEntry | None:
        """Nome que o usuario listou (lista de alvos): sempre tokeniza."""

        mention = Mention(kind=kind, text=name, role="parte", canonical=name)
        entry = self.add_mention(mention, source="lista")
        if entry is not None and not entry.pinned:
            entry.action = TOKENIZE
        return entry

    def _find(self, kind: str, canonical: str, text: str) -> RosterEntry | None:
        for value in (canonical, text):
            key = self._by_variant.get((kind, entity_core(kind, value)))
            if key is not None:
                return self._entries[key]
        return self._entries.get(entity_key(kind, canonical))

    def _absorb(self, entry: RosterEntry, variant: str, role: str) -> None:
        if variant and variant not in entry.variants:
            entry.variants.append(variant)
            self._index_variant(entry, variant)
        if role and role not in entry.roles:
            entry.roles.append(role)
        if not entry.pinned:
            entry.action = policy_action(entry.roles)

    def to_dict(self, annotations: Mapping[str, Mapping] | None = None) -> dict:
        """``annotations``: por chave, campos so de leitura (token, ocorrencias) para revisar a mao."""

        entries = []
        for entry in self._entries.values():
            item = entry.to_dict()
            item.update(annotations.get(entry.key, {}) if annotations else {})
            entries.append(item)
        return {
            "aviso": "CONTEUDO SENSIVEL: NAO ENVIAR A SERVICOS EXTERNOS",
            "como_revisar": (
                "Troque 'acao' para 'manter' ou 'tokenizar' e marque 'fixo': true. "
                "Rode de novo com --elenco apontando para este arquivo "
                "(com --sem-rede, so o cache e usado)."
            ),
            "entradas": entries,
        }

    def dump(self, path: str | Path, annotations: Mapping[str, Mapping] | None = None) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(annotations), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "Roster":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        items = payload.get("entradas") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Elenco sem a lista 'entradas'.")
        return cls(RosterEntry.from_dict(item) for item in items)


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold_lexical(raw)).strip("_")


def _similar_length(left: str, right: str) -> bool:
    shorter, longer = sorted((len(left), len(right)))
    return longer > 0 and shorter / longer >= 0.6
