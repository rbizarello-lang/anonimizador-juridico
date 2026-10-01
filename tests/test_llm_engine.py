"""Portao do motor LLM: so roda com todas as condicoes. Nada aqui abre rede."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.credentials import ENV_VAR, VAULT_SERVICE, VAULT_USER
from anonimizador_app.llm_discovery import LlmConfigError
from anonimizador_app.llm_engine import (
    BLOCKED_CHANNELS,
    CHANNELS,
    WARNING_VERSION,
    EngineChoice,
    adc_available,
    audit_lines,
    build_discoverer,
    build_offline_discoverer,
    check_engine,
    config_key_findings,
    missing_packages,
    warning_paragraphs,
    warning_text,
)
from anonimizador_app.netblock import block_network
from anonimizador_app.settings import AppSettings, CustomRule, save_settings
from anonimizador_app.version import ENGINE_LOCAL, VersionInfo, engine_label
from test_credentials import FAKE, FakeVault

GOOD = EngineChoice(channel="gemini-pago", acknowledged=True, billing_confirmed=True)


def _env(**extra: str) -> dict[str, str]:
    return {ENV_VAR: FAKE, **extra}


def _ready(**kwargs):
    kwargs.setdefault("env", _env())
    kwargs.setdefault("vault", FakeVault())
    kwargs.setdefault("missing_fn", lambda _channel: [])
    return check_engine(GOOD, **kwargs)


class ChannelTests(unittest.TestCase):
    def test_empty_free_and_unknown_channels_are_blocked(self) -> None:
        for channel in ("", "  ", "gemini-gratuito", "GEMINI-GRATUITO", "gemini", "openai", "qualquer"):
            choice = EngineChoice(channel=channel, acknowledged=True, billing_confirmed=True)
            decision = check_engine(
                choice, env=_env(), vault=FakeVault(), missing_fn=lambda _c: []
            )
            self.assertFalse(decision.allowed, channel)
            self.assertIsNone(decision.config, channel)
            self.assertIsNone(decision.key, channel)
            self.assertEqual(decision.engine_id, ENGINE_LOCAL, channel)
            self.assertTrue(decision.reasons, channel)

    def test_free_channel_says_why(self) -> None:
        decision = check_engine(EngineChoice(channel="gemini-gratuito"))
        self.assertIn("gratuito", decision.blocked_message())
        self.assertIn("gemini-gratuito", BLOCKED_CHANNELS)
        self.assertNotIn("gemini-gratuito", CHANNELS)

    def test_the_two_remote_channels_exist(self) -> None:
        self.assertEqual({"gemini-pago", "vertex-sp"} - set(CHANNELS), set())


class GateTests(unittest.TestCase):
    def test_everything_in_place_allows(self) -> None:
        decision = _ready()
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.engine_id, "gemini-pago")
        self.assertEqual(decision.config.channel, "gemini-pago")
        self.assertEqual(decision.config.provider, "gemini")
        self.assertEqual(decision.config.api_key.reveal(), FAKE)
        self.assertNotIn(FAKE, repr(decision))
        self.assertNotIn(FAKE, repr(decision.config))

    def test_each_missing_condition_blocks_alone(self) -> None:
        cases = {
            "chave": dict(env={}, vault=FakeVault()),
            "aviso": dict(choice=dataclasses.replace(GOOD, acknowledged=False)),
            "faturamento": dict(choice=dataclasses.replace(GOOD, billing_confirmed=False)),
            "pacotes": dict(missing_fn=lambda _c: ["langextract"]),
        }
        for name, change in cases.items():
            choice = change.pop("choice", GOOD)
            kwargs = {"env": _env(), "vault": FakeVault(), "missing_fn": lambda _c: []}
            kwargs.update(change)
            decision = check_engine(choice, **kwargs)
            self.assertFalse(decision.allowed, name)
            self.assertEqual(len(decision.reasons), 1, (name, decision.reasons))
            self.assertIsNone(decision.config, name)
            self.assertIsNone(decision.key, name)

    def test_config_json_with_a_key_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text(
                json.dumps({"exclusions": [], "api_key": "qualquer coisa"}), encoding="utf-8"
            )
            decision = _ready(private_root=tmp)
        self.assertFalse(decision.allowed)
        self.assertIn("config.json", decision.blocked_message())
        self.assertNotIn("qualquer coisa", decision.blocked_message())

    def test_generic_google_variables_do_not_open_the_gate(self) -> None:
        decision = _ready(env={"GEMINI_API_KEY": FAKE, "GOOGLE_API_KEY": FAKE})
        self.assertFalse(decision.allowed)
        self.assertIn("GEMINI_API_KEY", decision.blocked_message())
        self.assertNotIn(FAKE, decision.blocked_message())

    def test_vault_key_opens_the_gate(self) -> None:
        vault = FakeVault({(VAULT_SERVICE, VAULT_USER): FAKE})
        decision = _ready(env={}, vault=vault)
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.key_source, "Gerenciador de Credenciais")

    def test_choice_is_not_part_of_the_saved_settings(self) -> None:
        names = {field.name for field in dataclasses.fields(AppSettings)}
        self.assertFalse({"channel", "canal", "engine", "motor", "api_key", "chave"} & names)
        with tempfile.TemporaryDirectory() as tmp:
            settings = AppSettings(
                custom_rules=[CustomRule("x", "X", "ROTULO", r"\d+")], exclusions=["Tema"]
            )
            save_settings(Path(tmp), settings)
            saved = (Path(tmp) / "config.json").read_text(encoding="utf-8")
            self.assertEqual(config_key_findings(tmp), [])
        for forbidden in ("gemini", "canal", "motor", "chave", "api_key"):
            self.assertNotIn(forbidden, saved.casefold())

    def test_engine_choice_defaults_to_no_channel(self) -> None:
        self.assertEqual(EngineChoice().channel, "")
        self.assertFalse(check_engine(EngineChoice()).allowed)


class VertexAndOllamaTests(unittest.TestCase):
    def test_vertex_needs_project_credential_and_confirmations(self) -> None:
        choice = EngineChoice(
            channel="vertex-sp", acknowledged=True, billing_confirmed=True, project="projeto-ficticio"
        )
        ready = check_engine(choice, missing_fn=lambda _c: [], adc_fn=lambda: True)
        self.assertTrue(ready.allowed, ready.reasons)
        self.assertEqual(ready.config.provider, "vertex")
        self.assertEqual(ready.config.location, "southamerica-east1")
        self.assertIsNone(ready.config.api_key)
        no_project = check_engine(
            dataclasses.replace(choice, project=""), missing_fn=lambda _c: [], adc_fn=lambda: True
        )
        self.assertFalse(no_project.allowed)
        no_adc = check_engine(choice, missing_fn=lambda _c: [], adc_fn=lambda: False)
        self.assertFalse(no_adc.allowed)

    def test_adc_check_only_looks_at_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            credential = Path(tmp) / "adc.json"
            self.assertFalse(adc_available({"GOOGLE_APPLICATION_CREDENTIALS": str(credential)}))
            credential.write_text("{}", encoding="utf-8")
            self.assertTrue(adc_available({"GOOGLE_APPLICATION_CREDENTIALS": str(credential)}))
            self.assertFalse(adc_available({"APPDATA": tmp, "USERPROFILE": tmp}))
            target = Path(tmp) / "gcloud"
            target.mkdir()
            (target / "application_default_credentials.json").write_text("{}", encoding="utf-8")
            self.assertTrue(adc_available({"APPDATA": tmp, "USERPROFILE": tmp}))

    def test_ollama_stays_on_this_computer(self) -> None:
        local = check_engine(EngineChoice(channel="ollama-local"), missing_fn=lambda _c: [])
        self.assertTrue(local.allowed, local.reasons)
        remote = check_engine(
            EngineChoice(channel="ollama-local", model_url="http://10.0.0.5:11434"),
            missing_fn=lambda _c: [],
        )
        self.assertFalse(remote.allowed)
        self.assertIn("localhost", remote.blocked_message())
        self.assertNotIn("leitura do aviso", " ".join(warning_paragraphs("ollama-local")))


class WarningTests(unittest.TestCase):
    def test_warning_states_what_governance_requires(self) -> None:
        text = warning_text("gemini-pago")
        for expected in (
            "nomes, endereços, fatos e valores seguem em claro",
            "CPF, CNPJ",
            "canal da chave",
            "SEM faturamento",
            "nível gratuito",
            "residência de dados",
            "Developer API",
        ):
            self.assertIn(expected, text)

    def test_vertex_warning_talks_about_the_regional_endpoint(self) -> None:
        text = warning_text("vertex-sp")
        self.assertIn("southamerica-east1", text)
        self.assertIn("endpoint global", text)
        self.assertNotIn("Developer API", text)

    def test_audit_lines_name_channel_and_source_but_never_the_key(self) -> None:
        decision = _ready()
        lines = "\n".join(audit_lines(decision, GOOD))
        self.assertIn("Canal: gemini-pago", lines)
        self.assertIn("variavel de ambiente", lines)
        self.assertIn(WARNING_VERSION, lines)
        self.assertNotIn(FAKE, lines)


class BuilderTests(unittest.TestCase):
    def test_blocked_decision_builds_nothing(self) -> None:
        blocked = check_engine(EngineChoice(channel="gemini-gratuito"))
        with self.assertRaises(LlmConfigError):
            build_discoverer(blocked, cache_dir=None)

    def test_direct_provider_without_channel_is_refused(self) -> None:
        from anonimizador_app.llm_discovery import LangExtractDiscoverer, LlmConfig

        for config in (LlmConfig(provider="gemini"), LlmConfig(provider="gemini", channel="gemini-pago")):
            with self.assertRaises(LlmConfigError):
                LangExtractDiscoverer(config)._build_model()

    def test_offline_discoverer_never_builds_a_model_nor_touches_the_network(self) -> None:
        discoverer = build_offline_discoverer("gemini-pago", cache_dir=None)
        text = "<!-- pagina-origem: 1 -->\nO sócio FULANO DE TAL foi citado e consta no processo.\n"
        with block_network() as attempts:
            found = discoverer.discover(text)
            with self.assertRaises(LlmConfigError):
                discoverer._build_model()
        self.assertEqual(attempts, [])
        self.assertEqual(found.mentions, [])
        self.assertEqual(found.report.sent, 0)
        self.assertEqual(len(found.report.failed), 1)
        self.assertIn("sem rede", found.report.failure_reasons[0])
        self.assertIn("sem rede", discoverer.describe())


class VersionLineTests(unittest.TestCase):
    def test_engine_label_and_line(self) -> None:
        info = VersionInfo(app="0.0.0", extractor="e|1", detection="9.9", code="abc1234")
        self.assertEqual(engine_label(), "motor local")
        self.assertEqual(engine_label("gemini-pago"), "motor LLM: gemini-pago")
        self.assertIn("motor local", info.line())
        self.assertIn("motor local", info.line(""))
        self.assertIn("motor LLM: gemini-pago", info.line("gemini-pago"))
        self.assertNotIn("motor local", info.line("gemini-pago"))


class PackagesTests(unittest.TestCase):
    def test_missing_packages_names_the_missing_ones(self) -> None:
        self.assertIsInstance(missing_packages("gemini-pago"), list)
        self.assertEqual(missing_packages("ollama-local").count("google-genai"), 0)


if __name__ == "__main__":
    unittest.main()
