"""I-N3: a queda do GLiNER para o spaCy nunca e silenciosa e nunca contamina o cache do GLiNER.

Sem modelo real: ``_load_gliner`` e ``_load_spacy_pt`` sao trocados por funcoes falsas. Dados inventados.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import anonimizador_app.local_ner as local_ner
from anonimizador_app.local_ner import LocalNerEngine, NerStatus
from anonimizador_app.pipeline import run_job
from anonimizador_app.settings import AppSettings

CORPUS = Path(__file__).parent / "corpus" / "caso_001_processo_sintetico.md"
TEXT = "Helena Ficticia da Silva moveu a acao. Uniao permanece."


def _predict(text: str):
    hits = []
    if "Helena Ficticia da Silva" in text:
        hits.append({"entity_group": "pessoa", "word": "Helena Ficticia da Silva", "score": 0.91})
    return hits


def _state(**extra):
    """Zera o estado do modulo (o que ja foi carregado e a sondagem) para cada cenario."""

    patches = {"_ACTIVE_STATUS": None, "_PROBED_STATUS": None, "_ACTIVE_BACKEND": "", "_NER_USED": False}
    patches.update(extra)
    return [mock.patch.object(local_ner, name, value) for name, value in patches.items()]


class _Scenario(unittest.TestCase):
    def setUp(self) -> None:
        self._patches = _state()
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def spacy_only(self, calls: list[str] | None = None):
        """GLiNER ausente: o carregamento falha e cai para o spaCy falso."""

        def spacy_predict():
            if calls is not None:
                calls.append("spacy")
            return _predict

        patches = [
            mock.patch.object(local_ner, "_gliner_unavailable_reason", return_value="modelo ausente (teste)"),
            mock.patch.object(local_ner, "_load_gliner", side_effect=OSError("modelo ausente")),
            mock.patch.object(local_ner, "_load_spacy_pt", side_effect=spacy_predict),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def gliner_ok(self, calls: list[str] | None = None):
        def gliner_predict():
            if calls is not None:
                calls.append("gliner")
            return _predict

        patches = [
            mock.patch.object(local_ner, "_gliner_unavailable_reason", return_value=None),
            mock.patch.object(local_ner, "_load_gliner", side_effect=gliner_predict),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)


class CacheIdentityTests(_Scenario):
    def test_the_cache_id_carries_backend_model_and_revision(self) -> None:
        self.gliner_ok()
        engine = LocalNerEngine(use_cache=True, cache_dir=Path("."))
        identity = engine._cache_model_id()
        self.assertTrue(identity.startswith("gliner:"))
        self.assertIn(local_ner.GLINER_MODEL, identity)
        self.assertIn(local_ner.GLINER_REVISION, identity)

    def test_spacy_never_shares_a_key_with_gliner(self) -> None:
        self.gliner_ok()
        gliner_key = LocalNerEngine(use_cache=True, cache_dir=Path("."))._cache_key(TEXT)
        with mock.patch.object(local_ner, "_PROBED_STATUS", None):
            self.spacy_only()
            engine = LocalNerEngine(use_cache=True, cache_dir=Path("."))
            self.assertTrue(engine._cache_model_id().startswith("spacy_pt:"))
            self.assertNotEqual(engine._cache_key(TEXT), gliner_key)

    def test_spacy_results_are_written_under_the_spacy_key_and_not_served_to_gliner(self) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            self.spacy_only(calls)
            engine = LocalNerEngine(use_cache=True, cache_dir=cache_dir)
            found = engine.detect(TEXT)
            self.assertTrue(found)
            self.assertEqual(calls, ["spacy"])
            saved = [json.loads(path.read_text(encoding="utf-8")) for path in cache_dir.glob("*.json")]
            self.assertEqual(len(saved), 1)
            self.assertTrue(saved[0]["modelo"].startswith("spacy_pt:"))
            self.assertNotIn(local_ner.GLINER_REVISION, saved[0]["modelo"])
            # Mesmo processo, mesma maquina: o proximo detect acerta o cache do spaCy (sem recarregar).
            self.assertEqual(len(LocalNerEngine(use_cache=True, cache_dir=cache_dir).detect(TEXT)), len(found))
            self.assertEqual(calls, ["spacy"])

        # Depois de instalar o GLiNER (outro processo, modelo disponivel): o cache do spaCy nao serve.
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            self.spacy_only()
            LocalNerEngine(use_cache=True, cache_dir=cache_dir).detect(TEXT)
            for patch in self._patches:
                patch.stop()
            self._patches = _state()
            for patch in self._patches:
                patch.start()
            calls_gliner: list[str] = []
            with (
                mock.patch.object(local_ner, "_gliner_unavailable_reason", return_value=None),
                mock.patch.object(
                    local_ner,
                    "_load_gliner",
                    side_effect=lambda: (calls_gliner.append("gliner"), _predict)[1],
                ),
            ):
                LocalNerEngine(use_cache=True, cache_dir=cache_dir).detect(TEXT)
            self.assertEqual(calls_gliner, ["gliner"])
            self.assertEqual(len(list(cache_dir.glob("*.json"))), 2)

    def test_a_gliner_that_looks_available_but_fails_to_load_is_not_saved_under_its_key(self) -> None:
        # A sondagem diz "disponivel", o carregamento falha e cai para o spaCy: a chave e refeita.
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            patches = [
                mock.patch.object(local_ner, "_gliner_unavailable_reason", return_value=None),
                mock.patch.object(local_ner, "_load_gliner", side_effect=OSError("falhou ao carregar")),
                mock.patch.object(local_ner, "_load_spacy_pt", return_value=_predict),
            ]
            for patch in patches:
                patch.start()
                self.addCleanup(patch.stop)
            LocalNerEngine(use_cache=True, cache_dir=cache_dir).detect(TEXT)
            saved = [json.loads(path.read_text(encoding="utf-8")) for path in cache_dir.glob("*.json")]
            self.assertEqual([item["modelo"].split(":")[0] for item in saved], ["spacy_pt"])


class AuditAndWarningTests(_Scenario):
    def _run(self, root: Path):
        engine = LocalNerEngine(use_cache=False)
        return run_job(
            [CORPUS],
            export_root=root / "exportaveis",
            private_root=root / "privados",
            job_id="lote_ner",
            contextual=engine,
            settings=AppSettings(local_ner_enabled=True),
        )

    def test_fallback_is_recorded_in_the_audit_the_guide_and_the_warning(self) -> None:
        self.spacy_only()
        with tempfile.TemporaryDirectory() as directory:
            job = self._run(Path(directory))
            audit = (job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            guide = (job.export_dir / "LEIA-ME.txt").read_text(encoding="utf-8")
        self.assertIn("Motor de NER local: spaCy PT", audit)
        self.assertIn("fallback_error: GLiNER indisponivel", audit)
        self.assertIn("GLiNER", job.export_warning)
        self.assertIn("spaCy", job.export_warning)
        self.assertIn("ATENCAO: O GLiNER nao esta disponivel", guide)
        self.assertTrue(job.export_allowed)  # aviso, nao bloqueio

    def test_gliner_available_has_no_warning_and_the_audit_names_the_revision(self) -> None:
        self.gliner_ok()
        with tempfile.TemporaryDirectory() as directory:
            job = self._run(Path(directory))
            audit = (job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            guide = (job.export_dir / "LEIA-ME.txt").read_text(encoding="utf-8")
        self.assertIn("Motor de NER local: GLiNER", audit)
        self.assertIn(local_ner.GLINER_REVISION[:12], audit)
        self.assertNotIn("fallback_error", audit)
        self.assertNotIn("GLiNER nao esta disponivel", job.export_warning)
        self.assertNotIn("GLiNER nao esta disponivel", guide)

    def test_a_batch_that_did_not_use_the_local_ner_says_nothing_about_it(self) -> None:
        self.spacy_only()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = run_job(
                [CORPUS],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_sem_ner",
                contextual=_NoNer(),
                settings=AppSettings(local_ner_enabled=False),
            )
            audit = (job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertNotIn("Motor de NER local", audit)
        self.assertNotIn("fallback_error", audit)
        self.assertNotIn("spaCy", job.export_warning)

    def test_status_helpers(self) -> None:
        status = NerStatus("spacy_pt", "modelo", "1.0", "GLiNER indisponivel: x")
        self.assertTrue(status.fallback)
        self.assertEqual(status.identity, "spacy_pt:modelo@1.0")
        self.assertFalse(NerStatus("gliner", "m", "r").fallback)


class _NoNer:
    """Motor que nao usa o NER local (so o contrato ``detect``/``scan_residual``)."""

    provider = "teste"

    def detect(self, text: str):
        return []

    def scan_residual(self, text: str):
        return []


if __name__ == "__main__":
    unittest.main()
