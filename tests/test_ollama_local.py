"""Canal LLM local (Ollama em loopback, H8), contra um servidor Ollama FALSO em 127.0.0.1.

Nenhum teste abre rede real: o unico socket e o do servidor falso, no proprio loopback.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anonimizador_app.llm_discovery import LangExtractDiscoverer, LlmConfig, LlmConfigError
from anonimizador_app.llm_engine import EngineChoice, build_discoverer, check_engine
from fake_ollama import FakeOllama

HAS_LANGEXTRACT = importlib.util.find_spec("langextract") is not None

TEXT = (
    "<!-- pagina-origem: 1 -->\nEXECUTADO: ACME COMÉRCIO DE GRÃOS LTDA\nSócio: JOÃO DA SILVA SAURO\n\n"
    "<!-- pagina-origem: 2 -->\nO sócio João da Silva Sauro reside na Rua das Palmeiras, 45.\n"
)


def local_config(**changes) -> LlmConfig:
    values = dict(provider="ollama", channel="ollama-local", model_id="qwen3:4b", chunk_chars=2000, max_workers=1)
    values.update(changes)
    return LlmConfig(**values)


@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class OllamaProviderTests(unittest.TestCase):
    def test_discoverer_extracts_from_the_fake_server_without_a_permit(self) -> None:
        with FakeOllama() as server:
            discoverer = LangExtractDiscoverer(local_config(chunk_chars=200), loopback_port=server.port)
            self.assertIsNone(discoverer.permit)
            found = discoverer.discover(TEXT)
            requests = list(server.requests)
        self.assertEqual(found.report.failed, [])
        texts = {mention.text for mention in found.mentions}
        self.assertEqual(texts, {"ACME COMÉRCIO DE GRÃOS LTDA", "JOÃO DA SILVA SAURO", "João da Silva Sauro",
                                 "Rua das Palmeiras, 45"})
        for mention in found.mentions:
            self.assertTrue(mention.anchored, mention)
            self.assertEqual(TEXT[mention.start : mention.end], mention.source_text)
        roles = {mention.text: mention.role for mention in found.mentions}
        self.assertEqual(roles["ACME COMÉRCIO DE GRÃOS LTDA"], "parte")
        self.assertGreaterEqual(len(requests), 1)
        self.assertEqual(discoverer.network_check.host, "127.0.0.1")
        self.assertIn("nada enviado", "\n".join(discoverer.network_check.audit_lines()))
        self.assertEqual(
            discoverer.usage_lines(), [f"LLM local: {len(requests)} chamada(s), 0 com erro."]
        )

    def test_each_request_carries_the_local_settings_and_no_credentials(self) -> None:
        with FakeOllama() as server:
            discoverer = LangExtractDiscoverer(local_config(), loopback_port=server.port)
            discoverer.discover(TEXT)
            requests = list(server.requests)
        self.assertTrue(requests)
        for request in requests:
            body = request["body"]
            self.assertEqual(request["path"], "/api/generate")
            self.assertEqual(body["model"], "qwen3:4b")
            self.assertIs(body["think"], False)
            self.assertEqual(body["format"], "json")
            self.assertIs(body["stream"], False)
            self.assertEqual(body["options"]["num_ctx"], 4096)
            self.assertEqual(body["options"]["temperature"], 0.0)
            self.assertEqual(body["options"]["num_predict"], 8192)
            # keep_alive curto (2 min), em segundos, nas duas posicoes que o provedor usa.
            self.assertEqual(body["keep_alive"], 120)
            self.assertEqual(body["options"]["keep_alive"], 120)
            self.assertNotIn("max_workers", body["options"])
            headers = {name.lower() for name in request["headers"]}
            self.assertNotIn("authorization", headers)

    def test_second_run_reads_the_cache_and_calls_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeOllama() as server:
            discoverer = LangExtractDiscoverer(local_config(), cache_dir=Path(tmp), loopback_port=server.port)
            discoverer.discover(TEXT)
            sent = len(server.requests)
            again = LangExtractDiscoverer(local_config(), cache_dir=Path(tmp), loopback_port=server.port)
            second = again.discover(TEXT)
        self.assertGreater(sent, 0)
        self.assertEqual(len(server.requests), sent)
        self.assertEqual(second.report.cached, second.report.chunks)
        self.assertIn("nenhuma chamada", again.usage_lines()[0])

    def test_server_errors_become_reported_failures_and_are_counted(self) -> None:
        with FakeOllama(status=500) as server:
            discoverer = LangExtractDiscoverer(local_config(retries=0), loopback_port=server.port)
            found = discoverer.discover(TEXT)
        self.assertTrue(found.report.failed)
        self.assertEqual(found.mentions, [])
        self.assertIn("com erro", discoverer.usage_lines()[0])
        self.assertNotIn("0 com erro", discoverer.usage_lines()[0])

    def test_environment_cannot_move_the_endpoint(self) -> None:
        """O LangExtract leria OLLAMA_BASE_URL sozinho; aqui o endereco vai fixo e a variavel e recusada."""

        with FakeOllama() as server:
            with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://10.0.0.5:11434"}):
                discoverer = LangExtractDiscoverer(local_config(), loopback_port=server.port)
                with self.assertRaises(LlmConfigError) as caught:
                    discoverer._build_model()
            self.assertNotIn("10.0.0.5", str(caught.exception))
            self.assertEqual(server.requests, [])
            with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://127.0.0.1:11434", "OLLAMA_HOST": ""}):
                # Loopback na porta padrao e aceito pelo portao, mas o modelo vai para a porta do teste.
                model = LangExtractDiscoverer(local_config(), loopback_port=server.port)._build_model()
            self.assertEqual(model._model_url, f"http://127.0.0.1:{server.port}")

    def test_model_url_is_checked_after_creation(self) -> None:
        from anonimizador_app.llm_discovery import verify_ollama_model
        from anonimizador_app.llm_gate import GateError, check_network_point

        check = check_network_point(channel="ollama-local", provider="ollama", env={})

        class Model:
            _api_key = None
            _requests = object()

            def __init__(self, url) -> None:
                self._model_url = url

        for bad in ("http://10.0.0.5:11434", "https://127.0.0.1:11434", "http://127.0.0.1:11435",
                    "http://127.0.0.1:11434/x", None):
            with self.assertRaises(GateError, msg=bad):
                verify_ollama_model(Model(bad), check)
        keyed = Model(check.base_url)
        keyed._api_key = "segredo-ficticio"
        with self.assertRaises(GateError):
            verify_ollama_model(keyed, check)
        ok = Model(check.base_url + "/")
        verify_ollama_model(ok, check)
        self.assertTrue(hasattr(ok._requests, "post"))

    def test_http_client_ignores_proxy_and_refuses_other_addresses(self) -> None:
        from anonimizador_app.llm_discovery import _LoopbackRequests

        client = _LoopbackRequests("http://127.0.0.1:11434")
        with self.assertRaises(Exception) as caught:
            client.post("http://10.0.0.5:11434/api/generate", json={})
        self.assertIn("recusado", str(caught.exception))
        with FakeOllama() as server:
            proxied = {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                       "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9"}
            with patch.dict(os.environ, proxied):
                reply = _LoopbackRequests(f"http://127.0.0.1:{server.port}").post(
                    f"http://127.0.0.1:{server.port}/api/generate", json={"prompt": "Q: x"}, timeout=5
                )
        self.assertEqual(reply.status_code, 200)

    def test_cloud_model_never_builds(self) -> None:
        discoverer = LangExtractDiscoverer(local_config(model_id="gpt-oss:120b-cloud"))
        with self.assertRaises(LlmConfigError):
            discoverer._build_model()

    def test_gemini_and_vertex_still_demand_the_permit(self) -> None:
        for provider, channel in (("gemini", "gemini-pago"), ("vertex", "vertex-sp")):
            discoverer = LangExtractDiscoverer(LlmConfig(provider=provider, channel=channel))
            with self.assertRaises(LlmConfigError) as caught:
                discoverer.discover("<!-- pagina-origem: 1 -->\nEXECUTADO: ACME COMERCIO LTDA, de Sao Paulo.\n")
            self.assertIn("confirmacao desta rodada", str(caught.exception), provider)

    def test_engine_builds_a_working_discoverer(self) -> None:
        decision = check_engine(EngineChoice(channel="ollama-local"), env={}, missing_fn=lambda _c: [])
        self.assertTrue(decision.allowed, decision.reasons)
        discoverer = build_discoverer(decision, cache_dir=None)
        self.assertEqual(discoverer.config.num_ctx, 4096)
        self.assertEqual(discoverer.config.chunk_chars, 2000)


if __name__ == "__main__":
    unittest.main()
