"""H8: o LeNER-Br como segundo detector do app, pelo mesmo LocalNerEngine do GLiNER. ``transformers`` simulado."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from anonimizador_app import contextual, local_ner
from anonimizador_app.settings import AppSettings, load_settings, save_settings

TEXTO = (
    "O sócio OLAVO RICARDO TEIXEIRA foi citado. A empresa Alfaville Metais Ltda não pagou.\n"
    "Consta que Marlene Oliveira Sampaio reside em Joinville.\n"
)


class FakePipe:
    """O pipeline de token-classification: acha nomes conhecidos; estoura em texto longo, como o BERT."""

    def __init__(self, *, max_chars: int | None = None) -> None:
        self.max_chars = max_chars
        self.calls: list[int] = []

    def __call__(self, text: str) -> list[dict]:
        self.calls.append(len(text))
        if self.max_chars is not None and len(text) > self.max_chars:
            raise RuntimeError("The expanded size of the tensor (700) must match the existing size (512) at non-singleton dimension 1")
        found = []
        for group, value in (("PESSOA", "OLAVO RICARDO TEIXEIRA"), ("PESSOA", "Marlene Oliveira Sampaio"),
                             ("ORGANIZACAO", "Alfaville Metais Ltda"), ("LOCAL", "Joinville")):
            start = text.find(value)
            if start >= 0:
                found.append({"entity_group": group, "word": value, "score": 0.97, "start": start, "end": start + len(value)})
        return found


def _fake_transformers(pipe: FakePipe):
    module = types.ModuleType("transformers")
    module.pipeline = lambda *args, **kwargs: pipe  # noqa: ARG005
    return module


class LenerPredictTests(unittest.TestCase):
    def test_loads_lazily_and_maps_lener_labels(self) -> None:
        pipe = FakePipe()
        with mock.patch.dict(sys.modules, {"transformers": _fake_transformers(pipe)}):
            predict = local_ner.load_lener_predict()
            self.assertEqual(pipe.calls, [])  # nada carregado antes da primeira chamada
            items = predict(TEXTO)
        self.assertEqual({local_ner._map_label(item) for item in items}, {"PERSON", "ORGANIZATION", "LOCATION"})
        self.assertEqual(pipe.calls, [len(TEXTO)])

    def test_a_piece_over_the_token_limit_is_split_until_it_fits(self) -> None:
        pipe = FakePipe(max_chars=60)
        with mock.patch.dict(sys.modules, {"transformers": _fake_transformers(pipe)}):
            items = local_ner.load_lener_predict()(TEXTO)
        words = {item["word"] for item in items}
        self.assertEqual(words, {"OLAVO RICARDO TEIXEIRA", "Marlene Oliveira Sampaio", "Alfaville Metais Ltda", "Joinville"})
        # O trecho inteiro estoura, as metades sao tentadas e repartidas ate caber: varias chamadas, as finais pequenas.
        self.assertGreater(len(pipe.calls), 1)
        self.assertTrue(any(size <= 60 for size in pipe.calls))

    def test_other_errors_are_not_swallowed(self) -> None:
        class Broken(FakePipe):
            def __call__(self, text: str) -> list[dict]:
                raise RuntimeError("modelo ausente")

        with mock.patch.dict(sys.modules, {"transformers": _fake_transformers(Broken())}):
            with self.assertRaises(RuntimeError):
                local_ner.load_lener_predict()("texto")


class EngineWiringTests(unittest.TestCase):
    def _gliner_predict(self, text: str):
        # O "GLiNER" do teste so acha o socio; a socia e a empresa ficam para o LeNER.
        start = text.find("OLAVO RICARDO TEIXEIRA")
        return [{"entity_group": "pessoa", "word": "OLAVO RICARDO TEIXEIRA", "score": 0.9}] if start >= 0 else []

    def test_extra_ner_adds_the_lener_engine_in_union_with_gliner(self) -> None:
        pipe = FakePipe()
        settings = AppSettings(extra_ner="lener", cnpj_lookup_enabled=False)
        with mock.patch.dict(sys.modules, {"transformers": _fake_transformers(pipe)}), \
             mock.patch.object(local_ner, "load_local_predict", lambda: self._gliner_predict):
            engine = contextual.build_contextual_engine(settings, use_ner_cache=False)
            found = {(span.entity_type, span.value_from(TEXTO)) for span in engine.detect(TEXTO)}
        self.assertIn(("PERSON", "OLAVO RICARDO TEIXEIRA"), found)
        self.assertIn(("PERSON", "Marlene Oliveira Sampaio"), found)
        self.assertIn(("ORGANIZATION", "Alfaville Metais Ltda"), found)
        self.assertGreaterEqual(len(pipe.calls), 1)

    def test_without_extra_ner_the_lener_is_not_even_imported(self) -> None:
        settings = AppSettings(cnpj_lookup_enabled=False)
        with mock.patch.dict(sys.modules, {"transformers": None}), \
             mock.patch.object(local_ner, "load_local_predict", lambda: self._gliner_predict):
            engine = contextual.build_contextual_engine(settings, use_ner_cache=False)
            found = {span.value_from(TEXTO) for span in engine.detect(TEXTO)}
        self.assertIn("OLAVO RICARDO TEIXEIRA", found)
        self.assertNotIn("Marlene Oliveira Sampaio", found)

    def test_the_two_detectors_never_share_a_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gliner = local_ner.LocalNerEngine(predict=self._gliner_predict, cache_dir=Path(tmp), model_label="gliner:teste")
            lener = local_ner.LocalNerEngine(predict=self._gliner_predict, cache_dir=Path(tmp), model_label="lener:teste")
            self.assertNotEqual(gliner._cache_key(TEXTO), lener._cache_key(TEXTO))


class SettingsTests(unittest.TestCase):
    def test_extra_ner_round_trips_and_defaults_off(self) -> None:
        self.assertEqual(AppSettings().extra_ner, "")
        with tempfile.TemporaryDirectory() as tmp:
            save_settings(Path(tmp), AppSettings(extra_ner="lener"))
            self.assertEqual(load_settings(Path(tmp)).extra_ner, "lener")
            self.assertEqual(load_settings(Path(tmp) / "nada").extra_ner, "")


if __name__ == "__main__":
    unittest.main()
