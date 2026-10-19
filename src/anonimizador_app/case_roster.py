"""Lista do caso como chave unica tambem no pipeline do elenco (modo LLM e pipeline unico; H9-i lote B1).

Ate o lote A a chave da lista (``alvo:...``) se perdia no modo LLM: o nome que o motor local ou o LLM achava ia para o
elenco pela grafia, e a forma parcial (prenome, nome fantasia, razao sem sufixo) abria entrada nova, com outro token.
Aqui cada entrada da lista (``TargetListEngine``) tem UMA entrada no elenco (origem ``lista``):

- o nome, as grafias adicionais e as frases de empresa (razao sem sufixo, prefixo, nome fantasia) entram como variantes;
- a busca da lista roda em todos os arquivos e ancora cada ocorrencia na entrada dela (inclusive particula com apoio);
- a mencao do motor local e a do LLM que casa com alguma forma da entrada (``resolve_mention``) vai para a entrada da
  lista antes da higiene do LLM e do juizo do lote: nunca e descartada como termo protegido, palavra comum ou "manter".
- processos, CDAs e contatos da lista entram como IDs (``case_list.CaseRules``).

Nada disso muda o texto mascarado enviado ao LLM: roda depois da descoberta.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from anonimizador_juridico import EntitySpan, resolve_overlaps
from anonimizador_juridico.pseudonymizer import fold_lexical

from .fidelity import drop_protected_spans
from .roster import Mention, Roster, normalize_kind, strip_token_tail
from .span_hygiene import sanitize_spans
from .target_list import TargetListEngine

_NAME_TYPES = {"PERSON": "pessoa", "ORGANIZATION": "empresa"}


class CaseLink:
    def __init__(self, settings: object, roster: Roster, extra_keep: Sequence[str] = ()) -> None:
        self.roster = roster
        self.extra_keep = tuple(extra_keep)
        self.engine = TargetListEngine(
            companies=getattr(settings, "target_companies", ()),
            people=getattr(settings, "target_people", ()),
            ids=getattr(settings, "target_ids", ()),
            extra_keep=self.extra_keep,
            lookup_cnpj_names=bool(getattr(settings, "cnpj_lookup_enabled", True)),
            aliases=getattr(settings, "target_aliases", ()),
            processes=getattr(settings, "target_processes", ()),
            cdas=getattr(settings, "target_cdas", ()),
            contacts=getattr(settings, "target_contacts", ()),
        )
        self.rules = self.engine.rules
        self.keys: dict[str, str] = {}
        roles = {fold_lexical(name): role for name, role in (getattr(settings, "target_roles", {}) or {}).items()}
        entries = self.engine.entries
        owners: dict[str, set[str]] = {}
        for entry in entries:
            for phrase in entry.phrases:
                owners.setdefault(fold_lexical(phrase), set()).add(entry.canonical_key)
        for entry in entries:
            role = next((roles[fold_lexical(name)] for name in entry.names if fold_lexical(name) in roles), "")
            listed = roster.add_listed(entry.kind, entry.original, role=role)
            if listed is None:
                continue
            self.keys[entry.canonical_key] = listed.key
            forms = [name for name in entry.names if name != entry.original]
            if entry.kind == "empresa":
                # Frases da empresa (razao sem sufixo, forma curta, nome fantasia) de uma entrada so. A de pessoa
                # (par inicial, sobrenomes) chega pelas ancoras da busca da lista, que respeita juiz e procurador.
                forms += [
                    phrase
                    for phrase in entry.phrases
                    if len(phrase.split()) >= 2 and owners.get(fold_lexical(phrase)) == {entry.canonical_key}
                ]
            for value in dict.fromkeys(forms):
                roster.add_mention(Mention(kind=entry.kind, text=value, canonical=listed.name), source="lista")

    def __bool__(self) -> bool:
        return bool(self.keys) or bool(self.rules) or bool(self.engine.ids)

    def key_for_span(self, span: EntitySpan, text: str) -> str | None:
        """A chave do elenco da entrada da lista a que o span pertence (pela chave ``alvo:`` ou pela forma)."""

        kind = _NAME_TYPES.get(span.entity_type)
        if kind is None:
            return None
        key = self.keys.get(span.canonical_key)
        if key is not None:
            return key
        if span.canonical_key.startswith("alvo:"):
            # Entrada que a busca da lista do motor local criou e esta nao conhece (indice de CNPJ, particula
            # sem nome completo no texto): entra no elenco como da lista.
            listed = self.roster.add_listed(kind, span.alvo or text[span.start : span.end])
            if listed is None:
                return None
            self.keys[span.canonical_key] = listed.key
            return listed.key
        entry = self.engine.resolve_mention(text[span.start : span.end], text=text, start=span.start, end=span.end)
        return self.keys.get(entry.canonical_key) if entry is not None else None

    def apply(self, text: str, ids: list[EntitySpan], anchored: list[tuple[int, int, str]]) -> list[EntitySpan]:
        """A busca da lista no arquivo: nomes viram ancoras da entrada da lista; IDs, processos, CDAs e contatos
        entram nos IDs. Devolve os IDs novos (ja unidos aos antigos)."""

        if not self:
            return ids
        found = resolve_overlaps(self.engine.detect(text))
        found = sanitize_spans(text, drop_protected_spans(text, found, self.extra_keep))
        extra: list[EntitySpan] = []
        for span in found:
            if span.entity_type in _NAME_TYPES:
                key = self.key_for_span(span, text)
                if key is not None:
                    anchored.append((span.start, span.end, key))
                continue
            if span.entity_type == "LOCATION" or span.entity_type not in _NAME_TYPES:
                extra.append(span)
        if not extra:
            return ids
        return resolve_overlaps([*ids, *extra])

    def route(self, mentions: Iterable[Mention], text: str) -> tuple[list[Mention], list[Mention]]:
        """(mencoes que sao da lista, ja apontadas para a entrada dela; as demais)."""

        routed: list[Mention] = []
        rest: list[Mention] = []
        for mention in mentions:
            kind = normalize_kind(mention.kind)
            value = strip_token_tail(mention.text)
            entry = None
            if kind in {"pessoa", "empresa"} and value:
                entry = self.engine.resolve_mention(value, text=text, start=mention.start, end=mention.end)
            listed = self.roster.get(self.keys.get(entry.canonical_key, "")) if entry is not None else None
            if listed is None:
                rest.append(mention)
                continue
            routed.append(replace(mention, kind=listed.kind, canonical=listed.name))
        return routed, rest
