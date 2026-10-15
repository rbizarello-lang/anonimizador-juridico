"""H9-f, parte 1: correcoes da revisao da higiene das mencoes do LLM e da URL com chave.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from test_llm_hygiene import mention, run


def _kept(kind: str, value: str, text: str, **kwargs) -> bool:
    out, _stats = run([mention(kind, value, text, **kwargs)], text)
    return len(out) == 1


class DuasPessoasH9fTests(unittest.TestCase):
    def test_nome_com_e_no_meio_e_um_nome(self) -> None:
        for value in ("João Pedro de Almeida e Silva", "MARIA DE SOUZA E SILVA", "Ana Paula Ferreira e Castro"):
            with self.subTest(value=value):
                text = f"O sócio {value}, brasileiro, foi citado."
                self.assertTrue(_kept("pessoa", value, text))

    def test_duas_pessoas_de_verdade_continuam_caindo(self) -> None:
        for value in ("Alessandro e sua filha", "Fulano Silva e Beltrana Souza", "Fulano e seu filho"):
            with self.subTest(value=value):
                text = f"Foram citados {value} ontem."
                out, stats = run([mention("pessoa", value, text)], text)
                self.assertEqual((out, stats.duas_pessoas), ([], 1))


class CidadeDeDuasPalavrasTests(unittest.TestCase):
    def test_intimem_se_e_demais_partes_nao_e_cidade(self) -> None:
        text = "Intimem-se Pedro Leopoldo e demais partes. Antônio Carlos compareceu. Paulo Lopes assinou."
        for value in ("Pedro Leopoldo", "Antônio Carlos", "Paulo Lopes"):
            with self.subTest(value=value):
                self.assertTrue(_kept("pessoa", value, text))

    def test_pista_distante_com_rotulo_ou_qualificacao(self) -> None:
        self.assertTrue(_kept("pessoa", "Maria Helena", "Nome: Maria Helena, residente na capital."))
        self.assertTrue(_kept("pessoa", "Maria Helena", "Maria Helena, viúva, brasileira, residente em Itajaí/SC."))
        self.assertTrue(
            _kept("pessoa", "Pedro Leopoldo", "Compareceu Pedro Leopoldo, nesta cidade, brasileiro, casado.")
        )

    def test_entrada_de_pessoa_do_elenco_fica(self) -> None:
        text = "Na comarca de Maria Helena/PR constou Maria Helena."
        out, _ = run([mention("pessoa", "Maria Helena", text, at=text.rfind("Maria Helena"))], text,
                     known_people=["Maria Helena Souza"])
        self.assertEqual(len(out), 1)

    def test_com_uf_ou_termo_de_lugar_continua_cidade(self) -> None:
        for text, value in (
            ("Foro da cidade de Pedro Leopoldo.", "Pedro Leopoldo"),
            ("Comarca de Antônio Carlos.", "Antônio Carlos"),
            ("Residente em Pedro Leopoldo/MG, bairro Centro.", "Pedro Leopoldo"),
            ("Distrito Federal, Paulo Lopes/SC.", "Paulo Lopes"),
            ("Município de Maria Helena, PR.", "Maria Helena"),
        ):
            with self.subTest(text=text):
                out, stats = run([mention("pessoa", value, text)], text)
                self.assertEqual((out, stats.descartadas), ([], 1))


class VarianteNomeFantasiaTests(unittest.TestCase):
    def test_nome_fantasia_e_apelido_de_varias_palavras_aceitos(self) -> None:
        text = "Alfa Comércio de Alimentos Ltda (Padaria Pão Quente). Joel Martinez Fagundes, vulgo Zé Gordo Alves."
        out, stats = run(
            [
                mention("empresa", "Padaria Pão Quente", text, canonical="Alfa Comércio de Alimentos Ltda"),
                mention("pessoa", "Zé Gordo Alves", text, canonical="Joel Martinez Fagundes"),
            ],
            text,
        )
        self.assertEqual(len(out), 2)
        self.assertEqual((stats.variante, stats.variante_aceita), (0, 2))

    def test_lixo_continua_descartado(self) -> None:
        text = "Alfa Comércio de Alimentos Ltda. dados cadastrais. Cadastro 2024. Ficha."
        out, stats = run(
            [
                mention("empresa", "dados cadastrais", text, canonical="Alfa Comércio de Alimentos Ltda"),
                mention("empresa", "Cadastro 2024", text, canonical="Alfa Comércio de Alimentos Ltda"),
                mention("empresa", "Ficha", text, canonical="Alfa Comércio de Alimentos Ltda"),
            ],
            text,
        )
        self.assertEqual(out, [])
        self.assertEqual(stats.variante_aceita, 0)


class UrlComChaveVenceProcessoTests(unittest.TestCase):
    def test_url_que_traz_processo_cnj_e_cpf_vira_um_unico_token(self) -> None:
        from anonimizador_juridico.detector import StructuredDetector
        from anonimizador_juridico.pseudonymizer import pseudonymize

        text = (
            "Veja https://x.jus.br/v?codigo=5048714-11.2025.4.04.7200&chave=XYZ99 e "
            "https://y.jus.br/c?cpf=52998224725&key=AB12 fim. Processo 5048714-11.2025.4.04.7200."
        )
        spans = StructuredDetector().detect(text)
        urls = [span for span in spans if span.entity_type == "BR_URL"]
        self.assertEqual(len(urls), 2)
        out = pseudonymize(text, spans).text
        self.assertNotIn("chave", out)
        self.assertNotIn("XYZ99", out)
        self.assertNotIn("key=", out)
        self.assertEqual(out.count("[URL_"), 2)
        self.assertIn("[PROCESSO_1]", out)  # o processo solto continua sendo processo


class EnderecoEEntidadesQueNaoSaoEntidadeTests(unittest.TestCase):
    def test_endereco_e_recortado_antes_da_cidade(self) -> None:
        text = (
            "Mora na Rua das Acácias, 120, Centro, Itajaí/SC. Sede na Avenida Brasil, 50, Joinville, SC. "
            "Escritório na Rua Sete, 9, Porto Alegre. Fica na Rua Quinze, 3, Itajaí, SC."
        )
        casos = {
            "Rua das Acácias, 120, Centro, Itajaí/SC": "Rua das Acácias, 120, Centro",
            "Avenida Brasil, 50, Joinville, SC": "Avenida Brasil, 50",
            "Rua Sete, 9, Porto Alegre": "Rua Sete, 9",
            "Rua Quinze, 3, Itajaí, SC": "Rua Quinze, 3",
        }
        for original, esperado in casos.items():
            with self.subTest(original=original):
                out, stats = run([mention("endereco", original, text)], text)
                self.assertEqual([item.text for item in out], [esperado])
                self.assertEqual(out[0].canonical, esperado)
                self.assertEqual(text[out[0].start : out[0].end], esperado)
                self.assertEqual(stats.endereco_recortado, 1)

    def test_endereco_sem_cidade_no_fim_nao_muda(self) -> None:
        text = "Mora na Rua das Acácias, 120, Centro. Outra, Rua do Porto, 30."
        out, stats = run([mention("endereco", "Rua das Acácias, 120, Centro", text)], text)
        self.assertEqual([item.text for item in out], ["Rua das Acácias, 120, Centro"])
        self.assertEqual(stats.endereco_recortado, 0)

    def test_marca_e_modelo_de_veiculo_nao_sao_empresa(self) -> None:
        text = "Veículos: Fiat Strada 2020, Honda CG 160. A Fiat Veículos Ltda vendeu. A Toyota do Brasil Ltda."
        for value in ("Fiat Strada 2020", "Honda CG 160"):
            with self.subTest(value=value):
                out, stats = run([mention("empresa", value, text)], text)
                self.assertEqual((out, stats.veiculo), ([], 1))
        for value in ("Fiat Veículos Ltda", "Toyota do Brasil Ltda"):
            with self.subTest(value=value):
                self.assertTrue(_kept("empresa", value, text))

    def test_url_sem_chave_nao_e_entidade_mas_dominio_nu_de_empresa_passa(self) -> None:
        text = "Veja https://www.exemplo-privado.com.br/produtos e https://www.trf4.jus.br e www.vidracaria-alvorada.com.br."
        for value in ("https://www.exemplo-privado.com.br/produtos", "https://www.trf4.jus.br"):
            with self.subTest(value=value):
                out, stats = run([mention("empresa", value, text)], text)
                self.assertEqual((out, stats.url), ([], 1))
        self.assertTrue(_kept("empresa", "www.vidracaria-alvorada.com.br", text))

    def test_linha_da_auditoria_leva_as_contagens(self) -> None:
        from anonimizador_app.llm_hygiene import LlmHygieneStats

        stats = LlmHygieneStats(veiculo=4, endereco_recortado=5)
        text = "\n".join(stats.audit_lines())
        self.assertIn("marca ou modelo de veiculo 4", text)
        self.assertIn("enderecos recortados antes da cidade 5", text)
