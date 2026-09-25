from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico import StructuredDetector, resolve_overlaps
from anonimizador_app.gemini_detector import (
    is_kept_term,
    locate_mentions,
    parse_entity_payload,
    parse_residual_payload,
)
from anonimizador_app.pipeline import run_job


CORPUS = Path(__file__).parent / "corpus" / "caso_001_processo_sintetico.md"


class FakeGemini:
    provider = "gemini"

    def __init__(self, leftovers: list[str] | None = None) -> None:
        self.leftovers = leftovers or []

    def detect(self, text: str):
        return locate_mentions(
            text,
            [
                ("PERSON", "Helena Ficticia da Silva"),
                ("ORGANIZATION", "Horizonte Ficticio Ltda."),
            ],
        )

    def scan_residual(self, text: str) -> list[str]:
        return list(self.leftovers)


class GeminiParsingTests(unittest.TestCase):
    def test_parse_entities_and_ignore_keep_terms(self) -> None:
        raw = """
        {"entities":[
            {"type":"PERSON","text":"Helena Ficticia da Silva"},
            {"type":"ORGANIZATION","text":"Uniao"},
            {"type":"LOCATION","text":"Rua das Acacias 100"}
        ]}
        """
        mentions = parse_entity_payload(raw)
        self.assertIn(("PERSON", "Helena Ficticia da Silva"), mentions)
        text = "Helena Ficticia da Silva e a Uniao na Rua das Acacias 100."
        spans = locate_mentions(text, mentions + [("ORGANIZATION", "Uniao")])
        values = [span.value_from(text) for span in spans]
        self.assertIn("Helena Ficticia da Silva", values)
        self.assertIn("Rua das Acacias 100", values)
        self.assertNotIn("Uniao", values)

    def test_kept_institutional_terms(self) -> None:
        self.assertTrue(is_kept_term("Uniao"))
        self.assertTrue(is_kept_term("Fazenda Nacional"))
        self.assertTrue(is_kept_term("SISBAJUD"))
        self.assertTrue(is_kept_term("Ministério Público Federal"))
        self.assertTrue(is_kept_term("Caixa Econômica Federal"))
        self.assertTrue(is_kept_term("Banco do Brasil S.A."))
        self.assertTrue(is_kept_term("Defensoria Pública da União"))
        self.assertTrue(is_kept_term("SNIPER"))
        self.assertTrue(is_kept_term("CENSEC"))
        self.assertTrue(is_kept_term("CCS-Bacen"))
        self.assertTrue(is_kept_term("SIMBA"))
        self.assertTrue(is_kept_term("SERASAJUD"))
        self.assertTrue(is_kept_term("AGU"))
        self.assertTrue(is_kept_term("PGF"))
        self.assertTrue(is_kept_term("PGE"))
        self.assertTrue(is_kept_term("PGM"))
        self.assertTrue(is_kept_term("Advocacia-Geral da União"))
        self.assertTrue(is_kept_term("Procuradoria-Geral Federal"))
        self.assertFalse(is_kept_term("Serasa"))
        self.assertFalse(is_kept_term("Serasa Experian"))
        self.assertFalse(is_kept_term("Helena Ficticia da Silva"))
        self.assertFalse(is_kept_term("Advogado João Silva"))
        self.assertFalse(is_kept_term("Executado Horizonte Ltda"))

    def test_gemini_engine_is_removed(self) -> None:
        import anonimizador_app.gemini_detector as module

        self.assertFalse(hasattr(module, "GeminiEngine"))

    def test_structured_spans_win_over_gemini(self) -> None:
        text = "CPF 529.982.247-25"
        structured = StructuredDetector().detect(text)
        gemini = locate_mentions(text, [("PERSON", "CPF 529.982.247-25")])
        merged = resolve_overlaps([*structured, *gemini])
        self.assertEqual([span.entity_type for span in merged], ["BR_CPF"])

    def test_residual_ignores_tokens(self) -> None:
        leftovers = parse_residual_payload(
            '{"leftovers":["[PESSOA_1]","Helena Ficticia da Silva","STF"]}'
        )
        self.assertEqual(leftovers, ["Helena Ficticia da Silva"])


class GeminiPipelineTests(unittest.TestCase):
    def test_gemini_anonymizes_names_and_allows_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = run_job(
                [CORPUS],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_gemini",
                contextual=FakeGemini(),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
            self.assertIn("[PESSOA_1]", markdown)
            self.assertIn("[EMPRESA_1]", markdown)
            self.assertIn("[CPF_1]", markdown)
            self.assertNotIn("Helena Ficticia da Silva", markdown)
            self.assertNotIn("Horizonte Ficticio Ltda.", markdown)
            self.assertIn("SISBAJUD", markdown)
            self.assertIn("Uniao", markdown)
            self.assertTrue(job.export_allowed)
            self.assertFalse(job.gemini_used)

    def test_residual_hit_warns_but_does_not_force_more_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = run_job(
                [CORPUS],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_residual",
                contextual=FakeGemini(leftovers=["Joao Ainda Visivel"]),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
            self.assertTrue(job.export_allowed)
            self.assertIn("Joao Ainda Visivel", job.residual_hits)
            self.assertIn("não foram substituídos", job.export_warning)
            self.assertNotIn("[PESSOA_2]", markdown)


if __name__ == "__main__":
    unittest.main()
