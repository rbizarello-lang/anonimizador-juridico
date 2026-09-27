from __future__ import annotations

import unittest

from anonimizador_app.lote_elenco import build_cast

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
        self.assertIn("| [PESSOA_40] | pessoa | 1 | autos_PARTE_2.md |", self.cast)


if __name__ == "__main__":
    unittest.main()
