from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.extraction_cache import cache_key, load_or_extract
from anonimizador_app.pipeline import run_job


class ExtractionCacheTests(unittest.TestCase):
    def test_miss_then_hit_keeps_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "peca.md"
            source.write_text("CPF 529.982.247-25\nUniao e SISBAJUD.\n", encoding="utf-8")
            cache_dir = root / "cache"
            first = load_or_extract(
                source, cache_dir=cache_dir, use_cache=True, version_string="v-teste-1"
            )
            self.assertFalse(first.cached)
            key = cache_key(source.read_bytes(), "v-teste-1")
            self.assertTrue((cache_dir / f"{key}.md").is_file())
            meta = json.loads((cache_dir / f"{key}.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["versao"], "v-teste-1")
            self.assertEqual(meta["extrator"], "txt")
            second = load_or_extract(
                source, cache_dir=cache_dir, use_cache=True, version_string="v-teste-1"
            )
            self.assertTrue(second.cached)
            self.assertEqual(first.document.markdown, second.document.markdown)
            self.assertEqual(second.document.extractor, "txt")

    def test_default_extractor_is_pymupdf_by_page_with_coverage(self) -> None:
        import pymupdf

        from anonimizador_app.fast_extract import FAST_EXTRACTOR_VERSION

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "autos.pdf"
            with pymupdf.open() as document:
                document.new_page().insert_text((72, 72), "Peticao da Uniao, SISBAJUD e R$ 10,00.")
                document.save(source)
            cache_dir = root / "cache"
            first = load_or_extract(source, cache_dir=cache_dir)
            key = cache_key(source.read_bytes(), FAST_EXTRACTOR_VERSION)
            meta = json.loads((cache_dir / f"{key}.json").read_text(encoding="utf-8"))
        self.assertEqual(first.document.extractor, "pymupdf")
        self.assertEqual(meta["versao"], FAST_EXTRACTOR_VERSION)
        self.assertEqual(meta["cobertura"]["nao_lidas"], [])
        self.assertTrue(first.document.markdown.startswith("<!-- cobertura: paginas=1;"))

    def test_version_change_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "peca.md"
            source.write_text("Evento 1 — peticao da Uniao.\n", encoding="utf-8")
            cache_dir = root / "cache"
            load_or_extract(
                source, cache_dir=cache_dir, use_cache=True, version_string="motor-a"
            )
            changed = load_or_extract(
                source, cache_dir=cache_dir, use_cache=True, version_string="motor-b"
            )
            self.assertFalse(changed.cached)
            self.assertEqual(len(list(cache_dir.glob("*.md"))), 2)

    def test_run_job_audit_mentions_cache_on_second_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "peca.md"
            source.write_text("CPF 529.982.247-25 SISBAJUD Uniao\n", encoding="utf-8")
            export_root = root / "exportaveis"
            private_root = root / "privados"
            first = run_job(
                [source],
                export_root=export_root,
                private_root=private_root,
                job_id="lote_miss",
                use_cache=True,
            )
            audit_miss = (first.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            self.assertNotIn("Extração: cache", audit_miss)
            self.assertFalse(first.outcomes[0].extraction_cached)
            second = run_job(
                [source],
                export_root=export_root,
                private_root=private_root,
                job_id="lote_hit",
                use_cache=True,
            )
            audit_hit = (second.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            self.assertTrue(second.outcomes[0].extraction_cached)
            self.assertIn("Extração: cache", audit_hit)
            self.assertIn("txt", audit_hit)


if __name__ == "__main__":
    unittest.main()
