"""v0.4.15 (deteccao 2.21.0): matricula de novo em claro (decisao do Operador); veto de fusao de empresas no caminho com
LLM (nome canonico do LLM, CNPJ diferente, grupos ·G); nome do elenco vence o sobrenome de familia.

Todos os nomes e numeros sao inventados (os CNPJs tem digito verificador calculado aqui).
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.lote_elenco import company_groups
from anonimizador_app.roster import Mention, Roster, company_canonical_fits
from anonimizador_app.roster_identity import fuse_identities
from anonimizador_app.settings import AppSettings
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

from test_llm_hygiene import _Pipeline
from test_variant_propagation import VariantDiscoverer, _page


def _cnpj(base: str, branch: str = "0001") -> str:
    digits = [int(char) for char in base + branch]
    for weights in ((5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)):
        rest = sum(d * w for d, w in zip(digits, weights)) % 11
        digits.append(0 if rest < 2 else 11 - rest)
    raw = "".join(map(str, digits))
    return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}/{raw[8:12]}-{raw[12:]}"


class VersaoTest(unittest.TestCase):
    def test_versoes(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.16")  # v0.4.16 sobe as duas (tests/test_v0416.py)
        self.assertEqual(DETECTION_VERSION, "2.22.0")


# ------------------------------------------------------------------------------ 1. veto de fusao de empresas (LLM)
class CanonicoDoLlmTests(unittest.TestCase):
    def test_canonico_so_liga_o_mesmo_nome(self) -> None:
        full = "Beta Comercio de Pecas Ltda"
        for text in ("BETA COMERCIO DE PECAS LTDA", "BETACOMERCIODEPECASLTDA", "Beta Comercio de Pecaz",
                     "www.betacomercio.com.br", "Betta"):
            with self.subTest(text=text):
                self.assertTrue(company_canonical_fits(text, full))
        for text in ("Marca Gaivota", "Beta Comercio", "Delta Textil Ltda", "Comercio de Pecas"):
            with self.subTest(text=text):
                self.assertFalse(company_canonical_fits(text, full))

    def test_marca_com_canonico_de_outra_empresa_fica_separada(self) -> None:
        roster = Roster()
        roster.add_mention(Mention(kind="empresa", text="Beta Comercio de Pecas Ltda", canonical="Beta Comercio de Pecas Ltda"))
        roster.add_mention(Mention(kind="empresa", text="Gaivota Kids", canonical="Beta Comercio de Pecas Ltda"))
        brand = roster.lookup("empresa", "Gaivota Kids")
        self.assertIsNotNone(brand)
        self.assertNotEqual(brand.key, roster.lookup("empresa", "Beta Comercio de Pecas Ltda").key)

    def test_forma_afirmada_de_duas_empresas_nao_funde_por_forma_curta(self) -> None:
        roster = Roster()
        for name in ("Beta Comercio de Pecas Ltda", "Gaivota Kids Textil Ltda"):
            roster.add_mention(Mention(kind="empresa", text=name, canonical=name))
        roster.add_mention(Mention(kind="empresa", text="Gaivota Kids", canonical="Beta Comercio de Pecas Ltda"))
        roster.add_mention(Mention(kind="empresa", text="Gaivota Kids", canonical="Gaivota Kids Textil Ltda"))
        self.assertTrue(roster.contested("empresa", "Gaivota Kids"))
        fuse_identities(roster, ["A Beta Comercio de Pecas Ltda e a Gaivota Kids Textil Ltda. A Gaivota Kids vende."])
        keys = {roster.lookup("empresa", value).key for value in ("Gaivota Kids", "Gaivota Kids Textil Ltda")}
        self.assertEqual(len(keys), 2)

    def test_cnpj_diferente_veta_a_fusao_por_ocr(self) -> None:
        first, second = _cnpj("11222333"), _cnpj("44555666")
        roster = Roster()
        roster.add_mention(Mention(kind="empresa", text="Gaivota Confeccoes Ltda", canonical="Gaivota Confeccoes Ltda"))
        roster.add_mention(Mention(kind="empresa", text="Gaivota Confeccoez Ltda", canonical="Gaivota Confeccoez Ltda"))
        text = f"A Gaivota Confeccoes Ltda, CNPJ {first}, comprou da Gaivota Confeccoez Ltda, CNPJ {second}."
        removed = fuse_identities(roster, [text])
        self.assertEqual(removed, {})
        self.assertGreaterEqual(roster.identity_reasons.get("veto_cnpj", 0), 1)
        # Sem CNPJ, a grafia de OCR continua sendo a mesma empresa.
        roster = Roster()
        roster.add_mention(Mention(kind="empresa", text="Gaivota Confeccoes Ltda", canonical="Gaivota Confeccoes Ltda"))
        roster.add_mention(Mention(kind="empresa", text="Gaivota Confeccoez Ltda", canonical="Gaivota Confeccoez Ltda"))
        self.assertEqual(len(fuse_identities(roster, ["A Gaivota Confeccoes Ltda e a Gaivota Confeccoez Ltda."])), 1)

    def test_denominacao_de_duas_raizes_nao_liga_a_ninguem(self) -> None:
        first, second = _cnpj("11222333"), _cnpj("44555666")
        roster = Roster()
        roster.add_mention(Mention(kind="empresa", text="Gaivota Confeccoes Ltda", canonical="Gaivota Confeccoes Ltda"))
        roster.add_mention(Mention(kind="empresa", text="Gaivota Confeccoez Ltda", canonical="Gaivota Confeccoez Ltda"))
        texts = [
            f"A Gaivota Confeccoes Ltda, CNPJ {first}, vendeu.\nDepois a Gaivota Confeccoes Ltda, CNPJ {second}, comprou.\n"
            f"A Gaivota Confeccoes Ltda e a Gaivota Confeccoez Ltda."
        ]
        self.assertEqual(fuse_identities(roster, texts), {})


class GrupoTests(unittest.TestCase):
    TEXT = (
        "A [EMPRESA_1] (antiga denominação: [EMPRESA_2]) vendeu.\n"
        "Vendedora: [EMPRESA_1], CNPJ [CNPJ_1].\n"
        "Compradora: [EMPRESA_2], CNPJ [CNPJ_2].\n"
    )

    def test_cnpj_diferente_nao_forma_grupo(self) -> None:
        self.assertEqual(company_groups([("doc_1.md", self.TEXT)], cnpj_roots={}), [])

    def test_matriz_e_filial_formam_grupo(self) -> None:
        roots = {"[CNPJ_1]": "11222333", "[CNPJ_2]": "11222333"}
        groups = company_groups([("doc_1.md", self.TEXT)], cnpj_roots=roots)
        self.assertEqual([group.members for group in groups], [("[EMPRESA_1]", "[EMPRESA_2]")])

    def test_cnpj_depois_do_ponto_do_ltda_tambem_veta(self) -> None:
        text = (
            "A [EMPRESA_1], atualmente denominada [EMPRESA_2], deve.\n"
            "A [EMPRESA_1]. (CNPJ nº [CNPJ_1]) vendeu. A [EMPRESA_2]. (CNPJ nº [CNPJ_2]) comprou.\n"
        )
        self.assertEqual(
            [group.members for group in company_groups([("doc_1.md", text)], cnpj_roots={})
             if "[EMPRESA_1]" in group.members and "[EMPRESA_2]" in group.members],
            [],
        )


class NomeDoElencoVenceSobrenomeTests(_Pipeline):
    def test_nome_inteiro_do_llm_vence_o_sobrenome_de_familia(self) -> None:
        text = _page(
            "Socios: OTAVIO ARNO REICHERT e DENISE MARIA REICHERT.",
            "Filiacao do socio: MAE: HILDA REICHERT.",
        )
        settings = AppSettings(
            target_people=["Otavio Arno Reichert", "Denise Maria Reichert"], cnpj_lookup_enabled=False,
            local_ner_enabled=False,
        )
        discoverer = VariantDiscoverer(
            [("pessoa", "OTAVIO ARNO REICHERT", "socio"), ("pessoa", "DENISE MARIA REICHERT", "socio"),
             ("pessoa", "HILDA REICHERT", "terceiro")],
            [],
        )
        _result, body, _audit = self.go(text, discoverer, settings=settings, local_floor=True)
        self.assertNotIn("HILDA", body)
        self.assertNotIn("[SOBRENOME_", body)
        self.assertRegex(body, r"MAE: \[PESSOA_\d+\]\.")
        self.assertEqual(len(set(re.findall(r"\[PESSOA_\d+\]", body))), 3)


# ------------------------------------------------------------------------------------ 2. vazamentos e excesso (motor)
from anonimizador_juridico import EntitySpan, PseudonymMap, pseudonymize  # noqa: E402

from anonimizador_app.address_heuristic import extend_address_tails  # noqa: E402
from anonimizador_app.batch_propagation import BatchRoster  # noqa: E402
from anonimizador_app.fidelity import doctrine_reference, is_magistrate_or_citation  # noqa: E402
from anonimizador_app.header_heuristic import HeaderHeuristicEngine  # noqa: E402
from anonimizador_app.public_agents import private_evidence, public_agent_context  # noqa: E402
from anonimizador_app.target_list import PLACE_NAME_BEFORE, TargetListEngine  # noqa: E402


def _ner(text: str, *values: str) -> list[EntitySpan]:
    spans = []
    for value in values:
        start = -1
        while True:
            start = text.find(value, start + 1)
            if start < 0:
                break
            spans.append(EntitySpan(start=start, end=start + len(value), entity_type="PERSON", score=0.9,
                                    recognizer="NerLocal"))
    return spans


def _batch(text: str, *names: str) -> str:
    roster = BatchRoster()
    spans = _ner(text, *names)
    roster.add(text, spans)
    roster.review([text])
    return pseudonymize(text, roster.apply(text, spans), PseudonymMap()).text


class PrenomeSoltoTests(unittest.TestCase):
    def test_prenome_de_uma_pessoa_leva_o_token_dela(self) -> None:
        text = (
            "Otavio Arno Reichert ingressa na sociedade. Depois Otavio Arno Reichert assina.\n"
            "A administracao fica a cargo de Otavio. Segundo o Sr. Otavio, nada e devido.\n"
        )
        out = _batch(text, "Otavio Arno Reichert")
        self.assertNotIn("Otavio", out)
        self.assertEqual(len(set(re.findall(r"\[PESSOA_\d+\]", out))), 1)

    def test_prenome_de_duas_pessoas_vira_token_de_prenome(self) -> None:
        text = "Hilda Prado Vasques e Hilda Souza Marins assinaram. A socia Hilda retirou-se.\n"
        out = _batch(text, "Hilda Prado Vasques", "Hilda Souza Marins")
        self.assertIn("A socia [PRENOME_1] retirou-se", out)

    def test_prenome_em_lugar_ou_dentro_de_outro_nome_fica(self) -> None:
        text = (
            "Otavio Arno Reichert vendeu. Otavio Arno Reichert comprou.\n"
            "Mora na Colonia Dona Otavio e na Rua Otavio Mendes. O Relator Otavio Bastos Pires decidiu.\n"
        )
        out = _batch(text, "Otavio Arno Reichert")
        self.assertIn("Colonia Dona Otavio", out)
        self.assertIn("Rua Otavio Mendes", out)
        self.assertIn("Otavio Bastos Pires", out)


class EmpresaDaListaTests(unittest.TestCase):
    def test_sigla_da_lista_sozinha_e_a_empresa(self) -> None:
        engine = TargetListEngine(companies=["XPT Comercio de Pecas Ltda"], lookup_cnpj_names=False)
        text = "A XPT Comercio de Pecas Ltda vendeu. Depois a XPT pagou. EMITENTE XPT LTDA EPP. A XPT MODAS e outra."
        found = {text[s.start : s.end] for s in engine.detect(text)}
        self.assertIn("XPT", found)
        self.assertEqual(sum(1 for s in engine.detect(text) if text[s.start : s.end] == "XPT"), 2)

    def test_sigla_de_duas_letras_ou_palavra_comum_nao(self) -> None:
        engine = TargetListEngine(companies=["CR Comercial Ltda", "COSTA Doce Transportes Ltda"], lookup_cnpj_names=False)
        text = "Pagou CR 1.000,00 em dinheiro. A COSTA leste."
        self.assertEqual(engine.detect(text), [])

    def test_razao_social_antiga_depois_de_denominacao(self) -> None:
        text = (
            "Ademais, a denominação social passa a ser QUILATE GESTAO DE BENS. A sede segue.\n"
            "A empresa (denominação anterior: QUILATE MALHARIA LTDA.) tem sede. Adota os títulos de estabelecimento "
            "\"PEQUENO QUILATE e outros\". A administração passa a ser EXERCIDA PELO SOCIO.\n"
        )
        found = {text[s.start : s.end] for s in HeaderHeuristicEngine().detect(text) if s.entity_type == "ORGANIZATION"}
        self.assertTrue({"QUILATE GESTAO DE BENS", "QUILATE MALHARIA LTDA", "PEQUENO QUILATE"} <= found, found)
        self.assertNotIn("EXERCIDA PELO SOCIO", found)


class MarcaDoGrupoTests(unittest.TestCase):
    def test_marca_tem_token_proprio_e_nao_se_junta_a_razao_que_comeca_igual(self) -> None:
        text = (
            "A GAIVOTA KIDS TEXTIL LTDA vendeu. A MALHAS SOL LTDA, nome fantasia GAIVOTA KIDS, comprou.\n"
            "Depois a loja GAIVOTA KIDS fechou. A GAIVOTA KIDS TEXTIL LTDA pagou.\n"
        )
        spans = [s for s in HeaderHeuristicEngine().detect(text) if s.entity_type == "ORGANIZATION"]
        for value in ("GAIVOTA KIDS TEXTIL LTDA", "MALHAS SOL LTDA"):
            start = -1
            while (start := text.find(value, start + 1)) >= 0:
                if not any(s.start <= start < s.end for s in spans):
                    spans.append(EntitySpan(start=start, end=start + len(value), entity_type="ORGANIZATION",
                                            score=0.9, recognizer="NerLocal"))
        roster = BatchRoster()
        roster.add(text, spans)
        roster.review([text])
        out = pseudonymize(text, roster.apply(text, spans), PseudonymMap()).text
        self.assertNotIn("GAIVOTA", out)
        brand = re.search(r"fantasia (\[EMPRESA_\d+\])", out).group(1)
        company = re.search(r"A (\[EMPRESA_\d+\]) vendeu", out).group(1)
        self.assertNotEqual(brand, company)
        self.assertIn(f"loja {brand} fechou", out)


class EnderecoTests(unittest.TestCase):
    def test_complemento_e_bairro_entram_no_token(self) -> None:
        text = "Sede na Rua das Flores, nº 10, apto. 602, bairro Vila Nova, Cidade/UF. Filial na Rua B, nº 2, Galpão C 07.\n"
        first = text.index("Rua das Flores")
        second = text.index("Rua B")
        spans = [
            EntitySpan(start=first, end=text.index(", apto"), entity_type="LOCATION", score=0.9, recognizer="x"),
            EntitySpan(start=second, end=text.index(", Galp"), entity_type="LOCATION", score=0.9, recognizer="x"),
        ]
        out = [text[s.start : s.end] for s in extend_address_tails(text, spans)]
        self.assertEqual(out, ["Rua das Flores, nº 10, apto. 602, bairro Vila Nova", "Rua B, nº 2, Galpão C 07"])

    def test_complemento_nao_come_texto_comum(self) -> None:
        text = "Rua das Flores, nº 10, sala de reuniões do edifício.\n"
        span = EntitySpan(start=0, end=text.index(", sala"), entity_type="LOCATION", score=0.9, recognizer="x")
        self.assertEqual(text[: extend_address_tails(text, [span])[0].end], "Rua das Flores, nº 10, sala")


class AdvogadoTests(unittest.TestCase):
    def test_lista_de_procuradores_do_eproc_e_nome_antes_da_oab(self) -> None:
        text = (
            "| ALFA LTDA (00.000.000/0000-00) - Pessoa Jurídica Procurador(es): MARCO TULIO DA ROSA PR021604 "
            "CAMILA RIBAS PR063287 |\n"
            "Visto: JOAO PEDRO OTTON BRASIL OAB/PR 3.518 - FOLHA 04\nConselho Federal da OAB.\n"
        )
        found = {text[s.start : s.end] for s in HeaderHeuristicEngine().detect(text) if s.entity_type == "PERSON"}
        self.assertTrue({"MARCO TULIO DA ROSA", "CAMILA RIBAS", "JOAO PEDRO OTTON BRASIL"} <= found, found)
        self.assertNotIn("Conselho Federal", found)


class AgentePublicoECitacaoTests(unittest.TestCase):
    def test_usuario_do_sistema_e_agente_publico(self) -> None:
        text = "DATA: 01/02/2023 USUARIO: RAUL TEMPO NOVAES\nCNPJ: 11.222.333/0001-81 CPF RESPONSAVEL: 123.456.789-09\n"
        start = text.index("RAUL")
        end = start + len("RAUL TEMPO NOVAES")
        self.assertFalse(private_evidence(text, start, end))
        self.assertTrue(public_agent_context(text, start, end))
        eproc = "Evento 1 - Usuário: P013*******2 - RAUL TEMPO NOVAES - PROCURADOR - Processo: 1"
        start = eproc.index("RAUL")
        self.assertTrue(public_agent_context(eproc, start, start + len("RAUL TEMPO NOVAES")))

    def test_autor_de_doutrina_na_referencia(self) -> None:
        text = "13 MEDEIROS FILHO, Berenice Tavares. Curso de direito societário. Rio de Janeiro: Editora X, 2008, p. 140."
        start = text.index("Berenice")
        self.assertTrue(doctrine_reference(text, start, start + len("Berenice Tavares")))
        self.assertTrue(is_magistrate_or_citation(text, start, start + len("Berenice Tavares")))
        other = "O socio, Berenice Tavares. Assinou o contrato."
        start = other.index("Berenice")
        self.assertFalse(doctrine_reference(other, start, start + len("Berenice Tavares")))

    def test_lugar_com_prenome(self) -> None:
        self.assertIsNotNone(PLACE_NAME_BEFORE.search("Endereço: Estrada X, casa 3, Colônia Dona "))
        self.assertIsNone(PLACE_NAME_BEFORE.search("a socia "))


if __name__ == "__main__":
    unittest.main()
