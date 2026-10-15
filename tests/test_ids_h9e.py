"""H9-e: identificadores que nenhuma versao cobria (RG de SC, NIRE, CNH, celular com o 9 separado,
processo CNJ partido em duas linhas, URL com chave). Textos inventados; positivo e negativo de cada um."""

from __future__ import annotations

import unittest

from anonimizador_app.catalog import STRUCTURED_TAGS
from anonimizador_app.fidelity import drop_protected_spans
from anonimizador_app.rules import build_recognizers
from anonimizador_app.settings import AppSettings
from anonimizador_juridico import StructuredDetector
from anonimizador_juridico.pseudonymizer import canonicalize, pseudonymize


def _found(text: str, entity_type: str) -> list[str]:
    spans = StructuredDetector().detect(text)
    return [span.value_from(text) for span in spans if span.entity_type == entity_type]


class RgSantaCatarinaTests(unittest.TestCase):
    def test_rg_barra_r_pontuado(self) -> None:
        self.assertEqual(_found("RG 8/R.1.964.808, SSP/SC.", "BR_RG"), ["8/R.1.964.808"])
        self.assertEqual(_found("portador do 8/R.1.964.808 e de outro", "BR_RG"), ["8/R.1.964.808"])
        self.assertEqual(_found("RG: 1/R.234.567", "BR_RG"), ["1/R.234.567"])

    def test_negativos(self) -> None:
        self.assertEqual(_found("fls. 8/R. 12 do volume", "BR_RG"), [])
        self.assertEqual(_found("proporcao 8/R.1 apenas", "BR_RG"), [])
        self.assertEqual(_found("data 08/10/2025 e R$ 1.964.808,00", "BR_RG"), [])

    def test_mesma_pessoa_mesmo_canonico(self) -> None:
        self.assertEqual(canonicalize("BR_RG", "8/R.1.964.808"), canonicalize("BR_RG", "8/R.1.964.808 "))


class NireTests(unittest.TestCase):
    def test_nire_com_e_sem_separadores(self) -> None:
        self.assertEqual(_found("NIRE 42 2 0123456 7 da empresa", "BR_NIRE"), ["42 2 0123456 7"])
        self.assertEqual(_found("NIRE: 42201234567.", "BR_NIRE"), ["42201234567"])
        self.assertEqual(_found("Nire 42.2.0123456-7 consta", "BR_NIRE"), ["42.2.0123456-7"])
        self.assertEqual(_found("NIRE nº 42201234567 e CNPJ", "BR_NIRE"), ["42201234567"])

    def test_nire_vence_cpf_com_os_mesmos_digitos(self) -> None:
        # 52998224725 e um CPF valido; depois do rotulo NIRE e NIRE.
        text = "NIRE 52998224725 na Junta"
        spans = StructuredDetector().detect(text)
        self.assertEqual([span.entity_type for span in spans], ["BR_NIRE"])

    def test_negativos(self) -> None:
        self.assertEqual(_found("NIRE 4195861746 na junta", "BR_NIRE"), [])  # 10 digitos
        self.assertEqual(_found("valor R$ 42201234567,00 em juizo", "BR_NIRE"), [])
        self.assertEqual(_found("registro 42201234567 sem rotulo", "BR_NIRE"), [])
        self.assertEqual(_found("NIRE em 12/03/2019 e 01/02/2020", "BR_NIRE"), [])


class CnhTests(unittest.TestCase):
    def test_cnh_rotulada(self) -> None:
        self.assertEqual(_found("CNH nº 01234567890 valida", "BR_CNH"), ["01234567890"])
        self.assertEqual(_found("CNH: 01234567890", "BR_CNH"), ["01234567890"])
        self.assertEqual(
            _found("Carteira Nacional de Habilitação (CNH) nº 01234567890", "BR_CNH"),
            ["01234567890"],
        )
        self.assertEqual(
            _found("Carteira Nacional de Habilitacao 01234567890", "BR_CNH"), ["01234567890"]
        )

    def test_negativos(self) -> None:
        self.assertEqual(_found("CNH 1234567890", "BR_CNH"), [])  # 10 digitos
        self.assertEqual(_found("CNH 012345678901", "BR_CNH"), [])  # 12 digitos
        self.assertEqual(_found("valor R$ 01234567890,00", "BR_CNH"), [])
        self.assertEqual(_found("numero 01234567890 solto", "BR_CNH"), [])


class CelularNonoDigitoTests(unittest.TestCase):
    def test_nono_digito_separado(self) -> None:
        self.assertEqual(_found("Tel 47 9 8473-5213 ok", "BR_PHONE"), ["47 9 8473-5213"])
        self.assertEqual(_found("celular (47) 9 8473 5213.", "BR_PHONE"), ["(47) 9 8473 5213"])
        self.assertEqual(_found("fone 47 98473-5213", "BR_PHONE"), ["47 98473-5213"])

    def test_canonico_igual_com_e_sem_espaco(self) -> None:
        self.assertEqual(
            canonicalize("BR_PHONE", "47 9 8473-5213"), canonicalize("BR_PHONE", "(47) 98473-5213")
        )

    def test_negativos(self) -> None:
        self.assertEqual(_found("em 12/09/2025 as 9 8473", "BR_PHONE"), [])
        self.assertEqual(_found("R$ 1.234.567,89 e 9 8473", "BR_PHONE"), [])
        self.assertEqual(_found("47 9 8473", "BR_PHONE"), [])  # curto demais


class ProcessoQuebradoTests(unittest.TestCase):
    NUMERO = "5048714-11.2025.4.04.7200"

    def test_numero_quebrado_depois_do_hifen(self) -> None:
        text = "Processo 5048714-\n11.2025.4.04.7200 e outro."
        self.assertEqual(_found(text, "BR_PROCESSO"), ["5048714-\n11.2025.4.04.7200"])

    def test_numero_quebrado_entre_grupos(self) -> None:
        text = "autos 5048714-11.2025.\n4.04.7200 em curso"
        self.assertEqual(_found(text, "BR_PROCESSO"), ["5048714-11.2025.\n4.04.7200"])

    def test_numero_inteiro_continua(self) -> None:
        self.assertEqual(_found(f"Processo {self.NUMERO}.", "BR_PROCESSO"), [self.NUMERO])

    def test_troca_cobre_as_duas_linhas_sem_engolir_vizinho(self) -> None:
        text = "Ref. 5048714-\n11.2025.4.04.7200\nSegunda linha de texto."
        spans = [
            span for span in StructuredDetector().detect(text) if span.entity_type == "BR_PROCESSO"
        ]
        spans = drop_protected_spans(text, spans)
        self.assertEqual(len(spans), 1)
        out = pseudonymize(text, spans).text
        self.assertEqual(out, "Ref. [PROCESSO_1]\nSegunda linha de texto.")

    def test_negativos(self) -> None:
        # Duas quebras ou texto no meio: nao e o mesmo numero.
        self.assertEqual(_found("5048714-\n\n11.2025.4.04.7200", "BR_PROCESSO"), [])
        self.assertEqual(_found("5048714-\nfls 11.2025.4.04.7200", "BR_PROCESSO"), [])
        # DV errado continua fora, mesmo com a quebra.
        self.assertEqual(_found("5048714-00\n.2025.4.04.7200", "BR_PROCESSO"), [])
        self.assertEqual(_found("data 11.2025.4.04 e R$ 7200,00", "BR_PROCESSO"), [])


class UrlComChaveTests(unittest.TestCase):
    def test_url_com_chave_inteira(self) -> None:
        text = "veja https://eproc.jfsc.jus.br/verificar?chave1=ABC123&chave2=XYZ789. Fim"
        self.assertEqual(
            _found(text, "BR_URL"), ["https://eproc.jfsc.jus.br/verificar?chave1=ABC123&chave2=XYZ789"]
        )

    def test_url_com_codigo_entre_parenteses(self) -> None:
        text = "consulte (https://site.gov.br/verificar?codigo=A1B2C3)."
        self.assertEqual(_found(text, "BR_URL"), ["https://site.gov.br/verificar?codigo=A1B2C3"])

    def test_www_sem_esquema(self) -> None:
        self.assertEqual(
            _found("em www.exemplo.gov.br/doc?hash=9f8e7d", "BR_URL"), ["www.exemplo.gov.br/doc?hash=9f8e7d"]
        )

    def test_troca_vira_token_url(self) -> None:
        text = "Autenticidade: https://eproc.exemplo.jus.br/v?chave1=A1&chave2=B2 fim"
        spans = [span for span in StructuredDetector().detect(text) if span.entity_type == "BR_URL"]
        self.assertEqual(pseudonymize(text, spans).text, "Autenticidade: [URL_1] fim")

    def test_url_institucional_fica_em_claro(self) -> None:
        self.assertEqual(_found("site https://www.trf4.jus.br e www.trf4.jus.br", "BR_URL"), [])
        self.assertEqual(_found("https://www.trf4.jus.br/trf4/index.php", "BR_URL"), [])

    def test_url_com_query_sem_chave_fica_em_claro(self) -> None:
        self.assertEqual(
            _found("www.trf4.jus.br/x?acao=pagina_visualizar&id_pagina=2", "BR_URL"), []
        )

    def test_nao_toca_cda_nem_processo_administrativo(self) -> None:
        text = "CDA 80 2 24 012345-67 e processo administrativo 10120.123456/2024-11"
        spans = StructuredDetector().detect(text)
        self.assertEqual(
            [span.entity_type for span in spans if span.entity_type in {"BR_URL", "BR_NIRE", "BR_CNH"}],
            [],
        )


class CatalogoTests(unittest.TestCase):
    def test_novas_categorias_ligadas_por_padrao(self) -> None:
        enabled = AppSettings().enabled_tags
        for entity in ("BR_NIRE", "BR_CNH", "BR_URL"):
            self.assertTrue(enabled[entity], entity)
        self.assertIn("[NIRE_n]", {tag.token for tag in STRUCTURED_TAGS})

    def test_desligar_a_tag_tira_o_reconhecedor(self) -> None:
        settings = AppSettings()
        settings.enabled_tags["BR_NIRE"] = False
        names = {rec.name for rec in build_recognizers(settings)}
        self.assertNotIn("BrNireRecognizer", names)
        self.assertIn("BrCnhRecognizer", names)


if __name__ == "__main__":
    unittest.main()
