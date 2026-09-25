from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.local_ner import LocalNerEngine
from anonimizador_app.pipeline import run_job
from anonimizador_app.settings import AppSettings


CORPUS = Path(__file__).parent / "corpus" / "caso_001_processo_sintetico.md"


def _fake_predict(text: str):
    hits = []
    if "Helena Ficticia da Silva" in text:
        hits.append(
            {"entity_group": "pessoa", "word": "Helena Ficticia da Silva", "score": 0.91}
        )
    if "Horizonte Ficticio Ltda." in text:
        hits.append(
            {
                "entity_group": "empresa privada",
                "word": "Horizonte Ficticio Ltda.",
                "score": 0.88,
            }
        )
    if "Uniao" in text:
        hits.append({"entity_group": "empresa privada", "word": "Uniao", "score": 0.9})
    if "Porto Alegre" in text:
        hits.append({"entity_group": "logradouro", "word": "Porto Alegre", "score": 0.9})
    return hits


class LocalNerTests(unittest.TestCase):
    def test_local_engine_tokenizes_names_and_keeps_legal_terms(self) -> None:
        engine = LocalNerEngine(predict=_fake_predict)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = run_job(
                [CORPUS],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_gliner",
                contextual=engine,
                settings=AppSettings(local_ner_enabled=True),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertTrue(job.export_allowed)
        self.assertTrue(job.contextual_used)
        self.assertFalse(job.gemini_used)
        self.assertIn("[PESSOA_1]", markdown)
        self.assertIn("[EMPRESA_1]", markdown)
        self.assertNotIn("Helena Ficticia da Silva", markdown)
        self.assertNotIn("Horizonte Ficticio Ltda.", markdown)
        self.assertIn("Uniao", markdown)
        self.assertIn("SISBAJUD", markdown)
        self.assertIn("14/05/2024", markdown)

    def test_generic_local_hits_are_dropped(self) -> None:
        engine = LocalNerEngine(predict=_fake_predict)
        text = "A Uniao atuou em Porto Alegre."
        spans = engine.detect(text)
        self.assertEqual(spans, [])

    def test_all_caps_eproc_mentions_are_located(self) -> None:
        def predict(text: str):
            hits = []
            if "MARINA FICTICIA LOPES" in text:
                hits.append(
                    {
                        "entity_group": "pessoa",
                        "word": "Marina Ficticia Lopes",
                        "score": 0.91,
                    }
                )
            if "NORTE INVENTADO S.A." in text:
                hits.append(
                    {
                        "entity_group": "empresa privada",
                        "word": "Norte Inventado S.A.",
                        "score": 0.88,
                    }
                )
            if "R GAMA 120" in text:
                hits.append(
                    {
                        "entity_group": "logradouro",
                        "word": "R GAMA 120",
                        "score": 0.86,
                    }
                )
            if "4a Vara Federal de Santa Maria" in text or "4A VARA FEDERAL" in text:
                hits.append(
                    {
                        "entity_group": "empresa privada",
                        "word": "4a Vara Federal de Santa Maria",
                        "score": 0.9,
                    }
                )
            if "Santa Maria" in text or "SANTA MARIA" in text:
                hits.append(
                    {"entity_group": "logradouro", "word": "Santa Maria", "score": 0.9}
                )
            return hits

        text = (
            "A UNIÃO, perante a 4a Vara Federal de Santa Maria, citou "
            "MARINA FICTICIA LOPES da NORTE INVENTADO S.A. em R GAMA 120."
        )
        engine = LocalNerEngine(predict=predict)
        spans = engine.detect(text)
        values = [span.value_from(text) for span in spans]
        self.assertIn("MARINA FICTICIA LOPES", values)
        self.assertIn("NORTE INVENTADO S.A.", values)
        self.assertIn("R GAMA 120", values)
        self.assertNotIn("Santa Maria", values)
        self.assertNotIn("SANTA MARIA", values)
        self.assertTrue(all("Vara Federal" not in item for item in values))
        self.assertIn("UNIÃO", text)

    def test_local_spans_use_ner_local_recognizer(self) -> None:
        engine = LocalNerEngine(predict=_fake_predict)
        text = "Helena Ficticia da Silva moveu a acao."
        spans = engine.detect(text)
        self.assertTrue(spans)
        self.assertTrue(all(span.recognizer == "NerLocal" for span in spans))
        self.assertNotIn("GeminiContextual", {span.recognizer for span in spans})


if __name__ == "__main__":
    unittest.main()
