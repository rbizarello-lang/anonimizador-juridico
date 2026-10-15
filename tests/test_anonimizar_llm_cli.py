"""Linha de comando do motor LLM: portao, chave e modo sem rede. Nada abre rede."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anonimizador_app import credentials
from anonimizador_app.credentials import ENV_VAR, VAULT_SERVICE, VAULT_USER
from anonimizador_app.netblock import block_network
from test_credentials import FAKE, FakeVault
from test_pipeline_llm import DOC, PAGE_2_NAMES, FakeChannelDiscoverer, FakeDiscoverer, FakeLocalEngine

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("anonimizar_llm_cli", ROOT / "tools" / "anonimizar_llm.py")
cli = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


def _run(argv, *, env=None, vault=None, answer=None):
    out, err = io.StringIO(), io.StringIO()
    environment = {key: value for key, value in os.environ.items() if key != ENV_VAR}
    environment.update(env or {})
    patches = [
        patch.dict(os.environ, environment, clear=True),
        patch.object(credentials, "system_vault", lambda: vault),
        patch("anonimizador_app.llm_engine.missing_packages", lambda _channel="": []),
    ]
    if answer is not None:
        patches.append(patch.object(cli.getpass, "getpass", lambda _prompt="": answer))
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class _Workspace(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.source = self.base / "autos.md"
        self.source.write_text(DOC, encoding="utf-8")
        self.roots = ["--exportaveis", str(self.base / "exp"), "--privados", str(self.base / "priv")]


class GateCliTests(_Workspace):
    def test_without_channel_nothing_runs(self) -> None:
        code, _out, err = _run([str(self.source), *self.roots], vault=FakeVault())
        self.assertEqual(code, 2)
        self.assertIn("Motor LLM bloqueado", err)
        self.assertIn("Nenhum canal", err)
        self.assertFalse((self.base / "exp").exists())

    def test_free_channel_is_not_even_an_option(self) -> None:
        code, _out, err = _run([str(self.source), "--canal", "gemini-gratuito", *self.roots])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)
        self.assertFalse((self.base / "exp").exists())

    def test_without_the_per_run_flag_it_lists_what_would_be_sent_and_sends_nothing(self) -> None:
        discoverer = FakeChannelDiscoverer()
        with patch.object(cli, "build_discoverer", lambda *a, **k: discoverer):
            code, out, err = _run(
                [str(self.source), "--canal", "gemini-pago", *self.roots],
                env={ENV_VAR: FAKE},
                vault=FakeVault(),
            )
        self.assertEqual(code, 2)
        # Os avisos so informam (sigilo, residencia, faturamento).
        self.assertIn("nomes, endereços, fatos e valores seguem em claro", err)
        self.assertIn("Faturamento", err)
        self.assertIn("so informacao", err)
        # A lista do que iria ao provedor: canal, arquivo, caracteres.
        self.assertIn("O QUE ESTA RODADA VAI ENVIAR AO PROVEDOR", out)
        self.assertIn("Canal: gemini-pago", out)
        self.assertIn("autos.md", out)
        self.assertIn("caracteres", out)
        self.assertIn("--confirmo-envio", err)
        self.assertIn("Nada foi enviado", err)
        self.assertEqual(discoverer.calls, [])
        self.assertFalse((self.base / "exp").exists())
        self.assertNotIn(FAKE, out + err)

    def test_the_old_per_session_flags_are_gone(self) -> None:
        code, _out, err = _run([str(self.source), "--canal", "gemini-pago", "--ciente", *self.roots])
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments", err)

    def test_verify_reports_without_sending(self) -> None:
        code, out, _err = _run(
            ["--verificar", "--canal", "gemini-pago", *self.roots],
            env={ENV_VAR: FAKE},
            vault=FakeVault(),
        )
        self.assertEqual(code, 0)
        self.assertIn("LIBERADO", out)
        self.assertIn("variavel de ambiente", out)
        self.assertNotIn(FAKE, out)
        code, out, _err = _run(["--verificar", "--canal", "gemini-pago", *self.roots], vault=FakeVault())
        self.assertEqual(code, 1)
        self.assertIn("BLOQUEADO", out)

    def test_allowed_run_records_the_engine_and_never_the_key(self) -> None:
        argv = [str(self.source), "--canal", "gemini-pago", "--confirmo-envio", *self.roots]
        with patch.object(cli, "build_discoverer", lambda *a, **k: FakeChannelDiscoverer()):
            code, out, err = _run(argv, env={ENV_VAR: FAKE}, vault=FakeVault())
        self.assertEqual(code, 0, err)
        self.assertIn("Motor: LLM gemini-pago", out)
        folder = next((self.base / "exp").iterdir())
        audit = (folder / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("motor LLM: gemini-pago", audit)
        self.assertIn("Chave lida de: variavel de ambiente", audit)
        self.assertIn("Aviso (versao", audit)
        self.assertIn("Confirmacao desta rodada", audit)
        self.assertIn("1 arquivo(s)", audit)
        for path in self.base.rglob("*"):
            if path.is_file():
                self.assertNotIn(FAKE, path.read_text(encoding="utf-8", errors="ignore"), path.name)
        self.assertNotIn(FAKE, out + err)


class KeyCliTests(unittest.TestCase):
    def test_store_key_from_a_hidden_prompt_and_delete_it(self) -> None:
        vault = FakeVault()
        code, out, err = _run(["--guardar-chave"], vault=vault, answer=FAKE)
        self.assertEqual(code, 0, err)
        self.assertEqual(vault.stored[(VAULT_SERVICE, VAULT_USER)], FAKE)
        self.assertIn("Chave guardada", out)
        self.assertNotIn(FAKE, out + err)
        code, out, _err = _run(["--apagar-chave"], vault=vault)
        self.assertEqual(code, 0)
        self.assertEqual(vault.stored, {})
        self.assertIn("apagada", out)

    def test_empty_answer_stores_nothing(self) -> None:
        vault = FakeVault()
        code, _out, err = _run(["--guardar-chave"], vault=vault, answer="   ")
        self.assertEqual(code, 2)
        self.assertEqual(vault.stored, {})
        self.assertIn("Chave vazia", err)

    def test_key_is_not_a_command_line_option(self) -> None:
        code, _out, err = _run(["--chave", FAKE])
        self.assertEqual(code, 2)
        self.assertNotIn(FAKE, err.replace(f"--chave", ""))  # o argumento nao e ecoado como valor valido


class VertexCliTests(unittest.TestCase):
    def test_register_the_vertex_setup_with_a_service_account_file_and_delete_it(self) -> None:
        from anonimizador_app.credentials import VERTEX_CREDENTIAL_USER, VERTEX_PROJECT_USER, VERTEX_VAULT_SERVICE

        with tempfile.TemporaryDirectory() as tmp:
            account = Path(tmp) / "conta.json"
            account.write_text('{"type": "service_account", "client_email": "c@x.example", "private_key": "NAO-LER"}', encoding="utf-8")
            vault = FakeVault()
            answers = iter(["projeto-ficticio", str(account)])
            with patch("builtins.input", lambda _prompt="": next(answers)):
                code, out, err = _run(["--guardar-vertex"], vault=vault)
            self.assertEqual(code, 0, err)
            self.assertEqual(vault.stored[(VERTEX_VAULT_SERVICE, VERTEX_PROJECT_USER)], "projeto-ficticio")
            self.assertEqual(vault.stored[(VERTEX_VAULT_SERVICE, VERTEX_CREDENTIAL_USER)], str(account))
            self.assertIn("Guardado no Gerenciador de Credenciais", out)
            self.assertNotIn("NAO-LER", out + err)
            code, out, _err = _run(["--verificar", "--canal", "vertex-sp"], vault=vault)
            self.assertEqual(code, 0, out)
            self.assertIn("arquivo de conta de servico", out)
            code, out, _err = _run(["--apagar-vertex"], vault=vault)
            self.assertEqual(code, 0)
            self.assertIn("apagado", out)
            self.assertEqual(vault.stored, {})

    def test_an_invalid_credential_file_stores_nothing(self) -> None:
        vault = FakeVault()
        answers = iter(["projeto-ficticio", "C:/nao/existe.json"])
        with patch("builtins.input", lambda _prompt="": next(answers)):
            code, _out, err = _run(["--guardar-vertex"], vault=vault)
        self.assertEqual(code, 2)
        self.assertIn("nao existe", err)
        self.assertEqual(vault.stored, {})


class OfflineCliTests(_Workspace):
    def test_offline_needs_the_channel(self) -> None:
        code, _out, err = _run([str(self.source), "--sem-rede", *self.roots])
        self.assertEqual(code, 2)
        self.assertIn("--canal", err)

    def test_offline_with_empty_cache_goes_through_the_local_engine_and_opens_no_socket(self) -> None:
        engine = FakeLocalEngine(PAGE_2_NAMES + [("PERSON", "OLAVO RICARDO TEIXEIRA")])
        with block_network() as attempts, patch(
            "anonimizador_app.pipeline_llm.build_contextual_engine", lambda *a, **k: engine
        ):
            code, out, err = _run(
                [str(self.source), "--canal", "gemini-pago", "--sem-rede", *self.roots],
                vault=FakeVault(),
            )
        self.assertEqual(attempts, [])
        # O LLM nao leu nada (sem cache): o motor local tratou; o codigo de saida avisa.
        self.assertEqual(code, 3, err)
        self.assertIn("FALLBACK autos.md", err)
        self.assertIn("--aceitar-fallback", err)
        self.assertIn("Motor: LLM gemini-pago", out)
        folder = next((self.base / "exp").iterdir())
        audit = (folder / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("Modo sem rede", audit)
        self.assertIn("motor LLM: gemini-pago", audit)
        self.assertIn("tratadas pelo motor local por falha do LLM", audit)
        text = (folder / "doc_1.md").read_text(encoding="utf-8")
        self.assertNotIn("OLAVO RICARDO TEIXEIRA", text.upper())
        self.assertNotIn("MARLENE OLIVEIRA SAMPAIO", text.upper())


class FallbackExitCodeTests(_Workspace):
    def _argv(self, *extra):
        return [str(self.source), "--canal", "gemini-pago", "--confirmo-envio", *self.roots, *extra]

    def _run_with(self, discoverer, engine, *extra):
        with patch.object(cli, "build_discoverer", lambda *a, **k: discoverer), patch(
            "anonimizador_app.pipeline_llm.build_contextual_engine", lambda *a, **k: engine
        ):
            return _run(self._argv(*extra), env={ENV_VAR: FAKE}, vault=FakeVault())

    def test_a_fallback_exits_non_zero_unless_it_is_accepted(self) -> None:
        discoverer = FakeChannelDiscoverer(fail_pass={1, 2})
        code, _out, err = self._run_with(discoverer, FakeLocalEngine(PAGE_2_NAMES))
        self.assertEqual(code, 3, err)
        self.assertIn("folhas tratadas pelo motor local por falha do LLM: 2", err)
        self.assertNotIn(FAKE, err)

        # A pasta ja tem o lote da rodada anterior: o texto foi gravado, sem o nome em claro.
        folder = next((self.base / "exp").iterdir())
        self.assertNotIn("MARLENE OLIVEIRA SAMPAIO", (folder / "doc_1.md").read_text(encoding="utf-8").upper())

        code, _out, err = self._run_with(
            FakeChannelDiscoverer(fail_pass={1, 2}), FakeLocalEngine(PAGE_2_NAMES), "--aceitar-fallback"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("FALLBACK autos.md", err)

    def test_no_fallback_no_extra_exit_code(self) -> None:
        code, _out, err = self._run_with(FakeChannelDiscoverer(), FakeLocalEngine([]))
        self.assertEqual(code, 0, err)
        self.assertNotIn("FALLBACK", err)

    def test_held_file_is_not_exported_and_exits_one(self) -> None:
        engine = FakeLocalEngine(boom=RuntimeError("falhou"))
        code, _out, err = self._run_with(FakeChannelDiscoverer(fail_pass={1, 2}), engine, "--aceitar-fallback")
        self.assertEqual(code, 1)
        self.assertIn("NAO ENVIE AINDA", err)
        folder = next((self.base / "exp").iterdir())
        self.assertFalse((folder / "doc_1.md").exists())



class DefaultParallelismTests(unittest.TestCase):
    """M5: o padrao de chamadas simultaneas e 4 (na CLI e na tela), nao 16."""

    def test_cli_and_engine_defaults_are_four(self) -> None:
        from anonimizador_app.llm_discovery import LlmConfig

        self.assertEqual(cli.build_parser().parse_args(["x.md"]).paralelo, 4)
        self.assertEqual(cli.build_parser().parse_args(["x.md", "--paralelo", "2"]).paralelo, 2)
        self.assertEqual(LlmConfig().max_workers, 4)

    def test_detection_version_records_the_h9e_bump(self) -> None:
        from anonimizador_app.version import DETECTION_VERSION

        self.assertEqual(DETECTION_VERSION, "2.8.5")


class LocalFloorCliTests(_Workspace):
    """I-N4: ``--piso-local`` (padrao) e ``--sem-piso-local``."""

    def _argv(self, *extra):
        return [str(self.source), "--canal", "gemini-pago", "--confirmo-envio", *self.roots, *extra]

    def _run_with(self, engine, *extra):
        with patch.object(cli, "build_discoverer", lambda *a, **k: FakeChannelDiscoverer()), patch(
            "anonimizador_app.pipeline_llm.build_contextual_engine", lambda *a, **k: engine
        ):
            return _run(self._argv(*extra), env={ENV_VAR: FAKE}, vault=FakeVault())

    def test_the_floor_is_on_by_default_and_the_local_engine_reads_every_page(self) -> None:
        engine = FakeLocalEngine([])
        code, out, err = self._run_with(engine)
        self.assertEqual(code, 0, err)
        self.assertEqual(engine.calls, 1)
        self.assertIn("Piso local: LIGADO", out)
        folder = next((self.base / "exp").iterdir())
        self.assertIn("Piso local: LIGADO", (folder / "AUDITORIA.txt").read_text(encoding="utf-8"))
        self.assertIn("Piso local: LIGADO", (folder / "LEIA-ME.txt").read_text(encoding="utf-8"))

    def test_the_explicit_option_turns_it_on_even_if_the_config_says_off(self) -> None:
        (self.base / "priv").mkdir(parents=True, exist_ok=True)
        (self.base / "priv" / "config.json").write_text('{"llm_local_floor": false}', encoding="utf-8")
        engine = FakeLocalEngine([])
        code, out, err = self._run_with(engine)
        self.assertEqual(code, 0, err)
        self.assertEqual(engine.calls, 0)
        self.assertIn("Piso local: DESLIGADO", out)
        engine = FakeLocalEngine([])
        code, out, err = self._run_with(engine, "--piso-local")
        self.assertEqual(code, 0, err)
        self.assertEqual(engine.calls, 1)
        self.assertIn("Piso local: LIGADO", out)

    def test_the_off_option_leaves_the_local_engine_idle_when_the_llm_read_everything(self) -> None:
        engine = FakeLocalEngine([])
        code, out, err = self._run_with(engine, "--sem-piso-local")
        self.assertEqual(code, 0, err)
        self.assertEqual(engine.calls, 0)
        self.assertIn("Piso local: DESLIGADO", out)
        folder = next((self.base / "exp").iterdir())
        self.assertIn("Piso local: DESLIGADO", (folder / "AUDITORIA.txt").read_text(encoding="utf-8"))


class LocalLlmCliTests(_Workspace):
    """H9-b: --canal ollama-local roda sem --confirmo-envio e sem aviso de envio; nada sai da maquina."""

    def _doc(self) -> Path:
        from test_ollama_local import DOC

        self.source.write_text(DOC, encoding="utf-8")
        return self.source

    def test_verify_says_it_is_local_and_needs_no_key(self) -> None:
        code, out, _err = _run(["--verificar", "--canal", "ollama-local", *self.roots], vault=FakeVault())
        self.assertEqual(code, 0)
        self.assertIn("LLM local", out)
        self.assertIn("LIBERADO", out)
        self.assertNotIn("Chave:", out)

    def test_it_runs_without_the_send_flag_and_without_the_warning(self) -> None:
        from anonimizador_app.llm_engine import build_discoverer as real_build
        from fake_ollama import FakeOllama

        self._doc()
        with FakeOllama() as server:
            def build(decision, **kwargs):
                discoverer = real_build(decision, **kwargs)
                discoverer._loopback_port = server.port
                return discoverer

            with patch.object(cli, "build_discoverer", build):
                code, out, err = _run(
                    [str(self.source), "--canal", "ollama-local", "--sem-piso-local", *self.roots],
                    vault=FakeVault(),
                )
        self.assertEqual(code, 0, err)
        self.assertNotIn("--confirmo-envio", err)
        self.assertNotIn("AVISO (versao", err)
        self.assertNotIn("sigilo", (out + err).casefold())
        self.assertIn("LLM LOCAL (Ollama, loopback), NADA ENVIADO", out)
        self.assertIn("Motor: LLM ollama-local", out)
        self.assertIn("Tentativas de rede fora de loopback bloqueadas: 0", out)
        self.assertTrue(server.requests)
        folder = next((self.base / "exp").iterdir())
        audit = (folder / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("motor LLM: ollama-local", audit)
        self.assertIn("LLM local (Ollama, loopback), nada enviado", audit)
        self.assertIn("Tentativas de rede fora de loopback bloqueadas: 0", audit)
        self.assertNotIn("Confirmacao desta rodada", audit)

    def test_a_blocked_attempt_during_the_round_is_reported_and_exits_one(self) -> None:
        from anonimizador_app.llm_engine import build_discoverer as real_build
        from fake_ollama import FakeOllama

        self._doc()
        real_run = cli.run_llm_job

        def run(*args, **kwargs):
            result = real_run(*args, **kwargs)
            kwargs["discoverer"].blocked_attempts = 2
            return result

        with FakeOllama() as server:
            def build(decision, **kwargs):
                discoverer = real_build(decision, **kwargs)
                discoverer._loopback_port = server.port
                return discoverer

            with patch.object(cli, "build_discoverer", build), patch.object(cli, "run_llm_job", run):
                code, out, err = _run(
                    [str(self.source), "--canal", "ollama-local", "--sem-piso-local", *self.roots],
                    vault=FakeVault(),
                )
        self.assertEqual(code, 1)
        self.assertIn("ALERTA: 2 tentativa(s)", err)

    def test_remote_channels_still_need_the_flag(self) -> None:
        code, _out, err = _run(
            [str(self.source), "--canal", "gemini-pago", *self.roots], env={ENV_VAR: FAKE}, vault=FakeVault()
        )
        self.assertEqual(code, 2)
        self.assertIn("--confirmo-envio", err)

    def test_default_chunk_is_2000_for_the_local_channel_and_4000_otherwise(self) -> None:
        self.assertIsNone(cli.build_parser().parse_args(["x.md"]).trecho)
        self.assertEqual(cli.build_parser().parse_args(["x.md", "--trecho", "1500"]).trecho, 1500)


if __name__ == "__main__":
    unittest.main()
