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
| 10.540.704/0001-00 |   326627103 | 9BFZC52P8CB908264 | PR/ATY8599 | LONDRINA    |
| 10.540.704/0001-00 |   331597063 | 9BFZC52P6CB909543 | PR/AUB9920 | LONDRINA    |
| 01.294.953/0001-72 |  1316111366 | 9UWSHX76ANN033722 | PR/SDS3A82 | LONDRINA    |

<!-- pagina-origem: 59 -->
"""

STACKED = """\
DETRANPR
Renavam:
Chassi
Placa:
326627103
9BFZC52P8CB908264
PR/ATY8599
Marca:
FORD
"""

NEGATIVES = """\
Procurador(es): CARLOS TESTE PR900001 PAULO JOSE EXEMPLO JUNIOR PR900002
Pago em 14/06/1996 no Evento 12, valor R$ 9.473,86.
Placa PR/SDS3A82 no texto corrido sem tabela.
"""


class TableLabelsTests(unittest.TestCase):
    def test_table_header_emits_placa_renavam_chassi(self) -> None:
        engine = TableLabelsEngine()
        spans = engine.detect(TABLE)
        types = {span.entity_type for span in spans}
        self.assertEqual(types, {"BR_PLACA", "BR_RENAVAM", "BR_CHASSI"})
        values = {TABLE[span.start : span.end].strip() for span in spans}
        self.assertIn("PR/ATY8599", values)
        self.assertIn("PR/SDS3A82", values)
        self.assertIn("326627103", values)
        self.assertIn("1316111366", values)
        self.assertIn("9BFZC52P8CB908264", values)
        placas = [span for span in spans if span.entity_type == "BR_PLACA"]
        self.assertEqual({span.canonical_key for span in placas}, {"ATY8599", "AUB9920", "SDS3A82"})

    def test_plate_canonical_strips_uf(self) -> None:
        self.assertEqual(plate_canonical("PR/ATY8599"), "ATY8599")
        self.assertEqual(plate_canonical("ATY-8599"), "ATY8599")
        self.assertEqual(plate_canonical("SDS3A82"), "SDS3A82")
        self.assertEqual(canonicalize("BR_PLACA", "PR/ATY8599"), "ATY8599")

    def test_stacked_form_label_then_value(self) -> None:
        engine = TableLabelsEngine()
        spans = engine.detect(STACKED)
        values = {STACKED[span.start : span.end] for span in spans}
        self.assertIn("326627103", values)
        self.assertIn("9BFZC52P8CB908264", values)
        self.assertIn("PR/ATY8599", values)

    def test_inline_labeled_plate_without_uf_prefix(self) -> None:
        text = "Veiculo com placa ATY-8599 e outro com placa SDS3A82."
        spans = TableLabelsEngine().detect(text)
        values = {text[span.start : span.end] for span in spans}
        self.assertIn("ATY-8599", values)
        self.assertIn("SDS3A82", values)

    def test_uf_plate_only_in_table_not_running_text(self) -> None:
        engine = TableLabelsEngine()
        running = "Placa PR/SDS3A82 no DETRAN da peticao."
        types = {span.entity_type for span in engine.detect(running)}
        self.assertNotIn("BR_PLACA", types)
        self.assertTrue(
            any(
                span.entity_type == "BR_PLACA" and "PR/SDS3A82" in TABLE[span.start : span.end]
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
| sede | 86032-010 | PR |
"""
        values = {
            table[span.start : span.end]
            for span in TableLabelsEngine().detect(table)
            if span.entity_type == "BR_CEP"
        }
        self.assertIn("86032-010", values)

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
