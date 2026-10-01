"""Portao no ponto da rede (I4): canal cadastrado, host oficial, variaveis que desviam o trafego.

Nada aqui abre rede: o cliente do SDK e criado (o que nao conecta) sob ``block_network``.
"""

from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

from anonimizador_app.credentials import ApiKey
from anonimizador_app.llm_discovery import LangExtractDiscoverer, LlmConfig, LlmConfigError
from anonimizador_app.llm_engine import EngineChoice, check_engine, confirmation_lines
from anonimizador_app.llm_gate import (
    CHANNEL_PROVIDERS,
    FilePlan,
    GateError,
    SendPlan,
    check_network_point,
    env_refusals,
    env_warnings,
    expected_host,
    verify_client_endpoint,
)
from anonimizador_app.netblock import block_network
from test_credentials import FAKE, FakeVault

HAS_SDK = importlib.util.find_spec("langextract") is not None and importlib.util.find_spec("google.genai") is not None
FAKE_URL = "https://desvio.example.invalid/segredo-na-url"


class HostTests(unittest.TestCase):
    def test_the_two_registered_channels_and_their_providers(self) -> None:
        self.assertEqual(CHANNEL_PROVIDERS, {"gemini-pago": "gemini", "vertex-sp": "vertex"})

    def test_expected_hosts(self) -> None:
        self.assertEqual(expected_host("gemini"), "generativelanguage.googleapis.com")
        self.assertEqual(expected_host("vertex", "southamerica-east1"), "southamerica-east1-aiplatform.googleapis.com")
        self.assertEqual(expected_host("vertex", "us-central1"), "us-central1-aiplatform.googleapis.com")
        self.assertEqual(expected_host("vertex", "global"), "aiplatform.googleapis.com")
        self.assertEqual(expected_host("vertex", "eu"), "aiplatform.eu.rep.googleapis.com")
        with self.assertRaises(GateError):
            expected_host("ollama")

    def test_network_point_accepts_the_registered_channels(self) -> None:
        gemini = check_network_point(channel="gemini-pago", provider="gemini", env={})
        self.assertEqual(gemini.host, "generativelanguage.googleapis.com")
        self.assertEqual(gemini.base_url, "https://generativelanguage.googleapis.com/")
        vertex = check_network_point(
            channel="vertex-sp", provider="vertex", location="southamerica-east1", project="projeto-ficticio", env={}
        )
        self.assertEqual(vertex.host, "southamerica-east1-aiplatform.googleapis.com")
        self.assertEqual(vertex.region, "southamerica-east1")
        self.assertIn("Regiao do Vertex AI usada na chamada: southamerica-east1", "\n".join(vertex.audit_lines()))

    def test_the_vertex_region_is_shown_and_not_forced(self) -> None:
        other = check_network_point(
            channel="vertex-sp", provider="vertex", location="us-central1", project="projeto-ficticio", env={}
        )
        self.assertEqual(other.region, "us-central1")
        self.assertEqual(other.host, "us-central1-aiplatform.googleapis.com")

    def test_channel_must_be_registered_and_match_its_provider(self) -> None:
        for channel in ("", "gemini-gratuito", "ollama-local", "outro", "gemini"):
            with self.assertRaises(GateError, msg=channel):
                check_network_point(channel=channel, provider="gemini", env={})
        with self.assertRaises(GateError):
            check_network_point(channel="gemini-pago", provider="vertex", location="us-central1", project="projeto-ficticio", env={})
        with self.assertRaises(GateError):
            check_network_point(channel="vertex-sp", provider="gemini", env={})

    def test_a_region_or_project_cannot_smuggle_another_host(self) -> None:
        for region in ("evil.com/x#", "a.b", "us-central1.evil.com", "", "southamerica east1", "x" * 3 + "/../"):
            with self.assertRaises(GateError, msg=region):
                check_network_point(
                    channel="vertex-sp", provider="vertex", location=region, project="projeto-ficticio", env={}
                )
        for project in ("", "a b", "x/../../y", "p?x=1"):
            with self.assertRaises(GateError, msg=project):
                check_network_point(
                    channel="vertex-sp", provider="vertex", location="us-central1", project=project, env={}
                )


class EnvironmentTests(unittest.TestCase):
    def test_base_url_overrides_are_refused_and_the_value_is_never_shown(self) -> None:
        for name in ("GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL"):
            refusals = env_refusals({name: FAKE_URL})
            self.assertEqual(len(refusals), 1, name)
            self.assertIn(name, refusals[0])
            self.assertNotIn("desvio", refusals[0])
            with self.assertRaises(GateError) as caught:
                check_network_point(channel="gemini-pago", provider="gemini", env={name: FAKE_URL})
            self.assertNotIn("segredo", str(caught.exception))
        self.assertEqual(env_refusals({"GOOGLE_GEMINI_BASE_URL": "   "}), [])
        self.assertEqual(env_refusals({}), [])

    def test_proxy_and_private_certificate_authority_only_warn(self) -> None:
        env = {"HTTPS_PROXY": "http://proxy.example.invalid:3128", "SSL_CERT_FILE": "ca.pem"}
        check = check_network_point(channel="gemini-pago", provider="gemini", env=env)
        text = "\n".join(check.warnings)
        self.assertIn("HTTPS_PROXY", text)
        self.assertIn("SSL_CERT_FILE", text)
        self.assertNotIn("proxy.example.invalid", text)
        self.assertEqual(env_warnings({}), [])
        self.assertIn("Aviso de rede", "\n".join(check.audit_lines()))

    def test_the_engine_gate_also_refuses_a_redirect_and_names_the_variable(self) -> None:
        decision = check_engine(
            EngineChoice(channel="gemini-pago"),
            env={"ANONIMIZADOR_GEMINI_PAGO_CHAVE": FAKE, "GOOGLE_VERTEX_BASE_URL": FAKE_URL},
            vault=FakeVault(),
            missing_fn=lambda _c: [],
        )
        self.assertFalse(decision.allowed)
        self.assertIn("GOOGLE_VERTEX_BASE_URL", decision.blocked_message())
        self.assertNotIn("segredo", decision.blocked_message())

    def test_the_confirmation_lists_proxy_warnings(self) -> None:
        plan = SendPlan(files=(FilePlan("a.pdf", 1, 100, 1, 0, 100),))
        decision = check_engine(
            EngineChoice(channel="gemini-pago"),
            env={"ANONIMIZADOR_GEMINI_PAGO_CHAVE": FAKE},
            vault=FakeVault(),
            missing_fn=lambda _c: [],
        )
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.example.invalid:3128"}):
            text = "\n".join(confirmation_lines(decision, plan))
        self.assertIn("proxy", text)


@unittest.skipUnless(HAS_SDK, "langextract/google-genai nao instalados")
class NetworkPointTests(unittest.TestCase):
    """O modelo real do SDK e criado (sem conectar) e o endereco e conferido no cliente."""

    def _discoverer(self, **config) -> LangExtractDiscoverer:
        base = dict(provider="gemini", channel="gemini-pago", api_key=ApiKey(FAKE, "teste"))
        base.update(config)
        return LangExtractDiscoverer(LlmConfig(**base))

    def test_gemini_client_points_at_the_official_host_and_the_gate_is_recorded(self) -> None:
        with block_network() as attempts:
            discoverer = self._discoverer()
            model = discoverer._build_model()
        self.assertEqual(attempts, [])
        self.assertEqual(model._client._api_client._http_options.base_url, "https://generativelanguage.googleapis.com/")
        self.assertEqual(discoverer.network_check.host, "generativelanguage.googleapis.com")

    def test_vertex_client_points_at_the_regional_host(self) -> None:
        with block_network() as attempts:
            discoverer = self._discoverer(
                provider="vertex", channel="vertex-sp", api_key=None, project="projeto-ficticio", location="southamerica-east1"
            )
            model = discoverer._build_model()
        self.assertEqual(attempts, [])
        self.assertEqual(
            model._client._api_client._http_options.base_url, "https://southamerica-east1-aiplatform.googleapis.com/"
        )
        self.assertEqual(discoverer.network_check.region, "southamerica-east1")

    def test_a_base_url_variable_blocks_the_model_before_anything_is_created(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_GEMINI_BASE_URL": FAKE_URL}):
            with block_network() as attempts:
                with self.assertRaises(LlmConfigError) as caught:
                    self._discoverer()._build_model()
        self.assertEqual(attempts, [])
        self.assertIn("GOOGLE_GEMINI_BASE_URL", str(caught.exception))
        self.assertNotIn("segredo", str(caught.exception))

    def test_an_unregistered_channel_never_builds_a_model(self) -> None:
        for channel in ("ollama-local", "gemini-gratuito", "x"):
            with self.assertRaises(LlmConfigError, msg=channel):
                self._discoverer(channel=channel)._build_model()

    def test_the_client_endpoint_is_verified_after_creation(self) -> None:
        check = check_network_point(channel="gemini-pago", provider="gemini", env={})

        class FakeOptions:
            base_url = "https://desvio.example.invalid/"

        class FakeApi:
            _http_options = FakeOptions()

        class FakeClient:
            _api_client = FakeApi()

        class FakeModel:
            _client = FakeClient()

        with self.assertRaises(GateError) as caught:
            verify_client_endpoint(FakeModel(), check)
        self.assertIn("desvio.example.invalid", str(caught.exception))
        FakeOptions.base_url = "http://generativelanguage.googleapis.com/"  # sem https
        with self.assertRaises(GateError):
            verify_client_endpoint(FakeModel(), check)
        FakeOptions.base_url = "https://generativelanguage.googleapis.com/"
        verify_client_endpoint(FakeModel(), check)  # o esperado passa
        with self.assertRaises(GateError):
            verify_client_endpoint(object(), check)  # nao consegue confirmar: recusa


if __name__ == "__main__":
    unittest.main()
