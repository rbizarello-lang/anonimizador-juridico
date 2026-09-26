from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anonimizador_app.llm_discovery import (
    LangExtractDiscoverer,
    LlmConfig,
    LlmConfigError,
    page_of,
    split_chunks,
)

HAS_LANGEXTRACT = importlib.util.find_spec("langextract") is not None


def _paged(*pages: str) -> str:
    return "".join(
        f"<!-- pagina-origem: {number} -->\n{body}\n\n"
        for number, body in enumerate(pages, start=1)
    )


class SplitChunksTests(unittest.TestCase):
    def test_chunks_follow_page_anchors_and_keep_offsets(self) -> None:
        text = _paged("A" * 150, "B" * 150, "C" * 150)
        chunks = split_chunks(text, 400)

        self.assertEqual([chunk.text for chunk in chunks], [text[c.start : c.end] for c in chunks])
        self.assertEqual(chunks[0].start, 0)
        self.assertEqual(chunks[-1].end, len(text))
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 400)
            # Cada trecho comeca numa ancora de pagina: fronteira estavel para o cache.
            self.assertTrue(chunk.text.startswith("<!-- pagina-origem:"))

    def test_oversized_page_is_cut_at_paragraph(self) -> None:
        body = "\n\n".join(f"Paragrafo {index} " + "x" * 80 for index in range(20))
        text = _paged(body)
        chunks = split_chunks(text, 500)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunk.text for chunk in chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 500)

    def test_chunk_without_letters_is_skipped(self) -> None:
        text = _paged("12345 67890", "Texto com letras")
        chunks = split_chunks(text, 200)

        self.assertEqual(len(chunks), 1)
        self.assertIn("Texto com letras", chunks[0].text)

    def test_page_of_position(self) -> None:
        text = _paged("primeira", "segunda", "terceira")
        self.assertEqual(page_of(text, text.index("segunda")), 2)
        self.assertEqual(page_of(text, text.index("terceira")), 3)
        # Trecho comeca na propria ancora: ja e a pagina nova.
        self.assertEqual(page_of(text, text.index("<!-- pagina-origem: 2")), 2)
        self.assertEqual(page_of("sem ancora", 3), 0)


class ConfigTests(unittest.TestCase):
    @unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
    def test_gemini_without_key_is_refused(self) -> None:
        clean = {key: value for key, value in os.environ.items() if "API_KEY" not in key}
        with patch.dict(os.environ, clean, clear=True):
            discoverer = LangExtractDiscoverer(LlmConfig(provider="gemini"))
            with self.assertRaises(LlmConfigError):
                discoverer._build_model()

    @unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
    def test_vertex_requires_project(self) -> None:
        discoverer = LangExtractDiscoverer(LlmConfig(provider="vertex"))
        with self.assertRaises(LlmConfigError):
            discoverer._build_model()

    def test_describe_names_the_region(self) -> None:
        text = LlmConfig(provider="vertex", project="p").describe()
        self.assertIn("southamerica-east1", text)


@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class LangExtractAdapterTests(unittest.TestCase):
    """Roda o LangExtract de verdade com um modelo falso (sem rede)."""

    NAMES = {
        "JOÃO DA SILVA SAURO": ("pessoa", "socio"),
        "ACME COMÉRCIO DE GRÃOS LTDA": ("empresa", "parte"),
        "Rua das Palmeiras, 45": ("endereco", "socio"),
    }

    def _fake_model(self, *, fail_marker: str | None = None, calls: list | None = None):
        from langextract.core import base_model
        from langextract.core import types as lx_types

        names = self.NAMES

        class FakeModel(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **kwargs):
                for prompt in batch_prompts:
                    if calls is not None:
                        calls.append(prompt)
                    chunk = prompt.rsplit("Q: ", 1)[-1]
                    if fail_marker and fail_marker in chunk:
                        yield [lx_types.ScoredOutput(score=1.0, output="{truncado")]
                        continue
                    items = []
                    for value, (kind, role) in names.items():
                        if value.casefold() in chunk.casefold():
                            items.append(
                                {
                                    kind: value,
                                    f"{kind}_attributes": {"papel": role, "nome_canonico": value},
                                }
                            )
                    yield [lx_types.ScoredOutput(score=1.0, output=json.dumps({"extractions": items}))]

        return FakeModel()

    def _text(self) -> str:
        return _paged(
            "EXECUTADO: ACME COMÉRCIO DE GRÃOS LTDA\nSócio: JOÃO DA SILVA SAURO",
            "Valor R$ 10.000,00 em 01/02/2024. Nada mais.",
            "O sócio João da Silva Sauro reside na Rua das Palmeiras, 45.",
        )

    def test_offsets_are_absolute_in_the_full_text(self) -> None:
        text = self._text()
        discoverer = LangExtractDiscoverer(
            LlmConfig(chunk_chars=200, max_workers=4),
            model_factory=lambda: self._fake_model(),
        )
        found = discoverer.discover(text)

        self.assertEqual(found.report.failed, [])
        self.assertGreaterEqual(found.report.chunks, 2)
        anchored = [mention for mention in found.mentions if mention.anchored]
        self.assertTrue(anchored)
        for mention in anchored:
            self.assertEqual(
                text[mention.start : mention.end].casefold(), mention.text.casefold()
            )
            self.assertEqual(mention.source_text, text[mention.start : mention.end])
        roles = {mention.text: mention.role for mention in found.mentions}
        self.assertEqual(roles["ACME COMÉRCIO DE GRÃOS LTDA"], "parte")
        self.assertEqual(roles["Rua das Palmeiras, 45"], "socio")

    def test_second_run_reads_the_cache(self) -> None:
        text = self._text()
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            discoverer = LangExtractDiscoverer(
                LlmConfig(chunk_chars=200, max_workers=2),
                cache_dir=Path(tmp),
                model_factory=lambda: self._fake_model(calls=calls),
            )
            first = discoverer.discover(text)
            sent = len(calls)
            second = discoverer.discover(text)

        self.assertGreater(sent, 0)
        self.assertEqual(len(calls), sent)
        self.assertEqual(second.report.cached, second.report.chunks)
        self.assertEqual(
            sorted(mention.text for mention in first.mentions),
            sorted(mention.text for mention in second.mentions),
        )

    def test_cache_survives_token_renumbering(self) -> None:
        first_text = "EXECUTADO: ACME COMÉRCIO DE GRÃOS LTDA ([CNPJ_1])\nSócio: JOÃO DA SILVA SAURO ([CPF_1])"
        renumbered = first_text.replace("[CNPJ_1]", "[CNPJ_12]").replace("[CPF_1]", "[CPF_7]")
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            discoverer = LangExtractDiscoverer(
                LlmConfig(chunk_chars=400, max_workers=1),
                cache_dir=Path(tmp),
                model_factory=lambda: self._fake_model(calls=calls),
            )
            discoverer.discover(first_text)
            again = discoverer.discover(renumbered)

        self.assertEqual(len(calls), 1)
        self.assertEqual(again.report.cached, 1)
        for mention in again.mentions:
            self.assertTrue(mention.anchored)
            self.assertEqual(renumbered[mention.start : mention.end], mention.source_text)
            self.assertEqual(mention.source_text.casefold(), mention.text.casefold())

    def test_broken_chunk_is_reported_and_does_not_abort_the_rest(self) -> None:
        text = self._text()
        with tempfile.TemporaryDirectory() as tmp:
            discoverer = LangExtractDiscoverer(
                LlmConfig(chunk_chars=200, max_workers=4, retries=1),
                cache_dir=Path(tmp),
                model_factory=lambda: self._fake_model(fail_marker="Rua das Palmeiras"),
            )
            found = discoverer.discover(text)
            cached_files = list(Path(tmp).glob("*.json"))

        self.assertEqual(len(found.report.failed), 1)
        start, end = found.report.failed[0]
        self.assertIn("Rua das Palmeiras", text[start:end])
        self.assertIn("resposta fora do formato JSON", found.report.failure_reasons)
        # O resto do documento foi lido; o trecho quebrado nao entra no cache.
        self.assertIn("ACME COMÉRCIO DE GRÃOS LTDA", {m.text for m in found.mentions})
        self.assertEqual(len(cached_files), found.report.chunks - 1)

    def test_examples_align_with_their_own_text(self) -> None:
        from langextract import prompt_validation, resolver

        from anonimizador_app.llm_discovery import build_examples

        report = prompt_validation.validate_prompt_alignment(
            examples=build_examples(),
            aligner=resolver.WordAligner(),
            policy=prompt_validation.AlignmentPolicy(),
        )
        self.assertEqual(report.issues, [])

    def test_examples_do_not_extract_protected_terms(self) -> None:
        from anonimizador_app.llm_discovery import _example_specs

        banned = re.compile(r"(?i)\b(?:uni[aã]o|fazenda nacional|sisbajud|tema|r\$)\b")
        for _text, items in _example_specs():
            for _kind, value, _role, _canonical in items:
                self.assertIsNone(banned.search(value), value)


if __name__ == "__main__":
    unittest.main()
