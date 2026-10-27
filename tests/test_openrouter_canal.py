"""H9-p: canal de teste openrouter-teste (trava do documento sintetico, chave oculta) e Gemma na Gemini API.

Nada abre rede: o cliente HTTP e falso.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anonimizador_app.credentials import (
    OPENROUTER_ENV_VAR,
    OPENROUTER_VAULT_SERVICE,
    VAULT_USER,
    ApiKey,
    load_openrouter_key,
)
from anonimizador_app.llm_discovery import (
    LangExtractDiscoverer,
    LlmConfig,
    LlmConfigError,
    UsageMeter,
    _MeteredModels,
)
from anonimizador_app.llm_engine import CHANNELS, EngineChoice, check_engine
from anonimizador_app.llm_gate import (
    SYNTHETIC_SHA256,
    RunPermit,
    check_network_point,
    file_sha256,
    scrub_secrets,
    synthetic_lock_refusals,
)
from anonimizador_app.openrouter_model import build_openrouter_model, clean_output
from test_anonimizar_llm_cli import _run, _Workspace, cli
from test_credentials import FAKE, FakeVault
from test_pipeline_llm import FakeChannelDiscoverer

SYNTHETIC = "1b16179e9fc2fe2a02cd4990528497a5a8c2dabae8a8b9341f10de540c711ec8"
OR_KEY = "sk-or-v1-" + "0" * 40 + "ficticia"


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, *, json, headers, timeout):  # noqa: A002 - mesma assinatura do requests
        self.requests.append({"url": url, "json": copy.deepcopy(json), "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


def _ok(content: str, cost: float = 0.0001) -> _Response:
    return _Response(
        {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost},
        }
    )


class SyntheticLockTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def test_the_allowlist_is_the_synthetic_document_only(self) -> None:
        self.assertEqual(SYNTHETIC_SHA256, frozenset({SYNTHETIC}))

    def test_a_file_outside_the_list_is_refused(self) -> None:
        real = self.base / "autos_reais.pdf"
        real.write_bytes(b"%PDF-1.7 processo real")
        refusals = synthetic_lock_refusals([real])
        self.assertEqual(len(refusals), 1)
        self.assertIn("autos_reais.pdf", refusals[0])
        self.assertIn("SHA-256", refusals[0])

    def test_one_bad_file_refuses_the_whole_batch_and_no_file_is_refused_too(self) -> None:
        good = self.base / "a.pdf"
        good.write_bytes(b"sintetico")
        bad = self.base / "b.pdf"
        bad.write_bytes(b"real")
        allowed = frozenset({hashlib.sha256(b"sintetico").hexdigest()})
        self.assertEqual(synthetic_lock_refusals([good], allowed), [])
        self.assertEqual(len(synthetic_lock_refusals([good, bad], allowed)), 1)
        self.assertTrue(synthetic_lock_refusals([], allowed))
        self.assertTrue(synthetic_lock_refusals([self.base / "nao_existe.pdf"], allowed))

    def test_the_synthetic_hash_is_accepted(self) -> None:
        fake = self.base / "autos_sinteticos.pdf"
        fake.write_bytes(b"conteudo")
        with patch("anonimizador_app.llm_gate.file_sha256", lambda _path: SYNTHETIC):
            self.assertEqual(synthetic_lock_refusals([fake]), [])

    def test_the_discoverer_does_not_build_the_model_without_the_lock(self) -> None:
        config = LlmConfig(
            provider="openrouter", channel="openrouter-teste", model_id="qwen/qwen3-30b-a3b",
            api_key=ApiKey(OR_KEY, "teste"),
        )
        discoverer = LangExtractDiscoverer(config)
        with self.assertRaises(LlmConfigError) as caught:
            discoverer._build_model()
        self.assertIn("trava do documento sintetico", str(caught.exception))
        discoverer.synthetic_lock = ("0" * 64,)
        with self.assertRaises(LlmConfigError):
            discoverer._build_model()
        discoverer.synthetic_lock = (SYNTHETIC,)
        model = discoverer._build_model()
        self.assertNotIn(OR_KEY, repr(model) + str(model))


class OpenRouterModelTests(unittest.TestCase):
    def _model(self, session, meter=None, thinking="minimo"):
        return build_openrouter_model(
            model_id="qwen/qwen3-30b-a3b",
            api_key=ApiKey(OR_KEY, "teste"),
            max_output_tokens=2000,
            thinking=thinking,
            meter=meter or UsageMeter(),
            session=session,
        )

    def test_request_carries_privacy_preferences_and_the_key_only_in_the_header(self) -> None:
        session = _Session([_ok('```json\n{"extractions": []}\n```')])
        meter = UsageMeter()
        model = self._model(session, meter)
        outputs = list(model.infer(["texto sintetico"]))
        self.assertEqual(outputs[0][0].output, '{"extractions": []}')
        sent = session.requests[0]
        self.assertEqual(sent["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(sent["json"]["provider"], {"data_collection": "deny"})
        self.assertEqual(sent["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(sent["json"]["reasoning"], {"enabled": False})
        self.assertEqual(sent["json"]["temperature"], 0.0)
        self.assertNotIn(OR_KEY, json.dumps(sent["json"]))
        self.assertEqual(sent["headers"]["Authorization"], "Bearer " + OR_KEY)
        self.assertNotIn(OR_KEY, repr(model) + str(model) + f"{model}")
        self.assertEqual((meter.calls, meter.prompt_tokens, meter.output_tokens), (1, 100, 20))
        self.assertAlmostEqual(meter.cost_usd, 0.0001)

    def test_errors_never_carry_the_key(self) -> None:
        session = _Session([_Response({"error": {"message": f"bad key {OR_KEY}"}}, status=401)])
        model = self._model(session)
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("teste").debug("so para abrir o contexto")
            with self.assertRaises(Exception) as caught:
                list(model.infer(["x"]))
        self.assertIn("401", str(caught.exception))
        self.assertNotIn(OR_KEY, str(caught.exception) + "\n".join(logs.output))

    def test_a_rejected_reasoning_setting_is_dropped_once(self) -> None:
        session = _Session([_Response({"error": {"message": "reasoning is not supported"}}, status=400), _ok("{}")])
        meter = UsageMeter()
        list(self._model(session, meter).infer(["x"]))
        self.assertIn("reasoning", session.requests[0]["json"])
        self.assertNotIn("reasoning", session.requests[1]["json"])
        self.assertTrue(meter.thinking_rejected)

    def test_only_the_official_endpoint(self) -> None:
        from anonimizador_app.llm_gate import GateError

        with self.assertRaises(GateError):
            build_openrouter_model(
                model_id="x/y", api_key=ApiKey(OR_KEY, "t"), max_output_tokens=10, thinking="minimo",
                meter=UsageMeter(), base_url="https://desvio.example.invalid/api/v1", session=_Session([]),
            )
        check = check_network_point(channel="openrouter-teste", provider="openrouter", env={})
        self.assertEqual(check.base_url, "https://openrouter.ai/api/v1")

    def test_clean_output(self) -> None:
        self.assertEqual(clean_output('<think>x</think>\n```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_scrub_removes_openrouter_keys(self) -> None:
        self.assertNotIn(OR_KEY, scrub_secrets(f"erro {OR_KEY}"))


class EngineTests(unittest.TestCase):
    def test_the_channel_is_cli_only_and_reads_its_own_key(self) -> None:
        self.assertNotIn("openrouter-teste", CHANNELS)
        vault = FakeVault({(OPENROUTER_VAULT_SERVICE, VAULT_USER): OR_KEY})
        decision = check_engine(
            EngineChoice(channel="openrouter-teste", model_id="qwen/qwen3-30b-a3b"),
            env={},
            vault=vault,
            missing_fn=lambda _c: [],
        )
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.config.provider, "openrouter")
        self.assertNotIn(OR_KEY, repr(decision) + repr(decision.config))
        self.assertEqual(load_openrouter_key(env={OPENROUTER_ENV_VAR: FAKE}, vault=vault).reveal(), FAKE)

    def test_without_a_model_id_or_with_a_free_variant_it_is_blocked(self) -> None:
        vault = FakeVault({(OPENROUTER_VAULT_SERVICE, VAULT_USER): OR_KEY})
        for model in ("gemini-3.1-flash-lite", "google/gemma-4-26b-a4b-it:free"):
            decision = check_engine(
                EngineChoice(channel="openrouter-teste", model_id=model), env={}, vault=vault, missing_fn=lambda _c: []
            )
            self.assertFalse(decision.allowed, model)

    def test_the_gemini_key_does_not_open_the_openrouter_channel(self) -> None:
        decision = check_engine(
            EngineChoice(channel="openrouter-teste", model_id="qwen/qwen3-30b-a3b"),
            env={"ANONIMIZADOR_GEMINI_PAGO_CHAVE": FAKE},
            vault=FakeVault(),
            missing_fn=lambda _c: [],
        )
        self.assertFalse(decision.allowed)
        self.assertIn("OpenRouter", decision.blocked_message())


class CliTests(_Workspace):
    def test_parser_accepts_the_channel(self) -> None:
        args = cli.build_parser().parse_args(["x.pdf", "--canal", "openrouter-teste", "--modelo", "qwen/qwen3-8b"])
        self.assertEqual(args.canal, "openrouter-teste")
        self.assertEqual(args.paralelo, 4)

    def test_a_real_file_is_refused_before_anything_is_read(self) -> None:
        built = []
        with patch.object(cli, "build_discoverer", lambda *a, **k: built.append(1)):
            code, out, err = _run(
                [str(self.source), "--canal", "openrouter-teste", "--modelo", "qwen/qwen3-30b-a3b",
                 "--confirmo-envio", *self.roots],
                env={OPENROUTER_ENV_VAR: FAKE},
                vault=FakeVault(),
            )
        self.assertEqual(code, 2)
        self.assertIn("Canal de teste recusado", err)
        self.assertIn("autos.md", err)
        self.assertEqual(built, [])
        self.assertFalse((self.base / "exp").exists())
        self.assertNotIn(FAKE, out + err)

    def test_the_synthetic_file_runs_with_the_flag_and_never_records_the_key(self) -> None:
        allowed = frozenset({file_sha256(self.source)})
        discoverer = FakeChannelDiscoverer()
        with patch.object(cli, "synthetic_lock_refusals", functools.partial(synthetic_lock_refusals, allowed=allowed)), \
                patch.object(cli, "build_discoverer", lambda *a, **k: discoverer):
            argv = [str(self.source), "--canal", "openrouter-teste", "--modelo", "qwen/qwen3-30b-a3b", *self.roots]
            code, _out, err = _run(argv, env={OPENROUTER_ENV_VAR: FAKE}, vault=FakeVault())
            self.assertEqual(code, 2)
            self.assertIn("--confirmo-envio", err)
            code, out, err = _run([*argv, "--confirmo-envio"], env={OPENROUTER_ENV_VAR: FAKE}, vault=FakeVault())
        self.assertEqual(code, 0, err)
        self.assertIn("Trava do canal de teste", out)
        self.assertEqual(discoverer.synthetic_lock, (file_sha256(self.source),))
        for path in self.base.rglob("*"):
            if path.is_file():
                self.assertNotIn(FAKE, path.read_text(encoding="utf-8", errors="ignore"), path.name)
        self.assertNotIn(FAKE, out + err)

    def test_store_the_openrouter_key_from_a_hidden_prompt(self) -> None:
        vault = FakeVault()
        code, out, err = _run(["--guardar-chave-openrouter"], vault=vault, answer=FAKE)
        self.assertEqual(code, 0, err)
        self.assertEqual(vault.stored[(OPENROUTER_VAULT_SERVICE, VAULT_USER)], FAKE)
        self.assertNotIn(FAKE, out + err)


class GemmaJsonModeTests(unittest.TestCase):
    """Gemma na Gemini API pode recusar o modo JSON: a rodada segue sem response_schema, sem perder o trecho."""

    class _Models:
        def __init__(self, errors: list[Exception]) -> None:
            self.errors = list(errors)
            self.configs: list[dict] = []

        def generate_content(self, *, model, contents, config):
            self.configs.append(dict(config))
            if self.errors:
                raise self.errors.pop(0)
            return type("R", (), {"usage_metadata": None, "candidates": []})()

    def test_json_mode_and_thinking_refusals_are_dropped_and_remembered(self) -> None:
        models = self._Models([
            RuntimeError("400 INVALID_ARGUMENT. Thinking is not supported for this model."),
            RuntimeError("400 INVALID_ARGUMENT. JSON mode is not enabled for models/gemma-4-26b-a4b-it"),
        ])
        meter = UsageMeter()
        metered = _MeteredModels(models, meter, "LOW")
        config = {"response_mime_type": "application/json", "response_schema": {"type": "OBJECT"}}
        metered.generate_content(model="gemma-4-26b-a4b-it", contents="x", config=config)
        self.assertEqual(len(models.configs), 3)
        self.assertNotIn("response_schema", models.configs[-1])
        self.assertNotIn("thinking_config", models.configs[-1])
        self.assertTrue(meter.schema_rejected and meter.thinking_rejected)
        self.assertEqual((meter.calls, meter.failed), (1, 0))
        metered.generate_content(model="gemma-4-26b-a4b-it", contents="y", config=config)
        self.assertEqual(len(models.configs), 4)
        self.assertNotIn("response_mime_type", models.configs[-1])
        self.assertIn("Modo JSON recusado", "\n".join(meter.lines(LlmConfig(model_id="gemma-4-26b-a4b-it"))))

    def test_other_errors_still_fail(self) -> None:
        models = self._Models([RuntimeError("503 UNAVAILABLE")])
        meter = UsageMeter()
        with self.assertRaises(RuntimeError):
            _MeteredModels(models, meter, None).generate_content(
                model="gemma-4-26b-a4b-it", contents="x", config={"response_mime_type": "application/json"}
            )
        self.assertEqual(meter.failed, 1)


class PermitTests(unittest.TestCase):
    def test_the_channel_still_needs_the_run_permit(self) -> None:
        config = LlmConfig(
            provider="openrouter", channel="openrouter-teste", model_id="qwen/qwen3-30b-a3b",
            api_key=ApiKey(OR_KEY, "teste"), chunk_chars=500,
        )
        discoverer = LangExtractDiscoverer(config)
        discoverer.synthetic_lock = (SYNTHETIC,)
        with self.assertRaises(LlmConfigError):
            discoverer.discover("O executado JOAO FICTICIO DA SILVA foi citado. " * 3)
        self.assertIsInstance(RunPermit(channel="openrouter-teste", files=(), total_chars=0), RunPermit)


if __name__ == "__main__":
    unittest.main()
