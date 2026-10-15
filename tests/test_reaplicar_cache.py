"""tools/reaplicar_cache.py: posicoes do texto cru para o mascarado, e a chamada da CLI sem rede."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import reaplicar_cache  # noqa: E402
from anonimizador_app.pipeline_llm import OffsetMap  # noqa: E402

# Original: "Socio JOAO SILVA, CPF 529.982.247-25, mora na Rua X."  (CPF de 14 caracteres nas posicoes 22..36)
# Mascarado: "Socio JOAO SILVA, CPF [CPF_1], mora na Rua X."       (token de 7 caracteres nas posicoes 22..29)
ORIGINAL = "Socio JOAO SILVA, CPF 529.982.247-25, mora na Rua X."
MASKED = "Socio JOAO SILVA, CPF [CPF_1], mora na Rua X."
OFFSETS = OffsetMap(new_starts=(22,), new_ends=(29,), orig_ends=(36,))


class PositionTests(unittest.TestCase):
    def test_before_the_token_nothing_moves(self) -> None:
        start, end = ORIGINAL.index("JOAO"), ORIGINAL.index("JOAO") + len("JOAO SILVA")
        self.assertEqual(reaplicar_cache.original_to_masked(OFFSETS, start, end), (start, end))
        self.assertEqual(MASKED[start:end], "JOAO SILVA")

    def test_after_the_token_the_shift_is_applied(self) -> None:
        start = ORIGINAL.index("Rua X")
        end = start + len("Rua X")
        moved = reaplicar_cache.original_to_masked(OFFSETS, start, end)
        self.assertIsNotNone(moved)
        assert moved is not None
        self.assertEqual(MASKED[moved[0] : moved[1]], "Rua X")
        # E o inverso do app: voltando pelo OffsetMap chega-se a posicao original.
        self.assertEqual(OFFSETS.to_original(*moved), (start, end))

    def test_a_mention_touching_the_masked_id_is_refused(self) -> None:
        start = ORIGINAL.index("529")
        self.assertIsNone(reaplicar_cache.original_to_masked(OFFSETS, start, start + 7))
        self.assertIsNone(reaplicar_cache.original_to_masked(OFFSETS, start - 4, start + 3))

    def test_clamp_never_refuses(self) -> None:
        first, last = reaplicar_cache.clamp_to_masked(OFFSETS, 0, len(ORIGINAL))
        self.assertEqual((first, last), (0, len(MASKED)))


class CliTests(unittest.TestCase):
    def test_the_forwarded_arguments_force_the_local_channel_without_network(self) -> None:
        args = argparse.Namespace(
            arquivos=["a.pdf", "b.pdf"], modelo="gemma4:e4b", privados="P", exportaveis="", config="",
            trecho=None, sem_piso_local=False,
        )
        forwarded = reaplicar_cache.cli_arguments(args)
        self.assertEqual(forwarded[:2], ["a.pdf", "b.pdf"])
        self.assertIn("--sem-rede", forwarded)
        self.assertEqual(forwarded[forwarded.index("--canal") + 1], "ollama-local")
        self.assertEqual(forwarded[forwarded.index("--modelo") + 1], "gemma4:e4b")
        self.assertEqual(forwarded[forwarded.index("--verificacao") + 1], "0")
        self.assertEqual(forwarded[forwarded.index("--privados") + 1], "P")
        self.assertNotIn("--exportaveis", forwarded)
        self.assertNotIn("--confirmo-envio", forwarded)

    def test_a_missing_raw_cache_is_refused_before_anything_runs(self) -> None:
        code = reaplicar_cache.main(["x.pdf", "--modelo", "qwen3:4b", "--cache", str(ROOT / "nao-existe")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
