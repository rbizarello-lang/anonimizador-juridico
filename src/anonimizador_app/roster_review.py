"""Revisao do elenco (L3): quem o LLM achou, com que papel, e o que o usuario quer fazer.

A tela mostra uma linha por entidade (tipo, nome, papel, mencoes, token) com
a escolha **tokenizar / manter**. A decisao do usuario fica "fixa" no
elenco (``fixo: true``) e vence a tabela de papeis em qualquer rodada
seguinte, inclusive quando o LLM volta a apontar a mesma entidade.

Reaplicar o elenco revisado **nao usa rede**: o texto e lido do cache de
extracao, o LLM so e consultado no cache por trecho e a troca e local. O
trecho que o cache nao tem fica sem leitura (fail-closed), como uma falha
de API.

Este modulo trabalha com nomes reais (e o elenco privado): a tela e local e o
que ela grava vai para a pasta privada, nunca para a exportavel.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .llm_discovery import DEFAULT_MODEL, Discoverer, LlmConfigError
from .llm_engine import build_offline_discoverer
from .pipeline_llm import LLM_CACHE_SUBDIR, LlmJobResult, run_llm_job
from .roster import KEEP, TOKENIZE, UNKNOWN_ROLE, Roster, policy_action
from .settings import AppSettings

REVIEWED_FILENAME = "elenco_revisado.json"
KIND_ORDER = ("empresa", "pessoa", "endereco")
KIND_LABELS = {"empresa": "Empresa", "pessoa": "Pessoa", "endereco": "Endereço"}
ROLE_LABELS = {
    "parte": "parte",
    "socio": "sócio",
    "advogado": "advogado",
    "terceiro": "terceiro",
    "magistrado": "magistrado",
    "servidor_publico": "servidor público",
    "procurador_publico": "procurador público",
    "orgao_publico": "órgão público",
    "autor_citado": "autor citado",
    UNKNOWN_ROLE: "sem papel",
}
ACTION_LABELS = {TOKENIZE: "Tokenizar", KEEP: "Manter"}


@dataclass(frozen=True, slots=True)
class ReviewRow:
    key: str
    kind: str
    name: str
    roles: tuple[str, ...]
    occurrences: int
    token: str
    action: str
    default_action: str
    pinned: bool
    source: str

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def role_label(self) -> str:
        return ", ".join(ROLE_LABELS.get(role, role) for role in self.roles) or ROLE_LABELS[UNKNOWN_ROLE]

    @property
    def changed(self) -> bool:
        """Decisao do usuario: a acao difere da que a tabela de papeis daria."""

        return self.action != self.default_action


def build_rows(
    roster: Roster,
    *,
    occurrences: Mapping[str, int] | None = None,
    tokens: Mapping[str, str] | None = None,
) -> list[ReviewRow]:
    """Linhas da tela: por tipo (empresa, pessoa, endereco) e, dentro dele, mais mencoes primeiro."""

    counts = occurrences or {}
    mapped = tokens or {}
    rows = [
        ReviewRow(
            key=entry.key,
            kind=entry.kind,
            name=entry.name,
            roles=tuple(entry.roles),
            occurrences=int(counts.get(entry.key, 0)),
            token=mapped.get(entry.key, ""),
            action=entry.action,
            default_action=policy_action(entry.roles),
            pinned=entry.pinned,
            source=entry.source,
        )
        for entry in roster.entries
    ]
    order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    rows.sort(key=lambda row: (order.get(row.kind, len(order)), -row.occurrences, row.name.casefold()))
    return rows


def summarize(rows: Iterable[ReviewRow], decisions: Mapping[str, str] | None = None) -> str:
    """"12 a tokenizar, 5 a manter · 2 decisoes suas"."""

    chosen = decisions or {}
    items = [chosen.get(row.key, row.action) for row in rows]
    decided = sum(
        1 for row in rows if chosen.get(row.key, row.action) != row.default_action or row.pinned
    )
    text = f"{items.count(TOKENIZE)} a tokenizar, {items.count(KEEP)} a manter"
    return text + (f" · {decided} decisão(ões) sua(s)" if decided else "")


def apply_decisions(roster: Roster, decisions: Mapping[str, str]) -> int:
    """Aplica as escolhas do usuario (chave -> ``tokenizar``/``manter``) e as fixa.

    Devolve quantas entradas mudaram de acao. Chave desconhecida e acao invalida
    sao erro: a tela nunca deve gerar nenhuma das duas.
    """

    changed = 0
    for key, action in decisions.items():
        if action not in (TOKENIZE, KEEP):
            raise ValueError(f"Acao invalida: {action!r}")
        entry = roster.get(key)
        if entry is None:
            raise KeyError(f"Entidade fora do elenco: {key!r}")
        if entry.action != action:
            changed += 1
        entry.action = action
        entry.pinned = True
    return changed


def save_reviewed(
    roster: Roster, previous_roster_path: Path, *, rows: Sequence[ReviewRow] = ()
) -> Path:
    """Grava ``elenco_revisado.json`` ao lado do elenco da rodada (pasta privada)."""

    notes = {row.key: {"token": row.token, "ocorrencias": row.occurrences} for row in rows}
    return roster.dump(Path(previous_roster_path).with_name(REVIEWED_FILENAME), annotations=notes)


@dataclass(frozen=True, slots=True)
class ReapplyResult:
    result: LlmJobResult
    reviewed_path: Path
    changed: int


def reapply_offline(
    previous: LlmJobResult,
    decisions: Mapping[str, str],
    files: Sequence[str | Path],
    *,
    export_root: Path,
    private_root: Path,
    settings: AppSettings | None = None,
    model_id: str = DEFAULT_MODEL,
    verification_rounds: int = 1,
    discoverer: Discoverer | None = None,
    engine_notes: Sequence[str] = (),
) -> ReapplyResult:
    """Refaz o lote com o elenco revisado, SEM rede (so cache).

    Parte do ``elenco.json`` da rodada anterior (do disco, nao da memoria),
    aplica as decisoes, grava o ``elenco_revisado.json`` e roda de novo num
    lote novo. ``discoverer`` so existe para teste; o padrao e o descobridor
    sem rede do canal da rodada anterior.
    """

    roster = Roster.load(previous.roster_path)
    changed = apply_decisions(roster, decisions)
    rows = build_rows(roster, occurrences=previous.occurrences, tokens=previous.tokens)
    reviewed = save_reviewed(roster, previous.roster_path, rows=rows)
    if discoverer is None:
        if not previous.engine:
            raise LlmConfigError("Rodada anterior sem canal registrado: nao ha cache a reaproveitar.")
        discoverer = build_offline_discoverer(
            previous.engine,
            cache_dir=Path(private_root) / LLM_CACHE_SUBDIR,
            model_id=model_id,
        )
    notes = [
        f"Elenco revisado reaplicado sem rede (cache): {changed} entidade(s) com acao alterada.",
        *engine_notes,
    ]
    result = run_llm_job(
        files,
        export_root=export_root,
        private_root=private_root,
        discoverer=discoverer,
        settings=settings,
        roster_path=reviewed,
        verification_rounds=verification_rounds,
        use_cache=True,
        engine=previous.engine or None,
        engine_notes=notes,
    )
    return ReapplyResult(result=result, reviewed_path=reviewed, changed=changed)

