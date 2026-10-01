from __future__ import annotations

import unittest

from anonimizador_app.address_heuristic import AddressHeuristicEngine, RECOGNIZER
from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.settings import AppSettings
from anonimizador_juridico.detector import source_rank
from anonimizador_juridico.types import EntitySpan


SYNTHETIC = """\
## Capa

A sede fica na Rua Ari Jose de Oliveira Meireles nº 563, Vila Aurora.
Filial em Rua Ari Jose de Oliveira Meireles, nº 579, Parque Industrial.

Logradouro

RUA ANSELMO ROCHA PENTEADO

Número

150

Complemento

CASA 17

Residente à Rua Ernesto Garbelotto, 54, Jardim Universitário.
AV. Juscelino Kubitschek, 3631 – APTO.03, Vila Ipiranga.
domiciliado à Rua Raposo Tavares n°
281, Apto 202, Centro.
à Rua Santos, 1159 .
estabelecida à Rua Brasil, 1331 – 1º Andar – Centro.
alterado para Av. Santos Dumont, 674 – CJ.
domiciliada em Londrina-PR., a Rua
Raposo Tavares n° 281, Centro.
na Avn. Santos Dumont. nº674. Id. Santa Rita.
à Rua Senador Pinheiro Lobo, 182 - sala 106.

## Negativos

Evento 12
R$ 1.234
14/06/1996
Lei 6.830
A Uniao requereu SISBAJUD.
"""


class AddressHeuristicTests(unittest.TestCase):
    def test_eight_streets_in_synthetic_markdown(self) -> None:
        engine = AddressHeuristicEngine()
        spans = engine.detect(SYNTHETIC)
        blob = " ".join(SYNTHETIC[span.start : span.end] for span in spans)
        self.assertTrue(all(span.recognizer == RECOGNIZER for span in spans))
        self.assertTrue(all(span.entity_type == "LOCATION" for span in spans))
        self.assertTrue(all(span.score == 0.90 for span in spans))
        self.assertIn("563", blob)
        self.assertIn("579", blob)
        self.assertIn("ANSELMO ROCHA PENTEADO", blob)
        self.assertIn("150", blob)
        self.assertIn("Ernesto Garbelotto", blob)
        self.assertIn("Juscelino Kubitschek", blob)
        self.assertIn("Raposo Tavares", blob)
        self.assertIn("281", blob)
        self.assertIn("Santos", blob)
        self.assertIn("1159", blob)
        self.assertIn("Brasil", blob)
        self.assertIn("1331", blob)
        self.assertIn("Santos Dumont", blob)
        self.assertIn("674", blob)
        self.assertIn("Pinheiro Lobo", blob)
        self.assertIn("182", blob)
        glued = "na Avn. Santos Dumont. nº674. Id. Santa Rita."
        glued_spans = AddressHeuristicEngine().detect(glued)
        self.assertTrue(any("674" in glued[span.start : span.end] for span in glued_spans))
        wrapped = "domiciliada em Londrina-PR., a Rua\nRaposo Tavares n° 281, Centro."
        wrapped_spans = AddressHeuristicEngine().detect(wrapped)
        wrapped_blob = " ".join(wrapped[span.start : span.end] for span in wrapped_spans)
        self.assertIn("Raposo", wrapped_blob)
        self.assertIn("281", wrapped_blob)

    def test_negatives_are_not_addresses(self) -> None:
        engine = AddressHeuristicEngine()
        spans = engine.detect(SYNTHETIC)
        values = [SYNTHETIC[span.start : span.end] for span in spans]
        joined = " | ".join(values)
        self.assertNotIn("Evento 12", joined)
        self.assertNotIn("1.234", joined)
        self.assertNotIn("14/06/1996", joined)
        self.assertNotIn("6.830", joined)
        self.assertFalse(any("Evento" in item for item in values))

    def test_notary_stamp_is_not_address(self) -> None:
        engine = AddressHeuristicEngine()
        text = "Av. Duqun de Coxias. B00/810. Terreo."
        self.assertEqual(engine.detect(text), [])
        engine = AddressHeuristicEngine()
        text = "Compareceu na Rua 12 da peticao. Evento 12."
        spans = engine.detect(text)
        self.assertEqual(spans, [])

    def test_same_rank_as_header(self) -> None:
        address = EntitySpan(0, 12, "LOCATION", 0.90, RECOGNIZER)
        header = EntitySpan(0, 12, "LOCATION", 0.88, "HeaderHeuristic")
        self.assertEqual(source_rank(address), source_rank(header))
        self.assertEqual(source_rank(address), 300)

    def test_wired_after_target_list(self) -> None:
        settings = AppSettings(local_ner_enabled=False)
        engine = build_contextual_engine(settings)
        self.assertIsNotNone(engine)
        providers = [getattr(inner, "provider", "") for inner in getattr(engine, "engines", ())]
        self.assertIn("address", providers)
        self.assertLess(providers.index("header"), providers.index("address"))

    def test_bare_r_needs_a_word(self) -> None:
        # "R" sem ponto so abrevia Rua antes de palavra: cabecalho espacado e celula de CDA.
        for text in (
            "R E G I S T R O    D E    I M Ó V E I S    D A    8°    C I R C U N S C R I Ç Ã O",
            "         1475   R D ATIVA -MULTA SOBRE ALGO- LEI NA               242,12      48,42",
        ):
            with self.subTest(text=text):
                self.assertEqual(AddressHeuristicEngine().detect(text), [])
        text = "Sede na R Marechal Deodoro, 500 - Centro."
        values = [text[span.start : span.end] for span in AddressHeuristicEngine().detect(text)]
        self.assertEqual(values, ["R Marechal Deodoro, 500"])

    def test_house_number_with_thousands_is_whole(self) -> None:
        text = "Cartorio na Av. Pres. Getulio Vargas, 2.350 - Cj. 3 a 9 - Centro."
        values = [text[span.start : span.end] for span in AddressHeuristicEngine().detect(text)]
        self.assertEqual(values, ["Av. Pres. Getulio Vargas, 2.350"])

    def test_street_name_does_not_cross_table_cell(self) -> None:
        text = "| Rua das Flores | Centro 123 | 80.000-000 |"
        values = [text[span.start : span.end] for span in AddressHeuristicEngine().detect(text)]
        self.assertFalse(any("|" in value for value in values))

    def test_ave_abbreviation_is_a_street(self) -> None:
        text = "Sediada à AVE CAPITAO INDIO BANDEIRA, 2400, Centro."
        values = {
            text[span.start : span.end]
            for span in AddressHeuristicEngine().detect(text)
        }
        self.assertTrue(any("2400" in item and "CAPITAO" in item for item in values))


if __name__ == "__main__":
    unittest.main()
