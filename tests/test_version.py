from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from anonimizador_app.audit import write_audit_report
from anonimizador_app.fast_extract import FAST_EXTRACTOR_VERSION
from anonimizador_app.pipeline import write_lote_guides
from anonimizador_app.version import APP_VERSION, code_fingerprint, git_head, version_info

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_app_version_matches_pyproject(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], APP_VERSION)

    def test_line_names_extractor_detection_and_code(self) -> None:
        line = version_info().line()
        self.assertIn(f"v{APP_VERSION}", line)
        self.assertIn(FAST_EXTRACTOR_VERSION, line)
        self.assertIn("detecção", line)
        self.assertIn(f"código {version_info().code}", line)

    def test_fingerprint_ignores_line_endings_and_follows_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
            crlf = code_fingerprint(root)
            (root / "a.py").write_bytes(b"x = 1\ny = 2\n")
            self.assertEqual(code_fingerprint(root), crlf)
            (root / "a.py").write_bytes(b"x = 1\ny = 3\n")
            self.assertNotEqual(code_fingerprint(root), crlf)

    def test_git_head_reads_branch_ref_and_detached_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = root / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/trabalho\n", encoding="utf-8")
            (git / "refs" / "heads" / "trabalho").write_text("0123456789abcdef\n", encoding="utf-8")
            self.assertEqual(git_head(root), ("0123456", "trabalho"))
            (git / "HEAD").write_text("fedcba9876543210\n", encoding="utf-8")
            self.assertEqual(git_head(root), ("fedcba9", ""))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(git_head(Path(tmp)), ("", ""))

    def test_each_lote_records_the_version_that_generated_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "exportaveis" / "lote_x"
            map_path = root / "privados" / "lote_x" / "mapa.json"
            write_lote_guides(job_id="lote_x", export_dir=export_dir, map_path=map_path, markdown_names=["peca.md"])
            write_audit_report(export_dir, [("peca.md", ["Extrator: pymupdf"])])
            audit = (export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            guide = (export_dir / "LEIA-ME.txt").read_text(encoding="utf-8")
            private = (map_path.parent / "LEIA-ME.txt").read_text(encoding="utf-8")
        line = version_info().line()
        self.assertIn(line, audit)
        self.assertIn(f"Gerado por: {line}", guide)
        self.assertIn(f"Gerado por: {line}", private)


if __name__ == "__main__":
    unittest.main()
