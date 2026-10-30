"""H9-o (app 0.4.9, deteccao 2.15.0): sobrenome do listado x nome de outra pessoa, 5o campo com marca, operacao da
revisao, trava do importador, data de nascimento, magistrado x grafia e denominacoes no ELENCO.
Nomes, documentos e lotes INVENTADOS."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from anonimizador_juridico import EntitySpan, StructuredDetector, resolve_overlaps
from anonimizador_juridico.recognizers import BrNascimentoRecognizer

from anonimizador_app import review_import as ri
from anonimizador_app.case_list import CaseRules, classify_contact, looks_like_date
from anonimizador_app.fidelity import drop_protected_spans
from anonimizador_app.local_ner import CombinedEngine
from anonimizador_app.lote_elenco import (
    CAST_NAME,
    DENOMINATIONS_TITLE,
    append_denominations_section,
    denomination_rows,
)
from anonimizador_app.settings import AppSettings, case_item_key, settings_for_operation
from anonimizador_app.target_list import (
    ANCHORED_NER_RECOGNIZER,
    TargetListEngine,
    in_public_agent_name,
    inside_protected_term,
)
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION


class _FakeNer:
    """NER de mentira: marca PERSON em cada ocorrencia dos nomes dados."""

    provider = "ner"

    def __init__(self, names: list[str]) -> None:
        self.names = names

    def detect(self, text: str) -> list[EntitySpan]:
        found: list[EntitySpan] = []
        for name in self.names:
            start = text.find(name)
            while start >= 0:
                found.append(EntitySpan(start=start, end=start + len(name), entity_type="PERSON", score=0.9,
                                        recognizer="NerLocal"))
                start = text.find(name, start + 1)
        return found

    def scan_residual(self, text: str) -> list[str]:
        return []


def _values(text: str, spans) -> list[tuple[str, str, str]]:
    return [(text[span.start : span.end], span.recognizer, span.canonical_key) for span in spans]


class VersaoTest(unittest.TestCase):
    def test_versoes(self):
        # v0.4.10 sobe as duas (ver tests/test_v0410.py).
        self.assertEqual(APP_VERSION, "0.4.16")  # v0.4.16 sobe as duas (tests/test_v0416.py)
        self.assertEqual(DETECTION_VERSION, "2.22.0")


# ------------------------------------------------------------------------------------------- 1. sobrenome x NER


class SobrenomeDeOutraPessoaTest(unittest.TestCase):
    PARTNER = "Quirino Bortolanza Fictício"
    KEY = "alvo:pessoa:quirino bortolanza ficticio"

    def detect(self, text: str, ner: list[str], **kwargs):
        engine = TargetListEngine(people=[self.PARTNER], lookup_cnpj_names=False, **kwargs)
        return CombinedEngine([engine, _FakeNer(ner)]).detect(text)

    def test_nome_longo_de_outra_pessoa_vence_o_sobrenome_do_listado(self):
        text = "O sócio Quirino Bortolanza Fictício foi citado. Compareceu Ernestina Moraes Bortolanza, irmã dele."
        spans = self.detect(text, ["Ernestina Moraes Bortolanza"])
        values = _values(text, spans)
        self.assertIn(("Ernestina Moraes Bortolanza", ANCHORED_NER_RECOGNIZER, ""), values)
        self.assertNotIn(("Bortolanza", "ListaDeAlvos", self.KEY), values)
        self.assertIn(("Quirino Bortolanza Fictício", "ListaDeAlvos", self.KEY), values)

    def test_nome_banal_ancorado_sobrevive_a_fidelidade(self):
        text = "Compareceu Maria Silva Bortolanza, irmã do sócio."
        spans = self.detect(text, ["Maria Silva Bortolanza"])
        kept = drop_protected_spans(text, spans)
        self.assertEqual(_values(text, kept), [("Maria Silva Bortolanza", ANCHORED_NER_RECOGNIZER, "")])

    def test_sobrenome_sozinho_e_prenome_sozinho_continuam_do_listado(self):
        text = "Depois Quirino assinou. Bortolanza pagou a dívida."
        values = _values(text, self.detect(text, []))
        self.assertIn(("Quirino", "ListaDeAlvos", self.KEY), values)
        self.assertIn(("Bortolanza", "ListaDeAlvos", self.KEY), values)

    def test_mesmo_prenome_do_listado_continua_dele(self):
        # v0.4.10: o listado por extenso (nome do meio a mais) vira UM token, o dele; o nome do meio nao fica em claro.
        text = "Assinou Quirino Antenor Bortolanza no recibo."
        values = _values(text, self.detect(text, ["Quirino Antenor Bortolanza"]))
        self.assertIn(("Quirino Antenor Bortolanza", "ListaDeAlvos", self.KEY), values)
        self.assertNotIn(ANCHORED_NER_RECOGNIZER, [item[1] for item in values])

    def test_grafia_cadastrada_continua_vencendo(self):
        text = "Assinou Ernestina Q. Bortolanza no recibo."
        values = _values(
            text,
            self.detect(text, ["Ernestina Q. Bortolanza"], aliases=[f"{self.PARTNER} = Q. Bortolanza"]),
        )
        self.assertIn(("Q. Bortolanza", "ListaDeAlvos", self.KEY), values)

    def test_nome_completo_listado_dentro_do_ner_vence(self):
        text = "Assinaram Ernestina Moraes e Quirino Bortolanza Fictício."
        values = _values(text, self.detect(text, ["Ernestina Moraes e Quirino Bortolanza Fictício"]))
        self.assertIn(("Quirino Bortolanza Fictício", "ListaDeAlvos", self.KEY), values)

    def test_rotulo_de_parte_nao_vira_pessoa(self):
        text = "Executado Bortolanza, citado."
        values = _values(text, self.detect(text, ["Executado Bortolanza"]))
        self.assertIn(("Bortolanza", "ListaDeAlvos", self.KEY), values)

    def test_outro_listado_recebe_o_nome_longo(self):
        text = "Compareceu Ernestina Moraes Bortolanza, irmã dele."
        engine = TargetListEngine(
            people=[self.PARTNER, "Ernestina Moraes Bortolanza"], lookup_cnpj_names=False
        )
        values = _values(text, CombinedEngine([engine, _FakeNer(["Ernestina Moraes Bortolanza"])]).detect(text))
        self.assertEqual(
            values, [("Ernestina Moraes Bortolanza", "ListaDeAlvos", "alvo:pessoa:ernestina moraes bortolanza")]
        )


# ------------------------------------------------------------------------------------------------ importador

DOC = (
    "A executada [EMPRESA_1] foi citada. Joana assinou o recibo; Joana pagou.\n"
    "Assina [PESSOA_1], CPF [CPF_1], e o advogado [PESSOA_3], OAB/SC [OAB_1].\n"
    "Compareceu Teodoro Exemplar Filho, filho de [PESSOA_4].\n"
    "Procuração a [EMPRESA_2]. Ofício à [EMPRESA_3]. O sócio [PESSOA_5], CPF [CPF_1], assinou.\n"
    "Nascido em 12/03/1960, residente na Rua das Flores, 10.\n"
)
RECORDS = [
    {"token": "[PESSOA_1]", "entity_type": "PERSON", "canonical": "x1",
     "originals": ["Joana Ernestina Fictícia"], "preferida": "Joana Ernestina Fictícia"},
    {"token": "[EMPRESA_1]", "entity_type": "ORGANIZATION", "canonical": "x2",
     "originals": ["Madeireira Exemplo Ltda"], "preferida": "Madeireira Exemplo Ltda"},
    {"token": "[PESSOA_3]", "entity_type": "PERSON", "canonical": "x3",
     "originals": ["Ulisses Fictício Prado"], "preferida": "Ulisses Fictício Prado"},
    {"token": "[PESSOA_4]", "entity_type": "PERSON", "canonical": "x4",
     "originals": ["Teodoro Exemplar"], "preferida": "Teodoro Exemplar"},
    {"token": "[EMPRESA_2]", "entity_type": "ORGANIZATION", "canonical": "x5",
     "originals": ["Prado e Valadares Advogados Associados"], "preferida": "Prado e Valadares Advogados Associados"},
    {"token": "[EMPRESA_3]", "entity_type": "ORGANIZATION", "canonical": "x6",
     "originals": ["Ordem dos Advogados do Brasil Seção Exemplo"],
     "preferida": "Ordem dos Advogados do Brasil Seção Exemplo"},
    {"token": "[PESSOA_5]", "entity_type": "PERSON", "canonical": "x7",
     "originals": ["Joana E. Fictícia"], "preferida": "Joana E. Fictícia"},
    {"token": "[PESSOA_6]", "entity_type": "PERSON", "canonical": "x8",
     "originals": ["Teodoro Exemplar Junior"], "preferida": "Teodoro Exemplar Junior"},
]


class _Lote(unittest.TestCase):
    JOB = "lote_20261020_100000"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.private = base / "privados"
        self.export = base / "exportaveis"
        (self.private / self.JOB).mkdir(parents=True)
        (self.export / self.JOB).mkdir(parents=True)
        payload = {"schema_version": 2, "warning": "x", "records": RECORDS}
        (self.private / self.JOB / "mapa.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (self.export / self.JOB / "doc_1.md").write_text(DOC, encoding="utf-8")

    def data(self) -> ri.LoteData:
        return ri.load_lote(ri.list_lotes(self.private, self.export)[0])

    def one(self, line: str, settings: AppSettings | None = None) -> ri.ReviewItem:
        items = ri.review(line, settings or AppSettings(), self.data())
        self.assertEqual(len(items), 1, items)
        return items[0]


class QuintoCampoComMarcaTest(_Lote):
    def test_prenome_mais_marca_vira_grafia_do_nome_real(self):
        item = self.one("TROCAR | pessoa | Joana | 1 | Joana [PESSOA_1]")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertTrue(item.checked)
        self.assertEqual(item.destination(), "Outras grafias: Joana Ernestina Fictícia = Joana")
        settings = AppSettings()
        ri.apply_review(settings, [item])
        stored = " ".join([*settings.target_people, *settings.target_aliases])
        self.assertNotIn("[", stored)
        self.assertEqual(settings.target_aliases, ["Joana Ernestina Fictícia = Joana"])

    def test_marca_com_espaco_e_sem_ser_a_unica(self):
        item = self.one("TROCAR | pessoa | Joana | 1 | [ PESSOA_1 ]")
        self.assertEqual(item.destination(), "Outras grafias: Joana Ernestina Fictícia = Joana")
        item = self.one("TROCAR | pessoa | Joana | 1 | [PESSOA_1] ou [PESSOA_5]")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertFalse(item.checked)
        self.assertNotIn("[", item.typed)

    def test_nome_digitado_com_marca_nunca_e_gravado(self):
        item = self.one("TROCAR | pessoa | Joana | 1 | Joana [PESSOA_9]")
        self.assertEqual(item.status, ri.DECIDE)
        item.type_principal("Joana [PESSOA_1]")
        settings = AppSettings()
        result = ri.apply_review(settings, [item])
        self.assertEqual(result.skipped, 1)
        self.assertEqual((settings.target_people, settings.target_aliases), ([], []))


class OperacaoDaRevisaoTest(_Lote):
    def _settings(self) -> AppSettings:
        settings = AppSettings(
            target_people=["Joana Ernestina Fictícia", "Ulisses Fictício Prado"],
            case_operations={
                case_item_key("target_people", "Joana Ernestina Fictícia"): ["OP-1"],
                case_item_key("target_people", "Ulisses Fictício Prado"): ["OP-2"],
            },
            case_operation_names={"OP-1": "Norte", "OP-2": "Sul"},
            case_operation="OP-2",
        )
        return settings

    def test_conferencia_contra_a_lista_da_operacao(self):
        settings = self._settings()
        name = "Teodoro Exemplar Filho"
        settings.target_people.append(name)
        settings.case_operations[case_item_key("target_people", name)] = ["OP-1"]
        # Listado so na OP-1: na OP-2 nao e "ja listado"; entra e ganha a OP-2.
        item = self.one(f"TROCAR | pessoa | {name} | 3", settings)
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertIsNotNone(item.proposal)
        result = ri.apply_review(settings, [item])
        self.assertEqual(settings.target_people.count(name), 1)
        self.assertEqual(settings.case_operations[case_item_key("target_people", name)], ["OP-1", "OP-2"])
        self.assertIn("Sul (OP-2)", result.message())
        settings.case_operation = "OP-1"
        item = self.one(f"TROCAR | pessoa | {name} | 3", settings)
        self.assertEqual(item.reason, "já está na lista do caso")

    def test_nome_novo_so_na_operacao_escolhida(self):
        settings = self._settings()
        item = self.one("TROCAR | pessoa | Teodoro Exemplar Filho | 3", settings)
        ri.apply_review(settings, [item])
        self.assertEqual(
            settings.case_operations[case_item_key("target_people", "Teodoro Exemplar Filho")], ["OP-2"]
        )

    def test_grafia_e_nome_novo_valem_na_operacao_escolhida(self):
        settings = self._settings()
        settings.target_aliases = ["Ulisses Fictício Prado = U. F. Prado"]  # digitada a mao: vale em todas
        item = self.one("TROCAR | pessoa | Joana | 1 | Joana Ernestina Fictícia", settings)
        self.assertEqual(item.status, ri.ACCEPTED)
        ri.apply_review(settings, [item])
        self.assertIn("Joana Ernestina Fictícia = Joana", settings.target_aliases)
        self.assertIn("Ulisses Fictício Prado = U. F. Prado", settings.target_aliases)
        self.assertEqual(settings.case_operations[case_item_key("target_aliases", "Joana Ernestina Fictícia = Joana")],
                         ["OP-2"])
        self.assertNotIn(case_item_key("target_aliases", "Ulisses Fictício Prado = U. F. Prado"),
                         settings.case_operations)
        active = settings_for_operation(settings)
        self.assertIn("Joana Ernestina Fictícia = Joana", active.target_aliases)
        settings.case_operation = "OP-1"
        self.assertNotIn("Joana Ernestina Fictícia = Joana", settings_for_operation(settings).target_aliases)

    def test_sem_operacao_escolhida_fica_como_antes(self):
        settings = AppSettings()
        item = self.one("TROCAR | pessoa | Joana | 1 | Joana Ernestina Fictícia", settings)
        ri.apply_review(settings, [item])
        self.assertEqual(settings.case_operations, {})
        self.assertEqual(settings.target_aliases, ["Joana Ernestina Fictícia = Joana"])

    def test_merge_de_grafias_leva_a_operacao_para_o_texto_novo(self):
        operations = {case_item_key("target_aliases", "Fulano Exemplo = F. Exemplo"): ["OP-1"]}
        lines = ri.merge_aliases(
            ["Fulano Exemplo = F. Exemplo"], [("Fulano Exemplo", "Fulaninho")], operations=operations, operation="OP-1"
        )
        self.assertEqual(lines, ["Fulano Exemplo = F. Exemplo; Fulaninho"])
        self.assertEqual(operations, {case_item_key("target_aliases", lines[0]): ["OP-1"]})
        # Outra operacao: linha separada, a da OP-1 fica como esta.
        lines = ri.merge_aliases(lines, [("Fulano Exemplo", "Fulano E.")], operations=operations, operation="OP-2")
        self.assertEqual(lines, ["Fulano Exemplo = F. Exemplo; Fulaninho", "Fulano Exemplo = Fulano E."])
        self.assertEqual(operations[case_item_key("target_aliases", lines[1])], ["OP-2"])


class TravaTest(_Lote):
    def test_unir_pai_e_filho_vai_para_decidir(self):
        item = self.one("UNIR | [PESSOA_4] | [PESSOA_6] | mesma pessoa")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertIn("pai e filho", item.reason)
        self.assertFalse(item.checked)

    def test_trocar_com_nome_do_pai_vai_para_decidir(self):
        item = self.one("TROCAR | pessoa | Teodoro Exemplar Filho | 3 | [PESSOA_4]")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertIn("pai e filho", item.reason)
        self.assertFalse(item.checked)

    def test_geracao(self):
        self.assertTrue(ri.generation_clash("Teodoro Exemplar", "Teodoro Exemplar Filho"))
        self.assertTrue(ri.generation_clash("Teodoro Exemplar Jr.", "Teodoro Exemplar Neto"))
        self.assertFalse(ri.generation_clash("Teodoro Exemplar", "Teodoro Exemplar"))
        self.assertFalse(ri.generation_clash("Teodoro", "Teodoro Exemplar Filho"))

    def test_manter_escritorio_de_advocacia_recusado(self):
        item = self.one("MANTER | [EMPRESA_2] | órgão público")
        self.assertEqual(item.status, ri.REJECTED)
        self.assertIn("advocacia", item.reason)

    def test_manter_com_oab_ao_lado_vai_para_decidir(self):
        item = self.one("MANTER | [PESSOA_3] | procurador público")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertIn("OAB", item.reason)
        self.assertFalse(item.checked)

    def test_manter_da_ordem_dos_advogados_continua(self):
        item = self.one("MANTER | [EMPRESA_3] | autarquia")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertTrue(item.checked)

    def test_unir_marcada_so_com_o_mesmo_cpf_ao_lado_das_duas(self):
        item = self.one("UNIR | [PESSOA_1] | [PESSOA_5] | mesmo [CPF_1] nas duas")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertTrue(item.checked)
        self.assertIn("[CPF_1]", item.reason)
        item = self.one("UNIR | [PESSOA_1] | [PESSOA_5] | mesma pessoa")
        self.assertFalse(item.checked)
        item = self.one("UNIR | [PESSOA_1] | [PESSOA_4] | mesmo [CPF_1]")
        self.assertFalse(item.checked)  # o CPF nao esta ao lado da segunda marca


# ------------------------------------------------------------------------------------- 5. data de nascimento


class DataDeNascimentoTest(_Lote):
    def test_data_nunca_vira_endereco_no_importador(self):
        for kind in ("endereco", "outro", "documento"):
            item = self.one(f"TROCAR | {kind} | 12/03/1960 | 5")
            self.assertEqual(item.status, ri.REJECTED, kind)
            self.assertIn("data", item.reason)

    def test_data_na_caixa_contatos_e_ignorada(self):
        for value in ("12/03/1960", "12.03.1960", "endereço: 12 de março de 1960", "1º de abril de 1950"):
            self.assertIsNone(classify_contact(value), value)
            self.assertTrue(looks_like_date(value.split(": ")[-1]), value)
        self.assertEqual(classify_contact("Rua das Flores, 10"), ("endereco", "Rua das Flores, 10"))
        text = "Nascido em 12/03/1960, residente."
        self.assertEqual(CaseRules(contacts=["12/03/1960"]).detect(text), [])

    def test_motor_tipa_nascimento_por_extenso_e_dn(self):
        recognizer = BrNascimentoRecognizer()
        cases = {
            "Fulano, nascido em 12 de março de 1960, residente": ("12 de março de 1960", "12031960"),
            "DN: 01/02/1970": ("01/02/1970", "01021970"),
            "data de nascimento: 1º de abril de 1950": ("1º de abril de 1950", "01041950"),
        }
        for text, (value, key) in cases.items():
            found = recognizer.analyze(text, ["BR_NASCIMENTO"])
            self.assertEqual([(text[r.start : r.end], r.recognition_metadata["canonical_key"]) for r in found],
                             [(value, key)], text)
        for text in ("em 12 de março de 1960 foi assinado", "dn 01/02/1970", "certidão de nascimento 12 de março de 1960"):
            self.assertEqual(recognizer.analyze(text, ["BR_NASCIMENTO"]), [], text)

    def test_nascimento_vence_o_contato_e_a_data_comum_fica(self):
        text = "Nascida em 12 de março de 1960. Contrato de 05/06/2010."
        spans = StructuredDetector().detect(text)
        self.assertEqual([(text[s.start : s.end], s.entity_type) for s in spans if "NASC" in s.entity_type],
                         [("12 de março de 1960", "BR_NASCIMENTO")])
        self.assertFalse(any(text[s.start : s.end] == "05/06/2010" for s in spans))


# ------------------------------------------------------------------------------------------- 6. magistrado


class MagistradoTest(unittest.TestCase):
    PERSON = "Clotilde Aparecida Souza"
    ALIASES = ["Clotilde Aparecida Souza = Maria; Fátima"]

    def test_julgador_pelo_rotulo(self):
        text = "Rel. Des. Maria de Fátima Exemplar, julgado em 2020."
        for word in ("Maria", "Fátima", "Exemplar"):
            start = text.find(word)
            self.assertTrue(in_public_agent_name(text, start, start + len(word)), word)
        text = "A executada Maria de Fátima Exemplar pagou."
        start = text.find("Fátima")
        self.assertFalse(in_public_agent_name(text, start, start + 6))
        text = "Maria de Fátima Exemplar, Relatora."
        start = text.find("Fátima")
        self.assertTrue(in_public_agent_name(text, start, start + 6))

    def test_grafia_de_prenome_nao_troca_nome_do_relator(self):
        text = "Rel. Des. Maria de Fátima Exemplar, julgado em 2020. Depois Maria compareceu como executada."
        engine = TargetListEngine(people=[self.PERSON], aliases=self.ALIASES, lookup_cnpj_names=False)
        spans = engine.detect(text)
        self.assertEqual([(text[s.start : s.end], s.start) for s in spans], [("Maria", text.rfind("Maria"))])

    def test_termo_protegido_do_operador_protege_o_nome_inteiro(self):
        text = "Voto de Maria de Fátima Exemplar no caso. Maria assinou."
        self.assertTrue(inside_protected_term(text, 8, 13, {"maria": ("Maria de Fátima Exemplar",)}))
        engine = TargetListEngine(
            people=[self.PERSON], aliases=self.ALIASES, extra_keep=["Maria de Fátima Exemplar"],
            lookup_cnpj_names=False,
        )
        spans = engine.detect(text)
        self.assertEqual([(text[s.start : s.end], s.start) for s in spans], [("Maria", text.rfind("Maria"))])

    def test_nome_completo_listado_continua(self):
        text = "Assinou Clotilde Aparecida Souza, executada."
        engine = TargetListEngine(people=[self.PERSON], aliases=self.ALIASES, lookup_cnpj_names=False)
        self.assertEqual([text[s.start : s.end] for s in engine.detect(text)], ["Clotilde Aparecida Souza"])


# ------------------------------------------------------------------------------------------- 7. ELENCO


@dataclass
class _Record:
    token: str
    entity_type: str
    originals: list[str]
    counts: dict[str, int] = field(default_factory=dict)


class DenominacoesTest(unittest.TestCase):
    RECORDS = [
        _Record("[EMPRESA_1]", "ORGANIZATION",
                ["Madeireira Exemplo Ltda", "MADEIREIRA EXEMPLO LTDA.", "Exemplo Comércio de Pinus S/A", "Madeireira Exemplo"],
                {"Madeireira Exemplo Ltda": 4, "MADEIREIRA EXEMPLO LTDA.": 2, "Exemplo Comércio de Pinus S/A": 3,
                 "Madeireira Exemplo": 1}),
        _Record("[EMPRESA_2]", "ORGANIZATION", ["Vidraçaria Fictícia Ltda", "Vidracaria Ficticia"]),
        _Record("[PESSOA_1]", "PERSON", ["Joana Exemplo", "Maria Fictícia"]),
    ]

    def test_conta_denominacoes_distintas(self):
        self.assertEqual(denomination_rows(self.RECORDS), [("[EMPRESA_1]", [7, 3])])

    def test_secao_no_elenco_sem_nomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / CAST_NAME).write_text("# ELENCO\n", encoding="utf-8")
            append_denominations_section(Path(tmp), self.RECORDS)
            body = (Path(tmp) / CAST_NAME).read_text(encoding="utf-8")
        self.assertIn(DENOMINATIONS_TITLE, body)
        self.assertIn("| [EMPRESA_1] | 2: denominação 1 (a primeira no texto), denominação 2 | 7, 3 |", body)
        for name in ("Madeireira", "Pinus", "Vidra"):
            self.assertNotIn(name, body)

    def test_sem_caso_nao_escreve(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(append_denominations_section(Path(tmp), self.RECORDS[1:]))
            self.assertFalse((Path(tmp) / CAST_NAME).exists())


if __name__ == "__main__":
    unittest.main()
