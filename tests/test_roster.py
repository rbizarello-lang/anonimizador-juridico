from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.roster import (
    KEEP,
    TOKENIZE,
    Mention,
    Roster,
    entity_key,
    normalize_role,
    policy_action,
    strip_token_tail,
)


class RoleTests(unittest.TestCase):
    def test_role_aliases(self) -> None:
        self.assertEqual(normalize_role("Sócia-administradora"), "socio")
        self.assertEqual(normalize_role("executada"), "parte")
        self.assertEqual(normalize_role("Juíza Federal"), "magistrado")
        self.assertEqual(normalize_role("Procuradora da Fazenda Nacional"), "procurador_publico")
        self.assertEqual(normalize_role("advogada"), "advogado")
        self.assertEqual(normalize_role("algo novo"), "desconhecido")

    def test_policy_protects_the_person_when_roles_disagree(self) -> None:
        self.assertEqual(policy_action(["magistrado"]), KEEP)
        self.assertEqual(policy_action(["autor_citado", "orgao_publico"]), KEEP)
        self.assertEqual(policy_action(["magistrado", "terceiro"]), TOKENIZE)
        self.assertEqual(policy_action(["desconhecido"]), TOKENIZE)


class KeyTests(unittest.TestCase):
    def test_company_suffix_and_case_do_not_split_the_entity(self) -> None:
        self.assertEqual(
            entity_key("empresa", "ACME COMÉRCIO LTDA."),
            entity_key("empresa", "Acme Comercio"),
        )

    def test_person_title_is_ignored(self) -> None:
        self.assertEqual(
            entity_key("pessoa", "Dr. Tiago Bortolotto"),
            entity_key("pessoa", "TIAGO BORTOLOTTO"),
        )

    def test_token_tail_is_cut(self) -> None:
        self.assertEqual(strip_token_tail("Rua X, 120, CEP [CEP_1]"), "Rua X, 120")
        self.assertEqual(strip_token_tail("MARIA SOUZA ([CPF_1])"), "MARIA SOUZA")
        self.assertEqual(strip_token_tail("[PESSOA_1] da Silva"), "")
        self.assertEqual(strip_token_tail("ALLAN"), "ALLAN")


class RosterTests(unittest.TestCase):
    def test_variants_with_same_canonical_share_one_entry(self) -> None:
        roster = Roster()
        first = roster.add_mention(
            Mention(kind="pessoa", text="CLAUDETE Hoffmelster", role="socia",
                    canonical="Claudete Hoffmeister")
        )
        second = roster.add_mention(
            Mention(kind="pessoa", text="Claudete Hoffmeister", role="socio",
                    canonical="Claudete Hoffmeister")
        )

        self.assertIs(first, second)
        self.assertEqual(len(roster), 1)
        self.assertEqual(first.variants, ["CLAUDETE Hoffmelster", "Claudete Hoffmeister"])
        self.assertEqual(first.action, TOKENIZE)

    def test_domain_joins_the_company_through_the_canonical_name(self) -> None:
        roster = Roster()
        company = roster.add_mention(
            Mention(kind="empresa", text="VIDRAÇARIA SOL LTDA - ME", role="parte",
                    canonical="Vidraçaria Sol Ltda")
        )
        domain = roster.add_mention(
            Mention(kind="empresa", text="www.vidracariasol.com.br", role="parte",
                    canonical="Vidraçaria Sol Ltda")
        )
        self.assertIs(company, domain)

    def test_magistrate_is_kept(self) -> None:
        roster = Roster()
        entry = roster.add_mention(
            Mention(kind="pessoa", text="LUCIANA MONTEFUSCO", role="Juíza Federal")
        )
        self.assertEqual(entry.action, KEEP)
        self.assertEqual(roster.to_tokenize(), ())

    def test_fuzzy_source_text_becomes_a_variant(self) -> None:
        roster = Roster()
        entry = roster.add_mention(
            Mention(kind="pessoa", text="JOÃO DA SILVA", role="parte",
                    source_text="JOAO DA SlLVA", start=0, end=13)
        )
        self.assertIn("JOAO DA SlLVA", entry.variants)

    def test_listed_name_always_tokenizes(self) -> None:
        roster = Roster()
        entry = roster.add_listed("pessoa", "Maria Aparecida Ferreira")
        self.assertEqual(entry.action, TOKENIZE)
        self.assertEqual(entry.source, "lista")

    def test_user_decision_survives_reload_and_new_mentions(self) -> None:
        roster = Roster()
        roster.add_mention(Mention(kind="pessoa", text="Ana Lu Costa", role="terceiro"))
        with tempfile.TemporaryDirectory() as tmp:
            path = roster.dump(Path(tmp) / "elenco.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            # O usuario so troca a acao; nao marca "fixo".
            payload["entradas"][0]["acao"] = KEEP
            path.write_text(json.dumps(payload), encoding="utf-8")
            reviewed = Roster.load(path)

        entry = reviewed.entries[0]
        self.assertTrue(entry.pinned)
        reviewed.add_mention(Mention(kind="pessoa", text="ANA LU COSTA", role="parte"))
        self.assertEqual(entry.action, KEEP)
        self.assertIn("ANA LU COSTA", entry.variants)

    def test_mentions_made_of_tokens_are_ignored(self) -> None:
        roster = Roster()
        self.assertIsNone(roster.add_mention(Mention(kind="pessoa", text="[PESSOA_1]")))
        self.assertIsNone(roster.add_mention(Mention(kind="cidade", text="Curitiba")))
        self.assertEqual(len(roster), 0)


if __name__ == "__main__":
    unittest.main()
