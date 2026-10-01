"""Chave do provedor: onde e lida, e que nunca aparece. Cofre falso: o real nao e tocado."""

from __future__ import annotations

import copy
import pickle
import unittest

from anonimizador_app.credentials import (
    ENV_VAR,
    SOURCE_ENV,
    SOURCE_VAULT,
    VAULT_SERVICE,
    VAULT_USER,
    ApiKey,
    CredentialsError,
    delete_key,
    describe_source,
    load_key,
    store_key,
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


if __name__ == "__main__":
    unittest.main()
