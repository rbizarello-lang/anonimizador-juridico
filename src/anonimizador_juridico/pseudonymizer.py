from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .address import canonicalize_location
from .types import EntitySpan
from .validators import map_ocr_digits, normalize_cnpj, only_digits


_TOKEN_LABELS = {
    "BR_CPF": "CPF",
    "BR_CNPJ": "CNPJ",
    "BR_PROCESSO": "PROCESSO",
    "EMAIL_ADDRESS": "EMAIL",
    "BR_PHONE": "TELEFONE",
    "BR_OAB": "OAB",
    "BR_RG": "RG",
    "BR_MATRICULA_IMOVEL": "MATRICULA_IMOVEL",
    "BR_CEP": "CEP",
    "BR_PIX": "PIX",
    "BR_CONTA_BANCARIA": "CONTA",
    "BR_RENAVAM": "RENAVAM",
    "BR_PLACA": "PLACA",
    "BR_CHASSI": "CHASSI",
    "BR_NASCIMENTO": "NASCIMENTO",
    "BR_TITULO_ELEITOR": "TITULO",
    "BR_PIS": "PIS",
    "BR_NIRE": "NIRE",
    "BR_CNH": "CNH",
    "BR_URL": "URL",
    "BR_CDA": "CDA",
    "BR_CADASTRO_RURAL": "CADASTRO_RURAL",
    "BR_REGISTRO_JUNTA": "REGISTRO_JUNTA",
    "BR_IP": "IP",
    "BR_PROCESSO_ADMINISTRATIVO": "PROCESSO_ADMINISTRATIVO",
    "BR_EPROC": "EPROC",
    "PERSON": "PESSOA",
    "ORGANIZATION": "EMPRESA",
    "LOCATION": "ENDERECO",
}

_PIX_LABEL = re.compile(
    r"^\s*(?:chave\s+(?:pix|aleat[óo]ria)|pix(?:\s+chave)?)\s*(?:n[.\s]*[oº°]?\s*)?:?\s*",
    re.IGNORECASE,
)
_PLACA_LABEL = re.compile(
    r"^\s*placa\s*(?:n[.\s]*[oº°]?\s*)?:?\s*",
    re.IGNORECASE,
)
_UF_SET = frozenset(
    {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
        "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    }
)
_PLATE_BODY_CANON = re.compile(r"^[A-Z]{3}(?:\d{4}|\d[A-Z]\d{2})$")


def _collapse_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


_APOSTROPHES = frozenset("'’`´ʼ′")


def fold_lexical(value: str) -> str:
    """NFKD sem combinantes, casefold, apostrofo ignorado, espacos colapsados."""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    collapsed = re.sub(r"\s+", " ", stripped).strip().casefold()
    collapsed = "".join(char for char in collapsed if char not in _APOSTROPHES)
    collapsed = collapsed.rstrip(".,;:!?…")
    return re.sub(r"\s+", " ", collapsed).strip()


def _split_oab_uf(compact: str) -> tuple[str, str]:
    """Separa UF da inscricao: OAB/SP 123 e OAB 123/SP viram o mesmo corpo."""

    if len(compact) >= 3 and compact[:2] in _UF_SET:
        rest = compact[2:]
        if rest.isdigit() or (
            len(rest) >= 2 and rest[:-1].isdigit() and rest[-1].isalpha()
        ):
            return compact[:2], rest
    if len(compact) >= 3 and compact[-2:] in _UF_SET:
        rest = compact[:-2]
        if rest.isdigit() or (
            len(rest) >= 2 and rest[:-1].isdigit() and rest[-1].isalpha()
        ):
            return compact[-2:], rest
    return "", compact


def _drop_oab_numero_marker(compact: str) -> str:
    """OAB/RS n.º 54321 compacta para RSN54321; tira o N de 'n.º'/'n.'."""

    if len(compact) >= 4 and compact[:2] in _UF_SET and compact[2] == "N":
        rest = compact[3:]
        if rest.isdigit() or (
            len(rest) >= 2 and rest[:-1].isdigit() and rest[-1].isalpha()
        ):
            return compact[:2] + rest
    if len(compact) >= 4 and compact[-2:] in _UF_SET and compact[0] == "N":
        rest = compact[1:-2]
        if rest.isdigit() or (
            len(rest) >= 2 and rest[:-1].isdigit() and rest[-1].isalpha()
        ):
            return rest + compact[-2:]
    return compact


def _canonicalize_oab(value: str) -> str:
    compact = re.sub(r"[^0-9A-Z]", "", value.upper())
    compact = re.sub(r"^N?OAB", "", compact)
    compact = _drop_oab_numero_marker(compact)
    uf, body = _split_oab_uf(compact)
    letter = ""
    digits = body
    if body and body[-1].isalpha() and body[:-1].isdigit():
        letter = body[-1]
        digits = body[:-1]
    if not digits.isdigit():
        return compact
    number = digits.lstrip("0") or "0"
    if uf:
        return f"{uf}{number}{letter}"
    return f"{number}{letter}"


def _canonicalize_rg(value: str) -> str:
    """Mantem o DV alfanumerico X (SP/RJ); 12.345.678-0 continua distinto."""

    compact = re.sub(r"[^0-9Xx]", "", value)
    if len(compact) >= 2 and compact[-1] in "Xx" and compact[:-1].isdigit():
        return compact[:-1] + "X"
    return re.sub(r"[^0-9]", "", compact)


def canonicalize(entity_type: str, value: str) -> str:
    if entity_type == "BR_CNPJ":
        without_label = re.sub(
            r"^\s*(?:C\.?\s*N\.?\s*P\.?\s*J\.?)(?:\s*/\s*MF)?\s*"
            r"(?:n[\W_]{0,3})?\s*[:;]?\s*",
            "",
            value,
            flags=re.IGNORECASE,
        )
        compact = normalize_cnpj(without_label)
        while compact.startswith("CNPJ") or compact.startswith("MF"):
            compact = compact[4:] if compact.startswith("CNPJ") else compact[2:]
        return compact
    if entity_type == "BR_CEP":
        digits = only_digits(value)
        return digits[:8] if len(digits) >= 8 else digits
    if entity_type == "BR_CPF":
        return map_ocr_digits(value)
    if entity_type == "BR_RG":
        return _canonicalize_rg(value)
    if entity_type in {
        "BR_PROCESSO",
        "BR_RENAVAM",
        "BR_CONTA_BANCARIA",
        "BR_NASCIMENTO",
        "BR_TITULO_ELEITOR",
        "BR_PIS",
        "BR_NIRE",
        "BR_CNH",
        "BR_PROCESSO_ADMINISTRATIVO",
        "BR_CADASTRO_RURAL",
        "BR_REGISTRO_JUNTA",
    }:
        return only_digits(value)
    if entity_type == "BR_CDA":
        return re.sub(r"[^0-9A-Z]", "", value.upper())
    if entity_type == "BR_PIX":
        if re.fullmatch(r"E\d{20}[0-9A-Za-z]{11}", value.strip()):
            return value.strip().upper()  # EndToEndId (alfanumerico)
        return re.sub(r"[^0-9a-f]", "", _PIX_LABEL.sub("", value, count=1).casefold())
    if entity_type == "BR_PLACA":
        compact = re.sub(r"[^A-Z0-9]", "", _PLACA_LABEL.sub("", value, count=1).upper())
        if (
            len(compact) >= 9
            and compact[:2] in _UF_SET
            and _PLATE_BODY_CANON.match(compact[2:])
        ):
            return compact[2:]
        return compact
    if entity_type == "BR_CHASSI":
        return re.sub(r"[^A-Z0-9]", "", value.upper())
    if entity_type == "BR_EPROC":
        return re.sub(r"[^A-Z0-9]", "", value.upper())
    if entity_type == "BR_PHONE":
        digits = only_digits(value)
        if len(digits) in (12, 13) and digits.startswith("55"):
            digits = digits[2:]
        return digits
    if entity_type == "EMAIL_ADDRESS":
        return value.strip().casefold()
    if entity_type == "BR_OAB":
        return _canonicalize_oab(value)
    if entity_type == "LOCATION":
        return canonicalize_location(value)
    if entity_type in {"PERSON", "ORGANIZATION"}:
        return fold_lexical(value)
    return _collapse_text(value)


@dataclass(slots=True)
class MapRecord:
    token: str
    entity_type: str
    canonical: str
    originals: list[str] = field(default_factory=list)
    alvo: str = ""

    def add_original(self, value: str) -> None:
        if value not in self.originals:
            self.originals.append(value)


class PseudonymMap:
    """Mapa sensivel e reversivel. Deve permanecer apenas em armazenamento local."""

    schema_version = 2
    supported_schema_versions = frozenset({1, 2})

    def __init__(
        self,
        records: list[MapRecord] | None = None,
        token_labels: dict[str, str] | None = None,
    ) -> None:
        self._records: dict[str, MapRecord] = {}
        self._index: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        self._token_labels = {**_TOKEN_LABELS, **(token_labels or {})}
        for record in records or []:
            self._insert(record)

    def _insert(self, record: MapRecord) -> None:
        if record.token in self._records:
            raise ValueError(f"Token duplicado no mapa: {record.token}")
        key = (record.entity_type, record.canonical)
        if key in self._index:
            raise ValueError("Entidade canonica duplicada no mapa")
        self._records[record.token] = record
        self._index[key] = record.token
        label, _, number = record.token.strip("[]").rpartition("_")
        if number.isdigit():
            self._counters[label] = max(self._counters.get(label, 0), int(number))

    def _location_alias(self, canonical: str) -> str | None:
        """Fundir via sem numero com a mesma via+numero (Itamar Rodriguez).

        Nao funde numeros prediais distintos (Meireles 563 vs 579).
        OCR que corta o ultimo digito (57 vs 579) cai no mesmo token.
        """

        street, _sep, maybe_num = canonical.rpartition(" ")
        if street and maybe_num.isdigit():
            token = self._index.get(("LOCATION", street))
            if token:
                return token
            for (entity_type, other), token in self._index.items():
                if entity_type != "LOCATION":
                    continue
                other_street, _s, other_num = other.rpartition(" ")
                if other_street != street or not other_num.isdigit():
                    continue
                shorter, longer = (
                    (maybe_num, other_num)
                    if len(maybe_num) <= len(other_num)
                    else (other_num, maybe_num)
                )
                if longer.startswith(shorter) and len(longer) == len(shorter) + 1:
                    return token
            return None
        prefix = canonical + " "
        for (entity_type, other), token in self._index.items():
            if entity_type != "LOCATION":
                continue
            if other.startswith(prefix) and other[len(prefix) :].isdigit():
                return token
        return None

    def _cpf_mask_alias(self, canonical: str) -> str | None:
        """CPF mascarado do gov.br ("MASK:123456", o miolo) recebe o token do CPF completo com o mesmo miolo, se for um so;
        o CPF completo que chega depois de um mascarado com o mesmo miolo reaproveita o token do mascarado."""

        if canonical.startswith("MASK:"):
            core = canonical[5:]
            tokens = {
                token
                for (kind, other), token in self._index.items()
                if kind == "BR_CPF" and len(other) == 11 and other[3:9] == core
            }
        elif len(canonical) == 11 and canonical.isdigit():
            token = self._index.get(("BR_CPF", f"MASK:{canonical[3:9]}"))
            tokens = {token} if token else set()
        else:
            return None
        return next(iter(tokens)) if len(tokens) == 1 else None

    def resolve(
        self,
        entity_type: str,
        value: str,
        *,
        canonical_key: str = "",
        alvo: str = "",
    ) -> str:
        canonical = canonical_key or canonicalize(entity_type, value)
        key = (entity_type, canonical)
        existing = self._index.get(key)
        if existing is None and entity_type == "BR_CPF":
            existing = self._cpf_mask_alias(canonical)
        if existing is None and entity_type == "LOCATION" and not canonical_key:
            existing = self._location_alias(canonical)
        if existing:
            record = self._records[existing]
            record.add_original(value)
            if alvo and not record.alvo:
                record.alvo = alvo
            return existing

        label = self._token_labels.get(entity_type, entity_type)
        self._counters[label] = self._counters.get(label, 0) + 1
        token = f"[{label}_{self._counters[label]}]"
        record = MapRecord(
            token=token,
            entity_type=entity_type,
            canonical=canonical,
            originals=[value],
            alvo=alvo,
        )
        self._insert(record)
        return token

    def token_for(self, entity_type: str, canonical: str) -> str:
        """Token ja atribuido a essa entidade canonica ("" se nenhum); nao cria token."""

        return self._index.get((entity_type, canonical), "")

    def to_dict(self) -> dict[str, Any]:
        records = []
        for record in self._records.values():
            item = {
                "token": record.token,
                "entity_type": record.entity_type,
                "canonical": record.canonical,
                "originals": list(record.originals),
            }
            if record.alvo:
                item["alvo"] = record.alvo
            records.append(item)
        return {
            "schema_version": self.schema_version,
            "warning": "CONTEUDO SENSIVEL: NAO ENVIAR A SERVICOS EXTERNOS",
            "records": records,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PseudonymMap":
        version = int(payload.get("schema_version") or 0)
        if version not in cls.supported_schema_versions:
            raise ValueError("Versao de mapa nao suportada")
        records = [
            MapRecord(
                token=item["token"],
                entity_type=item["entity_type"],
                canonical=item["canonical"],
                originals=list(item.get("originals", [])),
                alvo=str(item.get("alvo") or ""),
            )
            for item in payload.get("records", [])
        ]
        return cls(records)

    @classmethod
    def load(cls, path: str | Path) -> "PseudonymMap":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    def dump(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, destination)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @property
    def records(self) -> tuple[MapRecord, ...]:
        return tuple(self._records.values())


@dataclass(frozen=True, slots=True)
class PseudonymizationResult:
    text: str
    mapping: PseudonymMap
    replacements: int


def pseudonymize(
    text: str,
    entities: list[EntitySpan],
    mapping: PseudonymMap | None = None,
) -> PseudonymizationResult:
    active_map = mapping or PseudonymMap()
    ordered = sorted(entities, key=lambda span: (span.start, span.end))

    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise ValueError("Entidades sobrepostas devem ser consolidadas antes da pseudonimizacao")

    replacements: list[tuple[EntitySpan, str]] = []
    for entity in ordered:
        if entity.end > len(text):
            raise ValueError("Span fora dos limites do texto")
        value = entity.value_from(text)
        replacements.append(
            (
                entity,
                active_map.resolve(
                    entity.entity_type,
                    value,
                    canonical_key=entity.canonical_key,
                    alvo=entity.alvo,
                ),
            )
        )

    # Junta os pedacos numa passada: fatiar o texto inteiro a cada troca era
    # quadratico (dezenas de milhares de trocas num processo de mil folhas).
    pieces: list[str] = []
    cursor = 0
    for entity, token in replacements:
        pieces.append(text[cursor : entity.start])
        pieces.append(token)
        cursor = entity.end
    pieces.append(text[cursor:])
    result = "".join(pieces)

    return PseudonymizationResult(
        text=result,
        mapping=active_map,
        replacements=len(replacements),
    )
