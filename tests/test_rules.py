from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.gemini_detector import is_too_generic, locate_mentions
from anonimizador_app.pipeline import run_job
from anonimizador_app.rules import InvalidRuleError, slug_entity_type, validate_regex
from anonimizador_app.settings import AppSettings, CustomRule, default_enabled_tags, load_settings, save_settings

from tests.test_gemini import FakeGemini


CORPUS = Path(__file__).parent / "corpus" / "caso_001_processo_sintetico.md"


class RuleValidationTests(unittest.TestCase):
    def test_invalid_regex_is_rejected(self) -> None:
        with self.assertRaises(InvalidRuleError):
            validate_regex("[")

    def test_greedy_regex_is_rejected(self) -> None:
        with self.assertRaises(InvalidRuleError):
            validate_regex(".*")
        with self.assertRaises(InvalidRuleError):
            validate_regex(r"\w+")

    def test_slug_prefixes_br(self) -> None:
        self.assertEqual(slug_entity_type("PIS"), "BR_PIS")


class FidelityMentionTests(unittest.TestCase):
    def test_generic_mentions_are_not_replaced(self) -> None:
        self.assertTrue(is_too_generic("PERSON", "Silva"))
        self.assertTrue(is_too_generic("LOCATION", "Porto Alegre"))
        self.assertFalse(is_too_generic("PERSON", "Helena Ficticia da Silva"))
        self.assertFalse(is_too_generic("LOCATION", "Rua das Acacias 100"))
        self.assertFalse(is_too_generic("LOCATION", "Rua\nRaposo Tavares n° 281"))
        self.assertFalse(is_too_generic("LOCATION", "Avn. Jules Verne. nº674"))
        self.assertFalse(is_too_generic("LOCATION", "R GAMA 120"))
        self.assertTrue(is_too_generic("LOCATION", "4a Vara Federal de Santa Maria"))
        self.assertTrue(is_too_generic("ORGANIZATION", "4a Vara Federal de Santa Maria"))
        self.assertTrue(is_too_generic("ORGANIZATION", "Uniao - Fazenda Nacional"))
        self.assertFalse(is_too_generic("ORGANIZATION", "Norte Inventado S.A."))
        self.assertTrue(is_too_generic("ORGANIZATION", "SOCIEDADE EMPRESÁRIA LIMITADA"))
        self.assertTrue(is_too_generic("ORGANIZATION", "natureza jurídica"))
        self.assertTrue(is_too_generic("PERSON", "Dívida Ativa"))
        self.assertTrue(
            is_too_generic("PERSON", "LUIZ INACIO Assuntos Codigo Descricao Principal")
        )

        text = "Helena Ficticia da Silva atuou em Porto Alegre perante a Uniao."
        spans = locate_mentions(
            text,
            [
                ("PERSON", "Silva"),
                ("LOCATION", "Porto Alegre"),
                ("ORGANIZATION", "Uniao"),
            ],
        )
        self.assertEqual(spans, [])


class RulesPipelineTests(unittest.TestCase):
    def test_disabled_phone_is_left_intact(self) -> None:
        tags = default_enabled_tags()
        tags["BR_PHONE"] = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = run_job(
                [CORPUS],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_sem_telefone",
                settings=AppSettings(enabled_tags=tags),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("(11) 98765-4321", markdown)
        self.assertNotIn("[TELEFONE_", markdown)
        self.assertIn("[CPF_1]", markdown)

    def test_custom_pis_rule_tokenizes_only_that_pattern(self) -> None:
        rule = CustomRule(
            name="PIS",
            entity_type="BR_PIS",
            token_label="PIS",
            regex=r"(?i)\bPIS\s+\d{3}\.\d{5}\.\d{2}-\d\b",
            score=0.65,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pis.txt"
            source.write_text(
                "PIS 123.45678.90-1 no Evento 184, valor R$ 10,00.",
                encoding="utf-8",
            )
            job = run_job(
                [source],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_pis",
                settings=AppSettings(custom_rules=[rule]),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("[PIS_1]", markdown)
        self.assertNotIn("123.45678.90-1", markdown)
        self.assertIn("Evento 184", markdown)
        self.assertIn("R$ 10,00", markdown)

    def test_exclusion_keeps_company_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = run_job(
                [CORPUS],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_exclusao",
                contextual=FakeGemini(),
                settings=AppSettings(exclusions=["Horizonte Ficticio Ltda."]),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("Horizonte Ficticio Ltda.", markdown)
        self.assertIn("[PESSOA_1]", markdown)
        self.assertNotIn("Helena Ficticia da Silva", markdown)

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tags = default_enabled_tags()
            tags["BR_PHONE"] = False
            original = AppSettings(
                enabled_tags=tags,
                exclusions=["Fazenda Publica Estadual"],
                custom_rules=[
                    CustomRule(
                        name="PIS",
                        entity_type="BR_PIS",
                        token_label="PIS",
                        regex=r"\bPIS\s+\d+\b",
                    )
                ],
            )
            save_settings(root, original)
            loaded = load_settings(root)
        self.assertFalse(loaded.enabled_tags["BR_PHONE"])
        self.assertEqual(loaded.exclusions, ["Fazenda Publica Estadual"])
        self.assertEqual(loaded.custom_rules[0].token_label, "PIS")

    def test_settings_drop_legacy_gemini_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(
                '{"gemini_enabled": true, "gemini_api_key": "secret", '
                '"gemini_model": "gemini-2.5-flash", "local_ner_enabled": true, '
                '"enabled_tags": {}, "custom_rules": [], "exclusions": [], '
                '"target_companies": [], "target_people": [], "target_ids": []}',
                encoding="utf-8",
            )
            loaded = load_settings(root)
            save_settings(root, loaded)
            stored = config.read_text(encoding="utf-8")
        self.assertNotIn("gemini_enabled", stored)
        self.assertNotIn("gemini_api_key", stored)
        self.assertFalse(hasattr(loaded, "gemini_enabled"))


if __name__ == "__main__":
    unittest.main()
