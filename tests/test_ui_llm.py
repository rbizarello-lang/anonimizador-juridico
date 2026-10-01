"""Estado do motor na interface: sempre local ao abrir, nunca gravado."""

from __future__ import annotations

import unittest

from anonimizador_app.llm_engine import EngineChoice, check_engine
from anonimizador_app.ui_llm import EngineSession
from test_credentials import FAKE, FakeVault


class EngineSessionTests(unittest.TestCase):
    def test_a_new_session_is_local(self) -> None:
        engine = EngineSession()
        self.assertFalse(engine.active)
        self.assertEqual(engine.choice, EngineChoice())
        self.assertIsNone(engine.decision)

    def test_active_only_with_an_allowed_decision_and_back_to_local_resets(self) -> None:
        engine = EngineSession()
        choice = EngineChoice(channel="gemini-pago", acknowledged=True, billing_confirmed=True)
        engine.choice = choice
        engine.decision = check_engine(
            choice,
            env={"ANONIMIZADOR_GEMINI_PAGO_CHAVE": FAKE},
            vault=FakeVault(),
            missing_fn=lambda _c: [],
        )
        self.assertTrue(engine.active)
        engine.back_to_local()
        self.assertFalse(engine.active)
        self.assertEqual(engine.choice.channel, "")

    def test_blocked_decision_is_not_active(self) -> None:
        engine = EngineSession()
        engine.decision = check_engine(EngineChoice(channel="gemini-gratuito"))
        self.assertFalse(engine.active)

    def test_session_of_the_app_starts_local(self) -> None:
        from anonimizador_app.ui import Session

        self.assertFalse(Session().engine.active)


if __name__ == "__main__":
    unittest.main()
