from __future__ import annotations

import unittest
from pathlib import Path

from anonimizador_app.audit import audit_markdown
from anonimizador_app.coverage import Coverage, parse_coverage, unread_pages
from anonimizador_app.pipeline import FileOutcome, build_job_result


def _pages(count: int) -> str:
    return "".join(f"<!-- pagina-origem: {number} -->\nTexto da folha {number}.\n\n" for number in range(1, count + 1))


class CoverageCommentTests(unittest.TestCase):
    def test_comment_round_trip(self) -> None:
        coverage = Coverage(pages=5, native=2, scanned=1, vector=1, broken_font=0, blank=0, unread=(5,))
        self.assertEqual(parse_coverage(coverage.comment() + "\n" + _pages(5)), coverage)

    def test_markdown_without_comment_is_legacy(self) -> None:
        self.assertIsNone(parse_coverage(_pages(2)))
        self.assertEqual(unread_pages(_pages(2)), ())


class AuditCoverageTests(unittest.TestCase):
    def test_audit_reports_where_each_page_came_from_and_unread_alert(self) -> None:
        coverage = Coverage(pages=5, native=2, scanned=1, vector=1, unread=(5,))
        notes = audit_markdown(coverage.comment() + "\n" + _pages(5))
        joined = "\n".join(notes)
        self.assertIn("Cobertura: 5 folha(s); 2 com texto nativo; 2 por OCR", joined)
        self.assertIn("ALERTA: 1 folha(s) nao lida(s): 5.", joined)
        self.assertNotIn("ancora(s) para", joined)

    def test_audit_alerts_anchor_count_different_from_pages(self) -> None:
        coverage = Coverage(pages=3, native=3)
        notes = audit_markdown(coverage.comment() + "\n" + _pages(2))
        self.assertTrue(any("2 ancora(s) para 3 folha(s)" in note for note in notes))


class JobBlockTests(unittest.TestCase):
    def test_unread_pages_block_export_with_listing(self) -> None:
        outcome = FileOutcome(source=Path("autos_PARTE_2.pdf"), ok=True, unread_pages=(5, 6))
        job = build_job_result(
            job_id="lote_x",
            export_dir=Path("x"),
            map_path=Path("x/mapa.json"),
            outcomes=(outcome,),
            record_count=0,
            contextual_used=True,
        )
        self.assertFalse(job.export_allowed)
        self.assertIn("autos_PARTE_2.pdf: páginas 5, 6", job.export_block_reason)

    def test_full_coverage_keeps_previous_rules(self) -> None:
        outcome = FileOutcome(source=Path("autos.pdf"), ok=True)
        job = build_job_result(
            job_id="lote_y",
            export_dir=Path("y"),
            map_path=Path("y/mapa.json"),
            outcomes=(outcome,),
            record_count=0,
            contextual_used=True,
        )
        self.assertTrue(job.export_allowed)


if __name__ == "__main__":
    unittest.main()
