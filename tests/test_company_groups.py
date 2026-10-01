"""Sufixo de grupo ·Gn nos tokens de empresa ligados por vinculo declarado (endurecimento antes do L4).

Dados inventados. Os tokens continuam separados; o sufixo so diz que o ELENCO os liga.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from anonimizador_app import audit, lote_audit, roster
from anonimizador_app.lote_elenco import (
    apply_company_groups,
    apply_groups,
    build_cast,
    cast_summary,
    company_groups,
)
from anonimizador_app.lote_index import build_index, standalone_facts
from anonimizador_app.pipeline import FileOutcome, finalize_lote
from anonimizador_app.token_syntax import strip_groups, with_group
from anonimizador_juridico import PseudonymMap

RENAME = (
    "[fl. 9]\nEm 01/01/2000, houve alteração do nome social para [EMPRESA_3] e do objeto. "
    "Em 05/05/2005, houve nova alteração do nome social para [EMPRESA_7]. [EMPRESA_3] [EMPRESA_7] [EMPRESA_7]"
)
COVER_A = (
    "N.º do processo: [PROCESSO_1]\nClasse da ação: Execução Fiscal\n\nPartes e Representantes\n\n"
    "| EXECUTADO |\n|---|\n| [EMPRESA_20] ([CNPJ_2]) - Pessoa Jurídica |\n"
)
COVER_B = (
    "N.º do processo: [PROCESSO_2]\nClasse da ação: Agravo de Instrumento\n\nPartes e Representantes\n\n"
    "| AGRAVADO |\n|---|\n| [EMPRESA_21] ([CNPJ_2]) - Pessoa Jurídica |\n"
)


class GroupDetectionTests(unittest.TestCase):
    def test_renaming_chain_makes_one_group(self) -> None:
        groups = company_groups([("autos.md", RENAME)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].number, 1)
        self.assertEqual(groups[0].members, ("[EMPRESA_3]", "[EMPRESA_7]"))
        self.assertEqual(groups[0].reasons, ("cadeia de mudanças de nome",))

    def test_same_cnpj_in_two_covers_makes_one_group_with_the_cnpj_token(self) -> None:
        groups = company_groups([("a.md", COVER_A), ("b.md", COVER_B)])
        self.assertEqual([group.members for group in groups], [("[EMPRESA_20]", "[EMPRESA_21]")])
        self.assertEqual(groups[0].reasons, ("mesmo CNPJ [CNPJ_2]",))

    def test_groups_are_numbered_by_the_lowest_token_and_a_shared_token_joins_chains(self) -> None:
        # EMPRESA_3/7 (renomeacao) e EMPRESA_20/21 (CNPJ) sao dois grupos; se 7 e 20 forem ligados, viram um so.
        separate = company_groups([("a.md", RENAME), ("b.md", COVER_A), ("c.md", COVER_B)])
        self.assertEqual([(g.number, g.members[0]) for g in separate], [(1, "[EMPRESA_3]"), (2, "[EMPRESA_20]")])
        linked = RENAME + " Em 09/09/2009, houve ainda nova alteração do nome social para [EMPRESA_20]."
        merged = company_groups([("a.md", linked), ("b.md", COVER_A), ("c.md", COVER_B)])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].members, ("[EMPRESA_3]", "[EMPRESA_7]", "[EMPRESA_20]", "[EMPRESA_21]"))
        self.assertEqual(merged[0].reasons, ("cadeia de mudanças de nome", "mesmo CNPJ [CNPJ_2]"))

    def test_similar_numbers_or_names_never_make_a_group(self) -> None:
        text = "[EMPRESA_1] e [EMPRESA_2] aparecem juntas; a sucessora é [EMPRESA_3]. [EMPRESA_1] [EMPRESA_2]"
        self.assertEqual(company_groups([("autos.md", text)]), [])

    def test_different_cnpj_or_people_make_no_group(self) -> None:
        other = COVER_B.replace("[CNPJ_2]", "[CNPJ_9]")
        self.assertEqual(company_groups([("a.md", COVER_A), ("b.md", other)]), [])
        person_a = COVER_A.replace("[EMPRESA_20] ([CNPJ_2])", "[PESSOA_5] ([CPF_2])")
        person_b = COVER_B.replace("[EMPRESA_21] ([CNPJ_2])", "[PESSOA_6] ([CPF_2])")
        self.assertEqual(company_groups([("a.md", person_a), ("b.md", person_b)]), [])  # so empresa

    def test_result_does_not_depend_on_the_suffix_already_being_in_the_text(self) -> None:
        plain = company_groups([("autos.md", RENAME)])
        suffixed = company_groups([("autos.md", apply_groups(RENAME, plain))])
        self.assertEqual(plain, suffixed)


class TokenSyntaxTests(unittest.TestCase):
    def test_apply_is_idempotent_and_touches_only_company_members(self) -> None:
        groups = company_groups([("autos.md", RENAME)])
        text = RENAME + " [PESSOA_3] [EMPRESA_5]"
        once = apply_groups(text, groups)
        self.assertIn("[EMPRESA_3·G1]", once)
        self.assertIn("[EMPRESA_7·G1]", once)
        self.assertIn("[PESSOA_3]", once)
        self.assertIn("[EMPRESA_5]", once)
        self.assertEqual(apply_groups(once, groups), once)
        self.assertEqual(strip_groups(once), text)

    def test_with_group(self) -> None:
        self.assertEqual(with_group("[EMPRESA_3]", 2), "[EMPRESA_3·G2]")
        self.assertEqual(with_group("[EMPRESA_3·G1]", 2), "[EMPRESA_3·G2]")
        self.assertEqual(with_group("[EMPRESA_3]", None), "[EMPRESA_3]")


class CastLegendTests(unittest.TestCase):
    def test_cast_explains_each_group_and_marks_the_members(self) -> None:
        cast = build_cast([("autos.md", RENAME), ("a.md", COVER_A), ("b.md", COVER_B)])
        self.assertIn("- G1: cadeia de mudanças de nome. Tokens (ocorrências no lote): [EMPRESA_3·G1] (", cast)
        self.assertIn("- G2: mesmo CNPJ [CNPJ_2]. Tokens (ocorrências no lote): [EMPRESA_20·G2]", cast)
        self.assertNotIn("[EMPRESA_20]", cast)
        self.assertEqual(cast_summary(cast)["grupos"], 2)

    def test_cast_is_the_same_whether_or_not_the_markdown_already_carries_the_suffix(self) -> None:
        documents = [("autos.md", RENAME), ("a.md", COVER_A), ("b.md", COVER_B)]
        groups = company_groups(documents)
        grouped = [(name, apply_groups(text, groups)) for name, text in documents]
        self.assertEqual(build_cast(documents), build_cast(grouped))

    def test_no_group_no_legend(self) -> None:
        cast = build_cast([("autos.md", "[EMPRESA_1] e [EMPRESA_2].")])
        self.assertNotIn("Grupos", cast)
        self.assertEqual(cast_summary(cast)["grupos"], 0)


class ConsumersAcceptTheSuffixTests(unittest.TestCase):
    """Todo leitor de token do exportavel aceita ·Gn e conta a entidade certa."""

    TEXT = "A executada [EMPRESA_3·G1] e a [EMPRESA_7·G1] devem R$ [VALOR_1]. [PESSOA_4]"

    def test_audit_counts_the_label(self) -> None:
        self.assertEqual(audit.token_counts(self.TEXT).get("EMPRESA"), 2)
        self.assertEqual(lote_audit._TOKEN.findall(self.TEXT)[0], ("EMPRESA", "3"))

    def test_audit_glued_checks_still_see_a_suffixed_token(self) -> None:
        self.assertTrue(audit._MONEY_TOUCH.search("R$[EMPRESA_3·G1]"))
        self.assertTrue(audit._DATE_GLUED.search("[EMPRESA_3·G1]12/03/2026"))

    def test_roster_and_header_heuristic_see_a_token(self) -> None:
        self.assertTrue(roster.has_token("[EMPRESA_3·G1]"))

    def test_standalone_index_reads_the_company_token_with_the_suffix(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\nCertifico o registro sob o nº 123 em 01/02/2020 da empresa "
            "[EMPRESA_3·G1] e mais.\nalteração de contrato social\n"
        )
        facts = standalone_facts(text, "x_CONTRSOCIAL1.md")
        self.assertEqual(list(facts.companies), ["[EMPRESA_3·G1]"])

    def test_structure_ruler_regexes_accept_the_suffix(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "medir_estrutura", Path(__file__).resolve().parents[1] / "tools" / "medir_estrutura.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.TOKEN.findall("a [EMPRESA_3·G1] b [CNPJ_2]"), ["[EMPRESA_3·G1]", "[CNPJ_2]"])
        self.assertTrue(module.TOKEN_ON_DIGIT.search("[EMPRESA_3·G1],12"))  # colado em numero continua contado
        self.assertTrue(module.TOKEN_ON_DIGIT.search("12[EMPRESA_3·G1]"))
        self.assertFalse(module.TOKEN_ON_DIGIT.search("C1.[EMPRESA_3·G1]"))

    def test_leak_ruler_does_not_parse_tokens(self) -> None:
        # medir_vazamento procura frases e numeros em claro: o sufixo nao o afeta.
        spec = importlib.util.spec_from_file_location(
            "medir_vazamento", Path(__file__).resolve().parents[1] / "tools" / "medir_vazamento.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        text = "A [EMPRESA_3·G1] e a [EMPRESA_7·G1]; fulano de tal."
        patterns = [module._phrase("fulano de tal")]
        self.assertEqual(module._count(text, patterns, True), 1)
        self.assertEqual(module._count(strip_groups(text), patterns, True), 1)


class FinalizeLoteTests(unittest.TestCase):
    def test_finalize_suffixes_the_markdown_and_the_derived_files_and_touches_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            export = root / "exp" / "lote"
            private = root / "priv" / "lote"
            export.mkdir(parents=True)
            private.mkdir(parents=True)
            body = (
                "<!-- pagina-origem: 1 -->\r\n[fl. 1]\r\n" + RENAME.replace("\n", "\r\n") + "\r\nSem grupo: [EMPRESA_5].\r\n"
            )
            md = export / "autos.md"
            md.write_bytes(body.encode("utf-8"))
            outcome = FileOutcome(source=root / "autos.pdf", ok=True, markdown_path=md, page_count=1)
            mapping = PseudonymMap()
            finalize_lote(
                job_id="lote",
                export_dir=export,
                map_path=private / "mapa.json",
                outcomes=[outcome],
                mapping=mapping,
            )
            written = md.read_bytes().decode("utf-8")
            self.assertEqual(strip_groups(written), body)  # so os tokens mudaram (CRLF preservado)
            self.assertIn("[EMPRESA_3·G1]", written)
            self.assertIn("[EMPRESA_7·G1]", written)
            self.assertIn("[EMPRESA_5].", written)
            cast = (export / "ELENCO.md").read_text(encoding="utf-8")
            self.assertIn("- G1: cadeia de mudanças de nome.", cast)
            self.assertTrue((export / "INDICE.md").is_file())
            audit_text = (export / "AUDITORIA.txt").read_text(encoding="utf-8")
            self.assertIn("EMPRESA", audit_text)
            guide = (export / "LEIA-ME.txt").read_text(encoding="utf-8")
            self.assertIn("Sufixo de grupo", guide)

    def test_without_groups_the_markdown_is_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            md = Path(raw) / "a.md"
            data = "[EMPRESA_1] e [EMPRESA_2].\r\n".encode("utf-8")
            md.write_bytes(data)
            before = md.stat().st_mtime_ns
            self.assertEqual(apply_company_groups([md]), [])
            self.assertEqual(md.read_bytes(), data)
            self.assertEqual(md.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
