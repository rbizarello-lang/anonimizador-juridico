"""v0.4.14 (deteccao 2.20.0): sobrenome de 2+ pessoas da lista nao herda o token de uma pessoa so; pedacos da mesma
pessoa colados viram um token; numeros com palavra-gatilho (matricula, conta, RG, NIRE).

Todos os nomes e numeros sao inventados.
"""

from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan, pseudonymize

from anonimizador_app.local_ner import CombinedEngine
from anonimizador_app.target_list import TargetListEngine
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

FAMILY = ["Denise Maria Vasconcelos Reichert", "Ana Luiza Vasconcelos Reichert Lobato", "Otavio Arno Reichert"]


class _FakeNer:
    provider = "ner"

    def __init__(self, *values: str) -> None:
        self.values = values

    def detect(self, text: str) -> list[EntitySpan]:
        spans = []
        for value in self.values:
            start = text.index(value)
            spans.append(EntitySpan(start=start, end=start + len(value), entity_type="PERSON", score=0.8,
                                    recognizer="NerLocal"))
        return spans

    def scan_residual(self, text: str) -> list[str]:
        return []


def _spans(text: str, *ner: str) -> dict[str, EntitySpan]:
    engines = [TargetListEngine(people=FAMILY, lookup_cnpj_names=False)]
    if ner:
        engines.append(_FakeNer(*ner))
    found = CombinedEngine(engines).detect(text)
    return {text[span.start : span.end]: span for span in found}


class VersaoTest(unittest.TestCase):
    def test_versoes(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.16")  # v0.4.16 sobe as duas (tests/test_v0416.py)
        self.assertEqual(DETECTION_VERSION, "2.22.0")


class SobrenomeDeFamiliaTests(unittest.TestCase):
    def test_sobrenome_de_outra_pessoa_nao_leva_token_da_listada(self) -> None:
        text = "Denise Maria Vasconcelos Reichert assinou. Depois Marta Prado Vasconcelos retirou-se."
        found = _spans(text)
        self.assertEqual(found["Vasconcelos"].entity_type, "FAMILY_NAME")
        self.assertNotEqual(found["Vasconcelos"].canonical_key, found["Denise Maria Vasconcelos Reichert"].canonical_key)

    def test_nome_inteiro_de_outra_pessoa_partido_na_linha_vira_token_proprio(self) -> None:
        text = "Denise Maria Vasconcelos Reichert assinou, e por Maria Helena\nPrado Vasconcelos. A sociedade."
        found = _spans(text, "Maria Helena\nPrado Vasconcelos")
        self.assertIn("Maria Helena\nPrado Vasconcelos", found)
        self.assertNotIn("Vasconcelos", found)
        self.assertNotIn(found["Maria Helena\nPrado Vasconcelos"].canonical_key, {
            found["Denise Maria Vasconcelos Reichert"].canonical_key
        })

    def test_outro_parente_com_o_mesmo_sobrenome_final(self) -> None:
        text = "Otavio Arno Reichert vendeu ao socio Egon Rudolf\nWilhelm Reichert. Fim."
        found = _spans(text, "Egon Rudolf\nWilhelm Reichert")
        self.assertIn("Egon Rudolf\nWilhelm Reichert", found)
        self.assertNotEqual(found["Egon Rudolf\nWilhelm Reichert"].canonical_key, found["Otavio Arno Reichert"].canonical_key)

    def test_nome_da_listada_sem_o_ultimo_sobrenome_um_token_so(self) -> None:
        text = "Denise Maria Vasconcelos Reichert e a socia remanescente, Ana Luiza Vasconcelos\nReichert. Fim."
        found = _spans(text)
        self.assertIn("Ana Luiza Vasconcelos\nReichert", found)
        result = pseudonymize(text, list(found.values()))
        self.assertIn("remanescente, [PESSOA_2]. Fim.", result.text)
        self.assertNotRegex(result.text, r"\[PESSOA_(\d+)\]\s+\[PESSOA_\1\]")

    def test_prenome_e_sobrenomes_compartilhados_ficam_com_a_dona_do_prenome(self) -> None:
        text = "Otavio Arno Reichert e Denise Maria Vasconcelos Reichert. Assina DENISE VASCONCELOS REICHERT."
        found = _spans(text)
        self.assertEqual(
            found["DENISE VASCONCELOS REICHERT"].canonical_key, found["Denise Maria Vasconcelos Reichert"].canonical_key
        )

    def test_sobrenome_de_uma_pessoa_so_continua_com_ela(self) -> None:
        text = "Ana Luiza Vasconcelos Reichert Lobato assinou. A socia Lobato retirou-se."
        found = _spans(text)
        self.assertEqual(found["Lobato"].canonical_key, found["Ana Luiza Vasconcelos Reichert Lobato"].canonical_key)


# ------------------------------------------------------------------------------------- 2. numeros com palavra-gatilho
from anonimizador_juridico import PseudonymMap, StructuredDetector, resolve_overlaps  # noqa: E402

from anonimizador_app.known_forms import KnownForms  # noqa: E402
from anonimizador_app.rules import build_recognizers  # noqa: E402
from anonimizador_app.settings import AppSettings  # noqa: E402
from anonimizador_app.table_labels import TableLabelsEngine  # noqa: E402

_DETECTOR = StructuredDetector(recognizers=build_recognizers(AppSettings()))


def _found(text: str, entity_type: str) -> list[str]:
    return [text[s.start : s.end] for s in resolve_overlaps(_DETECTOR.detect(text)) if s.entity_type == entity_type]


def _matricula(text: str) -> list[str]:
    from anonimizador_juridico.recognizers import BrMatriculaImovelRecognizer

    found = BrMatriculaImovelRecognizer().analyze(text, ["BR_MATRICULA_IMOVEL"])
    return [text[item.start : item.end] for item in sorted(found, key=lambda item: item.start)]


class MatriculaTests(unittest.TestCase):
    """v0.4.15 (decisao do Operador): a matricula volta a ficar EM CLARO; o reconhecedor com gatilho fica pronto, fora
    do detector (``MATRICULA_EM_CLARO``)."""

    def test_matricula_fica_em_claro_no_detector(self) -> None:
        from anonimizador_juridico import recognizers

        self.assertIs(recognizers.MATRICULA_EM_CLARO, True)
        for text in ("o imóvel de matrícula nº 48.207, do 1º Registro", "matrículas números 27.554 e 38.114"):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_MATRICULA_IMOVEL"), [])

    def test_matricula_com_gatilho(self) -> None:
        cases = (
            ("o imóvel de matrícula nº 48.207, do 1º Registro de Imóveis", ["48.207"]),
            ("única garantia existente (matrícula 172.360) não basta", ["172.360"]),
            ("é o de matrícula número 38.114, do 3º Registro", ["38.114"]),
            ("MATRÍCULA N° 38.114 - 3º OFÍCIO DE REGISTRO", ["38.114"]),
            ("imóveis de matrículas números 27.554 e 38.114 (alienado)", ["27.554", "38.114"]),
            ("lotes matriculados sob n° 30.111, 30.112 e 30.113. Consta", ["30.111", "30.112", "30.113"]),
            ("matrícula nº 006.271, do Registro", ["006.271"]),
        )
        for text, values in cases:
            with self.subTest(text=text):
                self.assertEqual(_matricula(text), values)

    def test_sem_gatilho_ou_outra_matricula_fica(self) -> None:
        for text in (
            "Matricula8123-4 do servidor",
            "matrícula funcional 123456",
            "de acordo com a matrícula evento 52, MATRIMOVEL4",
            "valor de R$ 48.207,00 em 12/03/2020",
        ):
            with self.subTest(text=text):
                self.assertEqual(_matricula(text), [])


class ContaTests(unittest.TestCase):
    def test_conta_com_numero(self) -> None:
        cases = (
            ("(conta corrente número 380005572). Como", ["380005572"]),
            ("(vide, por exemplo, a conta de número 8613307, início do", ["8613307"]),
            ("(vide contas correntes de números 380005572 e 2025).", ["380005572", "2025"]),
        )
        for text, values in cases:
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_CONTA_BANCARIA"), values)

    def test_valor_e_numero_curto_ficam(self) -> None:
        for text in ("prestação de contas de R$ 1.234,56", "a conta de número 12, ok", "conta de 2019 a 2020"):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_CONTA_BANCARIA"), [])

    def test_segunda_tabela_do_ccs_na_mesma_folha(self) -> None:
        block = "Instituição que\nTipo B/D/V\nAgência\nConta\npossui o B/D/V\n{bank}\n{kind}\n{ag}\n{conta}\nNome IF: X\n"
        text = (
            block.format(bank="BANCO ALFA", kind="Outros", ag="352", conta="519273")
            + "Processo 123, Página 22\n\n"
            + block.format(bank="BANCO BETA", kind="Conta Corrente", ag="278", conta="604815")
        )
        spans = TableLabelsEngine().detect(text)
        values = sorted(text[s.start : s.end] for s in spans if s.entity_type == "BR_CONTA_BANCARIA")
        self.assertEqual(values, ["278", "352", "519273", "604815"])


class RgTests(unittest.TestCase):
    def test_rg_barra_r(self) -> None:
        cases = (
            ("Cédula de Identidade nr 3/R-158.432, expedida", ["3/R-158.432"]),
            ("Identidade n. 3R-847.557, expedida pela", ["3R-847.557"]),
            ("Cédula de Identidade nr. 3/R-1.438.658, expedida", ["3/R-1.438.658"]),
            ("Cédula de Identidade nr 3/R-158 457, expedida", ["3/R-158 457"]),
            ("portadora da Identidade nº 3/R-176.778-PR e CPF", ["3/R-176.778"]),
            ("Identidade nº. 615,843-1, expedida", ["615,843-1"]),
            ("o socio, RG 3/R-176.778, assina", ["3/R-176.778"]),
        )
        for text, values in cases:
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_RG"), values)

    def test_mesmo_rg_mesmo_token(self) -> None:
        text = "Identidade nr 3/R-158.432 e depois Identidade n. 3R-158 432."
        out = pseudonymize(text, resolve_overlaps(_DETECTOR.detect(text)), mapping=PseudonymMap()).text
        self.assertEqual(out.count("[RG_1]"), 2)

    def test_identidade_profissional_fica(self) -> None:
        self.assertEqual(_found("Carteira de Identidade Profissional n° 40321 OAB", "BR_RG"), [])


class NireTests(unittest.TestCase):
    def test_nire_pelo_contexto_da_junta(self) -> None:
        cases = (
            ("na Junta Comercial do Estado do Parana sob o n°. 52300841693 em sessao", ["52300841693"]),
            ("registrados e arquivados\nsob n. 5230084169 na Junta Comercial do Estado", ["5230084169"]),
            ("Junta Comercial. Alteracoes registradas sob n. 5230084169.3 em sessoes", ["5230084169.3"]),
            ("ABC0001\n12/03/1990\n52300841693\nJUNTACOMERCIAL DO ESTADO", ["52300841693"]),
            ("JUNTA COMERCIAL DO ESTADO\n52300841693\nCERTIDAO", ["52300841693"]),
            ("de 14/05/2017 NIRE.63107422958\nCertifico", ["63107422958"]),
            ("NIRE 52 3 0084169 3, CNPJ", ["52 3 0084169 3"]),
        )
        for text, values in cases:
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_NIRE"), values)

    def test_sem_junta_nao_e_nire(self) -> None:
        for text in (
            "registrados sob n. 5230084169 em 2020",
            "inscrita no CNPJ sob nº 12345678000199",
            "Protocolo de atendimento 52300841693",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_NIRE"), [])

    def test_registro_do_carimbo_da_junta(self) -> None:
        text = "JUNTA COMERCIAL DO ESTADO\nCERTIFICO O REGISTRO EM: 09/03/2006 SOB N°: 20060355512\nProtocolo"
        self.assertEqual(_found(text, "BR_REGISTRO_JUNTA"), ["20060355512"])

    def test_nire_conhecido_partido_na_linha_do_carimbo(self) -> None:
        forms = KnownForms()
        forms.add("BR_NIRE", "", "52300841693")
        text = "Empresa: 52 3\n0084169 3 ALFA LTDA"
        found = forms.spans(text, [])
        self.assertEqual([text[s.start : s.end] for s in found], ["52 3\n0084169 3"])


if __name__ == "__main__":
    unittest.main()
