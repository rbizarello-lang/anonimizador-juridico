from __future__ import annotations

import unittest

from anonimizador_app.lote_elenco import build_cast, cast_summary

CAPA = """### Capa do Processo

Nº do processo [PROCESSO_1]
Classe da ação:
 Incidente de Desconsideração de Personalidade Jurídica

Partes e Representantes

| REQUERENTE | REQUERIDO |
|---|---|
| UNIÃO - FAZENDA NACIONAL (00.394.460/0216-53) - Entidade [PESSOA_22] [EPROC_2] | [EMPRESA_1] ([CNPJ_1]) - Pessoa Jurídica Procurador(es): [PESSOA_12] [EPROC_1] |
|  | [PESSOA_10] ([CPF_1]) - Pessoa Física Procurador(es): [PESSOA_12] [EPROC_1] |
| PERITO |  |
| FULANO EM CLARO DA SILVA ([CPF_5]) |  |
| UNIDADE EXTERNA |  |
| Cartório 8º TABELIONATO DE NOTAS DE CURITIBA Procurador(es): BELTRANA EM CLARO |  |

Informações Adicionais
"""

CORPO = "O requerido [PESSOA_10] e sócio de [EMPRESA_1]. [EMPRESA_1] responde. Testemunha [PESSOA_40]."


class LoteElencoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cast = build_cast([("autos_PARTE_1.md", CAPA), ("autos_PARTE_2.md", CORPO)])

    def test_roles_from_cover_table(self) -> None:
        self.assertIn("## Processo [PROCESSO_1] — Incidente de Desconsideração de Personalidade Jurídica", self.cast)
        self.assertIn("| [EMPRESA_1] | requerido | pessoa jurídica | [PESSOA_12] | 3 |", self.cast)
        self.assertIn("| [PESSOA_10] | requerido | pessoa física | [PESSOA_12] | 2 |", self.cast)
        self.assertIn("| UNIÃO - FAZENDA NACIONAL | requerente | entidade | [PESSOA_22] |", self.cast)
        self.assertIn("| [PESSOA_12] | procurador(a) de [EMPRESA_1], [PESSOA_10] | pessoa |", self.cast)

    def test_names_in_clear_are_not_copied(self) -> None:
        self.assertNotIn("FULANO", self.cast)
        self.assertNotIn("BELTRANA", self.cast)
        self.assertIn("| parte sem token no texto | perito |", self.cast)
        self.assertIn("| Cartório 8º TABELIONATO DE NOTAS DE CURITIBA | unidade externa |", self.cast)

    def test_other_people_listed_with_counts(self) -> None:
        self.assertIn("| [PESSOA_40] | pessoa | 1 |  | autos_PARTE_2.md |", self.cast)


AGRAVO_GRADE = """## CAPA DO PROCESSO

Nº do processo [PROCESSO_3]
Classe da ação: Agravo de Instrumento
Processos relacionados:

Partes e Representantes

| AGRAVANTE | AGRAVADO |
|---|---|
| UNIÃO - FAZENDA NACIONAL (00.394.460/0216-53) - Entidade [PESSOA_30] [EPROC_2] | [EMPRESA_10] ([CNPJ_1]) - Pessoa Jurídica Procurador(es): [PESSOA_12] [EPROC_1] |

Informações Adicionais
[PROCESSO_1]/SC | Originário | Incidente de Desconsidera... | SCFLP10 |
"""

AGRAVO_INVERSO = """## CAPA DO PROCESSO

Nº do processo [PROCESSO_4]
Classe da ação:
 Agravo de Instrumento

Partes e Representantes

| AGRAVANTE | AGRAVADO |
|---|---|
| [EMPRESA_10] ([CNPJ_1]) - Pessoa Jurídica Procurador(es): [PESSOA_12] [EPROC_1] | UNIÃO - FAZENDA NACIONAL (00.394.460/0216-53) - Entidade |

Informações Adicionais
"""

CONTRATO = """[fl. 40 · Evento 7]
ALTERAÇÃO CONTRATUAL NOME INICIAL: [EMPRESA_1]. ALTERADO PARA: [EMPRESA_10]. CAPITAL SOCIAL: 10 quotas

[fl. 52]
Há alteração do nome social para [EMPRESA_1] e alteração do objeto social. Em 10/10/2010, há
nova alteração do nome social para [EMPRESA_10] e alteração do objeto social.
"""

CABECALHO = """AGRAVO DE INSTRUMENTO Nº [PROCESSO_3]
EXEQUENTE: UNIÃO - FAZENDA NACIONAL
EXECUTADO: [EMPRESA_2], [PESSOA_3]
EXECUTADOS: [PESSOA_3]
Em nome do executado [PESSOA_3].
"""


class TodasAsCapasTests(unittest.TestCase):
    """E13: a tabela de TODA capa do lote entra no elenco, em qualquer das formas da capa."""

    def setUp(self) -> None:
        self.cast = build_cast(
            [
                ("idpj.md", CAPA),
                ("agravo_a.md", AGRAVO_GRADE),
                ("agravo_b.md", AGRAVO_INVERSO),
                ("contrato.md", CONTRATO),
                ("peca.md", CABECALHO + " [EMPRESA_1] [EMPRESA_10] [PESSOA_3] [EMPRESA_2]"),
            ]
        )

    def test_every_cover_is_read(self) -> None:
        self.assertEqual(self.cast.count("## Processo "), 3)
        self.assertIn("## Processo [PROCESSO_3] — Agravo de Instrumento", self.cast)
        self.assertIn("## Processo [PROCESSO_4] — Agravo de Instrumento", self.cast)
        self.assertIn("| [EMPRESA_10·G1] | agravado | pessoa jurídica | [PESSOA_12] |", self.cast)
        self.assertIn("| [EMPRESA_10·G1] | agravante | pessoa jurídica | [PESSOA_12] |", self.cast)
        self.assertIn("| UNIÃO - FAZENDA NACIONAL | agravante | entidade | [PESSOA_30] |", self.cast)

    def test_process_label_is_the_process_of_the_cover_not_a_related_one(self) -> None:
        # A lista de relacionados traz outro [PROCESSO_n] perto da tabela.
        self.assertNotIn("## Processo [PROCESSO_1] — Agravo", self.cast)

    def test_class_is_read_on_the_same_line_or_on_the_next(self) -> None:
        self.assertIn("— Incidente de Desconsideração de Personalidade Jurídica", self.cast)
        self.assertIn("## Processo [PROCESSO_4] — Agravo de Instrumento", self.cast)

    def test_header_role_fills_the_party_that_has_no_cover(self) -> None:
        demais = self.cast.split("## Demais pessoas e empresas do lote")[1]
        self.assertIn("| [EMPRESA_2] | empresa | 2 | executado(a) (1) |", demais)
        self.assertIn("| [PESSOA_3] | pessoa | 4 | executado(a) (2) |", demais)

    def test_summary_counts_covers_and_roles(self) -> None:
        resumo = cast_summary(self.cast)
        self.assertEqual(resumo["capas"], 3)
        self.assertEqual(resumo["tokens_com_papel_de_cabecalho"], 2)
        self.assertGreaterEqual(resumo["tokens_com_papel_de_capa"], 3)


class VinculosTests(unittest.TestCase):
    """O vinculo entre tokens so sai quando o texto diz; os tokens nao se unem."""

    def _cast(self, *documents: tuple[str, str]) -> str:
        return build_cast(list(documents))

    def test_renamed_company_is_linked_when_the_document_states_the_renaming(self) -> None:
        cast = self._cast(("contrato.md", CONTRATO + " [EMPRESA_1] [EMPRESA_10]"))
        self.assertIn("| [EMPRESA_10·G1] | antiga denominação | [EMPRESA_1·G1] |", cast)
        self.assertIn("| [EMPRESA_1·G1] | denominação posterior | [EMPRESA_10·G1] |", cast)
        self.assertIn("contrato.md, fl. 40", cast)

    def test_successive_renamings_in_a_narrative_are_linked(self) -> None:
        texto = (
            "[fl. 9]\nEm 01/01/2000, há alteração do nome social para [EMPRESA_3] e do objeto. "
            "Em 05/05/2005, há nova alteração do nome social para [EMPRESA_7]. "
            "A [EMPRESA_7], antiga denominação [EMPRESA_3], consta da ficha."
        )
        cast = self._cast(("autos.md", texto))
        self.assertIn("| [EMPRESA_7·G1] | antiga denominação | [EMPRESA_3·G1] |", cast)

    def test_successive_renamings_alone_are_only_a_possible_link(self) -> None:
        # Duas empresas distintas, duas alteracoes proximas, nenhuma declaracao: sem grupo (I-N1).
        texto = (
            "[fl. 9]\nA [EMPRESA_3] fez alteração do nome social para [EMPRESA_7]. "
            "A [EMPRESA_8], outra empresa, fez alteração do nome social para [EMPRESA_9]."
        )
        cast = self._cast(("autos.md", texto))
        self.assertNotIn("·G", cast)
        self.assertNotIn("antiga denominação", cast)
        self.assertIn("possível vínculo", cast)

    def test_far_apart_mentions_are_not_a_renaming(self) -> None:
        texto = (
            "Há alteração do nome social para [EMPRESA_3]. "
            + "Texto de enchimento sem relacao. " * 80
            + "Há alteração do nome social para [EMPRESA_7]."
        )
        cast = self._cast(("autos.md", texto))
        self.assertNotIn("antiga denominação", cast)

    def test_same_cnpj_token_in_two_covers_links_the_two_company_tokens(self) -> None:
        cast = self._cast(("idpj.md", CAPA), ("agravo_a.md", AGRAVO_GRADE))
        # [EMPRESA_1] e [CNPJ_1] na capa do IDPJ; [EMPRESA_10] e o mesmo [CNPJ_1] na do agravo.
        self.assertIn("| [EMPRESA_1·G1] | mesmo CNPJ | [EMPRESA_10·G1] | [CNPJ_1] nas capas dos processos", cast)

    def test_different_cnpj_is_no_link(self) -> None:
        outro = AGRAVO_GRADE.replace("[CNPJ_1]", "[CNPJ_2]")
        cast = self._cast(("idpj.md", CAPA), ("agravo_a.md", outro))
        self.assertNotIn("mesmo CNPJ", cast)
        self.assertNotIn("## Vínculos entre tokens", cast)

    def test_similar_numbers_without_a_statement_are_no_link(self) -> None:
        cast = self._cast(("autos.md", "[EMPRESA_1] e [EMPRESA_2] aparecem juntas. Sucessora: [EMPRESA_5]."))
        self.assertNotIn("## Vínculos entre tokens", cast)

    def test_tokens_are_not_merged_and_no_names_leak(self) -> None:
        cast = self._cast(("contrato.md", CONTRATO + " [EMPRESA_1] [EMPRESA_10]"))
        self.assertIn("[EMPRESA_1·G1]", cast)
        self.assertIn("[EMPRESA_10·G1]", cast)
        self.assertNotIn("[EMPRESA_1]", cast)  # o token separado fica, mas sempre com o sufixo do grupo
        self.assertNotIn("FICTIC", cast)


if __name__ == "__main__":
    unittest.main()
