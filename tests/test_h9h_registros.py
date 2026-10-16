"""H9-h, item I: RG de SC "8/C.1.234.567" e registro da JUCESC com 11 digitos escritos como CPF.

Numeros inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_juridico import PseudonymMap, StructuredDetector, pseudonymize, resolve_overlaps


def _found(text: str) -> list[tuple[str, str]]:
    return [(span.entity_type, text[span.start : span.end]) for span in resolve_overlaps(StructuredDetector().detect(text))]


def _out(text: str) -> str:
    spans = resolve_overlaps(StructuredDetector().detect(text))
    return pseudonymize(text, spans, PseudonymMap()).text


class RgComLetraCTests(unittest.TestCase):
    def test_rg_sc_com_c_e_com_r(self) -> None:
        self.assertEqual(_found("RG 8/C.1.234.567."), [("BR_RG", "8/C.1.234.567")])
        self.assertEqual(_found("Documento 12/C.12.345.678 expedido"), [("BR_RG", "12/C.12.345.678")])
        self.assertEqual(_found("RG 8/R.1.964.808."), [("BR_RG", "8/R.1.964.808")])
        self.assertEqual(_out("portador do RG 8/C.1.234.567, SSP/SC"), "portador do RG [RG_1], SSP/SC")

    def test_citacao_e_fracao_nao_sao_rg(self) -> None:
        for text in ("fls. 8/C.1", "art. 8/C.1.23", "item 8/CF.1.234.567", "pagina 8/C", "Anexo 18/A.1.234.567"):
            with self.subTest(text=text):
                self.assertEqual([item for item in _found(text) if item[0] == "BR_RG"], [])


class RegistroDaJuntaTests(unittest.TestCase):
    def test_jucesc_e_junta_comercial_com_numero_no_formato_de_cpf(self) -> None:
        text = (
            "Arquivado na JUCESC sob registro nº 123.456.789-01 em 5 de março.\n"
            "Junta Comercial do Estado de Santa Catarina, registro 987.654.321-00.\n"
            "JUCESC 555.666.777-88\n"
        )
        found = [item for item in _found(text) if item[0] == "BR_NIRE"]
        self.assertEqual([value for _kind, value in found], ["123.456.789-01", "987.654.321-00", "555.666.777-88"])
        out = _out(text)
        self.assertIn("JUCESC sob registro nº [NIRE_1] em 5", out)
        self.assertIn("Santa Catarina, registro [NIRE_2].", out)

    def test_mesmo_registro_escrito_como_nire_tem_o_mesmo_token(self) -> None:
        out = _out("JUCESC registro 123.456.789-01. NIRE 12345678901.")
        self.assertEqual(out, "JUCESC registro [NIRE_1]. NIRE [NIRE_1].")

    def test_registro_nº_sozinho_so_vale_com_digito_verificador_de_cpf_errado(self) -> None:
        # 111.444.777-35 fecha como CPF: segue sendo CPF. 111.444.777-36 nao fecha: registro (NIRE).
        found = _found("registro nº 111.444.777-35 e registro nº 111.444.777-36.")
        self.assertEqual(found, [("BR_CPF", "111.444.777-35"), ("BR_NIRE", "111.444.777-36")])

    def test_cpf_rotulado_perto_da_junta_nao_vira_nire(self) -> None:
        for text in (
            "Junta Comercial e o CPF do sócio 111.444.777-36.",
            "JUCESC. Sócio: RG 12.345.678-9 e CPF 111.444.777-35.",
            "O CPF 111.444.777-35 consta na JUCESC.",
        ):
            with self.subTest(text=text):
                self.assertEqual([item for item in _found(text) if item[0] == "BR_NIRE"], [])

    def test_numero_no_formato_de_cpf_sem_junta_nem_registro_nao_vira_nire(self) -> None:
        self.assertEqual([item for item in _found("Anotação 123.456.789-01 da folha.") if item[0] == "BR_NIRE"], [])

    def test_junta_longe_ou_em_outra_linha_nao_conta(self) -> None:
        text = "JUCESC\n\nO número 123.456.789-01 foi anotado."
        self.assertEqual([item for item in _found(text) if item[0] == "BR_NIRE"], [])
        far = "Junta Comercial " + "x" * 60 + " 123.456.789-01"
        self.assertEqual([item for item in _found(far) if item[0] == "BR_NIRE"], [])


if __name__ == "__main__":
    unittest.main()
