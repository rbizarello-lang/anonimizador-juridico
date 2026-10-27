"""H9-f, item C: o nome do arquivo de origem nao vai para a pasta exportavel.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico.pseudonymizer import PseudonymMap

from anonimizador_app.file_names import NAMES_FILENAME, export_stem, write_name_map
from anonimizador_app.pipeline import run_job
from anonimizador_app.pipeline_llm import run_llm_job

from test_pipeline_llm import FakeDiscoverer

TEXT = (
    "<!-- pagina-origem: 1 -->\n\n"
    "O sócio FULANO DE TAL FICTICIO representa a empresa. Processo 5048714-11.2025.4.04.7200.\n"
)
NAME = "IDPJ EMPRESA FICTICIA LTDA.txt"
EPROC_NAME = "SC-50487141120254047200-2026-8-21_PARTE_1.txt"


def _exported_text(folder: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(folder.iterdir()) if path.is_file())


class ExportStemTests(unittest.TestCase):
    def test_nome_qualquer_vira_doc_n(self) -> None:
        mapping = PseudonymMap()
        self.assertEqual(export_stem(Path(NAME), mapping, 3), "doc_3")
        self.assertEqual(export_stem(Path("x.txt"), mapping, 0), "doc_1")

    def test_codigo_de_documento_conhecido_fica_e_o_desconhecido_cai(self) -> None:
        mapping = PseudonymMap()
        self.assertEqual(export_stem(Path("50059_1_CONTRSOCIAL13.pdf"), mapping, 2), "doc_2_CONTRSOCIAL13")
        self.assertEqual(export_stem(Path("ACME_FICTICIA_ACME2.pdf"), mapping, 2), "doc_2")

    def test_padrao_eproc_leva_o_token_do_processo(self) -> None:
        mapping = PseudonymMap()
        token = mapping.resolve("BR_PROCESSO", "5048714-11.2025.4.04.7200")
        label = token.strip("[]")
        self.assertEqual(export_stem(Path(EPROC_NAME), mapping, 1), f"SC-{label}_PARTE_1")
        # processo que o mapa nao conhecia ganha token novo
        other = export_stem(Path("PR-50011115420254047001-2026-8-21_PARTE_2.pdf"), mapping, 2)
        self.assertRegex(other, r"^PR-PROCESSO_\d+_PARTE_2$")
        self.assertNotIn("5001111", other)

    def test_mapa_de_nomes_na_pasta_privada(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(write_name_map(Path(tmp), []))
            path = write_name_map(Path(tmp), [("doc_1.md", NAME)])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["arquivos"], {"doc_1.md": NAME})
            self.assertEqual(path.name, NAMES_FILENAME)


class PastaExportavelNaoLevaONomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _source(self, name: str) -> Path:
        path = self.base / name
        path.write_text(TEXT, encoding="utf-8")
        return path

    def _check(self, result_dir: Path, private_dir: Path, exported: str, original: str) -> None:
        names = sorted(path.name for path in result_dir.iterdir())
        self.assertIn(exported, names)
        exported_text = _exported_text(result_dir)
        for needle in ("IDPJ EMPRESA", "FICTICIA LTDA", "50487141120254047200", Path(original).stem):
            self.assertNotIn(needle, exported_text + "\n".join(names))
        for required in ("INDICE.md", "ELENCO.md", "AUDITORIA.txt", "LEIA-ME.txt"):
            self.assertIn(required, names)
        mapped = json.loads((private_dir / NAMES_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(mapped["arquivos"][exported], original)

    def test_pipeline_com_llm(self) -> None:
        source = self._source(NAME)
        result = run_llm_job(
            [source], export_root=self.base / "exp", private_root=self.base / "priv",
            discoverer=FakeDiscoverer([("pessoa", "FULANO DE TAL FICTICIO", "socio")]),
            use_cache=False, local_floor=False, verification_rounds=0,
        )
        self.assertEqual(result.job.outcomes[0].markdown_path.name, "doc_1.md")
        self.assertEqual(result.job.outcomes[0].source.name, NAME)  # a tela segue com o nome real
        self._check(result.job.export_dir, result.job.map_path.parent, "doc_1.md", NAME)
        self.assertIn("doc_1.md", (result.job.export_dir / "INDICE.md").read_text(encoding="utf-8"))

    def test_pipeline_com_llm_no_padrao_eproc(self) -> None:
        source = self._source(EPROC_NAME)
        result = run_llm_job(
            [source], export_root=self.base / "exp", private_root=self.base / "priv",
            discoverer=FakeDiscoverer([("pessoa", "FULANO DE TAL FICTICIO", "socio")]),
            use_cache=False, local_floor=False, verification_rounds=0,
        )
        exported = result.job.outcomes[0].markdown_path.name
        self.assertRegex(exported, r"^SC-PROCESSO_\d+_PARTE_1\.md$")
        self._check(result.job.export_dir, result.job.map_path.parent, exported, EPROC_NAME)

    def test_pipeline_local(self) -> None:
        source = self._source(NAME)
        job = run_job([source], export_root=self.base / "exp", private_root=self.base / "priv", use_cache=False)
        self.assertEqual(job.outcomes[0].markdown_path.name, "doc_1.md")
        self._check(job.export_dir, job.map_path.parent, "doc_1.md", NAME)

    def test_fallback_e_quarentena_no_texto_exportavel_usam_o_nome_pseudonimizado(self) -> None:
        from test_pipeline_llm import FakeLocalEngine

        text = (
            "<!-- pagina-origem: 1 -->\n\nO sócio FULANO DE TAL FICTICIO foi citado.\n\n"
            "<!-- pagina-origem: 2 -->\n\nA empresa BELTRANA FICTICIA SA respondeu.\n"
        )
        source = self.base / NAME
        source.write_text(text, encoding="utf-8")
        discoverer = FakeDiscoverer([("pessoa", "FULANO DE TAL FICTICIO", "socio")], fail_pass={1, 2})
        result = run_llm_job(
            [source], export_root=self.base / "exp", private_root=self.base / "priv",
            discoverer=discoverer, use_cache=False, local_floor=False, verification_rounds=0,
            fallback_engine=FakeLocalEngine([("PERSON", "FULANO DE TAL FICTICIO"), ("ORGANIZATION", "BELTRANA FICTICIA SA")]),
        )
        exported_text = _exported_text(result.job.export_dir)
        self.assertNotIn("IDPJ EMPRESA", exported_text)
        self.assertNotIn("FICTICIA LTDA", exported_text)
        self.assertIn("doc_1.md: paginas 2", exported_text)
        # a tela segue com o nome real
        self.assertIn(NAME, result.reports)
