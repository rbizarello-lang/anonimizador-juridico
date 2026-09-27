"""Mede a fidelidade do OCR do app sobre folhas NATIVAS do lote. So contagens: nada de PII.

Uso:
    python tools/medir_ocr.py <pasta_dos_pdfs> [--folhas 30]

A folha nativa tem a verdade na camada de texto. Aqui ela e renderizada como imagem, passa
pelo mesmo OCR que o app usa nas folhas escaneadas e vetoriais, e o resultado e comparado
com o texto nativo (carimbo do eproc fora):

- linha perdida: linha nativa com 3+ palavras de que menos de 60% aparece no OCR;
- palavras achadas: fracao das palavras nativas (sem acento, 3+ letras) presentes no OCR;
- acentuadas exatas: palavras nativas com acento lidas com o acento certo;
- segundos por folha.

A amostra pega folhas com mais de 800 caracteres, espalhadas pelos arquivos. E a regua do
item E4 da fila 3.x (meta: perda de linha <= 1%).
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.ocr_pages import _recognize_image, _render_page, page_ocr_reason  # noqa: E402

STAMP = re.compile(r"Processo\s+\S+,\s*Evento\s+\d+\s*,\s*\S+\s*,\s*P.gina\s+\d+", re.I)


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char)).casefold()
    return re.findall(r"[a-z0-9]{3,}", folded)


def _accented(text: str) -> list[str]:
    return [word for word in re.findall(r"\w+", text.lower()) if len(word) >= 3 and not word.isascii()]


def _sample(pdf_dir: Path, limit: int) -> list[tuple[Path, int]]:
    import pymupdf

    candidates: list[tuple[Path, int]] = []
    for pdf in sorted(path for path in pdf_dir.iterdir() if path.suffix.lower() == ".pdf"):
        with pymupdf.open(pdf) as document:
            for number, page in enumerate(document, start=1):
                native = page.get_text("text").strip()
                if len(STAMP.sub(" ", native)) > 800 and page_ocr_reason(page, native) is None:
                    candidates.append((pdf, number))
    if len(candidates) <= limit:
        return candidates
    step = len(candidates) / limit
    return [candidates[int(index * step)] for index in range(limit)]


def measure(pdf_dir: Path, limit: int) -> dict[str, float]:
    import pymupdf

    totals: collections.Counter[str] = collections.Counter()
    started = time.perf_counter()
    for pdf, number in _sample(pdf_dir, limit):
        with pymupdf.open(pdf) as document:
            page = document[number - 1]
            native = STAMP.sub(" ", page.get_text("text"))
            text = _recognize_image(_render_page(page))
        got = collections.Counter(_words(text))
        truth = collections.Counter(_words(native))
        totals["palavras"] += sum(truth.values())
        totals["palavras_achadas"] += sum((truth & got).values())
        for line in native.splitlines():
            words = _words(line)
            if len(words) < 3:
                continue
            totals["linhas"] += 1
            if sum(1 for word in words if got[word]) / len(words) < 0.6:
                totals["linhas_perdidas"] += 1
        read = collections.Counter(re.findall(r"\w+", text.lower()))
        accented = _accented(native)
        totals["acentuadas"] += len(accented)
        totals["acentuadas_exatas"] += sum(1 for word in accented if read[word])
        totals["folhas"] += 1
    elapsed = time.perf_counter() - started
    folhas = max(1, totals["folhas"])
    return {
        "folhas": totals["folhas"],
        "linhas": totals["linhas"],
        "linhas_perdidas": totals["linhas_perdidas"],
        "perda_de_linha_pct": round(100 * totals["linhas_perdidas"] / max(1, totals["linhas"]), 2),
        "palavras_achadas_pct": round(100 * totals["palavras_achadas"] / max(1, totals["palavras"]), 2),
        "acentuadas_exatas_pct": round(100 * totals["acentuadas_exatas"] / max(1, totals["acentuadas"]), 2),
        "segundos_por_folha": round(elapsed / folhas, 1),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs")
    parser.add_argument("--folhas", type=int, default=30)
    args = parser.parse_args(argv)
    print(measure(Path(args.pdfs), args.folhas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
