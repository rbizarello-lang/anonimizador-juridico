"""H9-g, item 7: versao do cache de extracao so para .docx (o marcador de imagem vale em docx ja extraido).

Arquivos inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.extraction_cache import cache_key, docx_aware_version, load_or_extract
from anonimizador_app.fast_extract import FAST_DOCX_CACHE_SUFFIX, FAST_EXTRACTOR_VERSION

HAS_DOCX = importlib.util.find_spec("docx") is not None
OLD = "TEXTO ANTIGO DO CACHE ANTERIOR\n"


def _plant(cache: Path, data: bytes, version: str) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    key = cache_key(data, version)
    (cache / f"{key}.md").write_text(OLD, encoding="utf-8")
    (cache / f"{key}.json").write_text(json.dumps({"versao": version, "extrator": "antigo", "paginas": 1}), encoding="utf-8")


class VersaoDoCacheDocxTests(unittest.TestCase):
    def test_sufixo_so_para_docx_do_leitor_rapido(self) -> None:
        self.assertEqual(docx_aware_version("a.docx", FAST_EXTRACTOR_VERSION), FAST_EXTRACTOR_VERSION + FAST_DOCX_CACHE_SUFFIX)
        self.assertEqual(docx_aware_version("A.DOCX", FAST_EXTRACTOR_VERSION), FAST_EXTRACTOR_VERSION + FAST_DOCX_CACHE_SUFFIX)
        self.assertEqual(docx_aware_version("a.pdf", FAST_EXTRACTOR_VERSION), FAST_EXTRACTOR_VERSION)
        self.assertEqual(docx_aware_version("a.docx", "outro-motor"), "outro-motor")
        self.assertTrue(FAST_EXTRACTOR_VERSION.endswith("|15"))  # a versao dos PDFs nao mudou

    @unittest.skipUnless(HAS_DOCX, "python-docx nao instalado")
    def test_docx_ja_extraido_e_reextraido_e_o_pdf_nao(self) -> None:
        import pymupdf
        from docx import Document

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            docx = root / "peca.docx"
            document = Document()
            document.add_paragraph("Peticao da parte, texto ficticio.")
            document.save(docx)
            pdf = root / "autos.pdf"
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), "Peticao da Uniao, texto ficticio.")
                doc.save(pdf)
            # Cache do docx na chave ANTIGA (so a versao do extrator, sem sufixo) e cache do PDF na chave de sempre.
            _plant(cache, docx.read_bytes(), FAST_EXTRACTOR_VERSION)
            _plant(cache, pdf.read_bytes(), FAST_EXTRACTOR_VERSION)

            novo = load_or_extract(docx, cache_dir=cache)
            self.assertFalse(novo.cached)
            self.assertIn("Peticao da parte", novo.document.markdown)
            novo_json = json.loads((cache / f"{novo.digest}.json").read_text(encoding="utf-8"))
            self.assertTrue(novo_json["versao"].endswith(FAST_DOCX_CACHE_SUFFIX))
            self.assertTrue(load_or_extract(docx, cache_dir=cache).cached)  # a segunda vez ja vem do cache novo

            antigo = load_or_extract(pdf, cache_dir=cache)
            self.assertTrue(antigo.cached)
            self.assertEqual(antigo.document.markdown, OLD)


if __name__ == "__main__":
    unittest.main()
