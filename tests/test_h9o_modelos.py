"""H9-o: modelos maiores no Ollama local (lista de modelos, prazo, cache por modelo, nova tentativa), linhas novas
da AUDITORIA, botao com o motor e estimativa de tempo. Nenhum teste abre rede real (so o Ollama FALSO no loopback).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.llm_discovery import (
    OLLAMA_DEFAULT_NUM_CTX,
    PROMPT_VERSION,
    LangExtractDiscoverer,
    LlmConfig,
    OllamaModel,
    is_connection_error,
    is_timeout_error,
    list_ollama_models,
    model_billions,
    ollama_seconds_per_chunk,
    ollama_timeout_for,
    split_chunks,
)
from anonimizador_app.llm_engine import EngineChoice, audit_lines, check_engine, local_time_estimate
from anonimizador_app.llm_gate import FilePlan, SendPlan
from anonimizador_app.run_notes import (
    disabled_tag_lines,
    operation_free_lines,
    review_record_lines,
    run_note_lines,
)
from anonimizador_app.settings import AppSettings, case_item_key, default_enabled_tags
from fake_ollama import FakeOllama

HAS_LANGEXTRACT = importlib.util.find_spec("langextract") is not None

TAGS = (
    {"name": "gemma4:e4b", "size": 6_583_000_000, "digest": "c6eb396dbd59" + "0" * 52,
     "details": {"parameter_size": "7.5B", "quantization_level": "Q4_K_M"}},
    {"name": "gemma4:12b", "size": 8_020_000_000, "digest": "abc", "details": {"parameter_size": "11.9B"}},
    {"name": "qwen3:14b", "size": 9_280_000_000, "details": {"parameter_size": "14.8B"}},
    {"name": "gpt-oss:120b-cloud", "size": 384, "details": {}},
    {"name": "deepseek-v3.1:671b-cloud", "size": 384, "remote_host": "https://ollama.com:443", "details": {}},
    {"name": "minimax-m2", "size": 384, "remote_model": "minimax-m2", "remote_host": "https://ollama.com:443"},
    {"name": "x:cloud", "size": 1},
)


def _local(model_id: str = "gemma4:e4b", **changes) -> LlmConfig:
    values = dict(provider="ollama", channel="ollama-local", model_id=model_id, chunk_chars=2000, max_workers=1)
    values.update(changes)
    return LlmConfig(**values)


class ModelSizeTests(unittest.TestCase):
    def test_billions_from_the_name_or_the_ollama_detail(self) -> None:
        self.assertEqual(model_billions("gemma4:e4b"), 4)
        self.assertEqual(model_billions("gemma4:12b"), 12)
        self.assertEqual(model_billions("qwen3:14b"), 14)
        self.assertEqual(model_billions("ministral-3:14b"), 14)
        self.assertEqual(model_billions("gemma4:26b-a4b"), 26)
        self.assertEqual(model_billions("llama3.1:8b-instruct-q4_K_M"), 8)
        self.assertIsNone(model_billions("modelo:latest"))
        self.assertEqual(model_billions("modelo:latest", "11.9B"), 11.9)
        self.assertAlmostEqual(model_billions("modelo:latest", "270M"), 0.27)

    def test_timeout_grows_with_the_model_inside_the_limits(self) -> None:
        self.assertEqual(ollama_timeout_for("gemma4:e4b"), 600)
        self.assertEqual(ollama_timeout_for("qwen3:4b"), 600)
        self.assertEqual(ollama_timeout_for("gemma4:12b"), 1800)
        self.assertEqual(ollama_timeout_for("qwen3:14b"), 2100)
        self.assertEqual(ollama_timeout_for("gemma4:26b-a4b"), 3600)
        self.assertEqual(ollama_timeout_for("modelo:latest"), 1800)

    def test_check_engine_puts_the_timeout_of_the_model_in_the_config(self) -> None:
        for model, expected in (("gemma4:e4b", 600), ("gemma4:12b", 1800), ("gemma4:26b-a4b", 3600)):
            decision = check_engine(EngineChoice(channel="ollama-local", model_id=model), env={}, missing_fn=lambda _c: [])
            self.assertTrue(decision.allowed, decision.reasons)
            self.assertEqual(decision.config.timeout, expected)
            self.assertEqual(decision.config.model_id, model)
            self.assertEqual(decision.config.max_workers, 1)
            self.assertGreaterEqual(decision.config.keep_alive, 60)
        remote = check_engine(EngineChoice(channel="vertex-sp", project="p-ficticio"), missing_fn=lambda _c: [],
                              adc_fn=lambda: True)
        self.assertEqual(remote.config.timeout, LlmConfig().timeout)

    def test_cloud_model_stays_refused(self) -> None:
        decision = check_engine(EngineChoice(channel="ollama-local", model_id="gpt-oss:120b-cloud"), env={},
                                missing_fn=lambda _c: [])
        self.assertFalse(decision.allowed)

    def test_audit_lines_say_model_timeout_and_thinking(self) -> None:
        decision = check_engine(EngineChoice(channel="ollama-local", model_id="qwen3:14b"), env={},
                                missing_fn=lambda _c: [])
        text = "\n".join(audit_lines(decision))
        self.assertIn("Modelo: qwen3:14b", text)
        self.assertIn("Prazo por chamada: 2100 s", text)
        self.assertIn("think) desligado", text)


class ListModelsTests(unittest.TestCase):
    def test_lists_installed_models_without_cloud_ones(self) -> None:
        with FakeOllama(tags=TAGS) as server:
            models = list_ollama_models(port=server.port, env={})
            paths = [request["path"] for request in server.requests]
        self.assertEqual([model.name for model in models], ["gemma4:12b", "gemma4:e4b", "qwen3:14b"])
        self.assertEqual(paths, ["/api/tags"])
        e4b = next(model for model in models if model.name == "gemma4:e4b")
        self.assertEqual(e4b.label(), "gemma4:e4b (6,1 GB, 7.5B)")
        self.assertIn("digest c6eb396dbd59", e4b.audit_line())
        self.assertIn("Q4_K_M", e4b.audit_line())

    def test_ollama_down_gives_none(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertIsNone(list_ollama_models(port=port, env={}, timeout=0.5))

    def test_gate_refusal_gives_none_and_opens_nothing(self) -> None:
        with FakeOllama(tags=TAGS) as server:
            result = list_ollama_models(port=server.port, env={"OLLAMA_HOST": "http://10.0.0.5:11434"})
            self.assertEqual(server.requests, [])
        self.assertIsNone(result)
        with FakeOllama(tags=TAGS) as server:
            self.assertIsNone(list_ollama_models(port=server.port, env={"HTTPS_PROXY": "http://proxy:3128"}))
            self.assertEqual(server.requests, [])

    def test_select_options_keep_the_current_model(self) -> None:
        from anonimizador_app.ui_llm import model_options

        options = model_options([OllamaModel("gemma4:12b", 8_020_000_000, "11.9B")], "gemma4:e4b")
        self.assertEqual(options["gemma4:12b"], "gemma4:12b (7,5 GB, 11.9B)")
        self.assertIn("não encontrado", options["gemma4:e4b"])


class CacheKeyTests(unittest.TestCase):
    TEXT = "<!-- pagina-origem: 1 -->\nEXECUTADO: ACME COMÉRCIO DE GRÃOS LTDA, sócio JOÃO DA SILVA SAURO.\n"

    def test_each_model_has_its_own_cache(self) -> None:
        chunk = split_chunks(self.TEXT, 2000)[0]
        with tempfile.TemporaryDirectory() as tmp:
            small = LangExtractDiscoverer(_local("gemma4:e4b"), cache_dir=Path(tmp))
            big = LangExtractDiscoverer(_local("gemma4:12b"), cache_dir=Path(tmp))
            self.assertNotEqual(small._cache_path(chunk), big._cache_path(chunk))
            small._cache_save(chunk, [{"classe": "empresa", "texto": "ACME COMÉRCIO DE GRÃOS LTDA", "atributos": {}}])
            self.assertIsNotNone(small._cache_load(chunk))
            self.assertIsNone(big._cache_load(chunk))
            self.assertEqual(big.plan(self.TEXT)[2], 0)  # nada "ja no cache" para o modelo novo
            saved = json.loads(small._cache_path(chunk).read_text(encoding="utf-8"))
            self.assertEqual(saved["modelo"], "gemma4:e4b")

    def test_default_window_keeps_the_old_key_and_another_window_changes_it(self) -> None:
        chunk = split_chunks(self.TEXT, 2000)[0]
        with tempfile.TemporaryDirectory() as tmp:
            default = LangExtractDiscoverer(_local("gemma4:e4b"), cache_dir=Path(tmp))
            digest = hashlib.sha256()
            for part in (PROMPT_VERSION, "ollama", "gemma4:e4b", chunk.text):
                digest.update(part.encode("utf-8"))
                digest.update(b"\0")
            # A chave de antes (H9-b) segue valendo: o cache ja gravado do gemma4:e4b nao se perde.
            self.assertEqual(default._cache_path(chunk).name, f"{digest.hexdigest()}.json")
            wider = LangExtractDiscoverer(_local("gemma4:e4b", num_ctx=OLLAMA_DEFAULT_NUM_CTX * 2), cache_dir=Path(tmp))
            self.assertNotEqual(default._cache_path(chunk), wider._cache_path(chunk))


@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class RetryPolicyTests(unittest.TestCase):
    def _run(self, error: BaseException | None, *, local: bool, output: str = '{"extractions": []}'):
        from langextract.core import base_model
        from langextract.core import types as lx_types

        from anonimizador_app.llm_discovery import wrap_model

        calls: list[str] = []
        failures: list[tuple[str, str]] = []

        class Inner(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **kwargs):
                for prompt in batch_prompts:
                    calls.append(prompt)
                    if error is not None:
                        raise error
                    yield [lx_types.ScoredOutput(score=1.0, output=output)]

        model = wrap_model(Inner(), max_workers=1, retries=1, on_failure=lambda p, r: failures.append((p, r)),
                           local=local)
        list(model.infer(["Q: trecho"]))
        return calls, failures

    def _timeout(self) -> BaseException:
        import requests
        from langextract.core import exceptions

        original = requests.exceptions.ReadTimeout("read timed out")
        return exceptions.InferenceRuntimeError("Ollama Model timed out (timeout=600)", original=original,
                                                provider="Ollama")

    def test_timeout_is_repeated_only_once_on_the_local_channel(self) -> None:
        # v0.4.13: uma nova tentativa depois de tempo esgotado (o modelo pode ter sido descarregado); nao mais.
        error = self._timeout()
        self.assertTrue(is_timeout_error(error))
        calls, failures = self._run(error, local=True)
        self.assertEqual(len(calls), 2)
        self.assertIn("tempo esgotado", failures[0][1])

    def test_bad_json_and_server_error_are_not_repeated_locally(self) -> None:
        calls, failures = self._run(None, local=True, output="{truncado")
        self.assertEqual((len(calls), failures[0][1]), (1, "resposta fora do formato JSON"))
        calls, _failures = self._run(RuntimeError("Bad status code from Ollama: 500"), local=True)
        self.assertEqual(len(calls), 1)

    def test_connection_error_is_retried_locally(self) -> None:
        import requests
        from langextract.core import exceptions

        error = exceptions.InferenceRuntimeError(
            "Ollama request failed", original=requests.exceptions.ConnectionError("recusada"), provider="Ollama"
        )
        self.assertTrue(is_connection_error(error))
        self.assertFalse(is_connection_error(self._timeout()))
        calls, _failures = self._run(error, local=True)
        self.assertEqual(len(calls), 2)

    def test_remote_channels_keep_the_retry(self) -> None:
        calls, _failures = self._run(self._timeout(), local=False)
        self.assertEqual(len(calls), 2)
        calls, _failures = self._run(None, local=False, output="{truncado")
        self.assertEqual(len(calls), 2)

    def test_qwen3_is_called_without_thinking(self) -> None:
        text = "<!-- pagina-origem: 1 -->\nEXECUTADO: ACME COMÉRCIO DE GRÃOS LTDA\n"
        decision = check_engine(EngineChoice(channel="ollama-local", model_id="qwen3:14b"), env={},
                                missing_fn=lambda _c: [])
        with FakeOllama() as server:
            LangExtractDiscoverer(decision.config, loopback_port=server.port).discover(text)
            bodies = [request["body"] for request in server.requests]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertEqual(body["model"], "qwen3:14b")
            self.assertIs(body["think"], False)
            self.assertEqual(body["keep_alive"], decision.config.keep_alive)


class EstimateTests(unittest.TestCase):
    def test_estimate_for_the_local_channel_only(self) -> None:
        decision = check_engine(EngineChoice(channel="ollama-local", model_id="gemma4:12b"), env={},
                                missing_fn=lambda _c: [])
        plan = SendPlan(files=(FilePlan("a.pdf", 130, 300_000, 169, 69, 200_000),))
        text = local_time_estimate(decision, plan)
        self.assertIn("100 de 169 trecho(s)", text)
        self.assertIn("gemma4:12b", text)
        self.assertIn("min", text)
        remote = check_engine(EngineChoice(channel="vertex-sp", project="p-ficticio"), missing_fn=lambda _c: [],
                              adc_fn=lambda: True)
        self.assertEqual(local_time_estimate(remote, plan), "")
        cached = SendPlan(files=(FilePlan("a.pdf", 130, 300_000, 169, 169, 0),))
        self.assertIn("já estão no cache", local_time_estimate(decision, cached))

    def test_bigger_models_are_slower(self) -> None:
        self.assertLess(ollama_seconds_per_chunk("gemma4:e4b"), ollama_seconds_per_chunk("gemma4:12b"))
        self.assertLess(ollama_seconds_per_chunk("gemma4:12b"), ollama_seconds_per_chunk("qwen3:14b"))


class RunNotesTests(unittest.TestCase):
    def _settings(self) -> AppSettings:
        settings = AppSettings(
            target_people=["Fulano de Tal", "Beltrano Silva"],
            target_companies=["Acme Ltda", "Outra SA"],
            target_cdas=["123"],
        )
        settings.case_operations = {
            case_item_key("target_people", "Fulano de Tal"): ["op1"],
            case_item_key("target_companies", "Acme Ltda"): ["op1", "op2"],
        }
        settings.case_operation_names = {"op1": "OPERACAO UM"}
        return settings

    def test_lines_without_operation_are_counted_only_with_an_operation_chosen(self) -> None:
        settings = self._settings()
        self.assertEqual(operation_free_lines(settings), [])
        settings.case_operation = "op1"
        line = operation_free_lines(settings)[0]
        self.assertIn(": 3 (", line)
        self.assertIn("pessoas 1", line)
        self.assertIn("empresas 1", line)
        self.assertIn("CDAs 1", line)
        self.assertNotIn("Beltrano", line)

    def test_disabled_tags(self) -> None:
        tags = default_enabled_tags()
        settings = AppSettings(enabled_tags=tags)
        self.assertEqual(disabled_tag_lines(settings), ["Etiquetas desligadas nesta rodada: nenhuma"])
        tags.update(BR_CHASSI=False, BR_RENAVAM=False, BR_MATRICULA_IMOVEL=False)
        line = disabled_tag_lines(AppSettings(enabled_tags=tags, local_ner_enabled=False))
        self.assertEqual(line[0], "Etiquetas desligadas nesta rodada: CHASSI, MATRICULA_IMOVEL, RENAVAM")
        self.assertIn("DESLIGADO", line[1])

    def test_review_record_between_the_previous_lote_and_this_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lotes = root / "privados"
            for name in ("lote_20261019_100000", "lote_20261020_100000"):
                (lotes / name).mkdir(parents=True)
                (lotes / name / "mapa.json").write_text("{}", encoding="utf-8")
            reviews = root / "revisoes"
            reviews.mkdir()
            (reviews / "revisao_lote_20261019_100000_20261019_120000.txt").write_text(
                "Aplicadas: 7 (TROCAR 5, MANTER 1, UNIR 1)\n", encoding="utf-8"
            )
            (reviews / "revisao_lote_20261019_100000_20261018_120000.txt").write_text("velha\n", encoding="utf-8")
            (reviews / "revisao_lote_20261020_100000_20261020_130000.txt").write_text("depois\n", encoding="utf-8")
            lines = review_record_lines(lotes, "lote_20261020_100000")
            self.assertEqual(
                lines,
                ["Revisao externa aplicada: revisoes\\revisao_lote_20261019_100000_20261019_120000.txt "
                 "(registrada em 19/10/2026 12:00; 7 linha(s) aplicada(s))"],
            )
            self.assertEqual(review_record_lines(lotes, "sem_data"), [])
            self.assertEqual(review_record_lines(root / "nao_existe", "lote_20261020_100000"), [])
            joined = run_note_lines(AppSettings(), lotes, "lote_20261020_100000")
            self.assertIn("Etiquetas desligadas nesta rodada: nenhuma", joined)


class RunTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        from anonimizador_app import run_timing

        self.timing = run_timing
        run_timing.reset()

    def tearDown(self) -> None:
        self.timing.reset()

    def test_lines_have_each_stage_total_and_devices(self) -> None:
        timing = self.timing
        timing.mark_start()
        timing.add("extracao", 12.0)
        timing.count("extracao_cache")
        timing.add("extracao", 3.0)
        timing.add("ocr", 4.0)
        timing.add("ocr", 4.0)
        timing.add("ner", 30.0)
        timing.count("ner_cache")
        timing.add("ner", 0.5)
        for _ in range(4):
            timing.add("llm", 10.0)
        timing.count("llm_cache", 3)
        timing.note("ner_device", "cpu")
        timing.note("ocr_provider", "RapidOCR com ONNX Runtime")
        start = timing._STATE["start"]
        text = "\n".join(timing.lines(now=start + 125.0))
        self.assertIn("Tempo total da rodada: 2 min 05 s", text)
        self.assertIn("extracao do PDF: 15,0 s (1 extraido(s), 1 do cache)", text)
        self.assertIn("OCR (dentro da extracao): 8,0 s, 2 pagina(s)", text)
        self.assertIn("NER local: 30,5 s (1 leitura(s) pelo modelo, 1 do cache); dispositivo do GLiNER: cpu", text)
        self.assertIn("LLM: 40,0 s em 4 chamada(s) (media 10,0 s por chamada); trechos do cache do LLM: 3", text)
        self.assertIn("resto (lista do caso, resolucao, troca e gravacao): 39,5 s", text)
        self.assertIn("RapidOCR", text)

    def test_take_lines_resets_but_keeps_device_notes(self) -> None:
        self.timing.add("ner", 1.0)
        self.timing.note("ner_device", "cuda:0")
        self.assertTrue(self.timing.take_lines())
        self.assertEqual(self.timing.lines(), [])
        self.timing.add("ner", 1.0)
        self.assertIn("cuda:0", "\n".join(self.timing.lines()))

    def test_restart_clock_drops_the_wait_for_confirmation(self) -> None:
        self.timing.mark_start()
        self.timing._STATE["start"] -= 1000  # 1000 s de espera antes da rodada
        self.timing.add("extracao", 5.0)
        self.timing.restart_clock()
        line = self.timing.lines()[0]
        self.assertTrue(line.startswith("Tempo total da rodada: 5,"), line)

    def test_private_section_is_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lote" / "AUDITORIA_PRIVADA.txt"
            self.timing.append_private(path, ["Tempo total da rodada: 1,0 s"])
            path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            text = path.read_text(encoding="utf-8")
        self.assertIn("## Tempo de cada etapa da rodada", text)
        self.assertIn("- Tempo total da rodada: 1,0 s", text)

    def test_local_run_writes_the_timing_in_both_audits(self) -> None:
        from anonimizador_app.pipeline import run_job

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "peca.txt"
            source.write_text("EXECUTADO: fulano. CPF 529.982.247-25. Nada mais.\n", encoding="utf-8")
            result = run_job([source], export_root=root / "exp", private_root=root / "priv",
                             settings=AppSettings(local_ner_enabled=False))
            audit = (result.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            private = (result.map_path.parent / "AUDITORIA_PRIVADA.txt").read_text(encoding="utf-8")
        self.assertIn("Tempo total da rodada:", audit)
        self.assertIn("Tempo da extracao do PDF:", audit)
        self.assertIn("Tempo total da rodada:", private)
        self.assertIn("Etiquetas desligadas nesta rodada: nenhuma", audit)


class ButtonTests(unittest.TestCase):
    def test_button_names_engine_and_model(self) -> None:
        from anonimizador_app.ui_llm import EngineSession

        engine = EngineSession()
        self.assertEqual(engine.button_label(), "Anonimizar lote · motor local")
        engine.choice = EngineChoice(channel="ollama-local", model_id="gemma4:12b")
        engine.decision = check_engine(engine.choice, env={}, missing_fn=lambda _c: [])
        self.assertEqual(engine.button_label(), "Anonimizar lote · Local + LLM (gemma4:12b)")
        engine.choice = EngineChoice(channel="ollama-local")
        engine.decision = check_engine(engine.choice, env={}, missing_fn=lambda _c: [])
        self.assertEqual(engine.button_label(), "Anonimizar lote · Local + LLM (gemma4:e4b)")


if __name__ == "__main__":
    unittest.main()
