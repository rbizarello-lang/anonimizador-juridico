"""H9-h, item E: e-mail partido entre celulas de tabela ou entre linhas.

Textos e enderecos inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_juridico import PseudonymMap, StructuredDetector, pseudonymize, resolve_overlaps


def _run(text: str) -> tuple[str, list[tuple[str, str]]]:
    spans = resolve_overlaps(StructuredDetector().detect(text))
    result = pseudonymize(text, spans, PseudonymMap())
    return result.text, [(text[span.start : span.end], span.canonical_key) for span in spans if span.entity_type == "EMAIL_ADDRESS"]


class EmailPartidoTests(unittest.TestCase):
    def test_parte_local_e_dominio_em_celulas_vizinhas_viram_dois_tokens_iguais(self) -> None:
        text = "| Contato | fulano.silva |\n| @provedor.com |\n"
        out, found = _run(text)
        self.assertEqual(found[0][0], "fulano.silva")
        self.assertEqual(found[1][0], "@provedor.com")
        self.assertEqual({key for _value, key in found}, {"fulano.silva@provedor.com"})
        self.assertEqual(out, "| Contato | [EMAIL_1] |\n| [EMAIL_1] |\n")

    def test_barra_e_quebra_no_vao_ficam_fora_do_token(self) -> None:
        text = "Email: fulano.silva |\n| @provedor.com.br |"
        out, _found = _run(text)
        self.assertEqual(out, "Email: [EMAIL_1] |\n| [EMAIL_1] |")

    def test_mesmo_endereco_inteiro_em_outro_ponto_leva_o_mesmo_token(self) -> None:
        text = "| fulano.silva | @provedor.com |\n\nEscreva para Fulano.Silva@Provedor.com hoje."
        out, _found = _run(text)
        self.assertEqual(out.count("[EMAIL_1]"), 3)
        self.assertNotIn("[EMAIL_2]", out)

    def test_dois_enderecos_partidos_diferentes_tem_tokens_diferentes(self) -> None:
        text = "| ana.lima | @provedor.com |\n| beto.rocha | @provedor.com |"
        out, _found = _run(text)
        self.assertEqual(out, "| [EMAIL_1] | [EMAIL_1] |\n| [EMAIL_2] | [EMAIL_2] |")

    def test_email_inteiro_continua_um_token_so(self) -> None:
        out, found = _run("Escreva para fulano.silva@provedor.com.")
        self.assertEqual(out, "Escreva para [EMAIL_1].")
        self.assertEqual(len(found), 1)

    def test_espaco_simples_ou_dominio_sem_ponto_nao_e_email_partido(self) -> None:
        for text in ("o usuario fulano @ provedor.com assinou", "| Nome | @apelido |", "grupo @provedor.com sem barra"):
            with self.subTest(text=text):
                out, found = _run(text)
                self.assertEqual(out, text)
                self.assertEqual(found, [])

    def test_parte_local_curta_ou_numerica_nao_e_email_partido(self) -> None:
        for text in ("Ver fls. 12 |\n| @dominio.com.br", "| 12 |\n| @dominio.com.br", "| ab |\n| @dominio.com.br"):
            with self.subTest(text=text):
                out, found = _run(text)
                self.assertEqual(out, text)
                self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
