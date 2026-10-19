from __future__ import annotations

from pathlib import Path

from .address_heuristic import AddressHeuristicEngine
from .cnpj_lookup import CnpjLookupEngine
from .eproc_codes import EprocCodesEngine
from .form_fields import FormFieldsEngine
from .gemini_detector import ContextualEngine
from .header_heuristic import HeaderHeuristicEngine
from .local_ner import LENER_MODEL, CombinedEngine, LocalNerEngine, load_lener_predict
from .settings import AppSettings
from .table_labels import TableLabelsEngine
from .target_list import TargetListEngine


def build_contextual_engine(
    settings: AppSettings,
    *,
    use_ner_cache: bool = True,
    ner_cache_dir: Path | None = None,
) -> ContextualEngine | None:
    engines: list[ContextualEngine] = []
    targets = TargetListEngine(
        companies=settings.target_companies,
        people=settings.target_people,
        ids=settings.target_ids,
        extra_keep=settings.exclusions,
        lookup_cnpj_names=settings.cnpj_lookup_enabled,
        aliases=settings.target_aliases,
        processes=settings.target_processes,
        cdas=settings.target_cdas,
        contacts=settings.target_contacts,
    )
    if targets.has_targets():
        engines.append(targets)
    engines.append(
        EprocCodesEngine(
            people=settings.target_people,
            extra_keep=settings.exclusions,
            enabled_tags=settings.enabled_tags,
        )
    )
    cnpj = CnpjLookupEngine(
        extra_keep=settings.exclusions,
        enabled=settings.cnpj_lookup_enabled,
    )
    if cnpj.is_ready():
        engines.append(cnpj)
    engines.append(HeaderHeuristicEngine(extra_keep=settings.exclusions))
    engines.append(
        TableLabelsEngine(
            extra_keep=settings.exclusions,
            enabled_tags=settings.enabled_tags,
        )
    )
    engines.append(
        FormFieldsEngine(
            extra_keep=settings.exclusions,
            enabled_tags=settings.enabled_tags,
        )
    )
    engines.append(AddressHeuristicEngine(extra_keep=settings.exclusions))
    if settings.local_ner_enabled:
        engines.append(
            LocalNerEngine(
                extra_keep=settings.exclusions,
                shadow_case=settings.case_shadow_enabled,
                use_cache=use_ner_cache,
                cache_dir=ner_cache_dir,
            )
        )
        if settings.extra_ner == "lener":
            # Segundo detector (H8), pelo mesmo caminho do GLiNER: o cache tem identidade propria.
            engines.append(
                LocalNerEngine(
                    extra_keep=settings.exclusions,
                    shadow_case=settings.case_shadow_enabled,
                    use_cache=use_ner_cache,
                    cache_dir=ner_cache_dir,
                    predict=load_lener_predict(),
                    model_label=f"lener:{LENER_MODEL}",
                )
            )
    if not engines:
        return None
    if len(engines) == 1:
        return engines[0]
    return CombinedEngine(engines)
