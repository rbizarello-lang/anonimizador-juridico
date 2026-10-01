"""Pipeline com LLM, com um descobridor falso (sem rede).

O falso devolve uma mencao por entidade presente no trecho — como um LLM que
acerta quem e quem, mas aponta uma ocorrencia so. As outras ficam a cargo da
propagacao. Nomes, numeros e enderecos sao ficticios.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.credentials import ApiKey
from anonimizador_app.llm_discovery import Discovery, DiscoveryReport, LlmConfig
from anonimizador_app.pipeline_llm import LEGEND_FILENAME, ROSTER_FILENAME, run_llm_job
from anonimizador_app.roster import KEEP, Mention

DOC = """\
# EXECUÇÃO FISCAL

| EXEQUENTE | EXECUTADO |
|---|---|
| UNIÃO - FAZENDA NACIONAL | FAZENDA SANTA RITA AGROPECUARIA LTDA (CNPJ 11.222.333/0001-81) |

<!-- pagina-origem: 1 -->

A UNIÃO (FAZENDA NACIONAL), por intermédio da PGFN, ajuíza execução fiscal
contra Fazenda Santa Rita Agropecuária Ltda, conforme CDA anexa.

A PGFN requer a citação do sócio OLAVO RICARDO TEIXEIRA, CPF 529.982.247-25.

<!-- pagina-origem: 2 -->

Consta que MARLENE OLIVEIRA SAMPAIO, sócia, reside na Rua das Acácias, 120.
No Tema 1.209/STJ discute-se a responsabilidade; a UNIÃO COMERCIAL DE GRÃOS LTDA
recebeu os bens (art. 133 do CTN). Valor: R$ 1.234.567,89 em 12/03/2024.
Pedido de SISBAJUD e RENAJUD. Conforme o evento 12, Olavo Ricardo Teixeira foi citado.

Documento eletrônico assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.
"""

FAKE_KEY = "chave-ficticia-9999-zzzz"

ENTITIES = [
    ("empresa", "FAZENDA SANTA RITA AGROPECUARIA LTDA", "parte"),
    ("pessoa", "OLAVO RICARDO TEIXEIRA", "socio"),
    ("pessoa", "MARLENE OLIVEIRA SAMPAIO", "socio"),
    ("endereco", "Rua das Acácias, 120", "socio"),
    ("empresa", "UNIÃO COMERCIAL DE GRÃOS LTDA", "terceiro"),
    ("pessoa", "LUCIANA MONTEFUSCO DE ALMEIDA", "magistrado"),
]
MUST_STAY = [
    "UNIÃO - FAZENDA NACIONAL",
    "PGFN",
    "Tema 1.209/STJ",
    "art. 133 do CTN",
    "R$ 1.234.567,89",
    "12/03/2024",
    "SISBAJUD",
    "Conforme o evento 12,",
    "LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal",
    "<!-- pagina-origem: 2 -->",
]


class FakeDiscoverer:
    """Acha as entidades conhecidas; pode 'esquecer' algumas na 1a passada."""

    def __init__(self, entities=ENTITIES, *, miss_first=(), fail_pass=None) -> None:
        self.entities = list(entities)
        self.miss_first = set(miss_first)
        self.fail_pass = fail_pass
        self.calls: list[str] = []

    def describe(self) -> str:
        return "descobridor falso"

    def discover(self, text: str) -> Discovery:
        self.calls.append(text)
        first_pass = len(self.calls) == 1
        report = DiscoveryReport(chunks=1, sent=1)
        if self.fail_pass is not None and len(self.calls) in self.fail_pass:
            start = text.index("<!-- pagina-origem: 2 -->")
            report.failed = [(start, len(text))]
            report.failure_reasons = ["bloqueio simulado"]
        mentions = []
        for kind, value, role in self.entities:
            if first_pass and value in self.miss_first:
                continue
            index = text.casefold().find(value.casefold())
            if index < 0:
                continue
            if report.failed and report.failed[0][0] <= index:
                continue
            surface = text[index : index + len(value)]
            mentions.append(
                Mention(kind=kind, text=surface, role=role, canonical=value,
                        start=index, end=index + len(value), source_text=surface)
            )
        return Discovery(mentions=mentions, report=report)


class FakeChannelDiscoverer(FakeDiscoverer):
    """Como o descobridor real: leva o canal na configuracao (so o id, nunca a chave)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.config = LlmConfig(channel="gemini-pago", api_key=ApiKey(FAKE_KEY, "teste"))


class PipelineLlmTests(unittest.TestCase):
    def _run(self, discoverer, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(DOC, encoding="utf-8")
        result = run_llm_job(
            [source],
            export_root=base / "exportaveis",
            private_root=base / "privados",
            discoverer=discoverer,
            use_cache=False,
            **kwargs,
        )
        outcome = result.job.outcomes[0]
        self.assertTrue(outcome.ok, outcome.error)
        return result, outcome.markdown_path.read_text(encoding="utf-8")

    def test_no_leak_and_structure_intact(self) -> None:
        result, text = self._run(FakeDiscoverer())

        for kind, value, role in ENTITIES:
            if role == "magistrado":
                continue
            self.assertNotIn(value.casefold(), text.casefold(), value)
        # Grafia de Title Case (sem acento na razao) e a mesma entidade.
        self.assertNotIn("Fazenda Santa Rita", text)
        self.assertEqual(text.count("[EMPRESA_1]"), 2)
        self.assertEqual(text.count("[PESSOA_1]"), 2)
        for kept in MUST_STAY:
            self.assertIn(kept, text)
        self.assertTrue(result.job.export_allowed, result.job.export_block_reason)

    def test_ids_never_reach_the_llm(self) -> None:
        discoverer = FakeDiscoverer()
        self._run(discoverer)

        for sent in discoverer.calls:
            self.assertNotIn("11.222.333/0001-81", sent)
            self.assertNotIn("529.982.247-25", sent)
        self.assertIn("[CNPJ_1]", discoverer.calls[0])

    def test_verification_catches_what_discovery_missed(self) -> None:
        result, text = self._run(
            FakeDiscoverer(miss_first={"MARLENE OLIVEIRA SAMPAIO"}), verification_rounds=1
        )

        self.assertNotIn("MARLENE OLIVEIRA SAMPAIO", text)
        report = result.reports["autos.md"]
        self.assertGreaterEqual(report.new_in_verification, 1)
        self.assertEqual(len(report.verification), 1)

    def test_without_verification_the_miss_stays(self) -> None:
        _result, text = self._run(
            FakeDiscoverer(miss_first={"MARLENE OLIVEIRA SAMPAIO"}), verification_rounds=0
        )
        self.assertIn("MARLENE OLIVEIRA SAMPAIO", text)

    def test_page_unread_in_every_pass_blocks_export(self) -> None:
        result, _text = self._run(FakeDiscoverer(fail_pass={1, 2}), verification_rounds=1)

        report = result.reports["autos.md"]
        self.assertEqual(report.unread_pages, [2])
        self.assertFalse(result.job.export_allowed)
        self.assertIn("paginas 2", result.job.export_block_reason)
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("ALERTA: paginas 2 sem leitura do LLM", audit)

    def test_page_read_in_the_second_pass_does_not_block(self) -> None:
        result, text = self._run(FakeDiscoverer(fail_pass={1}), verification_rounds=1)

        self.assertEqual(result.reports["autos.md"].unread_pages, [])
        self.assertTrue(result.job.export_allowed)
        self.assertNotIn("MARLENE OLIVEIRA SAMPAIO", text)

    def test_roster_is_private_and_legend_has_no_names(self) -> None:
        result, _text = self._run(FakeDiscoverer())

        self.assertEqual(result.roster_path.name, ROSTER_FILENAME)
        self.assertEqual(result.roster_path.parent, result.job.map_path.parent)
        self.assertFalse((result.job.export_dir / ROSTER_FILENAME).exists())
        legend = (result.job.export_dir / LEGEND_FILENAME).read_text(encoding="utf-8")
        self.assertRegex(legend, r"\| \[PESSOA_1\] \| pessoa \| socio \| 2 \|")
        for _kind, value, _role in ENTITIES:
            self.assertNotIn(value.casefold(), legend.casefold())
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        for _kind, value, _role in ENTITIES:
            self.assertNotIn(value.casefold(), audit.casefold())

    def test_same_structure_as_the_local_engine(self) -> None:
        result, text = self._run(FakeDiscoverer())
        folder = result.job.export_dir

        # Ancora visivel por folha, depois do comentario de pagina.
        self.assertIn("<!-- pagina-origem: 1 -->\n[fl. 1]\n", text)
        self.assertIn("<!-- pagina-origem: 2 -->\n[fl. 2]\n", text)
        self.assertTrue((folder / "INDICE.md").is_file())
        index = (folder / "INDICE.md").read_text(encoding="utf-8")
        self.assertIn("## autos.md", index)
        cast = (folder / "ELENCO.md").read_text(encoding="utf-8")
        self.assertIn("# Elenco do lote (sem nomes)", cast)
        self.assertIn("## Papéis atribuídos pelo LLM", cast)
        self.assertLess(cast.index("# Elenco do lote"), cast.index("## Papéis atribuídos pelo LLM"))
        self.assertEqual(result.legend_path, folder / "ELENCO.md")
        for _kind, value, _role in ENTITIES:
            self.assertNotIn(value.casefold(), (index + cast).casefold())

    def test_roles_section_counts_mentions_per_token(self) -> None:
        result, _text = self._run(FakeDiscoverer())
        cast = (result.job.export_dir / "ELENCO.md").read_text(encoding="utf-8")
        section = cast.split("## Papéis atribuídos pelo LLM", 1)[1]

        self.assertIn("| Token | Tipo | Papel | Menções |", section)
        self.assertRegex(section, r"\| \[EMPRESA_1\] \| empresa \| parte \| 2 \|")
        self.assertRegex(section, r"\| \[PESSOA_2\] \| pessoa \| socio \| 1 \|")
        self.assertEqual(result.occurrences["pessoa:olavo ricardo teixeira"], 2)
        # Entrada mantida (juiza): nao tem token, mas a contagem existe para a tela de revisao.
        self.assertEqual(result.occurrences["pessoa:luciana montefusco de almeida"], 1)
        self.assertNotIn("LUCIANA", section)

    def test_without_legend_the_cast_is_still_written(self) -> None:
        result, _text = self._run(FakeDiscoverer(), write_legend=False)
        cast = (result.job.export_dir / "ELENCO.md").read_text(encoding="utf-8")
        self.assertNotIn("Papéis atribuídos pelo LLM", cast)
        self.assertIsNone(result.legend_path)

    def test_engine_is_in_audit_and_guides_and_the_key_is_nowhere(self) -> None:
        result, _text = self._run(
            FakeChannelDiscoverer(), engine_notes=["Canal: gemini-pago", "Chave lida de: teste"]
        )
        self.assertEqual(result.engine, "gemini-pago")
        folder = result.job.export_dir
        audit = (folder / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("motor LLM: gemini-pago", audit)
        self.assertIn("## motor", audit)
        self.assertIn("Chave lida de: teste", audit)
        self.assertIn("motor LLM: gemini-pago", (folder / "LEIA-ME.txt").read_text(encoding="utf-8"))
        private_guide = (result.job.map_path.parent / "LEIA-ME.txt").read_text(encoding="utf-8")
        self.assertIn("motor LLM: gemini-pago", private_guide)
        for root in (folder, result.job.map_path.parent, result.job.map_path.parent.parent):
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(FAKE_KEY, path.read_text(encoding="utf-8", errors="ignore"), path.name)

    def test_unlabeled_discoverer_is_reported_as_generic_llm(self) -> None:
        result, _text = self._run(FakeDiscoverer())
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("motor LLM: llm", audit)

    def test_reviewed_roster_is_respected(self) -> None:
        first, _text = self._run(FakeDiscoverer())
        payload = json.loads(first.roster_path.read_text(encoding="utf-8"))
        for item in payload["entradas"]:
            if item["nome"] == "MARLENE OLIVEIRA SAMPAIO":
                item["acao"] = KEEP
        with tempfile.TemporaryDirectory() as tmp:
            reviewed = Path(tmp) / "elenco.json"
            reviewed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            _second, text = self._run(FakeDiscoverer(), roster_path=reviewed)

        self.assertIn("MARLENE OLIVEIRA SAMPAIO", text)
        self.assertNotIn("OLAVO RICARDO TEIXEIRA", text)

    def test_roster_spans_the_whole_lote(self) -> None:
        """Nome so descoberto na PARTE_2 tambem sai da PARTE_1."""

        class PerFile(FakeDiscoverer):
            def discover(self, text: str) -> Discovery:
                found = super().discover(text)
                if "PARTE_1" in text:
                    found.mentions = [m for m in found.mentions if m.text != "OLAVO RICARDO TEIXEIRA"]
                return found

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        part_1 = base / "PARTE_1.md"
        part_2 = base / "PARTE_2.md"
        part_1.write_text("PARTE_1\nO sócio Olavo Ricardo Teixeira foi citado.\n", encoding="utf-8")
        part_2.write_text("PARTE_2\nA PGFN requer a citação do sócio OLAVO RICARDO TEIXEIRA.\n", encoding="utf-8")
        result = run_llm_job(
            [part_1, part_2],
            export_root=base / "exportaveis",
            private_root=base / "privados",
            discoverer=PerFile(),
            use_cache=False,
            verification_rounds=0,
        )

        texts = [outcome.markdown_path.read_text(encoding="utf-8") for outcome in result.job.outcomes]
        self.assertEqual(texts[0], "PARTE_1\nO sócio [PESSOA_1] foi citado.\n")
        self.assertIn("sócio [PESSOA_1].", texts[1])

    def test_listed_target_is_tokenized_even_if_llm_misses_it(self) -> None:
        from anonimizador_app.settings import AppSettings

        silent = FakeDiscoverer(entities=[])
        _result, text = self._run(
            silent, settings=AppSettings(target_people=["Marlene Oliveira Sampaio"])
        )
        self.assertNotIn("MARLENE OLIVEIRA SAMPAIO", text)
        self.assertIn("OLAVO RICARDO TEIXEIRA", text)



COVER_ALFA = (
    "N.º do processo: 5001111-54.2025.4.04.7001\nClasse da ação: Execução Fiscal\n\n"
    "Partes e Representantes\n\n| EXECUTADO |\n|---|\n"
    "| ALFA TRANSPORTES FICTICIOS LTDA (11.222.333/0001-81) - Pessoa Jurídica |\n\n"
    "<!-- pagina-origem: 1 -->\n\nA executada ALFA TRANSPORTES FICTICIOS LTDA deve.\n"
)
COVER_BETA = (
    "N.º do processo: 5002222-33.2025.4.04.7001\nClasse da ação: Agravo de Instrumento\n\n"
    "Partes e Representantes\n\n| AGRAVADO |\n|---|\n"
    "| BETA LOGISTICA FICTICIA LTDA (11.222.333/0001-81) - Pessoa Jurídica |\n\n"
    "<!-- pagina-origem: 1 -->\n\nA agravada BETA LOGISTICA FICTICIA LTDA contesta.\n"
)


class StructureParityTests(unittest.TestCase):
    """O motor LLM entrega o que a estrutura entregou: sufixo de grupo, trava do IBGE, matricula por cartorio."""

    def _lote(self, documents: dict[str, str], entities, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        sources = []
        for name, text in documents.items():
            path = base / name
            path.write_text(text, encoding="utf-8")
            sources.append(path)
        result = run_llm_job(
            sources,
            export_root=base / "exportaveis",
            private_root=base / "privados",
            discoverer=FakeDiscoverer(entities),
            use_cache=False,
            verification_rounds=0,
            **kwargs,
        )
        for outcome in result.job.outcomes:
            self.assertTrue(outcome.ok, outcome.error)
        texts = {o.source.name: o.markdown_path.read_text(encoding="utf-8") for o in result.job.outcomes}
        return result, texts

    def test_companies_with_the_same_cnpj_get_the_group_suffix(self) -> None:
        entities = [
            ("empresa", "ALFA TRANSPORTES FICTICIOS LTDA", "parte"),
            ("empresa", "BETA LOGISTICA FICTICIA LTDA", "parte"),
        ]
        result, texts = self._lote({"a.md": COVER_ALFA, "b.md": COVER_BETA}, entities)

        self.assertIn("[EMPRESA_1·G1]", texts["a.md"])
        self.assertIn("[EMPRESA_2·G1]", texts["b.md"])
        self.assertNotIn("ALFA TRANSPORTES", texts["a.md"])
        cast = (result.job.export_dir / "ELENCO.md").read_text(encoding="utf-8")
        self.assertIn("G1", cast)
        # A tabela de papeis do LLM (no fim do ELENCO) tambem leva o sufixo; o mapa privado, nao.
        section = cast.split("## Papéis atribuídos pelo LLM", 1)[1]
        self.assertIn("| [EMPRESA_1·G1] |", section)
        self.assertIn("| [EMPRESA_2·G1] |", section)
        mapa = result.job.map_path.read_text(encoding="utf-8")
        self.assertNotIn("·G", mapa)
        guide = (result.job.export_dir / "LEIA-ME.txt").read_text(encoding="utf-8")
        self.assertIn("Sufixo de grupo", guide)

    def test_one_word_company_that_is_a_city_does_not_eat_the_court_or_the_address(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Vara Federal de Joinville, Subseção Judiciária de Joinville/SC.\n"
            "A executada JOINVILLE METAIS LTDA tem sede na Rua das Acácias, 120, Joinville - SC.\n"
            "A empresa Joinville, segundo o contrato, vendeu os bens.\n"
        )

        class OneWordDiscoverer(FakeDiscoverer):
            def discover(self, body: str) -> Discovery:
                found = super().discover(body)
                for needle in ("Joinville,", "Joinville/SC", "Joinville - SC"):
                    index = body.find(needle)
                    found.mentions.append(
                        Mention(kind="empresa", text="Joinville", role="parte", canonical="Joinville Metais Ltda",
                                start=index, end=index + len("Joinville"), source_text="Joinville")
                    )
                return found

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(text, encoding="utf-8")
        result = run_llm_job(
            [source],
            export_root=base / "exportaveis",
            private_root=base / "privados",
            discoverer=OneWordDiscoverer([("empresa", "JOINVILLE METAIS LTDA", "parte")]),
            use_cache=False,
            verification_rounds=0,
        )
        body = result.job.outcomes[0].markdown_path.read_text(encoding="utf-8")

        self.assertIn("Vara Federal de Joinville,", body)
        self.assertIn("Subseção Judiciária de Joinville/SC", body)
        self.assertIn("Joinville - SC", body)
        self.assertNotIn("JOINVILLE METAIS", body)
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("recusada", audit)
        self.assertNotIn("Joinville", audit)
        private = (result.job.map_path.parent / "AUDITORIA_PRIVADA.txt").read_text(encoding="utf-8")
        self.assertIn("Joinville", private)
        self.assertFalse((result.job.export_dir / "AUDITORIA_PRIVADA.txt").exists())

    def test_registry_number_is_keyed_by_the_office_and_the_office_stays(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Imóvel da matrícula nº 12345 do 1º Registro de Imóveis de Joinville.\n"
            "Imóvel da matrícula nº 12345 do 2º Registro de Imóveis de Joinville.\n"
        )
        _result, texts = self._lote({"autos.md": text}, [])
        body = texts["autos.md"]

        self.assertIn("1º Registro de Imóveis de Joinville", body)
        self.assertIn("2º Registro de Imóveis de Joinville", body)
        self.assertNotIn("12345", body)
        self.assertEqual(len(set(re.findall(r"\[MATRICULA_IMOVEL_\d+\]", body))), 2)


if __name__ == "__main__":
    unittest.main()
