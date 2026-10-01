"""O modelo de NER nunca vai a rede sozinho (endurecimento I2): offline por padrao e revisao fixada."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import anonimizador_app.local_ner as local_ner

SRC = Path(local_ner.__file__).resolve().parents[1]
ROOT = SRC.parent

_SPY = "\n".join(
    [
        "import sys, os",
        "class Spy:",
        "    seen = 'nunca'",
        "    def find_spec(self, name, path=None, target=None):",
        "        if name.split('.')[0] in ('huggingface_hub', 'transformers', 'gliner') and Spy.seen == 'nunca':",
        "            Spy.seen = os.environ.get('HF_HUB_OFFLINE', 'sem valor')",
        "sys.meta_path.insert(0, Spy())",
        "import anonimizador_app.local_ner",
        "print(Spy.seen, os.environ['HF_HUB_OFFLINE'], os.environ['TRANSFORMERS_OFFLINE'])",
    ]
)


def _child_env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("HF_", "TRANSFORMERS_"))}
    env["PYTHONPATH"] = str(SRC)
    env.update(extra)
    return env


def _run(code: str, **extra: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code], env=_child_env(**extra), capture_output=True, text=True, timeout=300
    )
    return result.stdout.strip()


class OfflineByDefaultTests(unittest.TestCase):
    def test_importing_the_engine_turns_the_hub_off_before_any_hub_library_loads(self) -> None:
        # Primeiro valor: o que a variavel valia quando huggingface_hub/transformers/gliner foram importados
        # pela primeira vez (nunca "sem valor"); depois, os dois valores ao fim do import.
        seen, hub, transformers = _run(_SPY).split()
        self.assertIn(seen, {"1", "nunca"})
        self.assertEqual((hub, transformers), ("1", "1"))

    def test_explicit_online_choice_is_respected(self) -> None:
        out = _run("import os, anonimizador_app.local_ner; print(os.environ['HF_HUB_OFFLINE'])", HF_HUB_OFFLINE="0")
        self.assertEqual(out, "0")


class PinnedRevisionTests(unittest.TestCase):
    def test_revisions_are_full_commit_hashes(self) -> None:
        for revision in (local_ner.GLINER_REVISION, local_ner.GLINER_TOKENIZER_REVISION):
            self.assertRegex(revision, re.compile(r"^[0-9a-f]{40}$"))

    def test_gliner_is_loaded_pinned_and_local_only(self) -> None:
        calls: list[tuple[tuple, dict]] = []

        class FakeGLiNER:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                calls.append((args, kwargs))
                return cls()

            def predict_entities(self, *_args, **_kwargs):
                return []

        fake = types.ModuleType("gliner")
        fake.GLiNER = FakeGLiNER
        with mock.patch.dict(sys.modules, {"gliner": fake}), mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            local_ner._load_gliner()
        self.assertEqual(calls[0][0], (local_ner.GLINER_MODEL,))
        self.assertEqual(calls[0][1]["revision"], local_ner.GLINER_REVISION)
        self.assertTrue(calls[0][1]["local_files_only"])

    def test_online_installation_step_is_the_only_one_that_asks_for_the_hub(self) -> None:
        with mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "0"}):
            self.assertFalse(local_ner.hub_offline())
        with mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            self.assertTrue(local_ner.hub_offline())
        script = (ROOT / "tools" / "instalar_modelo_ner.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["HF_HUB_OFFLINE"] = "0"', script)
        self.assertIn("--baixar", script)


class FallbackTests(unittest.TestCase):
    def test_missing_gliner_still_falls_back_to_spacy(self) -> None:
        marker = lambda text: []  # noqa: E731
        with (
            mock.patch.object(local_ner, "_load_gliner", side_effect=OSError("modelo ausente")),
            mock.patch.object(local_ner, "_load_spacy_pt", return_value=marker),
            mock.patch.object(local_ner, "_ACTIVE_BACKEND", ""),
        ):
            self.assertIs(local_ner.load_local_predict(), marker)
            self.assertEqual(local_ner._ACTIVE_BACKEND, "spacy_pt")


if __name__ == "__main__":
    unittest.main()
