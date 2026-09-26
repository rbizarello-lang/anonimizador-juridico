from __future__ import annotations

import tempfile
import unicodedata
import unittest
from pathlib import Path

from anonimizador_app.audit import audit_markdown
from anonimizador_app.cnpj_index import CompanyRecord, CnpjIndex, extract_valid_cnpjs, write_records
from anonimizador_app.cnpj_lookup import CnpjLookupEngine
from anonimizador_app.geography import PAGE_PLACEHOLDER, serialize_geography
from anonimizador_app.local_ner import LocalNerEngine, shadow_case_copy
from anonimizador_juridico.validators import _cnpj_check_digit


def _make_cnpj(body: str = "123456780001") -> str:
    first = _cnpj_check_digit(body, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    second = _cnpj_check_digit(body + str(first), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    return body + str(first) + str(second)


class GeographyTests(unittest.TestCase):
    def test_numbers_page_breaks_and_splits_glued_event(self) -> None:
        raw = (
            "Capa do feito\n"
            f"{PAGE_PLACEHOLDER}\n"
            "Pedido. Evento 12 juntada da executada. SISBAJUD permanece.\n"
            "PÁGINA DE SEPARAÇÃO\n"
        )
        text = serialize_geography(raw)
        self.assertIn("<!-- pagina-origem: 1 -->", text)
        self.assertIn("<!-- pagina-origem: 2 -->", text)
        self.assertIn("## Evento 12", text)
        self.assertIn("<!-- bloco-peca -->", text)
        self.assertIn("SISBAJUD", text)

    def test_event_mention_inside_sentence_stays_in_place(self) -> None:
        for sentence in (
            "Conforme o evento 12, o socio JOAO FICTICIO foi citado.",
            "Conforme o Evento 12, o sócio foi citado.",
            "A decisão do Evento 12 - DESPADEC1 foi mantida.",
            "CONFORME O EVENTO 12, O SÓCIO FOI CITADO.",
            "Conforme o\nEvento 12, o sócio foi citado.",
            "Ver a certidão (cf. Evento 30 - CERT1).",
        ):
            with self.subTest(sentence=sentence):
                text = serialize_geography(f"Capa\n{sentence}\n")
                self.assertNotIn("## Evento", text)
                self.assertIn(sentence, text)

    def test_glued_event_header_still_splits(self) -> None:
        for raw, expected in (
            ("Pedido deferido. Evento 13 juntada da executada.", "## Evento 13"),
            ("Página 3 de 10 Evento 14 - DESPACHO", "## Evento 14"),
            ("<!-- pagina-origem: 2 -->\nEVENTO 15 - PETIÇÃO", "## Evento 15"),
            ("Texto da peça ## Evento 16", "## Evento 16"),
        ):
            with self.subTest(raw=raw):
                self.assertIn(expected, serialize_geography(raw))

    def test_marks_nfd_separation_heading(self) -> None:
        nfd = unicodedata.normalize("NFD", "PÁGINA DE SEPARAÇÃO")
        text = serialize_geography(f"Capa\n{nfd}\n")
        self.assertIn("<!-- bloco-peca -->", text)
        self.assertIn("## ", text)


class ShadowCaseTests(unittest.TestCase):
    def test_title_cases_all_caps_preserving_length(self) -> None:
        raw = "JOAO DA SILVA pediu SISBAJUD."
        shadow = shadow_case_copy(raw)
        self.assertEqual(len(shadow), len(raw))
        self.assertTrue(shadow.startswith("Joao Da Silva"))
        self.assertIn("pediu", shadow)

    def test_gliner_on_shadow_finds_original_span(self) -> None:
        text = "A executada NORTE INVENTADO COMERCIO LTDA informou."

        def predict(chunk: str):
            if "Norte Inventado Comercio Ltda" in chunk:
                return [
                    {
                        "entity_group": "empresa privada",
                        "word": "Norte Inventado Comercio Ltda",
                        "score": 0.91,
                    }
                ]
            return []

        off = LocalNerEngine(predict=predict, threshold=0.75, shadow_case=False)
        on = LocalNerEngine(predict=predict, threshold=0.75, shadow_case=True)
        self.assertEqual(off.detect(text), [])
        spans = on.detect(text)
        self.assertEqual(len(spans), 1)
        self.assertEqual(text[spans[0].start : spans[0].end], "NORTE INVENTADO COMERCIO LTDA")


class AuditTests(unittest.TestCase):
    def test_flags_token_next_to_money_and_counts(self) -> None:
        notes = audit_markdown("A Uniao pediu R$[EMPRESA_1]. SISBAJUD no Evento 3.")
        joined = "\n".join(notes)
        self.assertIn("EMPRESA", joined)
        self.assertIn("ALERTA: token encostado em valor em R$.", joined)
        self.assertIn("uniao", joined)
        self.assertIn("sisbajud", joined)
        self.assertIn("Tokens na capa", joined)
        self.assertIn("Tokens no corpo", joined)


class CnpjLookupTests(unittest.TestCase):
    def test_extracts_valid_and_skips_invalid(self) -> None:
        cnpj = _make_cnpj()
        formatted = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        found = extract_valid_cnpjs(f"CNPJ {formatted} e lixo 00.000.000/0000-00")
        self.assertEqual(found, [cnpj])

    def test_tokenizes_private_razao_and_skips_public(self) -> None:
        private = _make_cnpj("112223330001")
        public = _make_cnpj("000000000001")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cnpj_sul.sqlite"
            write_records(
                path,
                [
                    CompanyRecord(
                        cnpj=private,
                        cnpj_basico=private[:8],
                        razao_social="Norte Inventado Comercio Ltda",
                        nome_fantasia="NorteCom",
                        natureza="2062",
                        uf="RS",
                        publica=False,
                    ),
                    CompanyRecord(
                        cnpj=public,
                        cnpj_basico=public[:8],
                        razao_social="Uniao",
                        nome_fantasia="",
                        natureza="1015",
                        uf="RS",
                        publica=True,
                    ),
                ],
            )
            engine = CnpjLookupEngine(path)
            self.assertTrue(engine.is_ready())
            text = (
                f"A NORTE INVENTADO COMERCIO LTDA, CNPJ {private}, "
                f"e a Uniao CNPJ {public} requereram SISBAJUD."
            )
            spans = engine.detect(text)
            values = {text[span.start : span.end] for span in spans}
            self.assertTrue(any("NORTE INVENTADO" in item.upper() for item in values))
            self.assertNotIn("Uniao", values)
            self.assertEqual({span.recognizer for span in spans}, {"CnpjLookup"})


if __name__ == "__main__":
    unittest.main()
