"""Documento avulso (sem carimbo do eproc): codigo do nome do arquivo, ancora e INDICE (E14). Dados inventados."""

from __future__ import annotations

import unittest

from anonimizador_app.lote_index import add_sheet_anchors, build_index, document_code, standalone_facts

STAMPED = """<!-- pagina-origem: 1 -->
### Evento 1 - Doc. 2 - CONTRATO SOCIAL

Processo [PROCESSO_1]/PR, Evento 1, CONTRSOCIAL2, Página 1
Cláusula primeira.
"""

STANDALONE_A = """<!-- cobertura: paginas=3; nativas=2; escaneadas=1; vetoriais=0; fonte-quebrada=0; em-branco=0; nao-lidas=nenhuma -->
<!-- pagina-origem: 1 -->
Junta Comercial do Estado de Teste
Certifico o registro sob o nº 1234 em 12/03/2019 da empresa [EMPRESA_5] LTDA, NIRE 4321 e
protocolo 0001 - 01/03/2019.
PRIMEIRA ALTERAÇÃO E CONSOLIDAÇÃO DO CONTRATO SOCIAL
<!-- pagina-origem: 2 -->
<!-- ocr: escaneada -->
Certifico o registro sob o nº 1234 em 12/03/2019 da empresa [EMPRESA_5] LTDA, NIRE 4321 e
Cláusula primeira.
<!-- pagina-origem: 3 -->
Certifico o registro sob o nº 1234 em 12/03/2019 da empresa [EMPRESA_5] LTDA, NIRE 4321 e
"""

STANDALONE_B = """<!-- pagina-origem: 1 -->
Certifico o registro em: 05/10/2020 sob nº: 777
CONTRATO SOCIAL
Os sócios resolvem constituir uma sociedade limitada.
"""

STANDALONE_C = """<!-- pagina-origem: 1 -->
Certifico que o ato, alteração, da empresa [EMPRESA_7] LTDA, de NIRE 99 e protocolo sob o nº 5 em 01/02/2021,
foi arquivado na Junta Comercial sob o nº 8888, em 15/02/2021. O ato foi
"""

NAME_A = "5005_1_CONTRSOCIAL13.md"


class DocumentCodeTests(unittest.TestCase):
    def test_code_comes_from_the_file_name(self) -> None:
        self.assertEqual(document_code("50059368620264047104_1_CONTRSOCIAL13.md"), "CONTRSOCIAL13")
        self.assertEqual(document_code("11_RENAJUD1.md"), "RENAJUD1")
        self.assertEqual(document_code("50059_1_CONTRSOCIAL13_2.md"), "CONTRSOCIAL13")

    def test_names_without_a_code_give_none(self) -> None:
        self.assertEqual(document_code("autos_PARTE_1.md"), "")
        self.assertEqual(document_code("Matricula_173490_cod16302.md"), "")
        self.assertEqual(document_code("AGR 5006_082b400c4dd7a85c43cbfd84dc4a565f.md"), "")
        self.assertEqual(document_code(""), "")


class AnchorTests(unittest.TestCase):
    def test_anchor_carries_the_document_code(self) -> None:
        text = add_sheet_anchors(STANDALONE_A, NAME_A)
        self.assertIn("<!-- pagina-origem: 1 -->\n[CONTRSOCIAL13 · fl. 1]", text)
        self.assertIn("<!-- pagina-origem: 3 -->\n[CONTRSOCIAL13 · fl. 3]", text)
        self.assertEqual(add_sheet_anchors(text, NAME_A), text)  # idempotente

    def test_without_a_code_the_anchor_is_as_before(self) -> None:
        self.assertIn("<!-- pagina-origem: 1 -->\n[fl. 1]", add_sheet_anchors(STANDALONE_A, "contrato.md"))
        self.assertIn("<!-- pagina-origem: 1 -->\n[fl. 1]", add_sheet_anchors(STANDALONE_A))

    def test_eproc_anchor_is_unchanged_even_with_a_code_in_the_name(self) -> None:
        text = add_sheet_anchors(STAMPED, "autos_1_CONTRSOCIAL9.md")
        self.assertIn("[fl. 1 · Evento 1 · CONTRSOCIAL2 · p. 1]", text)
        self.assertEqual(text, add_sheet_anchors(STAMPED))


class IndexTests(unittest.TestCase):
    def test_index_gives_company_act_registry_and_sheets(self) -> None:
        index = build_index([(NAME_A, STANDALONE_A)])
        self.assertIn(
            "documento avulso CONTRSOCIAL13, folhas 1–3 · empresa [EMPRESA_5] · "
            "ato alteração e consolidação · registro na Junta Comercial 12/03/2019.",
            index,
        )

    def test_no_facts_at_all_gives_only_code_and_sheets(self) -> None:
        index = build_index([("11_RENAJUD1.md", "<!-- pagina-origem: 1 -->\nsaldo")])
        self.assertIn("documento avulso RENAJUD1, folhas 1–1.", index)

    def test_partial_facts_mark_the_rest_with_a_dash(self) -> None:
        index = build_index([("5_1_CONTRSOCIAL3.md", STANDALONE_B)])
        self.assertIn(
            "documento avulso CONTRSOCIAL3, folhas 1–1 · empresa — · ato constituição · "
            "registro na Junta Comercial 05/10/2020.",
            index,
        )

    def test_no_code_and_no_facts_keeps_the_old_line(self) -> None:
        index = build_index([("extrato.md", "<!-- pagina-origem: 1 -->\nsaldo")])
        self.assertIn("Sem carimbo nem marcador de evento: documento avulso, folhas 1–1.", index)

    def test_file_with_a_stamp_is_not_standalone(self) -> None:
        self.assertNotIn("documento avulso", build_index([("5005_1_CONTRSOCIAL2.md", STAMPED)]))


class FactsTests(unittest.TestCase):
    def test_registry_forms_and_acts(self) -> None:
        b = standalone_facts(STANDALONE_B, "x_1_CONTRSOCIAL3.md")
        self.assertEqual((b.dates, b.companies, b.act), (["05/10/2020"], [], "constituição"))
        c = standalone_facts(STANDALONE_C, "x_1_CONTRSOCIAL4.md")
        self.assertEqual((c.dates, c.companies, c.act), (["15/02/2021"], ["[EMPRESA_7]"], "alteração"))
        self.assertEqual(standalone_facts("<!-- pagina-origem: 1 -->\nsaldo", "").act, "")

    def test_two_registrations_and_two_companies_are_both_listed(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\nCertifico o registro sob o nº 1 em 01/02/2010 da empresa [EMPRESA_1] LTDA, NIRE 1 e\n"
            "<!-- pagina-origem: 2 -->\nCertifico o registro sob o nº 2 em 03/04/2012 da empresa [EMPRESA_2] LTDA, NIRE 1 e\n"
            "<!-- pagina-origem: 3 -->\nCertifico o registro sob o nº 2 em 03/04/2012 da empresa [EMPRESA_2] LTDA, NIRE 1 e\n"
        )
        facts = standalone_facts(text, "")
        self.assertEqual(facts.dates, ["01/02/2010", "03/04/2012"])
        self.assertEqual(facts.companies, ["[EMPRESA_1]", "[EMPRESA_2]"])

    def test_impossible_date_is_ignored(self) -> None:
        text = "<!-- pagina-origem: 1 -->\nCertifico o registro em: 45/13/2020 sob nº: 7"
        self.assertEqual(standalone_facts(text, "").dates, [])

    def test_company_not_tokenized_is_not_copied(self) -> None:
        text = "<!-- pagina-origem: 1 -->\nCertifico o registro sob o nº 1 em 01/02/2010 da empresa Fulana Teste LTDA, NIRE 1 e"
        self.assertEqual(standalone_facts(text, "").companies, [])

    def test_alteration_of_the_constitutive_act_is_not_a_constitution(self) -> None:
        text = "<!-- pagina-origem: 1 -->\nSEGUNDA ALTERAÇÃO DE ATO CONSTITUTIVO DA EMPRESA [EMPRESA_1] LTDA"
        self.assertEqual(standalone_facts(text, "x_1_CONTRSOCIAL2.md").act, "alteração")

    def test_three_acts_are_listed_in_order(self) -> None:
        text = "<!-- pagina-origem: 1 -->\nContrato de constituição. Alteração. Consolidação do contrato."
        self.assertEqual(standalone_facts(text, "x_1_CONTRSOCIAL2.md").act, "constituição, alteração e consolidação")

    def test_petition_that_cites_an_amendment_gets_no_act(self) -> None:
        text = "<!-- pagina-origem: 1 -->\nA alteração e consolidação do contrato social foi feita em 2010."
        self.assertEqual(standalone_facts(text, "x_1_INIC1.md").act, "")

    def test_ficha_cadastral_is_not_an_act_of_registry(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n[fl. 1]\nFicha Cadastral \n09/06/2026 09:35\n"
            "| Nome da Empresa: [EMPRESA_3] |  |\nHistórico: alteração e consolidação de contrato.\n"
        )
        facts = standalone_facts(text, "x_1_CONTRSOCIAL3.md")
        self.assertEqual(
            (facts.act, facts.issued, facts.companies, facts.dates),
            ("ficha cadastral da Junta Comercial", "09/06/2026", ["[EMPRESA_3]"], []),
        )
        index = build_index([("x_1_CONTRSOCIAL3.md", text)])
        self.assertIn("empresa [EMPRESA_3] · ato ficha cadastral da Junta Comercial · emitida em 09/06/2026", index)


class CastTests(unittest.TestCase):
    CONTRACT = (
        "<!-- pagina-origem: 1 -->\n"
        "Certifico o registro sob o nº 1 em {date} da empresa [EMPRESA_1] LTDA, NIRE 1 e\n"
        "{partners}\n"
        "Todos únicos sócios componentes da sociedade, resolvem alterar o contrato.\n"
        "DA ADMINISTRAÇÃO\n"
        "A administração da sociedade é exercida pelos sócios [PESSOA_1] e/ou [PESSOA_3].\n"
    )

    def _doc(self, number: int, date: str, partners: str) -> tuple[str, str]:
        return (f"5005_1_CONTRSOCIAL{number}.md", self.CONTRACT.format(date=date, partners=partners))

    def test_roles_only_from_what_the_text_says(self) -> None:
        from anonimizador_app.lote_elenco import contract_roles

        _name, text = self._doc(3, "01/02/2010", "[PESSOA_1], brasileiro, casado, inscrito no CPF [CPF_1];\n[PESSOA_2], brasileira, solteira.")
        partners, admins = contract_roles(text)
        self.assertEqual(partners, ["[PESSOA_1]", "[PESSOA_2]"])
        self.assertEqual(admins, ["[PESSOA_1]", "[PESSOA_3]"])
        # token solto no preambulo, sem qualificacao, nao e socio
        self.assertEqual(contract_roles("DE [PESSOA_9] e [PESSOA_8]\nresolvem alterar")[0], [])

    def test_cast_lists_contracts_by_company_and_date_with_ranges_of_files(self) -> None:
        from anonimizador_app.lote_elenco import build_cast

        docs = [
            self._doc(5, "03/04/2012", "[PESSOA_1], brasileiro, casado."),
            self._doc(3, "01/02/2010", "[PESSOA_1], brasileiro, casado."),
            self._doc(4, "05/06/2011", "[PESSOA_1], brasileiro, casado."),
        ]
        cast = build_cast(docs)
        self.assertIn("## Sócios e administradores dos contratos avulsos", cast)
        rows = [line for line in cast.splitlines() if line.startswith("| CONTRSOCIAL")]
        self.assertEqual([row.split(" | ")[0] for row in rows], ["| CONTRSOCIAL3", "| CONTRSOCIAL4", "| CONTRSOCIAL5"])
        self.assertIn("| CONTRSOCIAL3 | [EMPRESA_1] | 01/02/2010 | [PESSOA_1] | [PESSOA_1], [PESSOA_3] |", cast)
        self.assertIn("CONTRSOCIAL3–5", cast)  # coluna Arquivos: faixa, nao tres nomes

    def test_eproc_lot_has_no_contract_section_and_keeps_file_names(self) -> None:
        from anonimizador_app.lote_elenco import build_cast

        cast = build_cast([("autos_PARTE_1.md", STAMPED + "[PESSOA_1] e [EMPRESA_1]\n")])
        self.assertNotIn("contratos avulsos", cast)
        self.assertIn("autos_PARTE_1.md", cast)


if __name__ == "__main__":
    unittest.main()
