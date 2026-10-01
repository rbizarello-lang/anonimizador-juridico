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
from test_pipeline_llm import DOC, FakeChannelDiscoverer

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

    def test_missing_confirmations_block_and_the_warning_is_printed(self) -> None:
        code, _out, err = _run(
            [str(self.source), "--canal", "gemini-pago", *self.roots],
            env={ENV_VAR: FAKE},
            vault=FakeVault(),
        )
        self.assertEqual(code, 2)
        self.assertIn("nomes, endereços, fatos e valores seguem em claro", err)
        self.assertIn("aviso foi lido", err)
        self.assertIn("faturamento", err)
        self.assertNotIn(FAKE, err)

    def test_verify_reports_without_sending(self) -> None:
        code, out, _err = _run(
            ["--verificar", "--canal", "gemini-pago", "--ciente", "--faturamento-confirmado", *self.roots],
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
        argv = [
            str(self.source), "--canal", "gemini-pago", "--ciente", "--faturamento-confirmado", *self.roots,
        ]
        with patch.object(cli, "build_discoverer", lambda *a, **k: FakeChannelDiscoverer()):
            code, out, err = _run(argv, env={ENV_VAR: FAKE}, vault=FakeVault())
        self.assertEqual(code, 0, err)
        self.assertIn("Motor: LLM gemini-pago", out)
        folder = next((self.base / "exp").iterdir())
        audit = (folder / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("motor LLM: gemini-pago", audit)
        self.assertIn("Chave lida de: variavel de ambiente", audit)
        self.assertIn("Aviso (versao", audit)
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


class OfflineCliTests(_Workspace):
    def test_offline_needs_the_channel(self) -> None:
        code, _out, err = _run([str(self.source), "--sem-rede", *self.roots])
        self.assertEqual(code, 2)
        self.assertIn("--canal", err)

    def test_offline_with_empty_cache_blocks_the_export_and_opens_no_socket(self) -> None:
        with block_network() as attempts:
            code, out, err = _run(
                [str(self.source), "--canal", "gemini-pago", "--sem-rede", *self.roots],
                vault=FakeVault(),
            )
        self.assertEqual(attempts, [])
        self.assertEqual(code, 1)
        self.assertIn("NAO ENVIE AINDA", err)
        self.assertIn("Motor: LLM gemini-pago", out)
        folder = next((self.base / "exp").iterdir())
        audit = (folder / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("Modo sem rede", audit)
        self.assertIn("motor LLM: gemini-pago", audit)


if __name__ == "__main__":
    unittest.main()
