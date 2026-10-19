from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .catalog import STRUCTURED_TAGS
from .target_ids import parse_target_ids


def default_enabled_tags() -> dict[str, bool]:
    return {tag.entity_type: True for tag in STRUCTURED_TAGS}


@dataclass
class CustomRule:
    name: str
    entity_type: str
    token_label: str
    regex: str
    score: float = 0.8

    def to_dict(self) -> dict[str, str | float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "CustomRule":
        return cls(
            name=str(payload.get("name", "")).strip(),
            entity_type=str(payload.get("entity_type", "")).strip(),
            token_label=str(payload.get("token_label", "")).strip(),
            regex=str(payload.get("regex", "")).strip(),
            score=float(payload.get("score", 0.8)),
        )


@dataclass
class AppSettings:
    local_ner_enabled: bool = True
    enabled_tags: dict[str, bool] = field(default_factory=default_enabled_tags)
    custom_rules: list[CustomRule] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    target_companies: list[str] = field(default_factory=list)
    target_people: list[str] = field(default_factory=list)
    target_ids: list[str] = field(default_factory=list)
    # H9-i lote B1 (aba Caso): grafias adicionais ("nome principal = grafia1; grafia2"), processos, CDAs, contatos
    # (endereco, telefone, e-mail) e o papel de cada nome (so para o elenco). Uma entrada por linha.
    target_aliases: list[str] = field(default_factory=list)
    target_processes: list[str] = field(default_factory=list)
    target_cdas: list[str] = field(default_factory=list)
    target_contacts: list[str] = field(default_factory=list)
    target_roles: dict[str, str] = field(default_factory=dict)
    cnpj_lookup_enabled: bool = True
    case_shadow_enabled: bool = False
    # Segundo detector de nomes, em avaliacao (H8): "" (so o GLiNER) ou "lener" (GLiNER + BERT do LeNER-Br,
    # uniao, mesmos filtros). Desligado por padrao ate a comparacao contra o gabarito decidir.
    extra_ner: str = ""
    # Piso local do motor LLM (I-N4): o motor local roda em todas as folhas e o resultado e a uniao com o
    # LLM; o LLM so acrescenta protecao. Ligado por padrao ate o C3 medir o LLM com o gabarito.
    llm_local_floor: bool = True
    # H9-i lote B7: o LLM poder RETIRAR um nome que so o detector de nomes do motor local achou (folha que o LLM
    # leu e respondeu, sem mencao dele ali). Desligado por padrao: a AUDITORIA conta o que seria retirado, e o
    # Operador liga depois que a medicao mostrar que o LLM acerta. Nunca vale para CPF, CNPJ, lista do caso, base
    # de CNPJ nem regra.
    llm_may_retract: bool = False

    def token_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for rule in self.custom_rules:
            if rule.entity_type and rule.token_label:
                labels[rule.entity_type] = rule.token_label
        return labels


def config_path(private_root: Path) -> Path:
    return Path(private_root) / "config.json"


def load_settings(private_root: Path) -> AppSettings:
    candidate = Path(private_root)
    path = candidate if candidate.is_file() else config_path(candidate)
    enabled = default_enabled_tags()
    if not path.exists():
        return AppSettings(enabled_tags=enabled)
    payload = json.loads(path.read_text(encoding="utf-8"))
    stored = payload.get("enabled_tags") or {}
    for key, value in stored.items():
        enabled[str(key)] = bool(value)
    rules = [CustomRule.from_dict(item) for item in payload.get("custom_rules", [])]
    exclusions = [str(item).strip() for item in payload.get("exclusions", []) if str(item).strip()]
    target_companies = _string_list(payload.get("target_companies", []))
    target_people = _string_list(payload.get("target_people", []))
    target_ids = list(parse_target_ids(_string_list(payload.get("target_ids", []))))
    return AppSettings(
        local_ner_enabled=bool(payload.get("local_ner_enabled", True)),
        enabled_tags=enabled,
        custom_rules=rules,
        exclusions=exclusions,
        target_companies=target_companies,
        target_people=target_people,
        target_ids=target_ids,
        target_aliases=_string_list(payload.get("target_aliases", [])),
        target_processes=_string_list(payload.get("target_processes", [])),
        target_cdas=_string_list(payload.get("target_cdas", [])),
        target_contacts=_string_list(payload.get("target_contacts", [])),
        target_roles=_string_dict(payload.get("target_roles", {})),
        cnpj_lookup_enabled=bool(payload.get("cnpj_lookup_enabled", True)),
        case_shadow_enabled=bool(payload.get("case_shadow_enabled", False)),
        extra_ner=str(payload.get("extra_ner") or "").strip().lower(),
        llm_local_floor=bool(payload.get("llm_local_floor", True)),
        llm_may_retract=bool(payload.get("llm_may_retract", False)),
    )


def _string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _string_dict(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in raw.items() if str(key).strip() and str(value).strip()}


# H9-j: a lista do caso (aba Caso / planilha do Nexus). No teste de 16/10 a pasta privada foi trocada na tela e o app
# passou a ler o config.json da pasta nova, vazio: a lista sumiu da rodada sem aviso.
CASE_LIST_FIELDS = (
    "target_companies",
    "target_people",
    "target_ids",
    "target_aliases",
    "target_processes",
    "target_cdas",
    "target_contacts",
    "target_roles",
)


def has_case_list(settings: AppSettings) -> bool:
    return any(getattr(settings, name) for name in CASE_LIST_FIELDS)


def carry_case_list(source: AppSettings, target: AppSettings) -> bool:
    """Leva a lista do caso de ``source`` para ``target`` se ``target`` nao tem nenhuma. True se levou."""

    if has_case_list(target) or not has_case_list(source):
        return False
    for name in CASE_LIST_FIELDS:
        value = getattr(source, name)
        setattr(target, name, dict(value) if isinstance(value, dict) else list(value))
    return True


def case_list_lines(settings: AppSettings | None) -> list[str]:
    """Linhas da AUDITORIA (so contagens) sobre a lista do caso usada na rodada."""

    if settings is None:
        return []
    counts = (
        f"pessoas {len(settings.target_people)}, empresas {len(settings.target_companies)}, "
        f"CPF/CNPJ/NIRE {len(settings.target_ids)}, grafias {len(settings.target_aliases)}, "
        f"processos {len(settings.target_processes)}, CDAs {len(settings.target_cdas)}, "
        f"contatos/veiculos {len(settings.target_contacts)}"
    )
    if not has_case_list(settings):
        return [
            "ALERTA: lista do caso VAZIA nesta rodada (aba Caso / planilha do Nexus). Confira a pasta privada "
            "escolhida: a lista fica no config.json de cada pasta privada."
        ]
    return [f"Lista do caso usada: {counts}"]


def save_settings(private_root: Path, settings: AppSettings) -> None:
    destination = config_path(private_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "local_ner_enabled": settings.local_ner_enabled,
        "enabled_tags": settings.enabled_tags,
        "custom_rules": [rule.to_dict() for rule in settings.custom_rules],
        "exclusions": settings.exclusions,
        "target_companies": settings.target_companies,
        "target_people": settings.target_people,
        "target_ids": list(parse_target_ids(settings.target_ids)),
        "target_aliases": settings.target_aliases,
        "target_processes": settings.target_processes,
        "target_cdas": settings.target_cdas,
        "target_contacts": settings.target_contacts,
        "target_roles": settings.target_roles,
        "cnpj_lookup_enabled": settings.cnpj_lookup_enabled,
        "case_shadow_enabled": settings.case_shadow_enabled,
        "extra_ner": settings.extra_ner,
        "llm_local_floor": settings.llm_local_floor,
        "llm_may_retract": settings.llm_may_retract,
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
