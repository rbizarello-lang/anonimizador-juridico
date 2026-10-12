"""Portao do motor LLM: so roda com todas as condicoes. Nada aqui abre rede."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.credentials import ENV_VAR, VAULT_SERVICE, VAULT_USER
from anonimizador_app.llm_discovery import LlmConfigError
from anonimizador_app.llm_gate import FilePlan, RunPermit, SendPlan
from anonimizador_app.llm_engine import (
    BLOCKED_CHANNELS,
    CHANNELS,
    WARNING_VERSION,
    EngineChoice,
    adc_available,
    audit_lines,
    billing_note,
    build_discoverer,
    build_offline_discoverer,
    check_engine,
    config_key_findings,
    confirmation_lines,
    missing_packages,
    warning_paragraphs,
    warning_text,
)
from anonimizador_app.netblock import block_network
from anonimizador_app.settings import AppSettings, CustomRule, save_settings
from anonimizador_app.version import ENGINE_LOCAL, VersionInfo, engine_label
from test_credentials import FAKE, FakeVault

GOOD = EngineChoice(channel="gemini-pago")
PLAN = SendPlan(
    files=(
        FilePlan("PARTE_1.pdf", 10, 3000, 2, 0, 3000),
        FilePlan("PARTE_2.pdf", 8, 2000, 1, 1, 0),
    ),
    verification_rounds=1,
)


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
            choice = EngineChoice(channel=channel)
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


class VertexTests(unittest.TestCase):
    def test_vertex_needs_project_and_credential(self) -> None:
        choice = EngineChoice(channel="vertex-sp", project="projeto-ficticio")
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

    def test_a_service_account_file_replaces_the_gcloud_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            account = Path(tmp) / "conta.json"
            account.write_text(
                json.dumps({"type": "service_account", "client_email": "c@x.example", "private_key": "NAO-LER"}),
                encoding="utf-8",
            )
            choice = EngineChoice(channel="vertex-sp", project="projeto-ficticio", credential_path=str(account))
            # Sem gcloud, mas com o arquivo: libera, e o caminho vai na configuracao (so o caminho).
            decision = check_engine(choice, env={}, vault=FakeVault(), missing_fn=lambda _c: [], adc_fn=lambda: False)
            self.assertTrue(decision.allowed, decision.reasons)
            self.assertEqual(decision.config.credentials_path, str(account))
            self.assertNotIn("NAO-LER", repr(decision.config))
            lines = "\n".join(audit_lines(decision, choice))
            self.assertIn("arquivo de conta de servico (o caminho nao e gravado)", lines)
            self.assertNotIn(str(account), lines)
            bad = check_engine(
                dataclasses.replace(choice, credential_path=str(Path(tmp) / "nao-existe.json")),
                env={}, vault=FakeVault(), missing_fn=lambda _c: [], adc_fn=lambda: True,
            )
            self.assertFalse(bad.allowed)
            self.assertIn("nao existe", bad.blocked_message())

    def test_the_registered_project_and_credential_are_used_when_the_screen_gives_none(self) -> None:
        from anonimizador_app.credentials import VERTEX_PROJECT_USER, VERTEX_VAULT_SERVICE

        vault = FakeVault({(VERTEX_VAULT_SERVICE, VERTEX_PROJECT_USER): "projeto-cadastrado"})
        decision = check_engine(
            EngineChoice(channel="vertex-sp"), env={}, vault=vault, missing_fn=lambda _c: [], adc_fn=lambda: True
        )
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.config.project, "projeto-cadastrado")
        self.assertEqual(decision.config.credentials_path, "")
        audit = "\n".join(audit_lines(decision))
        self.assertIn("credencial padrao do gcloud", audit)
        nothing = check_engine(
            EngineChoice(channel="vertex-sp"), env={}, vault=FakeVault(), missing_fn=lambda _c: [], adc_fn=lambda: False
        )
        self.assertFalse(nothing.allowed)
        self.assertEqual(len(nothing.reasons), 2)  # projeto e credencial

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

    def test_ollama_local_is_a_registered_local_channel_since_h8(self) -> None:
        spec = CHANNELS["ollama-local"]
        self.assertEqual(spec.provider, "ollama")
        self.assertTrue(spec.is_local)
        self.assertFalse(spec.needs_key or spec.needs_project)
        self.assertIn("só esta máquina", spec.label)
        # Os dois canais remotos seguem remotos, com chave ou projeto.
        self.assertFalse(CHANNELS["gemini-pago"].is_local)
        self.assertFalse(CHANNELS["vertex-sp"].is_local)


def _local(**kwargs):
    kwargs.setdefault("env", {})
    kwargs.setdefault("vault", FakeVault())
    kwargs.setdefault("missing_fn", lambda _channel: [])
    return check_engine(EngineChoice(channel="ollama-local", **kwargs.pop("choice", {})), **kwargs)


class OllamaLocalChannelTests(unittest.TestCase):
    def test_it_needs_no_key_and_no_project_and_defaults_the_model(self) -> None:
        decision = _local()
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.engine_id, "ollama-local")
        self.assertEqual(decision.config.provider, "ollama")
        self.assertEqual(decision.config.model_id, "qwen3:4b")
        self.assertEqual(decision.config.max_workers, 1)
        self.assertEqual(decision.config.chunk_chars, 2000)
        self.assertIsNone(decision.key)
        self.assertEqual(_local(choice={"model_id": "llama3.2:3b"}).config.model_id, "llama3.2:3b")

    def test_it_needs_only_langextract(self) -> None:
        import importlib.util
        from unittest.mock import patch

        seen: list[str] = []

        def find(name):
            seen.append(name)
            return object()

        with patch.object(importlib.util, "find_spec", find):
            self.assertEqual(missing_packages("ollama-local"), [])
        self.assertEqual(seen, ["langextract"])
        seen.clear()
        with patch.object(importlib.util, "find_spec", find):
            missing_packages("gemini-pago")
        self.assertEqual(seen, ["langextract", "google.genai"])

    def test_missing_langextract_blocks(self) -> None:
        decision = _local(missing_fn=lambda _c: ["langextract"])
        self.assertFalse(decision.allowed)
        self.assertIn("langextract", decision.blocked_message())

    def test_an_ollama_host_outside_loopback_is_refused_without_echoing_the_value(self) -> None:
        for value in (
            "http://10.0.0.5:11434",
            "10.0.0.5",
            "0.0.0.0:11434",
            "https://127.0.0.1:11434",
            "http://127.0.0.1:9999",
            "http://localhost:11434/api",
            "http://evil.example.invalid",
        ):
            decision = _local(env={"OLLAMA_HOST": value})
            self.assertFalse(decision.allowed, value)
            message = decision.blocked_message()
            self.assertIn("OLLAMA_HOST", message)
            self.assertNotIn(value, message)
            self.assertNotIn("10.0.0.5", message)
        # O LangExtract le OLLAMA_BASE_URL sozinho: tambem recusada se sair do loopback.
        self.assertFalse(_local(env={"OLLAMA_BASE_URL": "http://10.0.0.5:11434"}).allowed)

    def test_an_ollama_host_in_loopback_is_fine(self) -> None:
        for value in ("127.0.0.1", "127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", ""):
            self.assertTrue(_local(env={"OLLAMA_HOST": value}).allowed, value)

    def test_proxy_is_refused_here_but_stays_a_warning_for_gemini(self) -> None:
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy"):
            decision = _local(env={name: "http://proxy.example.invalid:3128"})
            self.assertFalse(decision.allowed, name)
            self.assertIn(name, decision.blocked_message())
            self.assertNotIn("proxy.example.invalid", decision.blocked_message())
        self.assertTrue(_local(env={"HTTPS_PROXY": "  "}).allowed)
        # Gemini pago: proxy continua so aviso (INALTERADO).
        decision = _ready(env=_env(HTTPS_PROXY="http://proxy.example.invalid:3128"))
        self.assertTrue(decision.allowed, decision.reasons)

    def test_cloud_models_are_refused(self) -> None:
        for model in ("gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "x:cloud", "CLOUD-model:1b"):
            decision = _local(choice={"model_id": model})
            self.assertFalse(decision.allowed, model)
            self.assertIn("nuvem", decision.blocked_message())
        self.assertFalse(_local(choice={"model_id": "qwen3 4b; rm"}).allowed)

    def test_google_sdk_variables_do_not_block_the_local_channel(self) -> None:
        self.assertTrue(_local(env={"GOOGLE_GEMINI_BASE_URL": "https://x.example.invalid"}).allowed)

    def test_the_remote_channels_still_refuse_their_sdk_redirects(self) -> None:
        decision = _ready(env=_env(GOOGLE_GEMINI_BASE_URL="https://x.example.invalid"))
        self.assertFalse(decision.allowed)

    def test_confirmation_and_audit_say_nothing_is_sent(self) -> None:
        decision = _local()
        text = "\n".join(confirmation_lines(decision, PLAN))
        self.assertIn("LLM local (Ollama, loopback), nada enviado", text)
        self.assertNotIn("serão enviados", text)
        self.assertNotIn("sigilo", text.casefold())
        audit = "\n".join(audit_lines(decision))
        self.assertIn("LLM local (Ollama, loopback), nada enviado", audit)
        self.assertIn("num_ctx", audit)
        self.assertIn("qwen3:4b", audit)
        self.assertNotIn("enviado ao provedor", audit)
        self.assertNotIn("Aviso (versao", audit)

    def test_remote_texts_are_unchanged(self) -> None:
        decision = _ready()
        audit = "\n".join(audit_lines(decision))
        self.assertIn("foi enviado ao provedor", audit)
        self.assertIn(f"Aviso (versao {WARNING_VERSION}", audit)
        self.assertIn("serão enviados", "\n".join(confirmation_lines(decision, PLAN)))
        self.assertEqual(WARNING_VERSION, "2026-10-01.1")

    def test_build_discoverer_forces_one_worker_and_needs_no_permit(self) -> None:
        discoverer = build_discoverer(_local(), cache_dir=None, max_workers=8, chunk_chars=1500)
        self.assertEqual(discoverer.config.max_workers, 1)
        self.assertEqual(discoverer.config.chunk_chars, 1500)
        self.assertIsNone(discoverer.permit)


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
        permit = RunPermit.for_plan("gemini-pago", PLAN)
        lines = "\n".join(audit_lines(decision, GOOD, permit))
        self.assertIn("Canal: gemini-pago", lines)
        self.assertIn("variavel de ambiente", lines)
        self.assertIn(WARNING_VERSION, lines)
        self.assertIn("a decisao de enviar foi do Operador", lines)
        self.assertIn("Confirmacao desta rodada", lines)
        self.assertIn("2 arquivo(s), 5000 caractere(s)", lines)
        # A AUDITORIA exportavel nao leva nome de arquivo.
        self.assertNotIn("PARTE_1", lines)
        self.assertNotIn(FAKE, lines)

    def test_the_warnings_inform_and_do_not_block(self) -> None:
        # Decisao do Operador: o app avisa (sigilo, residencia, faturamento), nao bloqueia por politica.
        self.assertTrue(_ready().allowed)
        self.assertIn("é do Operador", warning_text("gemini-pago"))
        self.assertIn("Faturamento", billing_note("gemini-pago"))
        self.assertIn("institucional", billing_note("vertex-sp"))


class ConfirmationTests(unittest.TestCase):
    """O que o Operador ve antes de cada envio."""

    def test_confirmation_shows_channel_files_and_total(self) -> None:
        text = "\n".join(confirmation_lines(_ready(), PLAN))
        self.assertIn("Canal: gemini-pago", text)
        self.assertIn("PARTE_1.pdf: 3.000 caracteres, 2 trecho(s) (0 já no cache local)", text)
        self.assertIn("PARTE_2.pdf: 2.000 caracteres", text)
        self.assertIn("Total: 5.000 caracteres na descoberta, dos quais 3.000 ainda não estão no cache local", text)
        self.assertIn("3.000 ainda não estão no cache", text)
        self.assertIn("nomes, fatos e valores vão em claro", text)
        self.assertNotIn(FAKE, text)

    def test_vertex_confirmation_shows_the_region_and_does_not_force_it(self) -> None:
        for region in ("southamerica-east1", "us-central1"):
            choice = EngineChoice(channel="vertex-sp", project="projeto-ficticio", location=region)
            decision = check_engine(choice, missing_fn=lambda _c: [], adc_fn=lambda: True)
            self.assertTrue(decision.allowed, decision.reasons)
            text = "\n".join(confirmation_lines(decision, PLAN))
            self.assertIn(f"Região do Vertex AI: {region}", text)
            self.assertIn("projeto-ficticio", text)
            permit = RunPermit.for_plan("vertex-sp", PLAN, region=region)
            self.assertIn(f"Regiao do Vertex AI confirmada: {region}", "\n".join(permit.audit_lines()))

    def test_unreadable_file_is_listed_as_not_sent(self) -> None:
        plan = SendPlan(files=PLAN.files, errors=(("quebrado.pdf", "arquivo corrompido"),))
        text = "\n".join(confirmation_lines(_ready(), plan))
        self.assertIn("quebrado.pdf: NÃO será enviado", text)


class PermitTests(unittest.TestCase):
    def test_a_permit_lives_for_one_run_only(self) -> None:
        from anonimizador_app.llm_gate import GateError

        permit = RunPermit.for_plan("gemini-pago", PLAN)
        self.assertFalse(permit.active)
        with self.assertRaises(GateError):
            permit.require_active()
        permit.start()
        self.assertTrue(permit.active)
        permit.require_active()
        with self.assertRaises(GateError):
            permit.start()  # nao reinicia
        permit.finish()
        self.assertFalse(permit.active)
        with self.assertRaises(GateError):
            permit.require_active()
        with self.assertRaises(GateError):
            permit.start()  # expirou: a proxima rodada precisa de confirmacao nova

    def test_the_real_discoverer_will_not_reach_the_provider_without_an_active_permit(self) -> None:
        from anonimizador_app.llm_discovery import LangExtractDiscoverer

        decision = _ready()
        text = "<!-- pagina-origem: 1 -->\nO sócio FULANO DE TAL foi citado e consta no processo.\n"
        with block_network() as attempts:
            no_permit = build_discoverer(decision, cache_dir=None)
            with self.assertRaises(LlmConfigError) as caught:
                no_permit.discover(text)
            self.assertIn("confirmacao desta rodada", str(caught.exception))
            fresh = RunPermit.for_plan("gemini-pago", PLAN)
            armed = build_discoverer(decision, cache_dir=None, permit=fresh)
            with self.assertRaises(LlmConfigError):  # confirmacao ainda nao iniciada
                armed.discover(text)
            fresh.start()
            fresh.finish()
            with self.assertRaises(LlmConfigError):  # confirmacao expirada
                armed.discover(text)
        self.assertEqual(attempts, [])
        self.assertIsInstance(armed, LangExtractDiscoverer)


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
        self.assertIn("google-genai", missing_packages("gemini-pago") + ["google-genai"])


if __name__ == "__main__":
    unittest.main()
