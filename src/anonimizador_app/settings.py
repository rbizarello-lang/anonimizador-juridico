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
    cnpj_lookup_enabled: bool = True
    case_shadow_enabled: bool = False

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
        cnpj_lookup_enabled=bool(payload.get("cnpj_lookup_enabled", True)),
        case_shadow_enabled=bool(payload.get("case_shadow_enabled", False)),
    )


def _string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


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
        "cnpj_lookup_enabled": settings.cnpj_lookup_enabled,
        "case_shadow_enabled": settings.case_shadow_enabled,
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
