from __future__ import annotations

import pickle
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from anonimizador_app.branding import (
    ico_image_count,
    open_native_window,
    write_ico,
)


class BrandingTests(unittest.TestCase):
    def test_ico_contains_seven_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            png = folder / "mark.png"
            Image.new("RGBA", (64, 64), (27, 54, 93, 255)).save(png)
            ico = folder / "mark.ico"
            write_ico(png, ico)
            self.assertTrue(ico.is_file())
            self.assertEqual(ico_image_count(ico), 7)
            self.assertGreater(ico.stat().st_size, 1_000)

    def test_native_hook_is_pickleable(self) -> None:
        restored = pickle.loads(pickle.dumps(open_native_window, protocol=pickle.HIGHEST_PROTOCOL))
        self.assertEqual(restored.__name__, "open_native_window")
        self.assertEqual(restored.__module__, "anonimizador_app.branding")


if __name__ == "__main__":
    unittest.main()
