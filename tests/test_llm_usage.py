"""H6: uso do provedor (tokens, respostas cortadas) e nivel de raciocinio. Sem rede: cliente do SDK falso."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from anonimizador_app.llm_discovery import (
    DEFAULT_THINKING,
    LlmConfig,
    UsageMeter,
    install_meter,
)


def _response(prompt=1000, output=200, thoughts=300, finish="STOP"):
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=output,
            thoughts_token_count=thoughts,
            cached_content_token_count=0,
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
    )


class FakeModels:
    def __init__(self, *, reject_thinking=False, fail=False, finish="STOP") -> None:
        self.configs: list[dict] = []
        self.reject_thinking = reject_thinking
        self.fail = fail
        self.finish = finish

    def generate_content(self, *, model, contents, config=None):
        self.configs.append(dict(config or {}))
        if self.fail:
            raise RuntimeError("503 UNAVAILABLE")
        if self.reject_thinking and "thinking_config" in (config or {}):
            raise RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported for this model")
        return _response(finish=self.finish)


class FakeModel:
    """O modelo do LangExtract: guarda o cliente do SDK em ``_client``."""

    def __init__(self, models: FakeModels) -> None:
        self._client = SimpleNamespace(models=models, _api_client="cliente-interno", _debug_config="cfg")


def _call(model: FakeModel) -> None:
    model._client.models.generate_content(model="m", contents="trecho", config={"temperature": 0.0})


class UsageMeterTests(unittest.TestCase):
    def test_tokens_are_summed_and_the_thinking_level_is_sent(self) -> None:
        models = FakeModels()
        model, meter = FakeModel(models), UsageMeter()
        install_meter(model, meter, "baixo")
        _call(model)
        _call(model)

        self.assertEqual(models.configs[0]["thinking_config"], {"thinking_level": "LOW"})
        self.assertEqual(models.configs[0]["temperature"], 0.0)
        self.assertEqual((meter.calls, meter.prompt_tokens, meter.output_tokens, meter.thinking_tokens), (2, 2000, 400, 600))
        # As conferencias do portao continuam lendo o cliente original.
        self.assertEqual(model._client._api_client, "cliente-interno")
        self.assertEqual(model._client._debug_config, "cfg")

    def test_default_level_sends_nothing(self) -> None:
        models = FakeModels()
        model = FakeModel(models)
        install_meter(model, UsageMeter(), "padrao")
        _call(model)
        self.assertNotIn("thinking_config", models.configs[0])

    def test_a_model_that_rejects_the_level_falls_back_to_its_default(self) -> None:
        models = FakeModels(reject_thinking=True)
        model, meter = FakeModel(models), UsageMeter()
        install_meter(model, meter, "minimo")
        _call(model)
        _call(model)

        self.assertTrue(meter.thinking_rejected)
        self.assertEqual(meter.calls, 2)
        self.assertEqual(meter.failed, 0)
        # Depois da recusa, nao insiste: a segunda chamada ja vai sem o nivel.
        self.assertEqual([("thinking_config" in item) for item in models.configs], [True, False, False])
        self.assertIn("recusado pelo modelo", meter.lines(LlmConfig(thinking="minimo"))[0])

    def test_failures_are_counted_and_raised(self) -> None:
        model, meter = FakeModel(FakeModels(fail=True)), UsageMeter()
        install_meter(model, meter, "baixo")
        with self.assertRaises(RuntimeError):
            _call(model)
        self.assertEqual((meter.calls, meter.failed), (1, 1))

    def test_truncated_answers_are_an_alert(self) -> None:
        model, meter = FakeModel(FakeModels(finish="MAX_TOKENS")), UsageMeter()
        install_meter(model, meter, "baixo")
        _call(model)

        lines = meter.lines(LlmConfig())
        self.assertEqual(meter.truncated, 1)
        self.assertTrue(any(line.startswith("ALERTA") and "cortada" in line for line in lines))

    def test_no_call_says_everything_came_from_the_cache(self) -> None:
        self.assertIn("nenhuma chamada", UsageMeter().lines(LlmConfig())[0])

    def test_the_default_level_is_low(self) -> None:
        self.assertEqual(DEFAULT_THINKING, "baixo")
        self.assertEqual(LlmConfig().thinking, "baixo")


class AuditUsageTests(unittest.TestCase):
    def test_the_audit_carries_the_usage_lines_and_no_text(self) -> None:
        import tempfile
        from pathlib import Path

        from anonimizador_app.pipeline_llm import run_llm_job
        from test_pipeline_llm import DOC, FakeDiscoverer, FakeLocalEngine

        class MeteredFake(FakeDiscoverer):
            def usage_lines(self) -> list[str]:
                return ["Uso do provedor (modelo-teste, raciocinio: baixo): 3 chamada(s), 0 com erro"]

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text(DOC, encoding="utf-8")
            result = run_llm_job(
                [source],
                export_root=base / "exp",
                private_root=base / "priv",
                discoverer=MeteredFake(),
                use_cache=False,
                fallback_engine=FakeLocalEngine([]),
                verification_rounds=0,
            )
            audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("Uso do provedor (modelo-teste, raciocinio: baixo): 3 chamada(s)", audit)


if __name__ == "__main__":
    unittest.main()
