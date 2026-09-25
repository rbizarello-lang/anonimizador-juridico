from __future__ import annotations

import unittest

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.eproc_codes import EprocCodesEngine
from anonimizador_app.settings import AppSettings
from anonimizador_juridico.pseudonymizer import canonicalize


CAPA = """\
| UNIÃO - FAZENDA NACIONAL (00.394.460/0216-53) - Entidade ROGER DARLAN EICKHOFF BIZARELLO P3412557 | ALTMANN COMERCIO Procurador(es): DANILO MORAES PR077705 EDSON JOSE GIOCONDO JUNIOR PR077717 |
"""

EVENTS = """\
## Listagem dos Eventos

| 75 | 08/04/2026 | PETIÇÃO | PR077705 |
| 45 | 16/12/2025 | Confirmada a intimação | P3412557 |
| 26/08/2025 15:45:55 PFN2041305 - JULIANO FRANCO DIAS DOS REIS - PROCURADOR |
REsp 1.259.018
"""

PEOPLE = [
    "DANILO MORAES",
    "EDSON JOSE GIOCONDO JUNIOR",
    "ROGER DARLAN EICKHOFF BIZARELLO",
]


class EprocCodesTests(unittest.TestCase):
    def test_capa_and_event_column_share_token(self) -> None:
        text = CAPA + "\n" + EVENTS
        engine = EprocCodesEngine(people=PEOPLE)
        spans = engine.detect(text)
        codes = [text[span.start : span.end] for span in spans]
        self.assertIn("PR077705", codes)
        self.assertIn("PR077717", codes)
        self.assertIn("P3412557", codes)
        self.assertNotIn("PFN2041305", codes)
        self.assertTrue(all(span.entity_type == "BR_EPROC" for span in spans))
        self.assertEqual(canonicalize("BR_EPROC", "PR077705"), "PR077705")
        self.assertGreaterEqual(codes.count("PR077705"), 2)
        self.assertGreaterEqual(codes.count("P3412557"), 2)

    def test_pfn_code_stays_clear(self) -> None:
        text = (
            "Evento: 26/08/2025 15:45:55 PFN2041305 - JULIANO FRANCO DIAS DOS REIS "
            "- PROCURADOR DA FAZENDA NACIONAL"
        )
        spans = EprocCodesEngine(people=PEOPLE).detect(text)
        blob = " ".join(text[span.start : span.end] for span in spans)
        self.assertNotIn("PFN2041305", blob)
        self.assertEqual(spans, [])

    def test_resp_is_not_eproc(self) -> None:
        text = "Procurador(es): DANILO MORAES REsp 1.259.018 no STJ."
        spans = EprocCodesEngine(people=PEOPLE).detect(text)
        self.assertEqual(spans, [])

    def test_without_target_people_still_reads_capa_codes(self) -> None:
        text = CAPA
        codes = [text[span.start : span.end] for span in EprocCodesEngine(people=[]).detect(text)]
        self.assertIn("PR077705", codes)
        self.assertIn("PR077717", codes)
        self.assertNotIn("PFN2041305", codes)

    def test_wired_without_people(self) -> None:
        settings = AppSettings(local_ner_enabled=False, target_people=[])
        engine = build_contextual_engine(settings)
        providers = [getattr(inner, "provider", "") for inner in getattr(engine, "engines", ())]
        self.assertIn("eproc_codes", providers)

    def test_wired_when_people_in_settings(self) -> None:
        settings = AppSettings(
            local_ner_enabled=False,
            target_people=["DANILO MORAES"],
        )
        engine = build_contextual_engine(settings)
        providers = [getattr(inner, "provider", "") for inner in getattr(engine, "engines", ())]
        self.assertIn("eproc_codes", providers)


if __name__ == "__main__":
    unittest.main()
