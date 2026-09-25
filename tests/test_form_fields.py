from __future__ import annotations

import unittest

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.form_fields import FormFieldsEngine, RECOGNIZER
from anonimizador_app.settings import AppSettings
from anonimizador_app.target_list import TargetListEngine
from anonimizador_juridico import resolve_overlaps
from anonimizador_juridico.detector import source_rank
from anonimizador_juridico.pseudonymizer import canonicalize


RECEITA = """\
Nome

CAROLINE ALTMANN TENORIO BROGGI

Data de Nascimento

26/04/1980

Data de Inscrição 12/03/1998 Naturalidade ARAPONGAS Título de Eleitor 73750110655

Nome da Mãe

HILDA ALTMANN TENORIO

Sexo

Feminino

Título de Eleitor 64053650620 Nacionalidade

Título de Eleitor 114864520671
"""

NASCIDO = """\
MARCELO OBDULO BROGGI, brasileiro, nascido em dde
31/03/1978, do comércio.
REINALDO LUIZ BROGGI, solteiro. nascido em data de 25/12/1779, do comércio.
"""

NEGATIVES = """\
julgado em 24/09/2018 no DJe.
Data do contrato: 20/05/2014.
Protocolo: 221059318 DE 18/02/2022.
Data de Inscrição 12/03/1998.
Data de Início de Atividade 01/12/2008.
Evento 12 em 14/06/1996, valor R$ 9.473,86.
Data de emissão 01/02/2020.
Título de Eleitor 111111111111.
NIRE 4120352196.
Nome da Mãe
Não informado
Nome do Pai
-
Sexo
Feminino
"""


class FormFieldsTests(unittest.TestCase):
    def test_receita_nascimento_titulo_mae(self) -> None:
        engine = FormFieldsEngine()
        spans = engine.detect(RECEITA)
        by_type: dict[str, list[str]] = {}
        for span in spans:
            by_type.setdefault(span.entity_type, []).append(RECEITA[span.start : span.end])
        self.assertIn("26/04/1980", by_type.get("BR_NASCIMENTO", []))
        self.assertIn("73750110655", by_type.get("BR_TITULO_ELEITOR", []))
        self.assertIn("64053650620", by_type.get("BR_TITULO_ELEITOR", []))
        self.assertIn("114864520671", by_type.get("BR_TITULO_ELEITOR", []))
        self.assertTrue(any("HILDA" in item for item in by_type.get("PERSON", [])))
        self.assertFalse(any("Feminino" in item for item in by_type.get("PERSON", [])))
        self.assertEqual(canonicalize("BR_NASCIMENTO", "26/04/1980"), "26041980")
        self.assertEqual(canonicalize("BR_TITULO_ELEITOR", "73750110655"), "73750110655")

    def test_nascido_em_same_line_and_next(self) -> None:
        spans = FormFieldsEngine().detect(NASCIDO)
        values = {NASCIDO[span.start : span.end] for span in spans}
        self.assertIn("31/03/1978", values)
        self.assertIn("25/12/1779", values)

    def test_does_not_eat_event_contract_protocol_dates(self) -> None:
        spans = FormFieldsEngine().detect(NEGATIVES)
        blob = " ".join(NEGATIVES[span.start : span.end] for span in spans)
        self.assertNotIn("24/09/2018", blob)
        self.assertNotIn("20/05/2014", blob)
        self.assertNotIn("18/02/2022", blob)
        self.assertNotIn("12/03/1998", blob)
        self.assertNotIn("01/12/2008", blob)
        self.assertNotIn("14/06/1996", blob)
        self.assertNotIn("01/02/2020", blob)
        self.assertNotIn("111111111111", blob)
        self.assertNotIn("4120352196", blob)
        self.assertFalse(any(span.entity_type == "PERSON" for span in spans))

    def test_list_beats_form_fields_on_mae(self) -> None:
        text = "Nome da Mãe\n\nHILDA ALTMANN TENORIO\n"
        listed = TargetListEngine(people=["HILDA ALTMANN TENORIO"]).detect(text)
        form = FormFieldsEngine().detect(text)
        merged = resolve_overlaps([*listed, *form])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].recognizer, "ListaDeAlvos")
        self.assertGreater(source_rank(merged[0]), source_rank(form[0]))

    def test_header_rank_for_person(self) -> None:
        from anonimizador_juridico.types import EntitySpan

        span = EntitySpan(0, 5, "PERSON", 0.88, RECOGNIZER)
        self.assertEqual(source_rank(span), 300)

    def test_wired_in_contextual_engine(self) -> None:
        settings = AppSettings(local_ner_enabled=False)
        engine = build_contextual_engine(settings)
        providers = [getattr(inner, "provider", "") for inner in getattr(engine, "engines", ())]
        self.assertIn("form_fields", providers)


if __name__ == "__main__":
    unittest.main()
