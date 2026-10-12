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
        choice = EngineChoice(channel="gemini-pago")
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

    def test_button_says_the_engine_and_the_llm_one_announces_the_confirmation(self) -> None:
        engine = EngineSession()
        self.assertEqual(engine.button_label(), "Anonimizar lote · motor local")
        self.assertEqual(engine.button_icon(), "play_arrow")
        choice = EngineChoice(channel="vertex-sp", project="projeto-ficticio")
        engine.choice = choice
        engine.decision = check_engine(choice, missing_fn=lambda _c: [], adc_fn=lambda: True)
        self.assertTrue(engine.active)
        self.assertIn("LLM", engine.button_label())
        self.assertIn("vertex-sp", engine.button_label())
        self.assertTrue(engine.button_label().endswith("…"))
        self.assertEqual(engine.button_icon(), "cloud_upload")
        engine.back_to_local()
        self.assertEqual(engine.button_label(), "Anonimizar lote · motor local")

    def test_choosing_the_engine_does_not_arm_a_permit(self) -> None:
        engine = EngineSession()
        choice = EngineChoice(channel="gemini-pago")
        engine.choice = choice
        engine.decision = check_engine(
            choice,
            env={"ANONIMIZADOR_GEMINI_PAGO_CHAVE": FAKE},
            vault=FakeVault(),
            missing_fn=lambda _c: [],
        )
        self.assertTrue(engine.active)
        self.assertIsNone(engine.last_permit)  # nada autorizado: a confirmacao e por rodada

    def test_local_llm_has_no_confirmation_and_says_so_on_the_button(self) -> None:
        engine = EngineSession()
        choice = EngineChoice(channel="ollama-local")
        engine.choice = choice
        engine.decision = check_engine(choice, env={}, missing_fn=lambda _c: [])
        self.assertTrue(engine.active)
        self.assertTrue(engine.local_llm)
        self.assertIn("LLM local", engine.button_label())
        self.assertFalse(engine.button_label().endswith("…"))
        self.assertEqual(engine.button_icon(), "memory")
        self.assertIsNone(engine.last_permit)
        # Os remotos seguem com a confirmacao.
        remote = EngineSession()
        remote.choice = EngineChoice(channel="vertex-sp", project="projeto-ficticio")
        remote.decision = check_engine(remote.choice, missing_fn=lambda _c: [], adc_fn=lambda: True)
        self.assertFalse(remote.local_llm)
        self.assertTrue(remote.button_label().endswith("…"))

    def test_run_flow_skips_the_confirmation_dialog_for_the_local_channel(self) -> None:
        import inspect

        from anonimizador_app import ui_llm

        source = inspect.getsource(ui_llm.run_llm_flow)
        self.assertIn("if local:", source)
        self.assertLess(source.index("if local:"), source.index("await confirm_send("))

    def test_session_of_the_app_starts_local(self) -> None:
        from anonimizador_app.ui import Session

        self.assertFalse(Session().engine.active)


if __name__ == "__main__":
    unittest.main()
