"""H9-e: segunda rodada da higiene das mencoes do LLM (lixo que sobreviveu a primeira no lote IDPJ real).

Textos e nomes inventados; sem rede, sem modelo. Cada motivo novo: descarta o lixo, deixa passar o nome de
verdade parecido, e entra na contagem da AUDITORIA.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.llm_hygiene import LlmHygieneStats, variant_related
from anonimizador_app.roster import TOKENIZE

from test_llm_hygiene import DOC_JUDGE, _Pipeline, mention, run
from test_pipeline_llm import FakeDiscoverer


def _kept(kind: str, value: str, text: str, **kwargs) -> bool:
    out, _stats = run([mention(kind, value, text, **kwargs)], text)
    return len(out) == 1


def _reason(kind: str, value: str, text: str, field: str, **kwargs) -> bool:
    out, stats = run([mention(kind, value, text, **kwargs)], text)
    return out == [] and getattr(stats, field) == 1


class CidadeEOrgaoTests(unittest.TestCase):
    TEXT = (
        "Foro de Balneário Camboriú/SC. Juízo da 19ª Vara Federal de Porto Alegre. "
        "Itajaí - SC, Joinville, SC e Blumenau (SC). Balneário Camboriú. Porto Alegre."
    )

    def test_cidade_de_duas_palavras_e_com_uf_nao_e_pessoa(self) -> None:
        for value in ("Balneário Camboriú/SC", "Balneário Camboriú", "Porto Alegre", "Itajaí - SC", "Joinville, SC",
                      "Blumenau (SC)"):
            with self.subTest(value=value):
                self.assertTrue(_reason("pessoa", value, self.TEXT, "geografia"))

    def test_cidade_como_empresa_tambem_cai(self) -> None:
        self.assertTrue(_reason("empresa", "Balneário Camboriú/SC", self.TEXT, "geografia"))

    def test_vara_de_cidade_e_orgao(self) -> None:
        out, stats = run([mention("pessoa", "19ª Vara Federal de Porto Alegre", self.TEXT)], self.TEXT)
        self.assertEqual((out, stats.descartadas), ([], 1))
        out, stats = run([mention("empresa", "Vara Federal de Porto Alegre", self.TEXT)], self.TEXT)
        self.assertEqual((out, stats.descartadas), ([], 1))

    def test_pessoa_com_nome_de_municipio_e_contexto_de_pessoa_fica(self) -> None:
        text = "O sócio Pedro Leopoldo, brasileiro, casado, foi citado. A executada Maria Helena, CPF 1."
        self.assertTrue(_kept("pessoa", "Pedro Leopoldo", text))
        self.assertTrue(_kept("pessoa", "Maria Helena", text))

    def test_nome_comum_de_pessoa_passa(self) -> None:
        text = "O sócio Carlos Eduardo Pereira de Balneário mora longe."
        self.assertTrue(_kept("pessoa", "Carlos Eduardo Pereira", text))

    def test_empresa_com_nome_de_cidade_no_meio_passa(self) -> None:
        text = "A Balneário Camboriú Materiais de Construção Ltda vendeu."
        self.assertTrue(_kept("empresa", "Balneário Camboriú Materiais de Construção Ltda", text))


class RotuloDeFormularioTests(unittest.TestCase):
    TEXT = (
        "DOCUMENTO DE CONSULTA INTERNA. USO EXCLUSIVO DO REGIN. Razão Social: ACME. Razao Social. "
        "Nº Processo Judicial: 12. Registro de Imóveis. Ministério da Fazenda. CARÁTER EXCEPCIONAL. "
        "sociedade empresária limitada. Nome Empresarial."
    )

    def test_rotulos_sem_numero_caem(self) -> None:
        for value in ("DOCUMENTO DE CONSULTA INTERNA", "USO EXCLUSIVO DO REGIN", "Razão Social", "Razao Social",
                      "Nome Empresarial", "Registro de Imóveis", "Ministério da Fazenda", "CARÁTER EXCEPCIONAL"):
            with self.subTest(value=value):
                out, stats = run([mention("pessoa", value, self.TEXT)], self.TEXT)
                self.assertEqual(out, [])
                self.assertEqual(stats.descartadas, 1)

    def test_rotulo_em_minuscula_cai_por_outro_motivo_mas_cai(self) -> None:
        out, stats = run([mention("empresa", "sociedade empresária limitada", self.TEXT)], self.TEXT)
        self.assertEqual(out, [])
        self.assertEqual(stats.descartadas, 1)

    def test_rotulo_com_prefixo_e_resto_de_rotulo(self) -> None:
        text = self.TEXT + " Razão Social Empresarial."
        self.assertTrue(_reason("empresa", "Razão Social Empresarial", text, "rotulo_formulario"))
        self.assertTrue(_reason("empresa", "Nº Processo Judicial:", self.TEXT, "rotulo_formulario"))

    def test_cartorio_generico_com_ordinal_e_cidade(self) -> None:
        text = "Certidão do 1º Registro de Imóveis de Itajaí."
        out, stats = run([mention("empresa", "1º Registro de Imóveis de Itajaí", text)], text)
        self.assertEqual((out, stats.descartadas), ([], 1))

    def test_nome_com_uma_palavra_propria_nao_e_rotulo(self) -> None:
        text = "Razão Social: Beta Industrial Ltda. Registro de Imóveis Fulano Dias Ltda."
        self.assertTrue(_kept("empresa", "Beta Industrial Ltda", text))
        self.assertTrue(_kept("empresa", "Fulano Dias Ltda", text))
        self.assertTrue(_kept("pessoa", "Olavo Ricardo Teixeira", "O sócio Olavo Ricardo Teixeira."))

    def test_arquivo_de_rotulos_existe_e_tem_a_lista(self) -> None:
        from anonimizador_app.llm_hygiene import LABELS_FILE

        content = LABELS_FILE.read_text(encoding="utf-8")
        self.assertIn("razao social", content)
        self.assertIn("registro de imoveis", content)


class ClausulaOrdinalTests(unittest.TestCase):
    TEXT = (
        "Cláusula Décima. DÉCIMA PRIMEIRA. Parágrafo Primeiro. Cláusula Décima Terceira. Artigo 5. "
        "Parágrafo único. Capítulo IV. Seção Segunda."
    )

    def test_clausula_paragrafo_e_ordinal_isolado_caem(self) -> None:
        for value in ("Cláusula Décima", "DÉCIMA PRIMEIRA", "Parágrafo Primeiro", "Cláusula Décima Terceira",
                      "Artigo 5", "Parágrafo único", "Capítulo IV", "Seção Segunda"):
            with self.subTest(value=value):
                out, stats = run([mention("pessoa", value, self.TEXT)], self.TEXT)
                self.assertEqual(out, [])
                self.assertEqual(stats.descartadas, 1)
        self.assertTrue(_reason("empresa", "Cláusula Décima", self.TEXT, "ordinal"))
        self.assertTrue(_reason("pessoa", "DÉCIMA PRIMEIRA", self.TEXT, "ordinal"))

    def test_nome_com_ordinal_dentro_passa(self) -> None:
        text = "Segundo Alves Pereira e Primeira Cidade Comercial Ltda assinaram."
        self.assertTrue(_kept("pessoa", "Segundo Alves Pereira", text))
        self.assertTrue(_kept("empresa", "Primeira Cidade Comercial Ltda", text))


class UrlEDominioTests(unittest.TestCase):
    TEXT = (
        "Consulte https://eproc.jfsc.jus.br/verificar?chave1=A1 e www.trf4.jus.br e portal.sc.gov.br e "
        "www.vidracariaalvorada.com.br e www.leg.br e mp.br."
    )

    def test_url_e_dominio_institucional_caem_em_qualquer_classe(self) -> None:
        for kind in ("pessoa", "empresa", "endereco"):
            for value in ("https://eproc.jfsc.jus.br/verificar?chave1=A1", "www.trf4.jus.br", "portal.sc.gov.br"):
                with self.subTest(kind=kind, value=value):
                    self.assertTrue(_reason(kind, value, self.TEXT, "url"))

    def test_dominio_de_empresa_privada_continua_como_empresa(self) -> None:
        self.assertTrue(_kept("empresa", "www.vidracariaalvorada.com.br", self.TEXT))
        self.assertFalse(_kept("pessoa", "www.vidracariaalvorada.com.br", self.TEXT))

    def test_url_privada_com_query_de_chave_cai(self) -> None:
        text = "veja www.vidracariaalvorada.com.br/v?chave=1"
        self.assertTrue(_reason("empresa", "www.vidracariaalvorada.com.br/v?chave=1", text, "url"))


class DuasPessoasTests(unittest.TestCase):
    TEXT = "Alessandro e sua filha. FULANO DE TAL E BELTRANA SILVA. Fulano Souza e Beltrana Lima. Comércio e Indústria Ltda."

    def test_duas_pessoas_na_mesma_mencao_caem(self) -> None:
        for value in ("Alessandro e sua filha", "FULANO DE TAL E BELTRANA SILVA", "Fulano Souza e Beltrana Lima"):
            with self.subTest(value=value):
                self.assertTrue(_reason("pessoa", value, self.TEXT, "duas_pessoas"))

    def test_empresa_com_e_no_nome_nao_e_afetada(self) -> None:
        self.assertTrue(_kept("empresa", "Comércio e Indústria Ltda", self.TEXT))
        text = "Souza e Lima Comércio Ltda e Alves e Filhos Ltda."
        self.assertTrue(_kept("empresa", "Souza e Lima Comércio Ltda", text))
        self.assertTrue(_kept("empresa", "Alves e Filhos Ltda", text))

    def test_pessoa_com_nome_normal_passa(self) -> None:
        text = "O sócio Olavo Ricardo Teixeira e a sócia Marlene Sampaio."
        self.assertTrue(_kept("pessoa", "Olavo Ricardo Teixeira", text))
        self.assertTrue(_kept("pessoa", "Marlene Sampaio", text))


class VarianteSemRelacaoTests(unittest.TestCase):
    TEXT = "A Beta Industrial Ltda (nome empresarial) usa www.betaindustrial.com.br e a sigla BI. Alex e Alexandre."

    def test_relacionadas(self) -> None:
        self.assertTrue(variant_related("empresa", "Beta Industrial Ltda", "Beta Industrial"))
        self.assertTrue(variant_related("empresa", "Beta Industrial Ltda", "www.betaindustrial.com.br"))
        self.assertTrue(variant_related("empresa", "Beta Industrial Ltda", "BETA INDUSTRIAL LTDA - ME"))
        self.assertTrue(variant_related("empresa", "Beta Industrial Ltda", "Beta Industrlal"))  # OCR
        self.assertTrue(variant_related("empresa", "Companhia Brasileira de Laticínios", "CBL"))
        self.assertTrue(variant_related("pessoa", "Ana Souza Lima", "Ana"))
        self.assertTrue(variant_related("pessoa", "Alexandre Pires", "Alex Pires"))

    def test_sem_relacao(self) -> None:
        self.assertFalse(variant_related("empresa", "Beta Industrial Ltda", "nome empresarial"))
        self.assertFalse(variant_related("empresa", "Beta Industrial Ltda", "Gama Comercial"))
        self.assertFalse(variant_related("empresa", "Beta Industrial Ltda", "https://eproc.jfsc.jus.br/x?chave=1"))
        self.assertFalse(variant_related("empresa", "Beta Industrial Ltda", "Ltda"))

    def test_variante_sem_relacao_que_nao_parece_nome_e_descartada_e_contada(self) -> None:
        text = "A Gama Ficticia Ltda e o Omega. Veja a Beta Industrial Ltda e o Epsilon."
        out, stats = run(
            [
                mention("empresa", "Omega", text, canonical="Beta Industrial Ltda"),
                mention("empresa", "Epsilon", text, canonical="Beta Industrial Ltda"),
                mention("empresa", "Beta Industrial Ltda", text, canonical="Beta Industrial Ltda"),
                mention("empresa", "Gama Ficticia Ltda", text, canonical="Gama Ficticia"),
            ],
            text,
        )
        self.assertEqual([item.text for item in out], ["Beta Industrial Ltda", "Gama Ficticia Ltda"])
        self.assertEqual((stats.variante, stats.variante_aceita), (2, 0))

    def test_nome_fantasia_de_varias_palavras_e_aceito_e_contado_a_parte(self) -> None:
        text = "A Alfa Comércio de Alimentos Ltda, conhecida como Padaria Pão Quente."
        out, stats = run(
            [mention("empresa", "Padaria Pão Quente", text, canonical="Alfa Comércio de Alimentos Ltda")], text
        )
        self.assertEqual([item.text for item in out], ["Padaria Pão Quente"])
        self.assertEqual((stats.variante, stats.variante_aceita), (0, 1))
        self.assertIn("aceitas por parecerem nome proprio: 1", " ".join(stats.audit_lines()))

    def test_variante_relacionada_entra_na_mesma_entrada(self) -> None:
        text = "A Beta Industrial Ltda, www.betaindustrial.com.br."
        out, stats = run(
            [mention("empresa", "www.betaindustrial.com.br", text, canonical="Beta Industrial Ltda")], text
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(stats.variante, 0)


class BancoComoEnderecoTests(unittest.TestCase):
    def test_banco_sem_numero_nem_via_nao_e_endereco(self) -> None:
        text = "Bloqueio no Banco Alfa Ficticio SA e na Cooperativa de Crédito Beta. Rua do Banco, 12."
        self.assertTrue(_reason("endereco", "Banco Alfa Ficticio SA", text, "banco_endereco"))
        self.assertTrue(_reason("endereco", "Cooperativa de Crédito Beta", text, "banco_endereco"))
        self.assertTrue(_kept("endereco", "Rua do Banco, 12", text))

    def test_endereco_de_verdade_passa(self) -> None:
        text = "Mora na Rua das Acácias, 120, Centro."
        self.assertTrue(_kept("endereco", "Rua das Acácias, 120", text))


class AuditoriaH9eTests(unittest.TestCase):
    def test_linha_leva_as_contagens_novas_e_so_numeros(self) -> None:
        stats = LlmHygieneStats(rotulo_formulario=3, ordinal=4, url=5, duas_pessoas=6, variante=7, banco_endereco=8)
        line = stats.audit_lines()[0]
        self.assertIn(
            "rotulo de formulario 3, clausula ordinal 4, URL ou dominio 5, duas pessoas 6, "
            "variante sem relacao 7, banco como endereco 8)",
            line,
        )
        self.assertIn("descartadas 33 (", line)


DOC_BANCO_ENDERECO = """\
<!-- pagina-origem: 1 -->

A sócia MARLENE OLIVEIRA SAMPAIO foi citada. O sócio JOAO DA SILVA E COMERCIO LTDA respondeu.

Instituições da ordem:
00123 - ALFA FICTICIA SA
"""


class PipelineH9eTests(_Pipeline):
    def test_banco_do_sisbajud_classificado_como_endereco_continua_em_claro(self) -> None:
        # O LLM chamou o banco da lista da ordem de "endereco"; o juizo do lote o mantem em claro.
        entities = [
            ("pessoa", "MARLENE OLIVEIRA SAMPAIO", "socio"),
            ("endereco", "ALFA FICTICIA SA", "terceiro"),
        ]
        result, body, audit = self.go(DOC_BANCO_ENDERECO, FakeDiscoverer(entities))
        self.assertIn("00123 - ALFA FICTICIA SA", body)
        self.assertNotIn("MARLENE", body)
        self.assertNotIn("[ENDERECO_", body)
        self.assertRegex(audit, r"por instituicao financeira 1,")
        self.assertEqual(result.roster.lookup("pessoa", "MARLENE OLIVEIRA SAMPAIO").action, TOKENIZE)

    def test_segunda_higiene_nao_tira_protecao_de_socio_nem_de_empresa_com_e_no_nome(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "O sócio OLAVO RICARDO TEIXEIRA e sua filha, a sócia Marlene Sampaio Lima, com a SOUZA E LIMA COMERCIO LTDA, "
            "foram citados em Balneário Camboriú/SC (Cláusula Décima, Razão Social, www.trf4.jus.br).\n"
        )
        junk = [
            ("pessoa", "Balneário Camboriú/SC", "parte"),
            ("pessoa", "Cláusula Décima", "parte"),
            ("pessoa", "Razão Social", "parte"),
            ("pessoa", "www.trf4.jus.br", "parte"),
            ("pessoa", "OLAVO RICARDO TEIXEIRA e sua filha", "parte"),
        ]
        entities = [
            ("pessoa", "OLAVO RICARDO TEIXEIRA", "socio"),
            ("pessoa", "Marlene Sampaio Lima", "socio"),
            ("empresa", "SOUZA E LIMA COMERCIO LTDA", "parte"),
            *junk,
        ]
        _result, body, audit = self.go(text, FakeDiscoverer(entities))
        self.assertNotIn("OLAVO", body)
        self.assertNotIn("Marlene", body)
        self.assertNotIn("SOUZA E LIMA", body)
        for kept in ("e sua filha", "Balneário Camboriú/SC", "Cláusula Décima", "Razão Social", "www.trf4.jus.br"):
            self.assertIn(kept, body)
        line = next(item for item in audit.splitlines() if "Higiene das mencoes do LLM" in item)
        self.assertRegex(line, r"geografia 1, uma palavra 0, rotulo de formulario 1, clausula ordinal 1, URL ou dominio 1, duas pessoas 1")
        for forbidden in ("OLAVO", "Marlene", "Balneário", "Cláusula"):
            self.assertNotIn(forbidden, audit)


if __name__ == "__main__":
    unittest.main()
