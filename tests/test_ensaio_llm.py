"""O ensaio sem rede (tools/ensaio_llm.py) tem de passar inteiro: e a prova de que o caminho funciona."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HAS_LANGEXTRACT = importlib.util.find_spec("langextract") is not None


@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class EnsaioTests(unittest.TestCase):
    def test_dry_run_passes_with_the_network_blocked(self) -> None:
        spec = importlib.util.spec_from_file_location("ensaio_llm_tool", ROOT / "tools" / "ensaio_llm.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tool.main([])
        report = out.getvalue()
        self.assertEqual(code, 0, report)
        self.assertIn("Rede: BLOQUEADA", report)
        self.assertNotIn("FALHOU", report)
        self.assertNotIn(tool.FAKE_KEY, report)
        self.assertNotIn(tool.OTHER_FAKE_KEY, report)


if __name__ == "__main__":
    unittest.main()
