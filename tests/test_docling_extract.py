from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.docling_extract import _convert


class _FakeConverter:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def convert(self, path: Path) -> str:
        self.seen.append(str(path))
        return "convertido"


class ConvertPathTests(unittest.TestCase):
    def test_ascii_path_goes_straight_to_docling(self) -> None:
        converter = _FakeConverter()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "processo.pdf"
            source.write_bytes(b"%PDF-1.4")
            result = _convert(converter, source)
        self.assertEqual(result, "convertido")
        self.assertEqual(converter.seen, [str(source)])

    def test_accented_path_is_converted_via_ascii_copy(self) -> None:
        converter = _FakeConverter()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ANONIMIZAÇÃO.pdf"
            source.write_bytes(b"%PDF-1.4")
            result = _convert(converter, source)
        self.assertEqual(result, "convertido")
        self.assertEqual(len(converter.seen), 1)
        self.assertTrue(converter.seen[0].isascii(), converter.seen[0])
        self.assertTrue(converter.seen[0].endswith(".pdf"))


if __name__ == "__main__":
    unittest.main()
