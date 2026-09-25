from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.header_heuristic import HeaderHeuristicEngine, extract_header_mentions
from anonimizador_app.local_ner import DEFAULT_THRESHOLD
from anonimizador_app.pipeline import run_job
from anonimizador_app.settings import AppSettings


CORPUS = Path(__file__).parent / "corpus" / "caso_001_processo_sintetico.md"

EPROC_COVER = """\
Tipo documento: Evento: CAPA PROCESSO abertura

Juiz(a):

GUSTAVO FICTICIO BARCELOS

## Partes e Representantes

| EXEQUENTE | EXECUTADO |
|-----------|-----------|
| UNIÃO - FAZENDA NACIONAL (CNPJ 00.000.000/0001-91) - Entidade ALEX SERRA TRIAGEM.GDCJ | Norte Inventado S.A. (CNPJ 12.ABC.345/01DE-35) - Pessoa Jurídica Procurador(es): Marina Ficticia Lopes RS053667 Adriano Maggio Teste RS115075 |

| Grande devedor: Não | Valor da Causa: R$ 90.235,07 |

## Listagem dos Eventos do Processo

| Evento | Descrição | Usuário |
|--------|-----------|---------|
| 85 | Juntada de Ordem Cumprida - (Norte Inventado S.A.) | SECJF |

PÁGINA DE SEPARAÇÃO

## Evento 1

A Uniao requereu SISBAJUD. O art. 185 do CTN foi citado. Evento 184.

Devedor Identificacao Norte Inventado S.A.              CGC: 12.ABC.345/01DE-35 Endereco Telefone R GAMA 120 CEP       Bairro                   Municipio                     UF
Procuradoria: SANTA MARIA Endereco: RUA DAS ACACIAS                      100

No Evento 50, Helena Corpo Texto Silva protocolou peticao.

## Evento 4

JFO04 - JOAO BATISTA BRITO OSORIO - MAGISTRADO
Documento eletronico assinado por JOAO BATISTA BRITO OSORIO, Juiz Federal
A conferencia da autenticidade esta no endereco eletronico http://www.trf4.jus.br/verifica.php
"""


class HeaderHeuristicTests(unittest.TestCase):
    def test_gliner_threshold_unchanged(self) -> None:
        self.assertEqual(DEFAULT_THRESHOLD, 0.75)

    def test_cover_table_and_cda_labels(self) -> None:
        mentions = extract_header_mentions(EPROC_COVER)
        orgs = {value for kind, value in mentions if kind == "ORGANIZATION"}
        people = {value for kind, value in mentions if kind == "PERSON"}
        places = {value for kind, value in mentions if kind == "LOCATION"}
        self.assertTrue(any("Norte Inventado" in item for item in orgs))
        self.assertIn("Marina Ficticia Lopes", people)
        self.assertIn("Adriano Maggio Teste", people)
        self.assertTrue(any("R GAMA 120" in item for item in places))
        self.assertTrue(any("RUA DAS ACACIAS" in item and "100" in item for item in places))
        joined = " ".join(value for _kind, value in mentions)
        self.assertNotIn("UNIÃO", joined)
        self.assertNotIn("Uniao", joined)
        self.assertNotIn("GUSTAVO FICTICIO BARCELOS", joined)
        self.assertNotIn("JOAO BATISTA BRITO OSORIO", joined)
        self.assertNotIn("Helena Corpo Texto Silva", joined)
        self.assertNotIn("SANTA MARIA", joined)

    def test_detect_tokenizes_cover_and_keeps_structure(self) -> None:
        engine = HeaderHeuristicEngine()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capa.md"
            source.write_text(EPROC_COVER, encoding="utf-8")
            job = run_job(
                [source],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_capa",
                contextual=engine,
                settings=AppSettings(local_ner_enabled=True),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertTrue(job.export_allowed)
        self.assertIn("[EMPRESA_1]", markdown)
        self.assertIn("[PESSOA_1]", markdown)
        self.assertIn("[ENDERECO_", markdown)
        self.assertNotIn("Norte Inventado S.A.", markdown)
        self.assertNotIn("Marina Ficticia Lopes", markdown)
        self.assertIn("UNIÃO - FAZENDA NACIONAL", markdown)
        self.assertIn("GUSTAVO FICTICIO BARCELOS", markdown)
        self.assertIn("JOAO BATISTA BRITO OSORIO", markdown)
        self.assertIn("Helena Corpo Texto Silva", markdown)
        self.assertIn("SISBAJUD", markdown)
        self.assertIn("R$ 90.235,07", markdown)
        self.assertIn("Evento 184", markdown)
        self.assertIn("art. 185 do CTN", markdown)
        self.assertIn("SANTA MARIA", markdown)

    def test_inline_advogado_and_executado_pessoa(self) -> None:
        text = (
            "EXEQUENTE: UNIÃO - FAZENDA NACIONAL\n"
            "EXECUTADO: Helena Ficticia da Silva\n"
            "ADVOGADO: Marina Ficticia Lopes\n"
            "\n## Listagem dos Eventos do Processo\n"
        )
        mentions = extract_header_mentions(text)
        people = {value for kind, value in mentions if kind == "PERSON"}
        orgs = {value for kind, value in mentions if kind == "ORGANIZATION"}
        self.assertIn("Helena Ficticia da Silva", people)
        self.assertIn("Marina Ficticia Lopes", people)
        self.assertFalse(any("UNIÃO" in item or "Uniao" in item for item in orgs))

    def test_petition_sediada_and_domiciliado_are_anchors(self) -> None:
        text = (
            "EXEQUENTE: UNIÃO - FAZENDA NACIONAL\n"
            "EXECUTADO: Norte Inventado Comercio Ltda\n"
            "\n## Listagem dos Eventos do Processo\n"
            "Evento 1\n"
            "A empresa, sediada à Av. das Palmeiras, 100, Centro, Cidade/PR, "
            "CEP 86.000-000, e o socio, residente e domiciliado na Rua das "
            "Laranjeiras, 50, Casa 1, Cidade/PR.\n"
        )
        mentions = extract_header_mentions(text)
        places = {value for kind, value in mentions if kind == "LOCATION"}
        self.assertTrue(any("Av. das Palmeiras" in item and "100" in item for item in places))
        self.assertTrue(any("Rua das Laranjeiras" in item and "50" in item for item in places))
        self.assertFalse(any("Cidade/PR" in item for item in places))

    def test_sede_para_passa_a_ser_and_linebreak_street(self) -> None:
        text = (
            "EXEQUENTE: UNIÃO - FAZENDA NACIONAL\n"
            "EXECUTADO: Norte Inventado Comercio Ltda\n"
            "\n## Listagem dos Eventos do Processo\n"
            "mudança da sua sede para a Rua Suindara, 177, Vila Yara.\n"
            "a sede societária passa a ser na RUA NEI JOSE DE OLIVEIRA MACHADO, 579.\n"
            "é criada filial na Av. das Palmeiras, 100.\n"
            "o socio, residente e domiciliado na Rua das\nLaranjeiras, 50.\n"
        )
        mentions = extract_header_mentions(text)
        places = {value for kind, value in mentions if kind == "LOCATION"}
        joined = " ".join(places)
        self.assertTrue(any("Suindara" in item and "177" in item for item in places))
        self.assertTrue(any("NEI JOSE" in item and "579" in item for item in places))
        self.assertTrue(any("Palmeiras" in item and "100" in item for item in places))
        self.assertTrue(any("Laranjeiras" in item and "50" in item for item in places), joined)
        self.assertFalse(any("Vila Yara" == item for item in places))

    def test_unlabeled_corpus_is_not_scanned(self) -> None:
        text = CORPUS.read_text(encoding="utf-8")
        engine = HeaderHeuristicEngine()
        values = [span.value_from(text) for span in engine.detect(text)]
        self.assertTrue(all("Helena" not in item for item in values))
        self.assertTrue(all("Horizonte" not in item for item in values))


if __name__ == "__main__":
    unittest.main()
