from __future__ import annotations

import unittest

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.settings import AppSettings
from anonimizador_app.table_labels import TableLabelsEngine, plate_canonical
from anonimizador_juridico.detector import source_rank
from anonimizador_juridico.pseudonymizer import canonicalize
from anonimizador_juridico.types import EntitySpan

TABLE = """\
## Relação de veículos

| CNPJ/CPF Devedor   |   N.Renavam | Chassi            | UF/PLACA   | Município   |
|--------------------|-------------|-------------------|------------|-------------|
| 72.081.395/0001-05 |   411872205 | 9BXAB12C3DE456789 | PR/QWE4321 | LONDRINA    |
| 72.081.395/0001-05 |   422983116 | 9BXAB12C5DE456790 | PR/QWF5432 | LONDRINA    |
| 38.915.670/0001-00 |  1427094227 | 9UXYZ12A3BC098765 | PR/QAB1C23 | LONDRINA    |

<!-- pagina-origem: 59 -->
"""

STACKED = """\
DETRANPR
Renavam:
Chassi
Placa:
411872205
9BXAB12C3DE456789
PR/QWE4321
Marca:
FORD
"""

NEGATIVES = """\
Procurador(es): CARLOS TESTE PR900001 PAULO JOSE EXEMPLO JUNIOR PR900002
Pago em 14/06/1996 no Evento 12, valor R$ 9.473,86.
Placa PR/QAB1C23 no texto corrido sem tabela.
"""


class TableLabelsTests(unittest.TestCase):
    def test_table_header_emits_placa_renavam_chassi(self) -> None:
        engine = TableLabelsEngine()
        spans = engine.detect(TABLE)
        types = {span.entity_type for span in spans}
        self.assertEqual(types, {"BR_PLACA", "BR_RENAVAM", "BR_CHASSI"})
        values = {TABLE[span.start : span.end].strip() for span in spans}
        self.assertIn("PR/QWE4321", values)
        self.assertIn("PR/QAB1C23", values)
        self.assertIn("411872205", values)
        self.assertIn("1427094227", values)
        self.assertIn("9BXAB12C3DE456789", values)
        placas = [span for span in spans if span.entity_type == "BR_PLACA"]
        self.assertEqual({span.canonical_key for span in placas}, {"QWE4321", "QWF5432", "QAB1C23"})

    def test_plate_canonical_strips_uf(self) -> None:
        self.assertEqual(plate_canonical("PR/QWE4321"), "QWE4321")
        self.assertEqual(plate_canonical("QWE-4321"), "QWE4321")
        self.assertEqual(plate_canonical("QAB1C23"), "QAB1C23")
        self.assertEqual(canonicalize("BR_PLACA", "PR/QWE4321"), "QWE4321")

    def test_stacked_form_label_then_value(self) -> None:
        engine = TableLabelsEngine()
        spans = engine.detect(STACKED)
        values = {STACKED[span.start : span.end] for span in spans}
        self.assertIn("411872205", values)
        self.assertIn("9BXAB12C3DE456789", values)
        self.assertIn("PR/QWE4321", values)

    def test_inline_labeled_plate_without_uf_prefix(self) -> None:
        text = "Veiculo com placa QWE-4321 e outro com placa QAB1C23."
        spans = TableLabelsEngine().detect(text)
        values = {text[span.start : span.end] for span in spans}
        self.assertIn("QWE-4321", values)
        self.assertIn("QAB1C23", values)

    def test_uf_plate_only_in_table_not_running_text(self) -> None:
        engine = TableLabelsEngine()
        running = "Placa PR/QAB1C23 no DETRAN da peticao."
        types = {span.entity_type for span in engine.detect(running)}
        self.assertNotIn("BR_PLACA", types)
        self.assertTrue(
            any(
                span.entity_type == "BR_PLACA" and "PR/QAB1C23" in TABLE[span.start : span.end]
                for span in engine.detect(TABLE)
            )
        )

    def test_negatives_are_not_vehicle_ids(self) -> None:
        spans = TableLabelsEngine().detect(NEGATIVES)
        blob = " ".join(NEGATIVES[span.start : span.end] for span in spans)
        self.assertNotIn("PR900001", blob)
        self.assertNotIn("14/06/1996", blob)
        self.assertNotIn("9.473,86", blob)
        self.assertEqual(spans, [])

    def test_cep_column_header_tokenizes_cells(self) -> None:
        table = """\
| Nome | CEP | UF |
|------|-----|----|
| sede | 86287-124 | PR |
"""
        values = {
            table[span.start : span.end]
            for span in TableLabelsEngine().detect(table)
            if span.entity_type == "BR_CEP"
        }
        self.assertIn("86287-124", values)

    def test_disabled_tag_is_skipped(self) -> None:
        tags = {"BR_PLACA": False, "BR_RENAVAM": True, "BR_CHASSI": True}
        spans = TableLabelsEngine(enabled_tags=tags).detect(TABLE)
        self.assertNotIn("BR_PLACA", {span.entity_type for span in spans})
        self.assertIn("BR_RENAVAM", {span.entity_type for span in spans})

    def test_non_contextual_ids_rank_as_structured(self) -> None:
        span = EntitySpan(0, 7, "BR_PLACA", 0.88, "TableLabels")
        self.assertEqual(source_rank(span), 600)

    def test_wired_in_contextual_engine(self) -> None:
        settings = AppSettings(local_ner_enabled=False)
        engine = build_contextual_engine(settings)
        providers = [getattr(inner, "provider", "") for inner in getattr(engine, "engines", ())]
        self.assertIn("table_labels", providers)


if __name__ == "__main__":
    unittest.main()
