from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico import PseudonymMap, StructuredDetector, pseudonymize


CORPUS_DIR = Path(__file__).parent / "corpus"


def _load_corpus_cases() -> list[tuple[str, str, dict]]:
    cases: list[tuple[str, str, dict]] = []
    for expected_path in sorted(CORPUS_DIR.glob("caso_*_expected.json")):
        prefix = expected_path.name.replace("_expected.json", "")
        text = (CORPUS_DIR / f"{prefix}_processo_sintetico.md").read_text(encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        cases.append((prefix, text, expected))
    return cases


class PseudonymizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = []
        for prefix, text, expected in _load_corpus_cases():
            result = pseudonymize(text, StructuredDetector().detect(text))
            cls.cases.append((prefix, text, expected, result))
        cls.text = cls.cases[0][1]
        cls.expected = cls.cases[0][2]
        cls.result = cls.cases[0][3]

    def test_format_variants_share_tokens(self) -> None:
        self.assertEqual(self.result.text.count("[CPF_1]"), 2)
        self.assertEqual(self.result.text.count("[TELEFONE_1]"), 2)
        cpf_records = [
            record for record in self.result.mapping.records if record.entity_type == "BR_CPF"
        ]
        self.assertEqual(len(cpf_records), 1)
        self.assertEqual(len(cpf_records[0].originals), 2)

    def test_legal_information_is_preserved(self) -> None:
        self.assertGreaterEqual(len(self.cases), 2)
        for prefix, _text, expected, result in self.cases:
            with self.subTest(corpus=prefix):
                for value in expected["must_preserve"]:
                    self.assertIn(value, result.text)

    def test_first_occurrence_controls_numbering(self) -> None:
        self.assertLess(self.result.text.index("[PROCESSO_1]"), self.result.text.index("[CNPJ_1]"))

    def test_contextual_gap_remains_explicit(self) -> None:
        for prefix, _text, expected, result in self.cases:
            with self.subTest(corpus=prefix):
                for value in expected["known_contextual_entities_not_yet_covered"]:
                    self.assertIn(value, result.text)

    def test_labeled_ids_are_tokenized_in_second_corpus(self) -> None:
        result = next(item[3] for item in self.cases if item[0] == "caso_002")
        self.assertIn("[PIX_1]", result.text)
        self.assertIn("[CONTA_1]", result.text)
        self.assertIn("[RENAVAM_1]", result.text)
        self.assertIn("[PLACA_1]", result.text)
        self.assertNotIn("9b2c3d4e", result.text)
        self.assertNotIn("89012-3", result.text)
        self.assertNotIn("00345678901", result.text)
        self.assertNotIn("ABC1D23", result.text)

    def test_pix_hyphen_and_compact_share_token(self) -> None:
        first_text = "chave Pix 9b2c3d4e-5f6a-4b7c-8d9e-0a1b2c3d4e5f"
        first = pseudonymize(first_text, StructuredDetector().detect(first_text))
        second_text = "PIX 9b2c3d4e5f6a4b7c8d9e0a1b2c3d4e5f"
        second = pseudonymize(
            second_text,
            StructuredDetector().detect(second_text),
            mapping=first.mapping,
        )
        self.assertIn("[PIX_1]", first.text)
        self.assertIn("[PIX_1]", second.text)
        self.assertEqual(len(second.mapping.records), 1)

    def test_map_has_security_warning(self) -> None:
        payload = self.result.mapping.to_dict()
        self.assertIn("NAO ENVIAR", payload["warning"])

    def test_map_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapa.json"
            self.result.mapping.dump(path)
            loaded = PseudonymMap.load(path)
        self.assertEqual(loaded.to_dict(), self.result.mapping.to_dict())

    def test_mapping_persists_across_documents(self) -> None:
        first_text = "CPF 529.982.247-25"
        first = pseudonymize(first_text, StructuredDetector().detect(first_text))
        second_text = "Identificador repetido: 52998224725"
        second = pseudonymize(
            second_text,
            StructuredDetector().detect(second_text),
            mapping=first.mapping,
        )
        self.assertIn("[CPF_1]", first.text)
        self.assertIn("[CPF_1]", second.text)
        self.assertEqual(len(second.mapping.records), 1)

    def test_labeled_invalid_identifier_is_not_leaked(self) -> None:
        text = "CPF 123.456.789-00"
        result = pseudonymize(text, StructuredDetector().detect(text))
        self.assertEqual(result.text, "CPF [CPF_1]")
        self.assertNotIn("123", result.text)

    def test_accent_fold_merges_person_org_location(self) -> None:
        from anonimizador_juridico.pseudonymizer import canonicalize

        self.assertEqual(
            canonicalize("ORGANIZATION", "Comércio"),
            canonicalize("ORGANIZATION", "comercio"),
        )
        self.assertEqual(canonicalize("ORGANIZATION", "LTDA."), canonicalize("ORGANIZATION", "LTDA"))
        self.assertEqual(
            canonicalize("PERSON", "José  da  Silva."),
            canonicalize("PERSON", "jose da silva"),
        )
        self.assertNotEqual(
            canonicalize("BR_CNPJ", "12.345.678/0001-90"),
            canonicalize("ORGANIZATION", "12.345.678/0001-90"),
        )

    def test_oab_uf_before_and_after_share_canonical(self) -> None:
        from anonimizador_juridico.pseudonymizer import canonicalize

        self.assertEqual(
            canonicalize("BR_OAB", "OAB/SP 123.456"),
            canonicalize("BR_OAB", "OAB 123456/SP"),
        )
        self.assertEqual(canonicalize("BR_OAB", "OAB/SP 123.456"), "SP123456")
        self.assertEqual(
            canonicalize("BR_OAB", "OAB/RS n. 54.321"),
            canonicalize("BR_OAB", "OAB 54321/RS"),
        )
        self.assertEqual(canonicalize("BR_OAB", "OAB/RS n. 54.321"), "RS54321")
        self.assertEqual(
            canonicalize("BR_OAB", "OAB/RS n.\u00ba 54.321"),
            "RS54321",
        )
        self.assertEqual(
            canonicalize("BR_OAB", "OAB/SP 123.456-A"),
            canonicalize("BR_OAB", "OAB 123456-A/SP"),
        )
        self.assertEqual(canonicalize("BR_OAB", "N.OAB- 11223"), "11223")
        self.assertNotEqual(
            canonicalize("BR_OAB", "OAB/SP 123.456"),
            canonicalize("BR_OAB", "OAB/RS 123.456"),
        )

    def test_oab_format_variants_share_token(self) -> None:
        first_text = "Patrona OAB/SP 123.456 na capa."
        first = pseudonymize(first_text, StructuredDetector().detect(first_text))
        second_text = "Substabelecimento OAB 123456/SP no merito."
        second = pseudonymize(
            second_text,
            StructuredDetector().detect(second_text),
            mapping=first.mapping,
        )
        self.assertIn("[OAB_1]", first.text)
        self.assertIn("[OAB_1]", second.text)
        self.assertNotIn("[OAB_2]", second.text)

    def test_rg_preserves_alphanumeric_check_digit(self) -> None:
        from anonimizador_juridico.pseudonymizer import canonicalize

        self.assertEqual(canonicalize("BR_RG", "RG 12.345.678-X"), "12345678X")
        self.assertEqual(canonicalize("BR_RG", "RG 12.345.678-x"), "12345678X")
        self.assertEqual(canonicalize("BR_RG", "RG 12.345.678-0"), "123456780")
        self.assertNotEqual(
            canonicalize("BR_RG", "RG 12.345.678-X"),
            canonicalize("BR_RG", "RG 12.345.678-0"),
        )
        self.assertEqual(canonicalize("BR_RG", "RG 12.345.678-2"), "123456782")

    def test_canonical_key_aliases_surfaces_and_keeps_alvo(self) -> None:
        mapping = PseudonymMap()
        first = mapping.resolve(
            "ORGANIZATION",
            "Norte Inventado Comercio",
            canonical_key="alvo:empresa:norte inventado comercio",
            alvo="Norte Inventado Comercio Ltda",
        )
        second = mapping.resolve(
            "ORGANIZATION",
            "NORTE INVENTADO COMERCIO LTDA ME",
            canonical_key="alvo:empresa:norte inventado comercio",
            alvo="Norte Inventado Comercio Ltda",
        )
        self.assertEqual(first, second)
        record = mapping.records[0]
        self.assertEqual(record.alvo, "Norte Inventado Comercio Ltda")
        self.assertEqual(len(record.originals), 2)

    def test_location_canonical_merges_via_and_number_spellings(self) -> None:
        from anonimizador_juridico.address import canonicalize_location
        from anonimizador_juridico.pseudonymizer import canonicalize

        forms = [
            "Rua Ari Jose de Oliveira Meireles, 579",
            "Rua Ari Jose de Oliveira Meireles nº 579",
            "Rua Ari Jose de Oliveira Meireles n° 579",
            "Rua Ari Jose de Oliveira Meireles n. 579",
            "Rua Ari Jose de Oliveira Meireles no 579",
            "Rua Ari Jose de Oliveira Meireles n 579",
            "R. Ari Jose de Oliveira Meireles, 579",
            "r Ari Jose de Oliveira Meireles, 579",
            "Rúa Ari Jose de Oliveira Meireles, 579",
            "Rua Ary Jose de Oliveira Meireles, 579",
        ]
        keys = {canonicalize("LOCATION", item) for item in forms}
        self.assertEqual(keys, {"rua ari jose de oliveira meireles 579"})
        self.assertEqual(
            canonicalize_location("Av. Itamar Rodriguez, 5419"),
            canonicalize_location("AVENIDA ITAMAR RODRIGUEZ, 5419"),
        )
        self.assertEqual(
            canonicalize_location("Avn. Santos Dumont nº 674"),
            canonicalize_location("Avenida Santos Dumont, 674"),
        )
        self.assertEqual(
            canonicalize_location("Avn. Santos Dumont. nº674"),
            canonicalize_location("Av. Santos Dumont, 674"),
        )
        self.assertEqual(
            canonicalize_location("Rua Pedro Vaz de Caminha, 455"),
            canonicalize_location("Rua Pero Vaz de Caminht n° 455"),
        )
        self.assertEqual(
            canonicalize_location("Al. das Acácias, 100"),
            canonicalize_location("Alameda das Acácias n° 100"),
        )
        self.assertEqual(
            canonicalize_location("Pç. da Sé, 1"),
            canonicalize_location("Praça da Sé, 1"),
        )
        self.assertNotEqual(
            canonicalize_location("Rua Ary da Silva, 10"),
            canonicalize_location("Rua Ari da Silva, 10"),
        )
        self.assertEqual(
            canonicalize_location("RUA ANSELMO ROCHA PENTEADO Número 150"),
            canonicalize_location("Rua Anselmo Rocha Penteado, 150"),
        )
        self.assertNotEqual(
            canonicalize("LOCATION", "Rua Ari Jose de Oliveira Meireles, 563"),
            canonicalize("LOCATION", "Rua Ari Jose de Oliveira Meireles, 579"),
        )

    def test_location_numberless_shares_token_with_numbered(self) -> None:
        mapping = PseudonymMap()
        first = mapping.resolve("LOCATION", "Av. Itamar Rodriguez, 5419")
        second = mapping.resolve("LOCATION", "AVENIDA ITAMAR RODRIGUEZ")
        self.assertEqual(first, second)
        self.assertEqual(len(mapping.records), 1)
        third = mapping.resolve("LOCATION", "Avenida Itamar Rodriguez n° 5419")
        self.assertEqual(third, first)
        other = mapping.resolve("LOCATION", "Rua Ari Jose de Oliveira Meireles, nº 563")
        again = mapping.resolve("LOCATION", "Rua Ary Jose de Oliveira Meireles, 563")
        self.assertEqual(other, again)
        self.assertNotEqual(first, other)
        truncated = mapping.resolve("LOCATION", "R. Ari Jose de Oliveira Meireles,\n57")
        full = mapping.resolve("LOCATION", "Rua Ari Jose de Oliveira Meireles, 579")
        self.assertEqual(truncated, full)
        self.assertNotEqual(truncated, other)

    def test_schema_v1_still_loads(self) -> None:
        payload = {
            "schema_version": 1,
            "records": [
                {
                    "token": "[PESSOA_1]",
                    "entity_type": "PERSON",
                    "canonical": "ana ficticia",
                    "originals": ["Ana Ficticia"],
                }
            ],
        }
        loaded = PseudonymMap.from_dict(payload)
        self.assertEqual(loaded.records[0].token, "[PESSOA_1]")
        dumped = loaded.to_dict()
        self.assertEqual(dumped["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
