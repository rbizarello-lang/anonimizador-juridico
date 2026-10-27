"""H9-n: ligacao da revisao externa aos nomes do caso. Nomes, documentos e lotes INVENTADOS."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from anonimizador_juridico import PseudonymMap

from anonimizador_app import review_import as ri
from anonimizador_app.lote_elenco import SAME_PERSON_EMPTY, SAME_PERSON_TITLE, same_cpf_rows
from anonimizador_app.pipeline import FileOutcome, finalize_lote
from anonimizador_app.settings import AppSettings
from anonimizador_app.target_list import TargetListEngine, alias_needs_support

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer

DOC = (
    "A executada [EMPRESA_1] foi citada. Depois Joana assinou o recibo e Joana pagou.\n"
    "Testemunhas: Rosálio Fictício e R. C. Valadares, e também da Silva Mendonça.\n"
    "Compareceu Quirino Fictício Barbalho. A sócia Clotilde também assinou.\n"
    "Assina [PESSOA_1], e o procurador [PESSOA_3].\n"
)
RECORDS = [
    {"token": "[PESSOA_1]", "entity_type": "PERSON", "canonical": "x1",
     "originals": ["Joana Ernestina Fictícia"], "preferida": "Joana Ernestina Fictícia"},
    {"token": "[EMPRESA_1]", "entity_type": "ORGANIZATION", "canonical": "x2",
     "originals": ["Madeireira Exemplo Ltda"], "preferida": "Madeireira Exemplo Ltda"},
    {"token": "[PESSOA_3]", "entity_type": "PERSON", "canonical": "x3",
     "originals": ["Ulisses Fictício Prado"], "preferida": "Ulisses Fictício Prado"},
]


class _Lote(unittest.TestCase):
    JOB = "lote_20261019_100000"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Pasta privada fora da padrao (como a do Operador, em outra pasta do Desktop).
        self.base = Path(tmp.name) / "Teste anonimiza"
        self.private = self.base / "privados"
        self.export = self.base / "exportaveis"
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


class NomeDigitadoTest(_Lote):
    def test_prenome_sem_nome_principal_ganha_campo_e_aplica(self):
        item = self.one("TROCAR | pessoa | Clotilde | 3")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertTrue(item.name_field)
        self.assertFalse(item.checked)
        item.type_principal("Clotilde Aparecida Fictícia")
        self.assertTrue(item.checked)
        self.assertEqual(item.destination(), "Outras grafias: Clotilde Aparecida Fictícia = Clotilde")
        settings = AppSettings(target_aliases=["Clotilde Aparecida Fictícia = C. A. Fictícia"])
        ri.apply_review(settings, [item])
        self.assertEqual(settings.target_people, ["Clotilde Aparecida Fictícia"])
        self.assertEqual(settings.target_aliases, ["Clotilde Aparecida Fictícia = C. A. Fictícia; Clotilde"])

    def test_ambiguo_tambem_aceita_nome_digitado(self):
        settings = AppSettings(target_people=["Joana Ernestina Fictícia", "Joana Beatriz Exemplar"])
        item = self.one("TROCAR | pessoa | Joana | 1", settings)
        self.assertEqual(item.status, ri.DECIDE)
        self.assertTrue(item.candidates and item.name_field)
        item.type_principal("Joana Beatriz Exemplar")
        ri.apply_review(settings, [item])
        self.assertEqual(settings.target_people, ["Joana Ernestina Fictícia", "Joana Beatriz Exemplar"])
        self.assertEqual(settings.target_aliases, ["Joana Beatriz Exemplar = Joana"])


class QuintoCampoTest(_Lote):
    def test_nome_completo_existente_vira_grafia_aceita(self):
        item = self.one("TROCAR | pessoa | Joana | 1 | Joana Ernestina Fictícia")  # original do mapa
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertTrue(item.checked)
        self.assertEqual((item.proposal.principal, item.proposal.value), ("Joana Ernestina Fictícia", "Joana"))
        settings = AppSettings()
        ri.apply_review(settings, [item])
        self.assertEqual(settings.target_people, ["Joana Ernestina Fictícia"])
        self.assertEqual(settings.target_aliases, ["Joana Ernestina Fictícia = Joana"])

    def test_nome_completo_no_texto_exportado(self):
        item = self.one("TROCAR | pessoa | Barbalho | 3 | Quirino Fictício Barbalho")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertEqual(item.proposal.principal, "Quirino Fictício Barbalho")

    def test_nome_completo_inexistente_vai_para_decidir_ja_preenchido(self):
        item = self.one("TROCAR | pessoa | Clotilde | 3 | Clotilde Inventada Nenhures")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertEqual(item.typed, "Clotilde Inventada Nenhures")
        self.assertFalse(item.checked)  # o Operador confirma
        self.assertEqual(item.effective().principal, "Clotilde Inventada Nenhures")

    def test_nome_completo_como_marca(self):
        item = self.one("TROCAR | pessoa | Joana | 13 | [PESSOA_1]")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertEqual(item.proposal.principal, "Joana Ernestina Fictícia")
        self.assertEqual(item.proposal.principal_field, "target_people")

    def test_marca_inexistente_vai_para_decidir(self):
        item = self.one("TROCAR | pessoa | Joana | 13 | [PESSOA_99]")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertIn("não está no mapa", item.reason)
        self.assertTrue(item.name_field)

    def test_campo_vazio_e_sem_nome_completo(self):
        with_empty = self.one("TROCAR | pessoa | Joana | 1 | ")
        without = self.one("TROCAR | pessoa | Joana | 1")
        self.assertEqual((with_empty.status, with_empty.reason), (without.status, without.reason))
        # Sem nome completo, "Joana" e parte do nome de [PESSOA_1] (mapa): grafia dele.
        self.assertEqual(without.proposal.principal, "Joana Ernestina Fictícia")


class PedacoDeNomeTest(_Lote):
    def test_iniciais_sobrenome_e_particula_nao_viram_pessoa(self):
        for text in ("R. C. Valadares", "da Silva Mendonça", "Rosálio"):
            item = self.one(f"TROCAR | pessoa | {text} | 2")
            self.assertEqual(item.status, ri.DECIDE, text)
            self.assertIsNone(item.proposal, text)
            self.assertTrue(item.name_field, text)
            self.assertIn("pedaço de nome", item.reason)

    def test_heuristica(self):
        self.assertEqual(ri.name_fragment("R. C."), "só iniciais")
        self.assertEqual(ri.name_fragment("Silva Mendonça"), "sobrenome sem prenome")
        self.assertEqual(ri.name_fragment("Fictício Barbalho", {"quirino"}, {"ficticio", "barbalho"}),
                         "sobrenome sem prenome")
        self.assertEqual(ri.name_fragment("Rosálio Fictício"), "")

    def test_nome_com_prenome_segue_entrando(self):
        item = self.one("TROCAR | pessoa | Rosálio Fictício | 2")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertEqual(item.proposal.field_name, "target_people")


class GrafiaSozinhaTest(unittest.TestCase):
    TEXT = "Depois Joana assinou o recibo. Em Toledo ficou. Março passou. Rosa pagou.\n"

    def _engine(self):
        return TargetListEngine(
            people=["Joana Ernestina Fictícia"],
            aliases=["Joana Ernestina Fictícia = Joana; Toledo; Março"],
            extra_keep=["Rosa"],
        )

    def test_regra(self):
        self.assertFalse(alias_needs_support("Joana"))  # prenome comum: decisao consciente do Operador
        self.assertTrue(alias_needs_support("Toledo"))  # municipio
        self.assertTrue(alias_needs_support("Março"))  # mes (denylist)
        self.assertFalse(alias_needs_support("Joana Fictícia"))

    def test_motor_local(self):
        found = [self.TEXT[span.start : span.end] for span in self._engine().detect(self.TEXT)]
        self.assertEqual(found, ["Joana"])
        # Municipio cadastrado como grafia so casa com apoio (rotulo de parte).
        text = "A sócia Toledo assinou."
        self.assertEqual([text[s.start : s.end] for s in self._engine().detect(text)], ["Toledo"])

    def test_termo_protegido_nunca(self):
        engine = TargetListEngine(people=["Rosa Exemplar Fictícia"], aliases=["Rosa Exemplar Fictícia = Rosa"],
                                  extra_keep=["Rosa"])
        self.assertEqual(engine.detect("Rosa pagou."), [])

    def test_mencao_do_llm(self):
        engine = self._engine()
        self.assertIsNotNone(engine.resolve_mention("Joana"))
        self.assertIsNone(engine.resolve_mention("Toledo"))


class GrafiaSozinhaPipelineTest(_Pipeline):
    def test_modo_llm_troca_o_prenome_cadastrado(self):
        settings = AppSettings(
            local_ner_enabled=False,
            target_people=["Joana Ernestina Fictícia"],
            target_aliases=["Joana Ernestina Fictícia = Joana"],
        )
        _result, body, _audit = self.go("Depois Joana assinou o recibo e Joana pagou.\n", FakeDiscoverer([]),
                                        settings=settings)
        self.assertNotIn("Joana", body)


class ElencoModoLocalTest(unittest.TestCase):
    def _finalize(self, text: str, people: list[str]) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            job = "lote_20261019_090000"
            export_dir = base / "exportaveis" / job
            export_dir.mkdir(parents=True)
            md = export_dir / "doc_1.md"
            md.write_text(text, encoding="utf-8")
            mapping = PseudonymMap()
            for name in people:
                mapping.resolve("PERSON", name)
            outcome = FileOutcome(source=base / "a.txt", ok=True, markdown_path=md)
            finalize_lote(job_id=job, export_dir=export_dir, map_path=base / "privados" / job / "mapa.json",
                          outcomes=[outcome], mapping=mapping, settings=AppSettings())
            return (export_dir / "ELENCO.md").read_text(encoding="utf-8")

    def test_secao_vazia_diz_nenhum_caso(self):
        cast = self._finalize("Texto sem nada.\n", ["Joana Ernestina Fictícia"])
        self.assertIn(SAME_PERSON_TITLE, cast)
        self.assertIn(SAME_PERSON_EMPTY, cast)

    def test_prenome_e_mesmo_cpf(self):
        text = "Assinam [PESSOA_1], CPF [CPF_1], e [PESSOA_3] (CPF [CPF_1]).\n"
        cast = self._finalize(text, ["Joana Ernestina Fictícia", "Joana", "Ernestina Fictícia Souza"])
        self.assertIn("| [PESSOA_2] | [PESSOA_1] | prenome |", cast)
        self.assertIn("| [PESSOA_1] | [PESSOA_3] | mesmo CPF [CPF_1] ao lado |", cast)
        self.assertNotIn("Joana", cast)  # so tokens

    def test_cpf_de_uma_pessoa_so_nao_liga(self):
        self.assertEqual(same_cpf_rows([("a", "[PESSOA_1], CPF [CPF_1]. [PESSOA_1] de novo, [CPF_1].")]), [])


class RegistroPastaPrivadaTest(_Lote):
    def test_registro_na_pasta_privada_escolhida(self):
        items = ri.review("TROCAR | pessoa | Rosálio Fictício | 2\n", AppSettings(), self.data())
        result = ri.apply_review(AppSettings(), items)
        path = ri.write_review_record(self.private, self.JOB, "x", items, result, now=datetime(2026, 10, 19, 11, 0))
        self.assertEqual(path.parent, self.private / "revisoes")
        self.assertTrue(path.is_file())
        self.assertEqual(
            ri.review_audit_lines(self.private, "lote_20261019_120000"),
            ["Revisão externa aplicada antes desta rodada: 1 linhas (TROCAR 1, MANTER 0, UNIR 0)"],
        )


if __name__ == "__main__":
    unittest.main()
