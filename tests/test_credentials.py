"""Chave do provedor: onde e lida, e que nunca aparece. Cofre falso: o real nao e tocado."""

from __future__ import annotations

import copy
import json
import pickle
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.credentials import (
    ENV_VAR,
    ENV_VERTEX_CREDENTIAL,
    ENV_VERTEX_PROJECT,
    SOURCE_GCLOUD,
    SOURCE_SERVICE_ACCOUNT,
    VERTEX_CREDENTIAL_USER,
    VERTEX_PROJECT_USER,
    VERTEX_VAULT_SERVICE,
    SOURCE_ENV,
    SOURCE_VAULT,
    VAULT_SERVICE,
    VAULT_USER,
    ApiKey,
    CredentialsError,
    delete_key,
    delete_vertex_setup,
    describe_source,
    describe_vertex,
    load_key,
    load_vertex_setup,
    store_key,
    store_vertex_setup,
)
from anonimizador_app.llm_discovery import LlmConfig

FAKE = "chave-ficticia-0000-aaaa-bbbb"


class FakeVault:
    def __init__(self, stored: dict | None = None, *, broken: bool = False) -> None:
        self.stored = dict(stored or {})
        self.broken = broken
        self.calls: list[tuple] = []

    def get_password(self, service, username):
        self.calls.append(("get", service, username))
        if self.broken:
            raise RuntimeError("sem backend")
        return self.stored.get((service, username))

    def set_password(self, service, username, password):
        self.calls.append(("set", service, username))
        if self.broken:
            raise RuntimeError(f"falha com {password}")
        self.stored[(service, username)] = password

    def delete_password(self, service, username):
        self.calls.append(("delete", service, username))
        if self.broken:
            raise RuntimeError("sem backend")
        self.stored.pop((service, username), None)


class LoadTests(unittest.TestCase):
    def test_env_var_comes_first(self) -> None:
        vault = FakeVault({(VAULT_SERVICE, VAULT_USER): "do-cofre"})
        key = load_key(env={ENV_VAR: f"  {FAKE} "}, vault=vault)
        self.assertEqual(key.reveal(), FAKE)
        self.assertEqual(key.source, SOURCE_ENV)
        self.assertEqual(vault.calls, [])

    def test_vault_is_the_second_source(self) -> None:
        vault = FakeVault({(VAULT_SERVICE, VAULT_USER): FAKE})
        key = load_key(env={}, vault=vault)
        self.assertEqual(key.reveal(), FAKE)
        self.assertEqual(key.source, SOURCE_VAULT)
        self.assertEqual(vault.calls, [("get", VAULT_SERVICE, VAULT_USER)])

    def test_generic_google_variables_are_deliberately_ignored(self) -> None:
        env = {"GEMINI_API_KEY": FAKE, "GOOGLE_API_KEY": FAKE, "LANGEXTRACT_API_KEY": FAKE}
        self.assertIsNone(load_key(env=env, vault=FakeVault()))

    def test_empty_values_are_absent(self) -> None:
        self.assertIsNone(load_key(env={ENV_VAR: "   "}, vault=FakeVault({(VAULT_SERVICE, VAULT_USER): " "})))

    def test_broken_vault_counts_as_no_key(self) -> None:
        self.assertIsNone(load_key(env={}, vault=FakeVault(broken=True)))


class StoreTests(unittest.TestCase):
    def test_store_and_delete_round_trip(self) -> None:
        vault = FakeVault()
        stored = store_key(f" {FAKE}\n", vault=vault)
        self.assertEqual(stored.source, SOURCE_VAULT)
        self.assertEqual(load_key(env={}, vault=vault).reveal(), FAKE)
        self.assertTrue(delete_key(vault=vault))
        self.assertIsNone(load_key(env={}, vault=vault))
        self.assertFalse(delete_key(vault=vault))

    def test_empty_key_is_refused(self) -> None:
        vault = FakeVault()
        with self.assertRaises(CredentialsError):
            store_key("  ", vault=vault)
        self.assertEqual(vault.stored, {})

    def test_failure_message_never_carries_the_value(self) -> None:
        with self.assertRaises(CredentialsError) as caught:
            store_key(FAKE, vault=FakeVault(broken=True))
        self.assertNotIn(FAKE, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)


class HiddenValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = ApiKey(FAKE, SOURCE_VAULT)

    def test_value_is_in_no_textual_form(self) -> None:
        config = LlmConfig(channel="gemini-pago", api_key=self.key)
        forms = [
            repr(self.key),
            str(self.key),
            f"{self.key}",
            f"{self.key!r}",
            "%s" % (self.key,),
            repr(config),
            str(config),
            repr({"chave": self.key}),
            repr([self.key]),
            config.describe(),
        ]
        for text in forms:
            self.assertNotIn(FAKE, text)
        self.assertIn(SOURCE_VAULT, repr(self.key))
        self.assertEqual(describe_source(self.key), "chave encontrada (Gerenciador de Credenciais)")
        self.assertNotIn(FAKE, describe_source(self.key))

    def test_cannot_be_serialized_or_mutated(self) -> None:
        with self.assertRaises(TypeError):
            pickle.dumps(self.key)
        with self.assertRaises(AttributeError):
            self.key.source = "outra"
        with self.assertRaises(AttributeError):
            self.key.extra = 1
        self.assertIs(copy.deepcopy(self.key), self.key)

    def test_reveal_is_the_only_door(self) -> None:
        self.assertEqual(self.key.reveal(), FAKE)

    def test_traceback_of_a_failure_does_not_show_it(self) -> None:
        import traceback

        try:
            raise RuntimeError(f"falha com {self.key}")
        except RuntimeError:
            self.assertNotIn(FAKE, traceback.format_exc())



class VertexSetupTests(unittest.TestCase):
    """Cadastro do Vertex AI (canal vertex-sp): projeto e arquivo de conta de servico; so o CAMINHO."""

    def _service_account(self, folder: Path, **overrides) -> Path:
        payload = {"type": "service_account", "client_email": "conta@projeto-ficticio.iam.example", "private_key": "NAO-LER"}
        payload.update(overrides)
        path = folder / "conta.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_nothing_registered_means_the_gcloud_credential(self) -> None:
        setup = load_vertex_setup(env={}, vault=FakeVault())
        self.assertEqual((setup.project, setup.credential_path), ("", ""))
        self.assertEqual(setup.credential_label, SOURCE_GCLOUD)
        self.assertEqual(describe_vertex(setup), "nenhum projeto cadastrado")

    def test_store_and_load_the_project_and_the_path_never_the_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            account = self._service_account(Path(tmp))
            vault = FakeVault()
            saved = store_vertex_setup(" projeto-ficticio ", f'"{account}"', vault=vault)
            self.assertEqual(saved.project, "projeto-ficticio")
            self.assertEqual(saved.credential_path, str(account))
            self.assertEqual(vault.stored[(VERTEX_VAULT_SERVICE, VERTEX_PROJECT_USER)], "projeto-ficticio")
            self.assertEqual(vault.stored[(VERTEX_VAULT_SERVICE, VERTEX_CREDENTIAL_USER)], str(account))
            # O conteudo do arquivo (a chave privada) nunca passa pelo cofre nem pela descricao.
            self.assertNotIn("NAO-LER", " ".join(vault.stored.values()))
            loaded = load_vertex_setup(env={}, vault=vault)
            self.assertEqual((loaded.project, loaded.credential_path), (saved.project, saved.credential_path))
            self.assertEqual(loaded.credential_label, SOURCE_SERVICE_ACCOUNT)
            self.assertNotIn(str(account), describe_vertex(loaded))
            self.assertNotIn("NAO-LER", describe_vertex(loaded))

    def test_environment_comes_first_like_the_api_key(self) -> None:
        vault = FakeVault({(VERTEX_VAULT_SERVICE, VERTEX_PROJECT_USER): "do-cofre"})
        env = {ENV_VERTEX_PROJECT: "da-variavel", ENV_VERTEX_CREDENTIAL: "caminho.json"}
        setup = load_vertex_setup(env=env, vault=vault)
        self.assertEqual((setup.project, setup.project_source), ("da-variavel", SOURCE_ENV))
        self.assertEqual((setup.credential_path, setup.credential_source), ("caminho.json", SOURCE_ENV))
        self.assertEqual(vault.calls, [])

    def test_invalid_files_and_empty_project_store_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = FakeVault()
            with self.assertRaises(CredentialsError):
                store_vertex_setup("", vault=vault)
            with self.assertRaises(CredentialsError):
                store_vertex_setup("projeto-ficticio", str(Path(tmp) / "nao-existe.json"), vault=vault)
            bad = Path(tmp) / "ruim.json"
            bad.write_text("{nao e json", encoding="utf-8")
            with self.assertRaises(CredentialsError):
                store_vertex_setup("projeto-ficticio", str(bad), vault=vault)
            not_service = self._service_account(Path(tmp), type="authorized_user")
            with self.assertRaises(CredentialsError) as caught:
                store_vertex_setup("projeto-ficticio", str(not_service), vault=vault)
            self.assertNotIn("NAO-LER", str(caught.exception))
            self.assertEqual(vault.stored, {})

    def test_empty_credential_means_gcloud_and_forgets_the_old_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            account = self._service_account(Path(tmp))
            vault = FakeVault()
            store_vertex_setup("projeto-ficticio", str(account), vault=vault)
            store_vertex_setup("projeto-ficticio", "", vault=vault)
        self.assertNotIn((VERTEX_VAULT_SERVICE, VERTEX_CREDENTIAL_USER), vault.stored)
        self.assertEqual(load_vertex_setup(env={}, vault=vault).credential_label, SOURCE_GCLOUD)

    def test_delete_and_broken_vault(self) -> None:
        vault = FakeVault({(VERTEX_VAULT_SERVICE, VERTEX_PROJECT_USER): "p-ficticio"})
        self.assertTrue(delete_vertex_setup(vault=vault))
        self.assertFalse(delete_vertex_setup(vault=vault))
        broken = FakeVault(broken=True)
        self.assertEqual(load_vertex_setup(env={}, vault=broken).project, "")  # cofre com defeito = ausente
        with self.assertRaises(CredentialsError) as caught:
            store_vertex_setup("projeto-ficticio", vault=broken)
        self.assertNotIn("projeto-ficticio", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
