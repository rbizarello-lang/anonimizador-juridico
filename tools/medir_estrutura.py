"""Mede a estrutura de um lote contra os PDFs originais. So contagens: nada de PII na saida.

Uso:
    python tools/medir_estrutura.py <pasta_dos_pdfs> <pasta_do_lote_exportavel>

Por arquivo, compara tres camadas: o PDF (PyMuPDF), o texto extraido pelo app (cache
privado, antes da troca) e o Markdown exportavel (depois da troca):

- folhas x ancoras; cobertura (nativas / OCR / em branco / nao lidas);
- extracao: folha nativa cujo texto no cache perdeu mais de 10% das palavras do PDF;
- valores em R$, numeros decimais (colunas de valor sem "R$") e datas de folha nativa: quantos
  sumiram na extracao e quantos sumiram na troca (token que comeu valor/data; folha com
  [NASCIMENTO_n] nao conta);
- tokens colados em numero ("[X_n],12"; digito colado a letra, "C1.[X_n]", nao conta) e token
  no cabecalho de tabela (rotulo trocado);
- titulos so com rotulo ("## Evento:") e "## Evento N" partindo frase;
- folhas de separacao/capa do eproc e rotulos delas sem o valor na mesma linha (a IA teria
  de adivinhar os pares);
- folhas de relatorio (extrato SISBAJUD, lista do RENAJUD, capa do processo; fila 3.x E12):
  rotulos sem o valor na linha e valores em R$ sozinhos na linha (sem o rotulo da coluna).

E a regua da fase "estrutura primeiro" (GOVERNANCA, fila 3.x): perda de informacao
aparece aqui antes de chegar a IA de analise.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.coverage import parse_coverage  # noqa: E402
from anonimizador_app.watermark import without_watermark  # noqa: E402

CACHE = ROOT / "saidas" / "privados" / "cache_extracao"
ANCHOR = re.compile(r"<!-- pagina-origem: (\d+) -->")
TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")
STAMP = re.compile(r"Processo\s+[\d.\-/A-Z]+,\s*Evento\s+\d+\s*,\s*\S+\s*,\s*P.gina\s+\d+", re.I)
MONEY = re.compile(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}")
# Numero decimal sem "R$" (colunas de valor da CDA, do SISBAJUD, do CNIS).
DECIMAL = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})*,\d{2}(?![\d,])")
DATE = re.compile(r"(?<!\d)\d{2}/\d{2}/\d{4}(?!\d)")
# Digito de palavra antes do token nao e numero: "C1.[RG_n]" e OCR de "CI." (Cedula de
# Identidade) antes do rotulo "RG", nada comido.
TOKEN_ON_DIGIT = re.compile(r"\[[A-Z_]+_\d+\][.,/]?\d|(?<![^\W\d_])\d[.,/-]?\[[A-Z_]+_\d+\]")
LABEL_HEADING = re.compile(r"^#+ [^\n]{1,25}:\s*$", re.M)
SPLIT_EVENT = re.compile(r"\n\n## Evento \d+\n\n[,a-zà-ú]")
EPROC_LABEL_ALONE = re.compile(r"(?m)^[^:\n]{1,40}:[ \t]*$")
CDA_BOX_LABEL_ALONE = re.compile(
    r"(?m)^(?:Processo Administrativo|Valor Total Inscrito Originária|Valor Total Inscrito em|"
    r"UFIR \(Lei 8383/91\)|Nº do agrupamento de inscrições|Página)[ \t]*$"
)
# Folhas de relatorio com rotulo e valor (fila 3.x E12): extrato SISBAJUD, lista do RENAJUD e
# capa do processo, reconhecidas pelo rotulo fixo.
FORM_PAGE_MARKS = ("Juiz solicitante do bloqueio:", "Lista de Veículos", "Chave Processo:")
# Rotulo ("Numero do processo:", "Ordem sigilosa?") sozinho na linha; a linha de tabela, de
# titulo e de bloco alinhado nao conta.
FORM_LABEL_ALONE = re.compile(r"(?m)^(?![|#<`])[^:?\n]{1,60}[:?][ \t]*$")
FORM_MONEY_ALONE = re.compile(r"(?m)^[ \t]*R\$[ \t]*\d{1,3}(?:\.\d{3})*,\d{2}[ \t]*$")


def form_page_stats(pages: dict[int, str]) -> collections.Counter:
    """Folhas de relatorio, rotulos sem valor e valores R$ sem rotulo, por folha do Markdown."""

    stats: collections.Counter = collections.Counter()
    for page in pages.values():
        if not any(mark in page for mark in FORM_PAGE_MARKS):
            continue
        stats["folhas_relatorio"] += 1
        stats["rotulos_relatorio_sem_valor"] += len(FORM_LABEL_ALONE.findall(page))
        stats["valores_relatorio_sem_rotulo"] += len(FORM_MONEY_ALONE.findall(page))
    return stats


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _words(text: str) -> collections.Counter:
    return collections.Counter(re.findall(r"[a-z0-9]{3,}", _fold(text)))


def _pages(markdown: str) -> dict[int, str]:
    parts = ANCHOR.split(markdown)
    pages: dict[int, str] = collections.defaultdict(str)
    for index in range(1, len(parts), 2):
        pages[int(parts[index])] += parts[index + 1]
    return pages


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _cache_for(pdf: Path) -> str | None:
    best = None
    for meta_path in CACHE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if Path(str(meta.get("caminho_original", ""))).name.casefold() != pdf.name.casefold():
            continue
        if not str(meta.get("versao", "")).startswith("pymupdf"):
            continue
        if best is None or str(meta.get("data", "")) > best[0]:
            best = (str(meta.get("data", "")), meta_path.with_suffix(".md"))
    return best[1].read_text(encoding="utf-8") if best and best[1].is_file() else None


def measure(pdf: Path, exported: Path) -> dict:
    import pymupdf

    markdown = exported.read_text(encoding="utf-8")
    cache = _cache_for(pdf)
    out_pages = _pages(markdown)
    cache_pages = _pages(cache) if cache else {}
    coverage = parse_coverage(markdown)
    result = collections.Counter()
    weak_pages: list[int] = []
    with pymupdf.open(pdf) as document:
        result["folhas"] = document.page_count
        for number, page in enumerate(document, start=1):
            # Sem o selo do editor de PDF (E10): o app o tira, e a palavra dele nao e conteudo.
            native = STAMP.sub(" ", without_watermark(page).get_text("text"))
            if len("".join(native.split())) < 150:
                continue
            result["folhas_nativas"] += 1
            if cache and "<!-- ocr: fonte-quebrada" not in cache_pages.get(number, ""):
                # Folha de fonte quebrada: a camada nativa e lixo e o OCR a substitui de proposito.
                native_words = _words(native)
                kept = sum((native_words & _words(cache_pages.get(number, ""))).values())
                if kept / max(1, sum(native_words.values())) < 0.9:
                    weak_pages.append(number)
            exported_page = _compact(out_pages.get(number, ""))
            cached_page = _compact(cache_pages.get(number, "")) if cache else None
            birth_token = "[NASCIMENTO_" in out_pages.get(number, "")
            for kind, pattern in (("valores", MONEY), ("decimais", DECIMAL), ("datas", DATE)):
                for value in set(pattern.findall(native)):
                    value = _compact(value)
                    result[f"{kind}_no_pdf"] += 1
                    if cached_page is not None and value not in cached_page:
                        result[f"{kind}_perdidos_na_extracao"] += 1
                    elif value not in exported_page and not (kind == "datas" and birth_token):
                        result[f"{kind}_perdidos_na_troca"] += 1
    result["ancoras"] = len(ANCHOR.findall(markdown))
    result["tokens_colados_em_numero"] = len(TOKEN_ON_DIGIT.findall(markdown))
    result["tokens_em_cabecalho_de_tabela"] = _header_tokens(markdown)
    result["titulos_so_rotulo"] = len(LABEL_HEADING.findall(markdown))
    result["evento_partindo_frase"] = len(SPLIT_EVENT.findall(markdown))
    separators = [
        page for page in out_pages.values()
        if "Tipo documento:" in page or "PÁGINA DE SEPARAÇÃO" in page
    ]
    result["folhas_separacao_eproc"] = len(separators)
    result["rotulos_eproc_sem_valor"] = sum(len(EPROC_LABEL_ALONE.findall(page)) for page in separators)
    result.update(form_page_stats(out_pages))
    cda_boxes = [page for page in out_pages.values() if "Valor Total Inscrito" in page]
    result["folhas_quadro_cda"] = len(cda_boxes)
    result["rotulos_cda_sem_valor"] = sum(len(CDA_BOX_LABEL_ALONE.findall(page)) for page in cda_boxes)
    if coverage is not None:
        result["nao_lidas"] = len(coverage.unread)
        result["ocr"] = coverage.ocr
    result["folhas_extracao_fraca"] = len(weak_pages)
    return {"contagens": dict(result), "folhas_extracao_fraca": weak_pages[:15], "cache": bool(cache)}


def _header_tokens(markdown: str) -> int:
    lines = markdown.splitlines()
    count = 0
    for index, line in enumerate(lines[:-1]):
        if line.startswith("|") and lines[index + 1].startswith("|---"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            count += sum(1 for cell in cells if TOKEN.fullmatch(cell))
    return count


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    pdf_dir, lote = Path(argv[1]), Path(argv[2])
    totals = collections.Counter()
    for exported in sorted(lote.glob("*.md")):
        candidates = [pdf_dir / f"{exported.stem}{suffix}" for suffix in (".pdf", ".PDF")]
        pdf = next((path for path in candidates if path.is_file()), None)
        if pdf is None:
            continue
        report = measure(pdf, exported)
        counts = report["contagens"]
        totals.update(counts)
        label = exported.stem if len(exported.stem) <= 40 else exported.stem[:18] + "…" + exported.stem[-12:]
        print(f"{label}: {counts}")
        if report["folhas_extracao_fraca"]:
            print(f"    folhas com extracao fraca (amostra): {report['folhas_extracao_fraca']}")
        if not report["cache"]:
            print("    (sem cache privado: preservacao da extracao nao medida)")
    print(f"TOTAL: {dict(totals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
