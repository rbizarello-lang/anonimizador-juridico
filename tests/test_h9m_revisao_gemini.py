"""H9-m: importador da revisao externa (Gemini) na aba Caso. Nomes, documentos e lotes INVENTADOS."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from anonimizador_juridico import PseudonymMap

from anonimizador_app import review_import as ri
from anonimizador_app.pipeline import finalize_lote
from anonimizador_app.settings import AppSettings, load_settings, save_settings

# Texto exportado do lote de referencia (ja com marcas) e o mapa privado dele.
DOC_1 = (
    "# Folha 1\n\n"
    "O sócio [PESSOA_1] e a executada [EMPRESA_1] foram citados. A filha Quitéria\n"
    "Valadares assinou o termo. Testemunha: Bartolomeu\n"
    "Pires. Sr. Teodoro assinou depois. Endereço: Rua das Acácias Fictícias, 77.\n"
    "Processo 5000009-00.2024.4.04.7000 e o Juízo da Vara Ficticia [PESSOA_2].\n"
    "[PESSOA_1] e [PESSOA_3] compareceram. Valadares e Mendonça também.\n"
)
DOC_2 = "Petição de [PESSOA_1]. Pinhal Verde fornece madeira. [EMPRESA_2] e [PESSOA_4] e [PESSOA_5].\n"

RECORDS = [
    {"token": "[PESSOA_1]", "entity_type": "PERSON", "canonical": "teodoro quirino valadares",
     "originals": ["TEODORO QUIRINO VALADARES", "Teodoro Quirino Valadares"], "preferida": "Teodoro Quirino Valadares",
     "alvo": "Teodoro Quirino Valadares"},
    {"token": "[EMPRESA_1]", "entity_type": "ORGANIZATION", "canonical": "madeireira pinhal verde",
     "originals": ["Madeireira Pinhal Verde Ltda"], "preferida": "Madeireira Pinhal Verde Ltda",
     "alvo": "Madeireira Pinhal Verde Ltda"},
    {"token": "[PESSOA_2]", "entity_type": "PERSON", "canonical": "almerinda vara",
     "originals": ["Almerinda Fictícia Coutinho"], "preferida": "Almerinda Fictícia Coutinho"},
    {"token": "[PESSOA_3]", "entity_type": "PERSON", "canonical": "t q valadares",
     "originals": ["T. Q. Valadares"], "preferida": "T. Q. Valadares"},
    {"token": "[EMPRESA_2]", "entity_type": "ORGANIZATION", "canonical": "receita",
     "originals": ["Secretaria Ficticia de Fazenda"], "preferida": "Secretaria Ficticia de Fazenda"},
    {"token": "[PESSOA_4]", "entity_type": "PERSON", "canonical": "teodoro",
     "originals": ["Teodoro"], "preferida": "Teodoro"},
    {"token": "[PESSOA_5]", "entity_type": "PERSON", "canonical": "bartolomeu pires mendonca",
     "originals": ["Bartolomeu Pires Mendonça"], "preferida": "Bartolomeu Pires Mendonça"},
]


def _settings() -> AppSettings:
    return AppSettings(
        target_people=["Teodoro Quirino Valadares", "Quitéria Valadares Exemplar"],
        target_companies=["Madeireira Pinhal Verde Ltda"],
        target_aliases=["Teodoro Quirino Valadares = T. Quirino"],
        exclusions=["Tribunal Ficticio"],
    )


class _Lote(unittest.TestCase):
    JOB = "lote_20261019_100000"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.private = self.base / "privados"
        self.export = self.base / "exportaveis"
        (self.private / self.JOB).mkdir(parents=True)
        (self.export / self.JOB).mkdir(parents=True)
        payload = {"schema_version": 2, "warning": "x", "records": RECORDS}
        (self.private / self.JOB / "mapa.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (self.export / self.JOB / "doc_1.md").write_text(DOC_1, encoding="utf-8")
        (self.export / self.JOB / "doc_2.md").write_text(DOC_2, encoding="utf-8")
        (self.export / self.JOB / "INDICE.md").write_text("Fulano Inexistente Indice\n", encoding="utf-8")

    def data(self) -> ri.LoteData:
        lotes = ri.list_lotes(self.private, self.export)
        self.assertEqual([ref.job_id for ref in lotes], [self.JOB])
        return ri.load_lote(lotes[0])

    def one(self, line: str, settings: AppSettings | None = None) -> ri.ReviewItem:
        items = ri.review(line, settings or _settings(), self.data())
        self.assertEqual(len(items), 1, items)
        return items[0]


class ParseTest(unittest.TestCase):
    def test_tolerante(self):
        text = (
            "Segue a revisão:\n"
            "```\n"
            "  TROCAR  |  pessoa |  Fulano  de Tal   | 3 \n"
            "\n"
            "NADA\n"
            "manter | [EMPRESA_2] | órgão público\n"
            "- UNIR|[PESSOA_1]|[PESSOA_3]|mesma pessoa\n"
            "**TROCAR** | cda | 80.6.21.000000-00 | 9\n"
            "```\n"
            "Observação final sem formato\n"
        )
        lines = ri.parse_review(text)
        self.assertEqual([line.action for line in lines], ["", "TROCAR", "MANTER", "UNIR", "TROCAR", ""])
        self.assertEqual(lines[1].fields, ("pessoa", "Fulano  de Tal", "3"))
        self.assertEqual(lines[3].fields[:2], ("[PESSOA_1]", "[PESSOA_3]"))

    def test_fora_do_formato_vira_rejeitada(self):
        items = ri.review("Achei tudo certo, salvo um ponto.\n", AppSettings(), ri.LoteData("lote_x", {}, ["a"]))
        self.assertEqual((items[0].status, items[0].reason), (ri.REJECTED, "fora do formato"))
        self.assertFalse(items[0].checkable)


class TrocarTest(_Lote):
    def test_inexistente_rejeitado(self):
        item = self.one("TROCAR | pessoa | Fulano Inexistente Indice | 1")  # so no INDICE, que nao conta
        self.assertEqual((item.status, item.reason), (ri.REJECTED, "texto inexistente"))

    def test_ja_trocado(self):
        item = self.one("TROCAR | pessoa | [PESSOA_1] | 1")
        self.assertEqual((item.status, item.reason), (ri.REJECTED, "já trocado"))

    def test_texto_com_quebra_de_linha_e_sem_acento_aceito(self):
        item = self.one("TROCAR | pessoa | BARTOLOMEU PIRES | 1")  # no texto: "Bartolomeu\nPires"
        self.assertEqual(item.status, ri.ACCEPTED)
        # Parte do nome de [PESSOA_5] (mapa): grafia desse nome, nao entrada nova.
        self.assertEqual(item.proposal.field_name, "target_aliases")
        self.assertEqual(item.proposal.principal, "Bartolomeu Pires Mendonça")
        self.assertTrue(item.checked)

    def test_grafia_de_nome_listado(self):
        item = self.one("TROCAR | pessoa | Quitéria Valadares | 1")  # "Quitéria\nValadares" no texto
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertEqual(
            (item.proposal.field_name, item.proposal.principal, item.proposal.value),
            ("target_aliases", "Quitéria Valadares Exemplar", "Quitéria Valadares"),
        )

    def test_grafia_de_empresa_listada(self):
        item = self.one("TROCAR | empresa | Pinhal Verde | 2")
        self.assertEqual(item.proposal.principal, "Madeireira Pinhal Verde Ltda")

    def test_palavra_unica_ambigua_vai_para_decidir(self):
        item = self.one("TROCAR | pessoa | Valadares | 1")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertIn("ambíguo", item.reason)
        self.assertIn("Teodoro Quirino Valadares", item.candidates)
        self.assertIn("Quitéria Valadares Exemplar", item.candidates)
        self.assertFalse(item.checked)
        # Escolhido um candidato na tela e marcada, vira grafia dele.
        item.choice, item.checked = "Teodoro Quirino Valadares", True
        settings = _settings()
        ri.apply_review(settings, [item])
        self.assertIn("Teodoro Quirino Valadares = T. Quirino; Valadares", settings.target_aliases)

    def test_palavra_unica_de_um_nome_so_vira_grafia(self):
        item = self.one("TROCAR | pessoa | Mendonça | 1")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertEqual(item.proposal.principal, "Bartolomeu Pires Mendonça")

    def test_palavra_unica_sem_nome_nunca_vira_pessoa(self):
        item = self.one("TROCAR | pessoa | Juízo | 1")
        self.assertEqual(item.status, ri.DECIDE)
        self.assertIsNone(item.proposal)
        # H9-n: a linha ganha o campo "nome principal"; sem nada digitado, nao ha o que aplicar.
        self.assertTrue(item.name_field)
        self.assertFalse(item.checked)
        self.assertIsNone(item.effective())

    def test_nome_ja_listado(self):
        item = self.one("TROCAR | pessoa | TEODORO QUIRINO VALADARES | 1", AppSettings(
            target_people=["Teodoro Quirino Valadares"]))
        self.assertEqual(item.status, ri.REJECTED)  # "Teodoro Quirino Valadares" nao esta em claro no texto
        item = self.one("TROCAR | pessoa | Teodoro | 1", AppSettings(target_people=["Teodoro"]))
        self.assertEqual((item.status, item.reason), (ri.REJECTED, "já está na lista do caso"))

    def test_destinos_dos_outros_tipos(self):
        settings = _settings()
        text = (
            "TROCAR | endereco | Rua das Acácias Fictícias, 77 | 1\n"
            "TROCAR | processo | 5000009-00.2024.4.04.7000 | 1\n"
        )
        items = ri.review(text, settings, self.data())
        self.assertEqual([item.proposal.field_name for item in items], ["target_contacts", "target_processes"])
        self.assertTrue(all(item.status == ri.ACCEPTED for item in items))


class ManterTest(_Lote):
    def test_traduzido_pelo_mapa(self):
        item = self.one("MANTER | [EMPRESA_2] | órgão público")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertEqual((item.proposal.field_name, item.proposal.value), ("exclusions", "Secretaria Ficticia de Fazenda"))
        self.assertIn("Secretaria Ficticia de Fazenda", item.subject)
        self.assertTrue(item.checked)

    def test_marca_com_sufixo_de_grupo_e_sem_colchete(self):
        item = self.one("MANTER | EMPRESA_2·G1 | órgão")
        self.assertEqual(item.proposal.value, "Secretaria Ficticia de Fazenda")

    def test_palavra_unica_decidir(self):
        item = self.one("MANTER | [PESSOA_4] | prenome")
        self.assertEqual((item.status, item.reason), (ri.DECIDE, "palavra única: decidir"))
        self.assertFalse(item.checked)

    def test_conflito_com_a_lista(self):
        item = self.one("MANTER | [PESSOA_1] | parece servidor")
        self.assertEqual((item.status, item.reason), (ri.DECIDE, "conflito com a lista do caso"))
        self.assertFalse(item.checked)

    def test_marca_desconhecida(self):
        item = self.one("MANTER | [PESSOA_99] | x")
        self.assertEqual(item.status, ri.REJECTED)


class UnirTest(_Lote):
    def test_traduzido_e_desmarcado(self):
        item = self.one("UNIR | [PESSOA_3] | [PESSOA_1] | mesma pessoa")
        self.assertEqual(item.status, ri.ACCEPTED)
        self.assertFalse(item.checked)  # UNIR nunca vem marcada
        # A = a que esta na lista do caso, mesmo dada em segundo lugar.
        self.assertEqual(
            (item.proposal.field_name, item.proposal.principal, item.proposal.value),
            ("target_aliases", "Teodoro Quirino Valadares", "T. Q. Valadares"),
        )

    def test_sem_lista_vence_a_de_mais_ocorrencias(self):
        item = self.one("UNIR | [PESSOA_5] | [PESSOA_2] | mesma pessoa", AppSettings())
        # [PESSOA_2] aparece uma vez, [PESSOA_5] uma vez: empate fica a ordem dada.
        self.assertEqual(item.proposal.principal, "Bartolomeu Pires Mendonça")
        item = self.one("UNIR | [PESSOA_3] | [PESSOA_1] | mesma pessoa", AppSettings())
        self.assertEqual(item.proposal.principal, "Teodoro Quirino Valadares")  # 3 ocorrencias contra 1


class AplicarTest(_Lote):
    def test_mescla_sem_duplicar_e_consolida_grafias(self):
        settings = _settings()
        settings.target_aliases = [
            "Teodoro Quirino Valadares = T. Quirino",
            "linha solta sem igual",
            "teodoro quirino valadares = Téo Valadares",
        ]
        text = (
            "UNIR | [PESSOA_1] | [PESSOA_3] | mesma pessoa\n"
            "MANTER | [EMPRESA_2] | órgão\n"
            "MANTER | [EMPRESA_2] | repetida\n"
            "TROCAR | processo | 5000009-00.2024.4.04.7000 | 1\n"
            "TROCAR | empresa | Pinhal Verde | 2\n"
        )
        items = ri.review(text, settings, self.data())
        items[0].checked = True  # UNIR confirmada linha a linha
        result = ri.apply_review(settings, items)
        self.assertEqual(
            settings.target_aliases,
            [
                "Teodoro Quirino Valadares = T. Quirino; Téo Valadares; T. Q. Valadares",
                "linha solta sem igual",
                "Madeireira Pinhal Verde Ltda = Pinhal Verde",
            ],
        )
        self.assertEqual(settings.exclusions, ["Tribunal Ficticio", "Secretaria Ficticia de Fazenda"])
        self.assertEqual(settings.target_processes, ["5000009-00.2024.4.04.7000"])
        self.assertEqual(settings.target_people, ["Teodoro Quirino Valadares", "Quitéria Valadares Exemplar"])
        self.assertEqual(result.by_action()["UNIR"], 1)
        # Aplicar de novo nao duplica nada.
        again = ri.apply_review(settings, items)
        self.assertEqual(sum(again.added.values()), 0)
        self.assertEqual(len(settings.target_aliases), 3)

    def test_registro_e_linha_da_auditoria_do_proximo_lote(self):
        settings = _settings()
        text = "MANTER | [EMPRESA_2] | órgão\nTROCAR | processo | 5000009-00.2024.4.04.7000 | 1\nNADA\n"
        items = ri.review(text, settings, self.data())
        result = ri.apply_review(settings, items)
        record = ri.write_review_record(self.private, self.JOB, text, items, result, now=datetime(2026, 10, 19, 11, 0, 0))
        self.assertEqual(record.name, f"revisao_{self.JOB}_20261019_110000.txt")
        body = record.read_text(encoding="utf-8")
        self.assertIn("Aplicadas: 2 (TROCAR 1, MANTER 1, UNIR 0)", body)
        self.assertIn(text.strip(), body)
        lines = ri.review_audit_lines(self.private, "lote_20261019_120000")
        self.assertEqual(lines, ["Revisão externa aplicada antes desta rodada: 2 linhas (TROCAR 1, MANTER 1, UNIR 0)"])
        # Lote anterior ao registro: a revisao nao e "antes desta rodada" para ele.
        (self.private / "lote_20261019_113000").mkdir()
        (self.private / "lote_20261019_113000" / "mapa.json").write_text("{}", encoding="utf-8")
        self.assertEqual(ri.review_audit_lines(self.private, "lote_20261019_120000"), [])

    def test_auditoria_do_lote_recebe_a_linha(self):
        settings = _settings()
        items = ri.review("MANTER | [EMPRESA_2] | órgão\n", settings, self.data())
        ri.write_review_record(self.private, self.JOB, "x", items, ri.apply_review(settings, items),
                               now=datetime(2026, 10, 19, 11, 0, 0))
        job = "lote_20261019_120000"
        export_dir = self.export / job
        export_dir.mkdir()
        finalize_lote(job_id=job, export_dir=export_dir, map_path=self.private / job / "mapa.json", outcomes=[],
                      mapping=PseudonymMap(), settings=settings)
        audit = (export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("Revisão externa aplicada antes desta rodada: 1 linhas (TROCAR 0, MANTER 1, UNIR 0)", audit)
        self.assertNotIn("Secretaria", audit)  # so contagens no exportavel

    def test_aplicar_grava_pelo_caminho_de_sempre(self):
        settings = _settings()
        items = ri.review("MANTER | [EMPRESA_2] | órgão\n", settings, self.data())
        ri.apply_review(settings, items)
        save_settings(self.private, settings)
        self.assertIn("Secretaria Ficticia de Fazenda", load_settings(self.private).exclusions)


class AuditoriaModoLocalTest(unittest.TestCase):
    def _audit(self, settings: AppSettings) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            job = "lote_20261019_090000"
            export_dir = base / "exportaveis" / job
            export_dir.mkdir(parents=True)
            # Como a tela no modo local chama: sem contagem de rede, com a lista usada.
            finalize_lote(job_id=job, export_dir=export_dir, map_path=base / "privados" / job / "mapa.json",
                          outcomes=[], mapping=PseudonymMap(), settings=settings)
            return (export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")

    def test_lista_do_caso_usada_no_modo_local(self):
        audit = self._audit(_settings())
        self.assertIn("Motor: local (sem LLM)", audit)
        self.assertIn("Lista do caso usada: pessoas 2, empresas 1", audit)

    def test_alerta_de_lista_vazia_no_modo_local(self):
        self.assertIn("ALERTA: lista do caso VAZIA", self._audit(AppSettings()))


if __name__ == "__main__":
    unittest.main()
