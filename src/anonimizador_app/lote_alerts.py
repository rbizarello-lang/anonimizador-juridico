"""Avisos do lote para quem vai mandar o exportado a uma IA externa (H9-j): sigilo, evento citado fora do lote e as
instrucoes para a IA redatora (``INSTRUCOES_IA.md``).

Do teste ponta a ponta de 16/10/2026: a peca redigida pela IA externa transcreveu uma decisao com sigilo nivel 2 sem
saber disso, e afirmou um "comparecimento espontaneo" no Ev. 125 que se referia ao Ev. 121, ausente do lote. Aqui so
saem numeros de folha e de evento: nenhum conteudo.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

INSTRUCTIONS_NAME = "INSTRUCOES_IA.md"

_PAGE = re.compile(r"<!--\s*pagina-origem:\s*(\d+)")
_STAMP = re.compile(r"Processo\s+\S+?,\s*Evento\s+(\d+)\s*,")
_BOOKMARK = re.compile(r"(?m)^#{1,6} Evento\.?\s+(\d+)\s*-\s*Doc")
_SEQUENCE = re.compile(r"Sequ[êe]ncia Evento:\s*(\d+)")
_REFERRED = re.compile(r"(?i)REFER\.?\s*AO\s+EVENTO:?\s*(\d+)")
_SECRECY = (
    (
        "nível de sigilo",
        re.compile(
            r"(?i)\bn[íi]vel\s+de\s+sigilo\s*([1-5])\b|\bsigilo\s*(?:n[íi]vel\s*)?([1-5])\b"
            r"|\bsigilo\b[^\n]{0,40}?\bn[íi]vel\s*([1-5])\b|\bn[íi]vel\s*([1-5])\b[^\n]{0,40}?\bsigil"
        ),
    ),
    ("segredo de justiça", re.compile(r"(?i)\bsegredo\s+de\s+justi[çc]a\b")),
    ("documento sigiloso", re.compile(r"(?i)\b(?:documento|decis[ãa]o|processo|autos)\s+sigilos[oa]s?\b")),
    ("sigilo fiscal", re.compile(r"(?i)\bsigilo\s+fiscal\b")),
)


@dataclass
class LoteAlerts:
    # arquivo -> [(folha, rotulo)]
    secrecy: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    # arquivo -> eventos citados ("REFER. AO EVENTO: N") que nao estao no lote
    missing_events: dict[str, list[int]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.secrecy) or bool(self.missing_events)


def _page_at(text: str, position: int) -> int:
    page = 0
    for match in _PAGE.finditer(text, 0, position):
        page = int(match.group(1))
    return page


def analyze(documents: Sequence[tuple[str, str]]) -> LoteAlerts:
    alerts = LoteAlerts()
    present: set[int] = set()
    for _name, text in documents:
        for pattern in (_STAMP, _BOOKMARK, _SEQUENCE):
            present.update(int(match.group(1)) for match in pattern.finditer(text))
    for name, text in documents:
        found: dict[int, set[str]] = {}
        for label, pattern in _SECRECY:
            for match in pattern.finditer(text):
                level = next((group for group in match.groups() if group), "") if match.groups() else ""
                found.setdefault(_page_at(text, match.start()), set()).add(f"{label} {level}".strip())
        if found:
            alerts.secrecy[name] = [(page, ", ".join(sorted(labels))) for page, labels in sorted(found.items())]
        referred = sorted({int(match.group(1)) for match in _REFERRED.finditer(text)} - present)
        if referred and present:
            alerts.missing_events[name] = referred
    return alerts


def alert_lines(alerts: LoteAlerts) -> list[str]:
    lines: list[str] = []
    for name, items in alerts.secrecy.items():
        levels = sorted({level for _page, label in items for level in re.findall(r"nível de sigilo (\d)", label)})
        pages = ", ".join(str(page) for page, _label in items[:30])
        lines.append(
            f"ATENCAO sigilo em {name}: folhas {pages}{'...' if len(items) > 30 else ''}"
            + (f" (nível de sigilo citado: {', '.join(levels)})" if levels else "")
            + ". Confira antes de mandar o exportado para fora."
        )
    for name, events in alerts.missing_events.items():
        lines.append(f"Evento(s) citado(s) e fora do lote em {name}: {', '.join(map(str, events))}")
    return lines


def audit_section(markdown_paths: Sequence[Path]) -> tuple[str, list[str]]:
    """Secao "avisos do lote" da AUDITORIA, lida dos textos ja gravados (so folhas e eventos)."""

    documents = [(Path(path).name, Path(path).read_text(encoding="utf-8")) for path in markdown_paths if Path(path).is_file()]
    lines = alert_lines(analyze(documents))
    return ("avisos do lote (sigilo, evento fora do lote)", lines or ["Nenhuma marca de sigilo e nenhum evento citado fora do lote."])


def append_to_index(index_path: Path, alerts: LoteAlerts) -> None:
    if not alerts or not index_path.is_file():
        return
    lines = ["", "## Avisos do lote", ""]
    lines += [f"- {line}" for line in alert_lines(alerts)]
    index_path.write_text(index_path.read_text(encoding="utf-8").rstrip("\n") + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def write_ai_instructions(export_dir: Path, alerts: LoteAlerts) -> Path:
    """``INSTRUCOES_IA.md``: o que a IA que vai ler este lote precisa saber (anexar junto com os textos)."""

    lines = [
        "# Instruções para a IA que vai ler estes autos",
        "",
        "Estes autos foram anonimizados. Antes de analisar ou redigir:",
        "",
        "1. **Tokens.** Pessoas, empresas, endereços e números identificadores foram trocados por marcadores como "
        "`[PESSOA_3]`, `[EMPRESA_1]`, `[CPF_2]`. O mesmo marcador é sempre a mesma pessoa ou empresa. Use o marcador "
        "exatamente como está (com colchetes e número) sempre que se referir a ela: a peça será revertida depois.",
        "2. **Marcadores diferentes podem ser a mesma pessoa.** Consulte o `ELENCO.md`: a seção \"Pode ser a mesma "
        "pessoa\" lista o marcador que é só um prenome ou sobrenome de outro; a seção de vínculos lista empresas "
        "ligadas. Não junte nem separe pessoas por conta própria: se a ligação for essencial, diga que é uma "
        "suposição.",
        "3. **Não deduza parentesco nem papel que o texto não diz.** Não chame alguém de genro, companheiro, cônjuge, "
        "sócio de fato, devedor ou laranja se os autos não disserem isso com essas palavras. Papel processual: use o "
        "que os autos dizem (requerida, executada, terceiro).",
        "4. **Preserve a força da prova.** Se os autos falam em indícios, não escreva \"comprovação\"; se falam em "
        "tentativa, não escreva consumação (e vice-versa). Datas, valores e números de evento estão em claro: cite-os "
        "com a coordenada (Evento, documento, página) do `INDICE.md`.",
        "5. **O lote pode ser parcial.** Não afirme o que não está aqui (\"os autos não contêm\", \"jamais juntou\"): "
        "diga \"nos documentos deste lote\".",
    ]
    if alerts.missing_events:
        events = sorted({event for items in alerts.missing_events.values() for event in items})
        lines.append(
            f"6. **Eventos citados e ausentes deste lote:** {', '.join(map(str, events))}. Não presuma o conteúdo deles."
        )
    if alerts.secrecy:
        pages = sorted({page for items in alerts.secrecy.values() for page, _label in items})
        lines.append(
            f"7. **Sigilo.** Há marca de sigilo nas folhas {', '.join(map(str, pages[:40]))}"
            f"{'...' if len(pages) > 40 else ''}. Não transcreva trecho dessas folhas sem avisar que é sigiloso."
        )
    lines += ["", "Datas, valores, números de evento, órgãos, juízes e servidores ficaram em claro de propósito."]
    path = Path(export_dir) / INSTRUCTIONS_NAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
