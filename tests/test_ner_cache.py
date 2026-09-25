from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.local_ner import LocalNerEngine
from anonimizador_app.ner_cache import cache_key, load_spans, save_spans


def _predict_helena(text: str):
    hits = []
    if "Helena Ficticia da Silva" in text:
        hits.append(
            {"entity_group": "pessoa", "word": "Helena Ficticia da Silva", "score": 0.91}
        )
    return hits


class NerCacheTests(unittest.TestCase):
    def test_miss_then_hit_skips_second_predict(self) -> None:
        calls = {"n": 0}

        def predict(text: str):
            calls["n"] += 1
            return _predict_helena(text)

        text = "Helena Ficticia da Silva moveu a acao. Uniao permanece."
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            engine = LocalNerEngine(
                predict=predict, use_cache=True, cache_dir=cache_dir
            )
            first = engine.detect(text)
            self.assertEqual(calls["n"], 1)
            self.assertTrue(first)
            self.assertTrue(list(cache_dir.glob("*.json")))
            second = engine.detect(text)
            self.assertEqual(calls["n"], 1)
            self.assertEqual(
                [(s.start, s.end, s.entity_type, s.recognizer) for s in first],
                [(s.start, s.end, s.entity_type, s.recognizer) for s in second],
            )
            self.assertEqual(text[second[0].start : second[0].end], "Helena Ficticia da Silva")

    def test_threshold_change_invalidates_cache(self) -> None:
        text = "Helena Ficticia da Silva moveu a acao."
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            first = LocalNerEngine(
                predict=_predict_helena,
                threshold=0.75,
                use_cache=True,
                cache_dir=cache_dir,
            )
            first.detect(text)
            second = LocalNerEngine(
                predict=_predict_helena,
                threshold=0.90,
                use_cache=True,
                cache_dir=cache_dir,
            )
            key_a = first._cache_key(text)
            key_b = second._cache_key(text)
            self.assertNotEqual(key_a, key_b)
            self.assertIsNotNone(load_spans(cache_dir, key_a))
            self.assertIsNone(load_spans(cache_dir, key_b))
            second.detect(text)
            self.assertEqual(len(list(cache_dir.glob("*.json"))), 2)

    def test_cached_residual_does_not_rerun_gliner(self) -> None:
        calls = {"n": 0}

        def predict(text: str):
            calls["n"] += 1
            return _predict_helena(text)

        original = "Helena Ficticia da Silva moveu a acao."
        tokenized = "[PESSOA_1] moveu a acao. Helena Ficticia da Silva restou."
        with tempfile.TemporaryDirectory() as directory:
            engine = LocalNerEngine(
                predict=predict, use_cache=True, cache_dir=Path(directory)
            )
            engine.detect(original)
            self.assertEqual(calls["n"], 1)
            first = engine.scan_residual(tokenized)
            self.assertEqual(calls["n"], 1)
            self.assertEqual(first, [])
            second = engine.scan_residual(tokenized)
            self.assertEqual(calls["n"], 1)
            self.assertEqual(second, [])

    def test_disabled_cache_does_not_write(self) -> None:
        text = "Helena Ficticia da Silva moveu a acao."
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            engine = LocalNerEngine(
                predict=_predict_helena, use_cache=False, cache_dir=cache_dir
            )
            engine.detect(text)
            self.assertEqual(list(cache_dir.glob("*.json")), [])

    def test_roundtrip_payload_has_span_fields(self) -> None:
        from anonimizador_juridico import EntitySpan

        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            text = "ABCDE"
            span = EntitySpan(
                start=0, end=5, entity_type="PERSON", score=0.72, recognizer="NerLocal"
            )
            key = cache_key(
                text,
                model="predict-custom",
                threshold=0.75,
                labels=("pessoa",),
                shadow_case=False,
                chunk_chars=1200,
                chunk_overlap=100,
            )
            save_spans(
                cache_dir,
                key,
                [span],
                text=text,
                model="predict-custom",
                threshold=0.75,
                labels=("pessoa",),
                shadow_case=False,
                chunk_chars=1200,
                chunk_overlap=100,
            )
            payload = json.loads((cache_dir / f"{key}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["spans"][0]["text"], "ABCDE")
            loaded = load_spans(cache_dir, key)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded[0].recognizer, "NerLocal")


if __name__ == "__main__":
    unittest.main()
