"""v0.4.12: avisos de incerteza para a IA leitora (decisao do Operador de 22/10, noite: o texto tokenizado tem de continuar
legivel e compreensivel; nada some em silencio).

Onde o motor casa com tolerancia um nome ou numero deturpado pelo OCR, ou recusa casar por ambiguidade (a grafia fica
perto de dois nomes da lista), a ocorrencia e registrada aqui durante a deteccao (offsets do texto) e, depois da troca,
vira uma linha do ``AVISOS.md`` exportavel: arquivo, folha, token e motivo. Nenhum dado real em claro: so o token, a
folha e o motivo. Os marcadores visiveis do OCR ("«[OCR incerto, fl. N: ...]»", "«[verso: ...]»", "«[imagem nao lida,
fl. N]»") sao contados por folha a partir do proprio texto exportado.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

NOTICES_NAME = "AVISOS.md"

FUZZY_NAME = "nome casado com tolerancia a erro de OCR"
FUZZY_NUMBER = "numero corrigido de erro de OCR (O->0, l/I->1, S->5)"
AMBIGUOUS = "grafia deturpada perto de 2+ nomes da lista: NAO casada com nenhum (fica em claro)"


@dataclass(frozen=True, slots=True)
class Pending:
    start: int
    end: int
    reason: str
    entity_type: str = ""
    canonical_key: str = ""
    # Para a recusa por ambiguidade: as entradas candidatas (tipo, chave), para dizer os tokens delas.
    candidates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Notice:
    document: str
    page: int
    token: str
    reason: str


_pending: dict[str, list[Pending]] = {}
_notices: list[Notice] = []


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()


def reset() -> None:
    _pending.clear()
    _notices.clear()


def record(text: str, item: Pending) -> None:
    bucket = _pending.setdefault(_key(text), [])
    if item not in bucket:
        bucket.append(item)


_PAGE = re.compile(r"<!--\s*pagina-origem:\s*(\d+)")


def page_at(text: str, position: int) -> int:
    page = 0
    for match in _PAGE.finditer(text, 0, position):
        page = int(match.group(1))
    return page


def collect(document: str, text: str, entities: Iterable[object], mapping: object) -> list[Notice]:
    """Depois da troca de um arquivo: as ocorrencias registradas que viraram token (ou a recusa) viram avisos."""

    entities = list(entities)
    pending = list(_pending.get(_key(text), []))
    for span in entities:
        if str(getattr(span, "recognizer", "")).startswith("OcrNumero"):
            pending.append(Pending(span.start, span.end, FUZZY_NUMBER, span.entity_type, span.canonical_key))
    if not pending:
        return []
    final = {(span.start, span.end): span for span in entities}
    found: list[Notice] = []
    for item in pending:
        page = page_at(text, item.start)
        if item.candidates:
            tokens = [mapping.token_for(kind, key) for kind, key in item.candidates]
            label = ", ".join(token for token in tokens if token) or "(nenhum token)"
            found.append(Notice(document, page, label, item.reason))
            continue
        span = final.get((item.start, item.end))
        if span is None:
            continue  # outra regra venceu no ponto (texto em claro ou outro token): nada a avisar
        token = mapping.token_for(span.entity_type, span.canonical_key or "") if span.canonical_key else ""
        if not token:
            from anonimizador_juridico.pseudonymizer import canonicalize

            token = mapping.token_for(span.entity_type, canonicalize(span.entity_type, text[span.start : span.end]))
        found.append(Notice(document, page, token or "(token)", item.reason))
    _notices.extend(found)
    return found


_OCR_MARKS = (
    (re.compile(r"«\[OCR incerto, fl\. (\d+|\?)"), "leitura incerta do OCR (texto marcado no lugar)"),
    (re.compile(r"«\[imagem não lida, fl\. (\d+|\?)\]»"), "imagem embutida NAO lida (conferir no original)"),
    (re.compile(r"«\[imagem sem texto legível, fl\. (\d+|\?)\]»"), "imagem embutida sem texto legivel"),
)
_MIRROR = re.compile(re.escape("«[verso: texto da frente vazado, omitido]»"))


def ocr_rows(document: str, exported: str) -> list[tuple[str, int, str, int]]:
    """(arquivo, folha, motivo, quantidade) dos marcadores visiveis do OCR no texto exportado."""

    counts: Counter[tuple[int, str]] = Counter()
    for pattern, label in _OCR_MARKS:
        for match in pattern.finditer(exported):
            page = int(match.group(1)) if match.group(1).isdigit() else _sheet_at(exported, match.start())
            counts[(page, label)] += 1
    for match in _MIRROR.finditer(exported):
        counts[(_sheet_at(exported, match.start()), "verso vazado omitido (texto da frente espelhado)")] += 1
    return [(document, page, label, count) for (page, label), count in sorted(counts.items())]


_SHEET = re.compile(r"\[fl\. (\d+)|<!--\s*pagina-origem:\s*(\d+)")


def _sheet_at(text: str, position: int) -> int:
    page = 0
    for match in _SHEET.finditer(text, 0, position):
        page = int(match.group(1) or match.group(2))
    return page


def write_notices(export_dir: Path, markdown_paths: Sequence[Path], notices: Sequence[Notice] | None = None) -> Path:
    """``AVISOS.md``: folha, token e motivo de cada casamento tolerante e de cada recusa por ambiguidade, e os trechos
    incertos do OCR por folha. So tokens, folhas e contagens."""

    notices = list(_notices if notices is None else notices)
    rows = [row for path in markdown_paths if Path(path).is_file() for row in ocr_rows(Path(path).name, Path(path).read_text(encoding="utf-8"))]
    lines = [
        "# Avisos de incerteza deste lote",
        "",
        "Trechos em que a leitura do PDF (OCR) ou o casamento de nomes e números é incerto. Toda conclusão que dependa",
        "de um destes trechos deve ser conferida no PDF original, na folha indicada. Nenhum dado real aparece aqui:",
        "só o arquivo, a folha, o marcador (token) e o motivo.",
        "",
        "## Casamentos tolerantes e recusas",
        "",
    ]
    if notices:
        lines += ["| Arquivo | Folha | Token | Motivo |", "|---|---|---|---|"]
        for notice in sorted(set(notices), key=lambda item: (item.document, item.page, item.token, item.reason)):
            lines.append(f"| {notice.document} | {notice.page or '?'} | {notice.token} | {notice.reason} |")
    else:
        lines.append("Nenhum casamento tolerante nem recusa por ambiguidade neste lote.")
    lines += ["", "## Trechos incertos do OCR (marcados no texto)", ""]
    if rows:
        lines += ["| Arquivo | Folha | Motivo | Quantidade |", "|---|---|---|---|"]
        lines += [f"| {doc} | {page or '?'} | {label} | {count} |" for doc, page, label, count in rows]
    else:
        lines.append("Nenhum trecho incerto marcado pelo OCR neste lote.")
    path = Path(export_dir) / NOTICES_NAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def summary_line(markdown_paths: Sequence[Path]) -> str:
    rows = [row for path in markdown_paths if Path(path).is_file() for row in ocr_rows(Path(path).name, Path(path).read_text(encoding="utf-8"))]
    marks = sum(row[3] for row in rows)
    return (
        f"Avisos de incerteza (AVISOS.md): {len(set(_notices))} casamento(s) tolerante(s) ou recusa(s); "
        f"{marks} trecho(s) incerto(s) do OCR marcados no texto."
    )
