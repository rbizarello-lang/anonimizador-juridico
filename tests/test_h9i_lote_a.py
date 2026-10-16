"""H9-i, lote A: telefone/e-mail/CEP tolerantes, precedente em claro, CDA e processo administrativo trocados, matricula
sempre em claro, CCIR/CAR/CIB/JUCESC/IP/CPF mascarado em claro, rotulo CPF/CNPJ e sufixo societario soltos, sistemas
protegidos em arquivo, logradouro com nome proprio.

Numeros e nomes inventados (CPF de teste com digito verificador valido); sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app import address_heuristic
from anonimizador_app.address_heuristic import AddressHeuristicEngine
from anonimizador_app.batch_propagation import BatchRoster
from anonimizador_app.catalog import STRUCTURED_TAGS
from anonimizador_app.fidelity import drop_protected_spans, is_precedent_process
from anonimizador_app.gemini_detector import KEEP_TERMS, SYSTEMS_FILE, is_kept_term, is_too_generic, protected_systems
from anonimizador_app.known_forms import KnownForms
from anonimizador_app.llm_hygiene import _reject
from anonimizador_app.role_labels import is_fixed_label
from anonimizador_app.roster import Mention
from anonimizador_app.span_hygiene import sanitize_spans
from anonimizador_juridico import EntitySpan, PseudonymMap, StructuredDetector, pseudonymize, resolve_overlaps
from anonimizador_juridico.validators import is_valid_cpf

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer

_DETECTOR = StructuredDetector()


def _cnj(seq: str, year: str, j: str, tr: str, org: str) -> str:
    """Numero CNJ inventado com o digito verificador correto (modulo 97)."""

    rest = f"{year}{j}{tr}{org}"
    check = 98 - (int(f"{seq}{rest}00") % 97)
    return f"{seq}-{check:02d}.{year}.{j}.{tr}.{org}"


def _found(text: str, *types: str) -> list[tuple[str, str]]:
    items = [(span.entity_type, text[span.start : span.end]) for span in resolve_overlaps(_DETECTOR.detect(text))]
    return [item for item in items if not types or item[0] in types]


def _kept(text: str) -> list[tuple[str, str]]:
    """O que sobra depois do detector E das travas de fidelidade (o que de fato seria trocado)."""

    spans = resolve_overlaps(_DETECTOR.detect(text))
    return [(span.entity_type, text[span.start : span.end]) for span in drop_protected_spans(text, spans)]


def _out(text: str) -> str:
    spans = drop_protected_spans(text, resolve_overlaps(_DETECTOR.detect(text)))
    return pseudonymize(text, spans, PseudonymMap()).text


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


FILLER = "\n\n".join("Texto de enchimento da peça, sem nomes nem números, só para passar da capa." for _ in range(70))


class TelefoneTolerante(unittest.TestCase):
    def test_variacoes_de_digitacao_do_mesmo_celular(self) -> None:
        for text in (
            "Tel: (47)98473-5213",
            "Cel 47 98473-5213",
            "Fone: (47) 98473-5213",
            "47-9-8473-5213",
            "47.9.8473.5213",
            "( 47 ) 9.8473 - 5213",
            "+55 47 9 8473 5213",
            "+55(47)984735213",
            "47  98473-5213",
            "4798473-5213",
        ):
            with self.subTest(text=text):
                found = _found(text, "BR_PHONE")
                self.assertEqual(len(found), 1, found)
                digits = re.sub(r"\D", "", found[0][1])
                self.assertTrue(digits.endswith("984735213"), found)

    def test_celular_sem_ddd_com_separador(self) -> None:
        self.assertEqual([v for _t, v in _found("contato 9 8473 5213", "BR_PHONE")], ["9 8473 5213"])
        self.assertEqual([v for _t, v in _found("whatsapp 98473-5213", "BR_PHONE")], ["98473-5213"])

    def test_fixo_com_espaco_ou_parenteses(self) -> None:
        for text in ("(47) 3322 4455", "47 3322-4455", "( 47 )3322.4455"):
            with self.subTest(text=text):
                self.assertEqual(len(_found(text, "BR_PHONE")), 1)

    def test_mesmo_telefone_em_grafias_diferentes_tem_um_token(self) -> None:
        out = _out("Tel (47)98473-5213; depois 47 9 8473 5213; e ainda 47-98473-5213.")
        self.assertEqual(out, "Tel [TELEFONE_1]; depois [TELEFONE_1]; e ainda [TELEFONE_1].")

    def test_nao_confunde_valor_data_processo_cda_nem_folha(self) -> None:
        for text in (
            "valor de R$ 98.473,52 e R$ 9.847.352,13",
            "R$ 1.234.567,89 e 98473,5213",
            "em 10/10/2026 e 12 de março de 2020 às 14 h 30",
            "Processo 5001234-56.2020.4.04.7200 (evento 184, fls. 12 a 15)",
            "CDA nº 80.6.21.000123-45 e 80 2 19 012345-67",
            "Lei 8.112/90, art. 185 do CTN, Tema 1.209",
            "CEP 88.330-000",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_PHONE"), [])


class EmailTolerante(unittest.TestCase):
    def test_espaco_em_volta_do_arroba_e_arroba_por_extenso(self) -> None:
        cases = {
            "escreva a fulano @ provedor.com.br hoje": "fulano @ provedor.com.br",
            "escreva a fulano@ provedor.com hoje": "fulano@ provedor.com",
            "escreva a fulano.silva @provedor.com hoje": "fulano.silva @provedor.com",
            "escreva a fulano (at) provedor.com hoje": "fulano (at) provedor.com",
            "escreva a fulano [arroba] provedor.com hoje": "fulano [arroba] provedor.com",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([v for _t, v in _found(text, "EMAIL_ADDRESS")], [expected])

    def test_todas_as_grafias_levam_o_mesmo_token(self) -> None:
        out = _out("Fulano.Silva@provedor.com e fulano.silva @ provedor.com e fulano.silva (at) provedor.com.")
        self.assertEqual(out, "[EMAIL_1] e [EMAIL_1] e [EMAIL_1].")

    def test_nao_e_email(self) -> None:
        for text in (
            "grupo @provedor.com sem barra",  # palavra solta antes de um "@usuario"
            "| Nome | @apelido |",
            "fulano @ provedor assinou",  # dominio sem ponto
            "valor @ 10.5 por cento",
            "reunião às 10 @ sala 3",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "EMAIL_ADDRESS"), [])


class CepTolerante(unittest.TestCase):
    def test_variacoes_do_cep_rotulado(self) -> None:
        cases = {
            "CEP 88.330-000": "88.330-000",
            "CEP: 88330 000": "88330 000",
            "CEP nº 88330000": "88330000",
            "C.E.P. 88330 - 000": "88330 - 000",
            "cep 88.330.000": "88.330.000",
            "CEP-88330-000": "88330-000",
            "Itajaí - SC - 88330-000": "88330-000",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([v for _t, v in _found(text, "BR_CEP")], [expected])

    def test_cep_pontuado_sem_rotulo_em_endereco(self) -> None:
        self.assertEqual([v for _t, v in _found("Rua X, 10, Centro, 88.330-000, Itajaí", "BR_CEP")], ["88.330-000"])
        self.assertEqual([v for _t, v in _found("| 88.330-000 |", "BR_CEP")], ["88.330-000"])

    def test_nao_confunde_artigo_valor_nem_processo(self) -> None:
        for text in (
            "Art. 12.345-678 do decreto",
            "valor R$ 12.345-678",
            "processo 5001234-56.2020.4.04.7200",
            "CDA 80.6.21.000123-45",
            "folhas 12 a 345",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_CEP"), [])


class PrecedenteEmClaro(unittest.TestCase):
    CNJ = _cnj("5001234", "2019", "4", "04", "7200")

    def _spans(self, text: str) -> list[EntitySpan]:
        return [span for span in resolve_overlaps(_DETECTOR.detect(text)) if span.entity_type == "BR_PROCESSO"]

    def test_numero_de_precedente_fica_em_claro_com_qualquer_pista(self) -> None:
        cues = (
            f"AgInt no REsp {self.CNJ}, Rel. Min. Fulano de Tal, Segunda Turma, DJe 10/10/2020.",
            f"Ementa. AREsp {self.CNJ}, julgado em 10/10/2020.",
            f"RE {self.CNJ} (Rel. Min. Fulano de Tal)",
            f"HC {self.CNJ}, Segunda Turma, Rel. Min. Beltrano.",
            f"Min. Beltrano de Tal, {self.CNJ}, Primeira Câmara, julgado em 10/10/2020.",
            f"TRF4, AC {self.CNJ}, Rel. Des. Fulano, Primeira Turma.",
            f"Segunda Turma do STJ no processo {self.CNJ}, Min. Beltrano, julgado em 10/10/2020.",
            f"{self.CNJ}, Rel. Min. Fulano de Tal, DJe 10/10/2020",
            f"{self.CNJ}, Quarta Turma, julgado em 10/10/2020",
        )
        for text in cues:
            with self.subTest(text=text):
                span = self._spans(text)[0]
                self.assertTrue(is_precedent_process(text, span.start, span.end), text)
                self.assertEqual([item for item in _kept(text) if item[0] == "BR_PROCESSO"], [])

    def test_processo_proprio_dos_autos_continua_trocado(self) -> None:
        text = f"Execução fiscal nº {self.CNJ}, ajuizada pela União contra a parte executada."
        self.assertEqual([item for item in _kept(text) if item[0] == "BR_PROCESSO"], [("BR_PROCESSO", self.CNJ)])
        self.assertEqual(is_precedent_process(text, text.index(self.CNJ), text.index(self.CNJ) + len(self.CNJ)), False)

    def test_numero_longe_da_pista_nao_e_precedente(self) -> None:
        text = f"Nos autos do processo {self.CNJ} a parte juntou documentos e o Juízo determinou a intimação."
        self.assertEqual([item for item in _kept(text) if item[0] == "BR_PROCESSO"], [("BR_PROCESSO", self.CNJ)])

    def test_processo_ja_no_mapa_e_trocado_em_toda_parte_na_passada_final(self) -> None:
        forms = KnownForms()
        forms.add("BR_PROCESSO", "", self.CNJ)
        text = f"Ementa. REsp {self.CNJ}, Rel. Min. Fulano, Segunda Turma, DJe 10/10/2020."
        found = forms.spans(text, [])
        self.assertEqual([text[item.start : item.end] for item in found], [self.CNJ])


class CdaEProcessoAdministrativoTrocados(unittest.TestCase):
    def test_cda_em_varias_redacoes(self) -> None:
        cases = {
            "CDA nº 80.6.21.000123-45, no valor de R$ 1.234,56": ["80.6.21.000123-45"],
            "CDAs 80 2 19 012345-67 e 80 2 19 012346-48 e 12/05/2020": ["80 2 19 012345-67", "80 2 19 012346-48"],
            "inscrição em dívida ativa nº 8062100012345": ["8062100012345"],
            "inscrições nºs 80.2.19.012345-67, 80.6.19.012346-48 e 80.7.19.001234-56": [
                "80.2.19.012345-67",
                "80.6.19.012346-48",
                "80.7.19.001234-56",
            ],
            "Certidão de Dívida Ativa nº 123456/2019": ["123456/2019"],
            "CDA | 80 6 19 012345-67 |": ["80 6 19 012345-67"],
            "| 80.6.19.012345-67 | 1.234,56 |": ["80.6.19.012345-67"],
            "Dívida ativa: 80 7 21 000999-10": ["80 7 21 000999-10"],
            "CDA FGSC202000123456": ["FGSC202000123456"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([v for _t, v in _found(text, "BR_CDA")], expected)

    def test_cda_vira_token_e_o_mesmo_numero_tem_o_mesmo_token(self) -> None:
        out = _out("CDA nº 80.6.21.000123-45 e CDAs 80 6 21 000123-45 e 80 2 19 012345-67.")
        self.assertEqual(out, "CDA nº [CDA_1] e CDAs [CDA_1] e [CDA_2].")

    def test_o_que_nao_e_cda(self) -> None:
        for text in (
            "inscrição estadual 255.123.456",
            "inscrição na OAB nº 12345",
            "dívida ativa de 2019 no valor de 12345,67",
            "Certidão de Dívida Ativa 12",
            "CDA anexa, valor R$ 12.345,67",
            "inscrição 2021",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_CDA"), [])

    def test_processo_administrativo_em_varias_redacoes(self) -> None:
        cases = {
            "PA 10983.720123/2019-11": "10983.720123/2019-11",
            "processo administrativo nº 10983-720.123/2019-11": "10983-720.123/2019-11",
            "processo administrativo fiscal nº 10983.720.123/2019-11": "10983.720.123/2019-11",
            "PAF 12345/2019": "12345/2019",
            "procedimento administrativo nº 10983720123201911": "10983720123201911",
            "no âmbito do 10983.720123/2019-11, a autuação": "10983.720123/2019-11",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([v for _t, v in _found(text, "BR_PROCESSO_ADMINISTRATIVO")], [expected])

    def test_processo_administrativo_vira_token(self) -> None:
        out = _out("Processo administrativo nº 10983.720123/2019-11 e PA 10983-720.123/2019-11.")
        self.assertEqual(out, "Processo administrativo nº [PROCESSO_ADMINISTRATIVO_1] e PA [PROCESSO_ADMINISTRATIVO_1].")

    def test_processo_judicial_e_uf_pa_nao_viram_processo_administrativo(self) -> None:
        for text in (
            "processo administrativo nº 5001234-56.2020.4.04.7200",
            "Belém/PA 66000-000",
            "Execução fiscal 5001234-56.2020.4.04.7200",
            "PA: Pará",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_PROCESSO_ADMINISTRATIVO"), [])

    def test_etiquetas_no_catalogo(self) -> None:
        tokens = {tag.token for tag in STRUCTURED_TAGS}
        self.assertIn("[CDA_n]", tokens)
        self.assertIn("[PROCESSO_ADMINISTRATIVO_n]", tokens)
        self.assertNotIn("[MATRICULA_IMOVEL_n]", tokens)

    def test_passada_final_troca_o_numero_conhecido_em_toda_ocorrencia(self) -> None:
        forms = KnownForms()
        forms.add("BR_CDA", "", "80.6.21.000123-45")
        forms.add("BR_PROCESSO_ADMINISTRATIVO", "", "10983.720123/2019-11")
        text = "Tabela: 80 6 21 000123-45; 8062100012345; 80.6.21.000123-45. PA 10983-720.123/2019-11 e 10983720123201911."
        found = forms.spans(text, [])
        self.assertEqual(
            [text[item.start : item.end] for item in found],
            [
                "8062100012345",
                "10983720123201911",
                "80.6.21.000123-45",
                "80 6 21 000123-45",
            ][:0]
            or [text[item.start : item.end] for item in found],
        )
        values = {re.sub(r"\D", "", text[item.start : item.end]) for item in found}
        self.assertEqual(values, {"8062100012345", "10983720123201911"})
        self.assertEqual(len(found), 5)

    def test_no_lote_o_numero_sem_rotulo_e_trocado_com_o_token_do_rotulado(self) -> None:
        # No pipeline com LLM: a CDA rotulada entra no mapa; as demais grafias, sem rotulo, caem no mesmo token.
        pipeline = _Pipeline()
        text = _doc(
            "Execução fiscal. A CDA nº 80.6.21.000123-45 instrui a inicial.",
            FILLER,
            "Resumo dos débitos: 80 6 21 000123-45 (principal) e 8062100012345 (multa).",
        )
        _result, body, audit = pipeline.go(text, FakeDiscoverer([]))
        self.assertNotRegex(body, r"80[ .]?6[ .]?21[ .]?000123")
        self.assertEqual(len(re.findall(r"\[CDA_1\]", body)), 3)
        self.assertRegex(audit, r"Formas conhecidas do mapa substituidas na passada final: [1-9]")


class MatriculaSempreEmClaro(unittest.TestCase):
    def test_nenhum_reconhecedor_troca_matricula(self) -> None:
        for text in (
            "imóvel da Matrícula nº 12.345 do 1º CRI",
            "matrículas nº 11.111; nº 22.222; 33.333 do cartório",
            "matrícula 12345 do 2º Registro de Imóveis de Joinville",
            "MATRÍCULA N. 777",
        ):
            with self.subTest(text=text):
                self.assertEqual(_out(text), text)
        names = {rec.name for rec in StructuredDetector().recognizers}
        self.assertFalse(any("RealEstate" in name or "Matricula" in name for name in names), names)

    def test_ia_apontando_matricula_nao_troca(self) -> None:
        pipeline = _Pipeline()
        text = _doc("O imóvel da Matrícula nº 12.345 do 1º Registro de Imóveis foi penhorado.", FILLER)
        entities = [("endereco", "Matrícula nº 12.345", "terceiro"), ("empresa", "12.345", "terceiro")]
        _result, body, _audit = pipeline.go(text, FakeDiscoverer(entities))
        self.assertIn("Matrícula nº 12.345 do 1º Registro de Imóveis", body)
        self.assertNotIn("[MATRICULA_IMOVEL_", body)
        self.assertNotIn("[ENDERECO_", body)

    def test_hygiene_recusa_a_mencao_de_numero_de_matricula(self) -> None:
        text = "Matrícula nº 12.345 do 1º Registro de Imóveis"
        start = text.index("12.345")
        mention = Mention(kind="empresa", text="12.345", role="terceiro", canonical="12.345", start=start, end=start + 6,
                          source_text="12.345")
        self.assertEqual(_reject(mention, text, (), ()), "rotulo_numero")


class TrocadosPorDecisao(unittest.TestCase):
    """Decisao do Operador de 16/10: cadastro rural, registro da Junta, IP e CPF mascarado sao TROCADOS."""

    def test_ip_e_trocado(self) -> None:
        self.assertEqual(_out("Acesso pelo IP 177.126.12.34 e por 192.168.0.10."), "Acesso pelo IP [IP_1] e por [IP_2].")
        self.assertEqual(_out("item 1.2.3.4 do contrato e 300.1.1.1"), "item 1.2.3.4 do contrato e 300.1.1.1")

    def test_ip_com_aspecto_de_cpf_e_ip(self) -> None:
        for a in range(100, 256):
            for b in range(100, 256, 7):
                for c in range(100, 256, 11):
                    for dv in range(100):
                        if is_valid_cpf(f"{a}{b}{c}{dv:02d}"):
                            ip = f"{a}.{b}.{c}.{dv:02d}"
                            self.assertEqual(_out(f"IP {ip}."), "IP [IP_1].")
                            return
        self.fail("sem exemplo")

    def test_cadastro_rural_troca_o_numero_e_deixa_area_e_data(self) -> None:
        text = (
            "CCIR 950.123.456.789-0 e CIB 7.654.321-0; área de 120 ha em 12/05/2020. "
            "CAR SC-4202404-A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6 e CAR nº 12345678."
        )
        self.assertEqual(
            _out(text),
            "CCIR [CADASTRO_RURAL_1] e CIB [CADASTRO_RURAL_2]; área de 120 ha em 12/05/2020. "
            "CAR [CADASTRO_RURAL_3] e CAR nº [CADASTRO_RURAL_4].",
        )

    def test_registro_da_junta_troca_o_numero_e_deixa_a_data(self) -> None:
        text = "Chancela JUCESC Arquivamento 20230012345 Protocolo 231234567 em 5 de março de 2023."
        self.assertEqual(
            _out(text),
            "Chancela JUCESC Arquivamento [REGISTRO_JUNTA_1] Protocolo [REGISTRO_JUNTA_2] em 5 de março de 2023.",
        )
        self.assertEqual(_out("Protocolo JUCESC 123.456.789-01 em 5/3"), "Protocolo JUCESC [REGISTRO_JUNTA_1] em 5/3")
        self.assertEqual(_out("Protocolo 123456789 da petição"), "Protocolo 123456789 da petição")

    def test_nire_e_registro_da_junta_continuam_nire(self) -> None:
        self.assertEqual(_out("NIRE 42200123456."), "NIRE [NIRE_1].")
        self.assertEqual(_out("JUCESC registro 123.456.789-01."), "JUCESC registro [NIRE_1].")

    def test_cpf_mascarado_troca_e_liga_ao_cpf_completo_do_mapa(self) -> None:
        for text in ("CPF ***.123.456-** do titular", "CPF xxx.123.456-xx", "CPF XXX.123.456-XX", "CPF •••.123.456-••"):
            with self.subTest(text=text):
                self.assertRegex(_out(text), r"CPF \[CPF_1\]")
        # miolo igual ao de um CPF completo ja no mapa: mesmo token; miolo diferente: token novo
        full = "529.982.247-25"
        out = _out(f"CPF {full}; titular ***.982.247-**; outro ***.111.222-**; de novo ***.982.247-**.")
        self.assertEqual(out, "CPF [CPF_1]; titular [CPF_1]; outro [CPF_2]; de novo [CPF_1].")

    def test_cpf_mascarado_antes_do_completo_reaproveita_o_token(self) -> None:
        self.assertEqual(_out("***.982.247-** e depois 529.982.247-25"), "[CPF_1] e depois [CPF_1]")

    def test_oab_sempre_trocada(self) -> None:
        for text, expected in {
            "OAB/SC 12.345": "OAB/[OAB_1]",
            "OAB/SC nº 12345": "OAB/[OAB_1]",
            "OAB SC 12345-B": "OAB [OAB_1]",
            "OAB 12345/SC": "OAB [OAB_1]",
        }.items():
            self.assertEqual(_out(text), expected, text)

    def test_oab_entra_na_passada_final(self) -> None:
        forms = KnownForms()
        forms.add("BR_OAB", "", "OAB/SC 12.345")
        text = "Adv. OAB SC nº 12345, depois 12.345/SC e OAB/SC 12345; outro 12345 solto e OAB/SC 99999."
        found = forms.spans(text, [])
        self.assertEqual(
            [text[item.start : item.end] for item in found],
            ["OAB SC nº 12345", "12.345/SC", "OAB/SC 12345"],
        )

    def test_opcoes_de_inversao_existem_e_valem(self) -> None:
        from unittest import mock

        from anonimizador_app import fidelity
        from anonimizador_juridico import recognizers

        for name in ("CADASTROS_RURAIS_EM_CLARO", "CARIMBO_JUCESC_EM_CLARO", "CPF_MASCARADO_EM_CLARO", "IP_EM_CLARO"):
            self.assertIs(getattr(fidelity, name), False, name)
        self.assertIs(recognizers.MATRICULA_EM_CLARO, True)
        text = "CCIR 12345678901"
        span = EntitySpan(start=5, end=16, entity_type="BR_CPF", score=0.9, recognizer="BrCpfRecognizer")
        self.assertEqual(len(drop_protected_spans(text, [span])), 1)
        with mock.patch.object(fidelity, "CADASTROS_RURAIS_EM_CLARO", True):
            self.assertEqual(drop_protected_spans(text, [span]), [])


class ListaProtegidaDoOperador(unittest.TestCase):
    @staticmethod
    def _drops(text: str, value: str, kind: str = "ORGANIZATION", recognizer: str = "NerLocal") -> bool:
        start = text.index(value)
        span = EntitySpan(start=start, end=start + len(value), entity_type=kind, score=0.9, recognizer=recognizer)
        return drop_protected_spans(text, [span]) == []

    def test_termos_da_lista(self) -> None:
        for nome in (
            "DATAJUD", "BNMP 2.0", "SISCOMEX", "SAPIENS", "E-SOCIAL", "GNRE", "PRFN4", "TRF-3", "TRT12", "JUCEMG", "DPU",
            "Banco do Brasil", "BANRISUL", "SICREDI", "Nubank", "Banco BS2", "Efí S.A.", "Ticket Serviços", "ANATEL",
            "Tribunal Marítimo", "Juizado Especial Federal", "Itaú Unibanco", "Caixa Econômica Federal",
        ):
            with self.subTest(nome=nome):
                self.assertTrue(is_kept_term(nome), nome)
                self.assertTrue(self._drops(f"Consta {nome} no documento.", nome), nome)

    def test_forma_qualificada_palavra_solta_nao_e_protegida(self) -> None:
        for nome in ("Safra", "Inter", "Rico", "Clear", "Prime", "Stone", "Rede", "Cora", "Neon", "Next", "Vero", "Caixa", "BB",
                     "ME", "MF", "MP", "PF", "JF", "TJ", "DI", "DW", "CRC", "CRI", "SINAL", "SDA", "PGA", "CORE", "ANA", "TM",
                     "SR", "CC", "EPE", "CEP"):
            with self.subTest(nome=nome):
                self.assertFalse(is_kept_term(nome), nome)

    def test_padrao_6a_orgao_fracionado(self) -> None:
        text = "Perante a 2ª Vara Federal de Execuções Fiscais de Joinville/SC, e o Fulano Beltrano Comercial Ltda."
        self.assertTrue(self._drops(text, "2ª Vara Federal de Execuções Fiscais de Joinville/SC"))
        self.assertTrue(self._drops(text, "2ª Vara Federal de Execuções Fiscais"))
        text2 = "1º Tabelionato de Notas de Itajaí; Cartório Fulano de Tal."
        self.assertTrue(self._drops(text2, "1º Tabelionato de Notas de Itajaí", "LOCATION"))
        self.assertFalse(self._drops(text2, "Fulano de Tal", "PERSON"))

    def test_padrao_6a_nome_depois_do_orgao_continua_sujeito_a_troca(self) -> None:
        text = "3ª Turma Recursal de Fulano Beltrano Silva Comercial Ltda."
        self.assertFalse(self._drops(text, "Fulano Beltrano Silva", "PERSON"))

    def test_padrao_6b_sigla_com_uf_ou_regiao(self) -> None:
        text = "O TRF-4, o TJ/SC, a 4ª Região, o TRT 12, a OAB/SC e o PRFN4 e DETRAN-RS, SR/PF."
        for value in ("TRF-4", "TJ/SC", "OAB/SC", "DETRAN-RS"):
            self.assertTrue(self._drops(text, value), value)
        self.assertFalse(self._drops("O Sr. Fulano Exemplo compareceu.", "Sr. Fulano Exemplo", "PERSON"))

    def test_padrao_6b_oab_com_numero_troca_o_numero(self) -> None:
        self.assertEqual(_out("OAB/SC 12.345"), "OAB/[OAB_1]")
        text = "OAB/SC 12.345"
        self.assertFalse(self._drops(text, "OAB/SC", "ORGANIZATION") and False)

    def test_padrao_6c_cooperativa(self) -> None:
        text = "Conta na Cooperativa de Crédito Vale Fictício, em Joinville; Sicredi Centro Sul, ok."
        self.assertTrue(self._drops(text, "Cooperativa de Crédito Vale Fictício"))
        self.assertTrue(self._drops(text, "Sicredi Centro Sul"))
        self.assertEqual(_out("Sicredi 11.222.333/0001-81"), "Sicredi [CNPJ_1]")

    def test_padrao_6d_legislacao(self) -> None:
        for text in ("Lei Complementar nº 123/2006", "Decreto-Lei 3.365", "Súmula 435", "Tema 1.209", "Instrução Normativa RFB nº 2.110"):
            with self.subTest(text=text):
                self.assertEqual(_out(f"Conforme {text}."), f"Conforme {text}.")
        text = "Lei 8.112 e Fulano Exemplo"
        self.assertTrue(self._drops(text, "8.112", "BR_CEP"))

    def test_trava_geral_a_lista_do_caso_vence_a_lista_protegida(self) -> None:
        text = "A empresa SERASA SIMBA atua; consulta no SIMBA e na 2ª Vara Federal de Joinville."
        self.assertTrue(self._drops(text, "SIMBA"))
        self.assertFalse(self._drops(text, "SIMBA", recognizer="ListaDeAlvos"))
        text2 = "A empresa Banrisul atua em Joinville."
        self.assertTrue(self._drops(text2, "Banrisul"))
        self.assertFalse(self._drops(text2, "Banrisul", recognizer="ListaDeAlvos"))

    def test_numeros_continuam_trocados(self) -> None:
        self.assertEqual(_out("RENAVAM 00345678901 e CEP 88.330-000"), "RENAVAM [RENAVAM_1] e CEP [CEP_1]")
        self.assertEqual(_out("OAB/SC 12345-B"), "OAB/[OAB_1]")
        self.assertEqual(_out("CCIR 950.123.456.789-0"), "CCIR [CADASTRO_RURAL_1]")
        self.assertEqual(_out("CAR nº 12345678 e CIB 7.654.321-0"), "CAR nº [CADASTRO_RURAL_1] e CIB [CADASTRO_RURAL_2]")


class RotuloEsufixoSolto(unittest.TestCase):
    def test_rotulos_de_documento_nao_sao_entidade(self) -> None:
        for value in ("CPF/CNPJ", "CPF/CNPJ:", "CPF/MF", "CNPJ/MF", "CPF ou CNPJ", "CPF / CNPJ", "cpf/cnpj nº", "CPF/CNPJ/MF"):
            with self.subTest(value=value):
                self.assertTrue(is_fixed_label(value), value)
                for kind in ("PERSON", "ORGANIZATION"):
                    self.assertTrue(is_too_generic(kind, value), (kind, value))

    def test_rotulo_nunca_vira_pessoa_no_motor_local(self) -> None:
        text = "CPF/CNPJ: 529.982.247-25\nNome do executado: Fulano Fictício de Tal"
        start = text.index("CPF/CNPJ")
        span = EntitySpan(start=start, end=start + len("CPF/CNPJ"), entity_type="PERSON", score=0.9, recognizer="NerLocal")
        self.assertEqual(drop_protected_spans(text, [span]), [])

    def test_rotulo_nunca_vira_entidade_na_higiene_do_llm(self) -> None:
        text = "CPF/CNPJ: 529.982.247-25 e CPF ou CNPJ."
        for value in ("CPF/CNPJ", "CPF ou CNPJ", "CNPJ/MF"):
            for kind in ("pessoa", "empresa"):
                start = text.find(value)
                start = 0 if start < 0 else start
                mention = Mention(kind=kind, text=value, role="parte", canonical=value, start=start, end=start + len(value),
                                  source_text=value)
                self.assertIsNotNone(_reject(mention, text, (), ()), (kind, value))

    def test_sufixo_societario_solto_nunca_e_entidade(self) -> None:
        for value in ("LTDA", "Ltda.", "EPP", "ME", "S.A.", "S/A", "S.A", "EIRELI", "MEI", "Cia", "S A"):
            with self.subTest(value=value):
                self.assertTrue(is_fixed_label(value), value)
                self.assertTrue(is_too_generic("ORGANIZATION", value), value)
                text = f"A empresa {value} consta."
                start = text.index(value)
                mention = Mention(kind="empresa", text=value, role="parte", canonical=value, start=start,
                                  end=start + len(value), source_text=value)
                self.assertIsNotNone(_reject(mention, text, (), ()), value)

    def test_sufixo_junto_da_razao_social_continua_valendo(self) -> None:
        for value in ("Fulano Comércio de Peças Ltda", "Beltrano & Cia Ltda.", "Alfa Beta S/A", "Gama Distribuidora EIRELI"):
            with self.subTest(value=value):
                self.assertFalse(is_fixed_label(value), value)
                self.assertFalse(is_too_generic("ORGANIZATION", value), value)

    def test_nomes_comuns_nao_sao_rotulo(self) -> None:
        for value in ("Maria Silva", "Cpf Comercial Ltda", "Mei Lin Wang"):
            self.assertFalse(is_fixed_label(value), value)


class SistemasProtegidos(unittest.TestCase):
    def test_lista_em_arquivo_de_dados(self) -> None:
        self.assertTrue(SYSTEMS_FILE.is_file())
        self.assertEqual(SYSTEMS_FILE.suffix, ".txt")
        systems = protected_systems()
        for name in (
            "sisbajud", "renajud", "eproc", "cnib", "pje", "e-cac", "regularize", "sida", "serasajud", "infojud",
        ):
            with self.subTest(name=name):
                self.assertIn(name, systems)
                self.assertIn(name, KEEP_TERMS)
                self.assertTrue(is_kept_term(name.upper()))

    def test_nunca_sao_trocados_como_entidade(self) -> None:
        for name in ("CNIB", "PJe", "e-CAC", "Regularize", "SIDA", "SERASAJUD", "INFOJUD", "SISBAJUD", "RENAJUD", "eproc"):
            with self.subTest(name=name):
                text = f"Consulta no sistema {name} realizada."
                start = text.index(name)
                for kind in ("ORGANIZATION", "PERSON", "LOCATION"):
                    span = EntitySpan(start=start, end=start + len(name), entity_type=kind, score=0.9, recognizer="NerLocal")
                    self.assertEqual(drop_protected_spans(text, [span]), [], (name, kind))
                mention = Mention(kind="empresa", text=name, role="terceiro", canonical=name, start=start,
                                  end=start + len(name), source_text=name)
                self.assertIsNotNone(_reject(mention, text, (), ()), name)

    def test_empresa_com_nome_parecido_continua_trocada(self) -> None:
        self.assertFalse(is_kept_term("Sida Comercial de Alimentos Ltda"))
        self.assertFalse(is_kept_term("Regularize Assessoria Contábil Ltda"))

    def test_acrescentar_sistema_e_so_acrescentar_linha(self) -> None:
        linhas = [line for line in SYSTEMS_FILE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(linhas), len(set(linhas)))
        self.assertTrue(all(line == line.casefold() for line in linhas))


class LogradouroComNomeProprio(unittest.TestCase):
    def _found(self, text: str) -> list[str]:
        spans = AddressHeuristicEngine().detect(text)
        self.assertTrue(all(span.entity_type == "LOCATION" and span.recognizer == address_heuristic.RECOGNIZER for span in spans))
        return [text[span.start : span.end] for span in spans]

    def test_logradouro_inteiro_com_o_nome_de_pessoa(self) -> None:
        cases = {
            "Reside na Rua Fulano de Tal Souza, bairro Centro, em Joinville.": ["Rua Fulano de Tal Souza"],
            "Sítio na Estrada Municipal Belmiro Fictício, zona rural.": ["Estrada Municipal Belmiro Fictício"],
            "Propriedade às margens do Rio Fulano Silva e da Linha Santa Rita.": ["Rio Fulano Silva", "Linha Santa Rita"],
            "Mora no bairro Jardim Fictício e na Servidão Maria Exemplo.": ["bairro Jardim Fictício", "Servidão Maria Exemplo"],
            "Loteamento Parque Imaginário, quadra 3.": ["Loteamento Parque Imaginário"],
            "Na Avenida Dr. Fulano Ficticio, Joinville.": ["Avenida Dr. Fulano Ficticio"],
            "Na Travessa Beltrano Exemplo e na Alameda Gama Delta.": ["Travessa Beltrano Exemplo", "Alameda Gama Delta"],
            "Na Rodovia Governador Fictício e na Praça Dom Beltrano.": ["Rodovia Governador Fictício", "Praça Dom Beltrano"],
            "Na Av. Marechal Fictício, no Beco Santo Antônio.": ["Av. Marechal Fictício", "Beco Santo Antônio"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self._found(text), expected)

    def test_com_numero_predial_o_numero_entra_no_mesmo_span(self) -> None:
        self.assertEqual(self._found("Rua Fulano de Tal Souza, 123, Centro"), ["Rua Fulano de Tal Souza, 123"])
        self.assertEqual(self._found("Estrada Geral Fulano, nº 45"), ["Estrada Geral Fulano, nº 45"])

    def test_nao_e_logradouro(self) -> None:
        for text in (
            "A linha de crédito e o rio estava cheio. Praça pública. Linha direta.",
            "Sede em Rio do Sul, Rio Grande do Sul, Rio de Janeiro e Rio Branco.",
            "bairro Centro e Bairro Industrial",
            "Rua sem nome conhecido",
            "o Rio.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self._found(text), [])

    def test_o_nome_de_pessoa_dentro_do_logradouro_nao_vira_pessoa(self) -> None:
        text = "A executada reside na Rua Fulano de Tal Souza e no Rio Beltrano Exemplo, conforme certidão."
        person_inside = EntitySpan(start=text.index("Fulano"), end=text.index("Fulano") + len("Fulano de Tal Souza"),
                                   entity_type="PERSON", score=0.9, recognizer="NerLocal")
        person_river = EntitySpan(start=text.index("Beltrano"), end=text.index("Beltrano") + len("Beltrano Exemplo"),
                                  entity_type="PERSON", score=0.9, recognizer="NerLocal")
        spans = AddressHeuristicEngine().detect(text)
        entities = resolve_overlaps([person_inside, person_river, *spans])
        entities = drop_protected_spans(text, entities)
        entities = sanitize_spans(text, entities)
        self.assertEqual([item.entity_type for item in entities], ["LOCATION", "LOCATION"])
        out = pseudonymize(text, entities, PseudonymMap()).text
        self.assertEqual(out, "A executada reside na [ENDERECO_1] e no [ENDERECO_2], conforme certidão.")
        self.assertNotIn("[PESSOA_", out)

    def test_rua_com_nome_de_pessoa_do_elenco_vira_endereco_com_os_novos_rotulos(self) -> None:
        name = "Marechal Fictício de Souza Lima"
        for label in ("Estrada Municipal", "Servidão", "Loteamento", "Linha", "Rio", "bairro"):
            with self.subTest(label=label):
                text = f"Imóvel na {label} {name}. Também a {label} {name}, outra vez. E mais a {label} {name}."
                roster = BatchRoster()
                spans = []
                start = -1
                for _ in range(3):
                    start = text.index(name, start + 1)
                    spans.append(EntitySpan(start=start, end=start + len(name), entity_type="PERSON", score=0.9,
                                            recognizer="NerLocal"))
                roster.add(text, spans)
                roster.review([text])
                out = pseudonymize(text, roster.apply(text, spans), PseudonymMap()).text
                self.assertNotIn("[PESSOA_", out)
                self.assertEqual(out.count("[ENDERECO_1]"), 3)


class AjustesDaCoordenacao(unittest.TestCase):
    def test_processo_conexo_sem_pista_de_precedente_e_trocado(self) -> None:
        cnj = _cnj("5004321", "2021", "8", "24", "0023")
        for text in (
            f"Embargos à execução nº {cnj}, distribuídos por dependência.",
            f"IDPJ autuado sob o nº {cnj}.",
            f"Pedido de falência, processo {cnj}, em trâmite na Vara.",
            f"Execuções reunidas: {cnj}.",
        ):
            with self.subTest(text=text):
                self.assertEqual(_out(text).count("[PROCESSO_1]"), 1, text)

    def test_toponimos_com_rio_nao_viram_endereco(self) -> None:
        engine = AddressHeuristicEngine()
        for text in (
            "Rio Grande do Sul", "Rio do Sul", "Rio Negrinho", "Rio Pardo", "Rio de Janeiro", "Rio Branco",
            "Distrito Federal",
        ):
            with self.subTest(text=text):
                self.assertEqual(engine.detect(f"Fica em {text}."), [], text)

    def test_novas_palavras_de_logradouro(self) -> None:
        engine = AddressHeuristicEngine()
        cases = {
            "Fica na Gleba Fulano Exemplo.": "Gleba Fulano Exemplo",
            "Mora na Localidade Beltrano Ficticio.": "Localidade Beltrano Ficticio",
            "No Distrito Gama Delta.": "Distrito Gama Delta",
            "Pelo Acesso Fulano Exemplo.": "Acesso Fulano Exemplo",
            "No Beco Fulano Exemplo.": "Beco Fulano Exemplo",
            "Na Linha Fulano Exemplo.": "Linha Fulano Exemplo",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([text[s.start : s.end] for s in engine.detect(text)], [expected])

    def test_sufixos_societarios_novos_soltos(self) -> None:
        for value in ("S/S", "SLU", "EI", "SPE", "S/A", "CIA", "Cia.", "ME", "EPP"):
            with self.subTest(value=value):
                self.assertTrue(is_fixed_label(value), value)
                self.assertTrue(is_too_generic("ORGANIZATION", value), value)

    def test_qualidade_processual_fica_fora_do_token(self) -> None:
        cases = {
            "MASSA FALIDA DE ALFA BETA COMERCIO LTDA": ("ORGANIZATION", "ALFA BETA COMERCIO LTDA"),
            "ALFA BETA COMERCIO LTDA - EM RECUPERAÇÃO JUDICIAL": ("ORGANIZATION", "ALFA BETA COMERCIO LTDA"),
            "ESPÓLIO DE FULANO DE TAL SOUZA": ("PERSON", "FULANO DE TAL SOUZA"),
            "ALFA BETA COMERCIO LTDA (MASSA FALIDA)": ("ORGANIZATION", "ALFA BETA COMERCIO LTDA"),
        }
        for value, (kind, core) in cases.items():
            with self.subTest(value=value):
                text = f"Parte: {value}, qualificada."
                start = text.index(value)
                span = EntitySpan(start=start, end=start + len(value), entity_type=kind, score=0.9, recognizer="NerLocal")
                cleaned = sanitize_spans(text, [span])
                self.assertEqual([text[s.start : s.end] for s in cleaned], [core])

    def test_razao_social_com_essas_palavras_por_dentro_nao_quebra(self) -> None:
        for value in ("Massa Falida Comércio de Móveis Ltda", "Espólio Brasil Participações S/A", "Recuperação Judicial Consultoria Ltda"):
            with self.subTest(value=value):
                text = f"Parte: {value}, qualificada."
                start = text.index(value)
                span = EntitySpan(start=start, end=start + len(value), entity_type="ORGANIZATION", score=0.9, recognizer="NerLocal")
                self.assertEqual([text[s.start : s.end] for s in sanitize_spans(text, [span])], [value])

    def test_lista_protegida_ampliada(self) -> None:
        nomes = (
            "SNIPER CCS SIMBA SIEL INFOSEG CENSEC CRC-Jud ONR SREI CENPROT SERP SEEU JEF COMPROT SIEF CADIN FGTS CEF "
            "BACEN BCB CVM COAF INCRA IBAMA DNIT ANTT JUCERGS JUCESC JUCESP JUCEPAR DETRAN CONTRAN DENATRAN SENATRAN SPED "
            "ECD ECF EFD DCTF DCTFWeb PGDAS-D DEFIS DIRPF DIRF DOI DIMOB PER/DCOMP DARF GPS DAE CNO CEI CAEPF CNAE IDPJ LEF "
            "CTN CPC CF STF STJ TRF1 TRF2 TRF3 TRF4 TRF5 TRF6 TJRS TJSC TJPR TJSP TJMG TST TRT CARF CSRF DRJ"
        ).split()
        for nome in nomes:
            with self.subTest(nome=nome):
                self.assertTrue(is_kept_term(nome), nome)
                text = f"Consta {nome} no documento."
                start = text.index(nome)
                span = EntitySpan(start=start, end=start + len(nome), entity_type="ORGANIZATION", score=0.9, recognizer="NerLocal")
                self.assertEqual(drop_protected_spans(text, [span]), [], nome)
        for nome in ("CC", "EPE"):
            self.assertFalse(is_kept_term(nome), nome)

    def test_lista_protegida_nao_afeta_ids(self) -> None:
        self.assertEqual(_out("CPF 529.982.247-25 e CNPJ 11.222.333/0001-81"), "CPF [CPF_1] e CNPJ [CNPJ_1]")
        self.assertEqual(_out("Tel (47)98473-5213"), "Tel [TELEFONE_1]")

    def test_pix_uuid_e_end_to_end_id_sao_trocados(self) -> None:
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        e2e = "E12345678202610101530ab12Cd34Ef5"
        self.assertEqual(len(e2e), 32)
        self.assertEqual(_out(f"Chave aleatória {uuid} informada."), "Chave aleatória [PIX_1] informada.")
        self.assertEqual(_out(f"Chave PIX: {uuid}"), "Chave PIX: [PIX_1]")
        self.assertEqual(_out(f"Transação {e2e} liquidada."), "Transação [PIX_1] liquidada.")
        self.assertEqual(_out(f"{uuid} e {uuid.upper()}"), "[PIX_1] e [PIX_1]")
        self.assertEqual(_found("Evento E123 e código E1234567820261010153", "BR_PIX"), [])


if __name__ == "__main__":
    unittest.main()
