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
    def test_gemini_without_key_is_refused_even_with_google_variables(self) -> None:
        env = {"GEMINI_API_KEY": "chave-ficticia", "GOOGLE_API_KEY": "chave-ficticia"}
        with patch.dict(os.environ, env):
            discoverer = LangExtractDiscoverer(LlmConfig(provider="gemini", channel="gemini-pago"))
            with self.assertRaises(LlmConfigError) as caught:
                discoverer._build_model()
        self.assertIn("Sem chave", str(caught.exception))

    @unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
    def test_vertex_requires_project(self) -> None:
        discoverer = LangExtractDiscoverer(LlmConfig(provider="vertex", channel="vertex-sp"))
        with self.assertRaises(LlmConfigError):
            discoverer._build_model()

    @unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
    def test_ollama_provider_exists_only_behind_the_local_channel(self) -> None:
        # H9-b (era "nao existe provedor Ollama", do I7): agora so existe pelo canal local, e so em loopback.
        with self.assertRaises(LlmConfigError):
            LangExtractDiscoverer(LlmConfig(provider="ollama", channel=""))._build_model()
        for channel in ("gemini-pago", "vertex-sp", "outro"):
            with self.assertRaises(LlmConfigError, msg=channel):
                LangExtractDiscoverer(LlmConfig(provider="ollama", channel=channel))._build_model()
        model = LangExtractDiscoverer(LlmConfig(provider="ollama", channel="ollama-local"))._build_model()
        self.assertEqual(model._model_url, "http://127.0.0.1:11434")
        # Gemini/vertex com o canal do Ollama continua recusado.
        with self.assertRaises(LlmConfigError):
            LangExtractDiscoverer(LlmConfig(provider="gemini", channel="ollama-local"))._build_model()

    def test_the_key_is_passed_explicitly_and_the_module_reads_no_environment(self) -> None:
        import inspect

        from anonimizador_app import llm_discovery

        source = inspect.getsource(llm_discovery)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)
        self.assertNotIn("API_KEY", source.replace("api_key", ""))

    def test_describe_names_the_region(self) -> None:
        text = LlmConfig(provider="vertex", project="p").describe()
        self.assertIn("southamerica-east1", text)

    def test_describe_with_channel_and_no_key_in_repr(self) -> None:
        from anonimizador_app.credentials import ApiKey

        config = LlmConfig(channel="gemini-pago", api_key=ApiKey("chave-ficticia-1234", "teste"))
        self.assertEqual(config.describe(), f"{config.model_id} via gemini-pago")
        self.assertNotIn("chave-ficticia-1234", repr(config))


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

    def test_empty_answer_is_reported_and_never_cached(self) -> None:
        text = _paged(
            "EXECUTADO: ACME COMÉRCIO DE GRÃOS LTDA\nSócio: JOÃO DA SILVA SAURO",
            "Valor R$ 10.000,00 em 01/02/2024, apurado conforme a CDA anexa, com juros e encargos "
            "legais e multa de mora sobre o principal inscrito. Nada mais consta nesta folha.",
            "O sócio João da Silva Sauro reside na Rua das Palmeiras, 45.",
        )
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
            cached_names = {path.name for path in Path(tmp).glob("*.json")}

        self.assertEqual(len(first.report.empty), 1)
        self.assertIn("Nada mais", text[first.report.empty[0][0] : first.report.empty[0][1]])
        # A pagina sem entidade e reenviada na segunda rodada (a vazia nao esta no cache) e volta como vazia.
        self.assertEqual(len(calls), sent + 1)
        self.assertEqual(len(second.report.empty), 1)
        self.assertEqual(second.report.cached, second.report.chunks - 1)
        self.assertEqual(len(cached_names), first.report.chunks - 1)

    def test_old_cache_with_an_empty_answer_is_not_served(self) -> None:
        text = _paged("Texto da pagina sem nenhum nome proprio aqui.")
        with tempfile.TemporaryDirectory() as tmp:
            discoverer = LangExtractDiscoverer(LlmConfig(chunk_chars=300), cache_dir=Path(tmp))
            chunk = split_chunks(text, 300)[0]
            path = discoverer._cache_path(chunk)
            path.write_text(json.dumps({"formato": 1, "extracoes": []}), encoding="utf-8")
            self.assertIsNone(discoverer._cache_load(chunk))
            path.unlink()
            discoverer._cache_save(chunk, [])
            self.assertFalse(path.exists())

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



@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class SecretsTests(unittest.TestCase):
    """L2: a chave nunca em repr do modelo do LangExtract; excecao crua nunca na AUDITORIA."""

    @unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
    def test_the_real_model_hides_the_key_in_repr_str_and_fields(self) -> None:
        from anonimizador_app.credentials import ApiKey
        from anonimizador_app.netblock import block_network

        key = "chave-ficticia-repr-8c1d77aa"
        with block_network() as attempts:
            discoverer = LangExtractDiscoverer(
                LlmConfig(provider="gemini", channel="gemini-pago", api_key=ApiKey(key, "teste"))
            )
            model = discoverer._build_model()
        self.assertEqual(attempts, [])
        shown = " ".join((repr(model), str(model), f"{model!r}", f"{model}", repr(vars(model)), repr(discoverer), repr(discoverer.config)))
        self.assertNotIn(key, shown)
        self.assertIn("credenciais ocultas", repr(model))
        self.assertEqual(model.api_key, "<oculta>")
        # O cliente do SDK ainda tem a chave: o modelo continua funcional.
        self.assertEqual(model._client._api_client.api_key, key)

    @unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
    def test_the_key_does_not_stay_in_the_provider_kwargs_after_the_model_exists(self) -> None:
        from unittest.mock import patch as mock_patch

        from anonimizador_app.credentials import ApiKey
        from anonimizador_app.netblock import block_network

        key = "chave-ficticia-kwargs-55ab"
        seen = {}
        import langextract as lx

        original = lx.factory.ModelConfig

        def spy(*args, **kwargs):
            config = original(*args, **kwargs)
            seen["config"] = config
            return config

        with mock_patch.object(lx.factory, "ModelConfig", spy), block_network():
            LangExtractDiscoverer(
                LlmConfig(provider="gemini", channel="gemini-pago", api_key=ApiKey(key, "teste"))
            )._build_model()
        self.assertNotIn("api_key", seen["config"].provider_kwargs)

    def test_a_provider_exception_reaches_the_report_as_type_and_code_only(self) -> None:
        class ApiError(Exception):
            def __init__(self) -> None:
                super().__init__("401 chave=chave-ficticia-xyz trecho: MARIA FICTICIA DA SILVA")
                self.code, self.status = 401, "UNAUTHENTICATED"

        from langextract.core import base_model

        class Exploding(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **kwargs):
                raise ApiError()
                yield  # pragma: no cover

        with tempfile.TemporaryDirectory() as tmp:
            discoverer = LangExtractDiscoverer(
                LlmConfig(chunk_chars=200, max_workers=1, retries=0),
                cache_dir=Path(tmp),
                model_factory=lambda: Exploding(),
            )
            found = discoverer.discover(_paged("O sócio MARIA FICTICIA DA SILVA reside na Rua das Palmeiras, 45."))
            cached = list(Path(tmp).glob("*.json"))
        self.assertEqual(found.report.failure_reasons, ["ApiError HTTP 401 UNAUTHENTICATED"])
        self.assertNotIn("chave-ficticia-xyz", " ".join(found.report.failure_reasons))
        self.assertNotIn("FICTICIA", " ".join(found.report.failure_reasons))
        self.assertEqual(cached, [])
        self.assertEqual(len(found.report.failed), 1)

    def test_the_warning_log_has_no_secret(self) -> None:
        import logging

        class ApiError(Exception):
            pass

        from langextract.core import base_model

        class Exploding(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **kwargs):
                raise ApiError("chave AIza" + "Q" * 30 + " texto JOAO DE TAL")
                yield  # pragma: no cover

        discoverer = LangExtractDiscoverer(
            LlmConfig(chunk_chars=200, max_workers=1, retries=0), model_factory=lambda: Exploding()
        )
        with self.assertLogs("anonimizador_app.llm_discovery", level="WARNING") as captured:
            discoverer.discover(_paged("O sócio JOAO DE TAL consta no processo e foi citado."))
        log = "\n".join(captured.output)
        self.assertNotIn("AIza", log)
        self.assertNotIn("JOAO", log)
        self.assertIn("ApiError", log)
        del logging



@unittest.skipUnless(
    HAS_LANGEXTRACT and importlib.util.find_spec("cryptography") is not None,
    "langextract/cryptography nao instalados",
)
class VertexServiceAccountTests(unittest.TestCase):
    """O Vertex AI com arquivo de conta de servico: a credencial e criada na hora de montar o cliente."""

    def _account_file(self, folder: Path) -> Path:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        path = folder / "conta.json"
        path.write_text(
            json.dumps(
                {
                    "type": "service_account",
                    "client_email": "conta@projeto-ficticio.iam.example",
                    "private_key": pem,
                    "private_key_id": "1",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "project_id": "projeto-ficticio",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_the_client_uses_the_service_account_and_the_private_key_never_shows(self) -> None:
        from anonimizador_app.netblock import block_network

        with tempfile.TemporaryDirectory() as tmp:
            account = self._account_file(Path(tmp))
            with block_network() as attempts:
                discoverer = LangExtractDiscoverer(
                    LlmConfig(
                        provider="vertex", channel="vertex-sp", project="projeto-ficticio",
                        location="southamerica-east1", credentials_path=str(account),
                    )
                )
                model = discoverer._build_model()
            shown = repr(model) + repr(vars(model)) + repr(discoverer.config)
        self.assertEqual(attempts, [])
        self.assertEqual(type(model._client._api_client._credentials).__name__, "Credentials")
        self.assertNotIn("PRIVATE KEY", shown)
        self.assertEqual(discoverer.network_check.host, "southamerica-east1-aiplatform.googleapis.com")

    def test_an_unreadable_credential_file_is_refused_without_quoting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "ruim.json"
            broken.write_text('{"type": "service_account", "private_key": "SEGREDO-NO-ARQUIVO"}', encoding="utf-8")
            discoverer = LangExtractDiscoverer(
                LlmConfig(
                    provider="vertex", channel="vertex-sp", project="projeto-ficticio",
                    location="southamerica-east1", credentials_path=str(broken),
                )
            )
            with self.assertRaises(LlmConfigError) as caught:
                discoverer._build_model()
        self.assertNotIn("SEGREDO-NO-ARQUIVO", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
