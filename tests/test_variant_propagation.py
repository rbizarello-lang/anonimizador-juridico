"""H9-f, item B: propagacao de variante aceita no elenco (marca curta, prenome, "familia X", OCR).

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.roster import Mention, Roster
from anonimizador_app.variant_propagation import COMMON_FILE, VariantPropagator, common_words

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer


class VariantDiscoverer(FakeDiscoverer):
    """Acha as entidades e, em cima, as variantes de uma palavra que o modelo apontou uma vez so."""

    def __init__(self, entities, variants) -> None:
        super().__init__(entities)
        self.variants = variants

    def discover(self, text: str):
        found = super().discover(text)
        for kind, value, role, canonical in self.variants:
            index = text.find(value)
            if index >= 0:
                found.mentions.append(
                    Mention(kind=kind, text=value, role=role, canonical=canonical, start=index,
                            end=index + len(value), source_text=value)
                )
        return found


def _page(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


class MarcaCurtaTests(_Pipeline):
    FULL = "Alfa Comércio de Alimentos Ltda"

    def test_marca_curta_e_propagada_com_o_mesmo_token(self) -> None:
        text = _page(
            f"A {self.FULL} foi citada e a {self.FULL} apresentou defesa.",
            "Depois a Alfa pagou o débito. Segundo a ALFA, nada é devido.",
            f"A {self.FULL} recorreu. Alfa Têxtil Ltda é outra empresa.",
        )
        discoverer = VariantDiscoverer(
            [("empresa", self.FULL, "parte")], [("empresa", "Alfa", "parte", self.FULL)]
        )
        result, body, audit = self.go(text, discoverer)
        # v0.4.11: "Alfa Têxtil Ltda" (outra razao social escrita no lote) abre com a mesma palavra: radical comum, a
        # marca curta nao diz de quem e e fica em claro (colisao e o pior erro; vazamento a revisao corrige).
        self.assertIn("Depois a Alfa pagou", body)
        self.assertIn("Segundo a ALFA,", body)
        self.assertIn("Alfa Têxtil", body)
        self.assertRegex(audit, r"Variantes propagadas pelo elenco: 0 ocorrencias \(marca curta 0, prenome 0")
        self.assertRegex(audit, r"recusadas por dicionario 0, geografia 0, protegido 0, ambiguidade 0, contexto 0")

    def test_palavra_comum_nao_propaga_sozinha_e_com_tres_mencoes_propaga(self) -> None:
        full = "Premium Fictício Ltda"
        two = _page(f"A {full} e a {full} compareceram.", "A Premium pagou.")
        discoverer = VariantDiscoverer([("empresa", full, "parte")], [("empresa", "Premium", "parte", full)])
        _result, body, audit = self.go(two, discoverer)
        self.assertIn("A Premium pagou", body)
        self.assertRegex(audit, r"marca curta 0.*recusadas por dicionario 1,")

        three = _page(f"A {full}, a {full} e a {full} compareceram.", "A Premium pagou.")
        _result, body, audit = self.go(three, VariantDiscoverer([("empresa", full, "parte")],
                                                                [("empresa", "Premium", "parte", full)]))
        self.assertNotIn("A Premium pagou", body)
        self.assertRegex(audit, r"marca curta 1")

    def test_uniao_nunca_propaga(self) -> None:
        full = "União Comercial de Grãos Ltda"
        text = _page(f"A {full}, a {full} e a {full} e a {full} compareceram.", "A União requereu.")
        discoverer = VariantDiscoverer([("empresa", full, "parte")], [("empresa", "União", "parte", full)])
        _result, body, audit = self.go(text, discoverer)
        self.assertIn("A União requereu", body)
        self.assertRegex(audit, r"marca curta 0")

    def test_municipio_nao_propaga(self) -> None:
        full = "Joinville Metais Ltda"
        text = _page(f"A {full} e a {full} compareceram.", "Foi em Joinville que a Joinville Metais Ltda atuou.")
        discoverer = VariantDiscoverer([("empresa", full, "parte")], [("empresa", "Joinville", "parte", full)])
        _result, body, _audit = self.go(text, discoverer)
        self.assertIn("em Joinville que", body)


class PessoaTests(_Pipeline):
    FULL = "Joel Martinez Tavernard"

    def test_familia_x_propaga_so_o_sobrenome(self) -> None:
        text = _page(
            f"O sócio {self.FULL} foi citado.",
            "A família Tavernard reside no local. As famílias Tavernard e Souza são vizinhas.",
        )
        _result, body, audit = self.go(text, FakeDiscoverer([("pessoa", self.FULL, "socio")]))
        # v0.4.11: o sobrenome de familia leva token de SOBRENOME, nao o da pessoa.
        token = re.search(r"\[SOBRENOME_\d+\]", body).group(0)
        self.assertIn(f"A família {token} reside", body)
        self.assertIn(f"As famílias {token} e Souza", body)
        self.assertNotIn("Tavernard", body)
        self.assertRegex(audit, r"sobrenome 2")

    def test_prenome_isolado_so_com_pista_de_pessoa(self) -> None:
        text = _page(
            f"O sócio {self.FULL} foi citado.",
            "Sr. Joel compareceu à audiência. Joel é um nome comum. O Sr. Joel Souza também veio.",
            "A filha de Joel disse que o executado Joel estava ausente.",
        )
        _result, body, audit = self.go(text, FakeDiscoverer([("pessoa", self.FULL, "socio")]))
        token = re.search(r"\[PESSOA_\d+\]", body).group(0)
        self.assertIn(f"Sr. {token} compareceu", body)
        self.assertIn("Joel é um nome comum", body)  # sem pista de pessoa
        self.assertIn("Sr. Joel Souza", body)  # abre outro nome
        self.assertIn(f"executado {token} estava", body)
        self.assertRegex(audit, r"prenome 3")

    def test_sobrenome_comum_exige_tres_mencoes_da_entrada_completa(self) -> None:
        full = "Pedro Alencar Silva"
        few = _page(f"O sócio {full} foi citado.", "A família Silva reside aqui.")
        _result, body, audit = self.go(few, FakeDiscoverer([("pessoa", full, "socio")]))
        self.assertIn("A família Silva reside", body)
        self.assertRegex(audit, r"sobrenome 0.*recusadas por dicionario 1,")

        many = _page(f"O sócio {full}, {full} e {full} foram citados.", "A família Silva reside aqui.")
        _result, body, _audit = self.go(many, FakeDiscoverer([("pessoa", full, "socio")]))
        self.assertNotIn("família Silva", body)
        self.assertRegex(body, r"A família \[SOBRENOME_\d+\] reside")

    def test_sobrenome_de_municipio_com_poucas_mencoes_fica(self) -> None:
        full = "Carlos Eduardo Santos"
        text = _page(f"O sócio {full} e {full} foram citados.", "A família Santos reside aqui.")
        _result, body, audit = self.go(text, FakeDiscoverer([("pessoa", full, "socio")]))
        # 2 mencoes da entrada completa: "Santos" (municipio e palavra comum) nao propaga.
        self.assertIn("A família Santos reside", body)
        self.assertRegex(audit, r"geografia 1")


class UnidadeTests(unittest.TestCase):
    def test_dicionario_tem_palavras_esperadas(self) -> None:
        words = common_words()
        for word in ("silva", "santos", "uniao", "capital", "central", "nacional", "porto", "serra"):
            self.assertIn(word, words)
        self.assertGreaterEqual(len(words), 150)
        self.assertTrue(COMMON_FILE.exists())

    def test_ocr_de_marca_com_similaridade_alta_propaga(self) -> None:
        roster = Roster()
        full = "Quintanilha Industrial Ltda"
        roster.add_mention(Mention(kind="empresa", text=full, canonical=full), source="llm")
        roster.add_mention(Mention(kind="empresa", text="Quintanilho", canonical=full), source="llm")
        text = _page(f"A {full} e a {full}.", "A Quintanilho pagou. A Quintal rezou.")
        propagator = VariantPropagator(roster)
        propagator.prepare([(text, None, [])])
        spans = propagator.spans(text, [])
        self.assertEqual([text[span.start : span.end] for span in spans], ["Quintanilho"])
        self.assertEqual(propagator.stats["ocr"], 1)
        self.assertEqual({span.canonical_key for span in spans}, {roster.lookup("empresa", full).key})

    def test_motor_local_exige_duas_mencoes_da_entrada_completa(self) -> None:
        roster = Roster()
        full = "Beltrano Quintanilha Ltda"
        roster.add_mention(Mention(kind="empresa", text=full, canonical=full), source="motor_local")
        roster.add_mention(Mention(kind="empresa", text="Beltrano", canonical=full), source="motor_local")
        one = _page(f"A {full} compareceu.", "A Beltrano pagou.")
        propagator = VariantPropagator(roster)
        propagator.prepare([(one, None, [])])
        self.assertEqual(propagator.spans(one, []), [])
        two = _page(f"A {full} e a {full} compareceram.", "A Beltrano pagou.")
        propagator = VariantPropagator(roster)
        propagator.prepare([(two, None, [])])
        self.assertEqual(len(propagator.spans(two, [])), 1)

    def test_mesma_palavra_em_duas_entradas_e_ambigua(self) -> None:
        roster = Roster()
        for full in ("Gamma Industrial Ltda", "Gamma Têxtil Ltda"):
            roster.add_mention(Mention(kind="empresa", text=full, canonical=full), source="llm")
            roster.add_mention(Mention(kind="empresa", text="Gamma", canonical=full), source="llm")
        text = _page("A Gamma Industrial Ltda e a Gamma Têxtil Ltda.", "A Gamma pagou.")
        propagator = VariantPropagator(roster)
        propagator.prepare([(text, None, [])])
        self.assertEqual(propagator.spans(text, []), [])
        self.assertGreaterEqual(propagator.stats["ambiguo"], 1)

    def test_linha_da_auditoria(self) -> None:
        propagator = VariantPropagator(Roster())
        propagator.stats.update(marca=3, prenome=2, sobrenome=1, ocr=1, dicionario=4, geografia=1, protegido=2)
        self.assertEqual(
            propagator.audit_line(),
            "Variantes propagadas pelo elenco: 7 ocorrencias (marca curta 3, prenome 2, sobrenome 1, OCR 1); "
            "recusadas por dicionario 4, geografia 1, protegido 2, ambiguidade 0, contexto 0",
        )
