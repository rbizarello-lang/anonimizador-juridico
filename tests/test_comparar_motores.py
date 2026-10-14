"""Comparador de detectores contra o gabarito (tools/comparar_motores.py), com motores simulados."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
_SPEC = importlib.util.spec_from_file_location("comparar_motores", ROOT / "tools" / "comparar_motores.py")
comparar = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = comparar
_SPEC.loader.exec_module(comparar)

TEXTO = """A UNIÃO (FAZENDA NACIONAL) ajuíza execução fiscal contra ALFAVILLE METAIS LTDA.
O sócio OLAVO RICARDO TEIXEIRA foi citado. A Alfaville Metais Ltda não pagou.
Consta que MARLENE OLIVEIRA SAMPAIO, sócia, reside na cidade.
Documento eletrônico assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.
O sócio OLAVO RICARDO TEIXEIRA foi citado de novo.
"""

GABARITO = {
    "entidades": [
        {"categoria": "EMPRESA-parte", "rotulo": "empresa 1", "tipo": "empresa", "acao": "tokenizar",
         "padroes": ["ALFAVILLE METAIS LTDA", "Alfaville Metais Ltda"]},
        {"categoria": "PESSOA-socio", "rotulo": "pessoa 1", "tipo": "pessoa", "acao": "tokenizar",
         "padroes": ["OLAVO RICARDO TEIXEIRA"]},
        {"categoria": "PESSOA-socio", "rotulo": "pessoa 2", "tipo": "pessoa", "acao": "tokenizar",
         "padroes": ["MARLENE OLIVEIRA SAMPAIO"]},
        {"categoria": "AGENTE-PUBLICO", "rotulo": "pessoa 3", "tipo": "pessoa", "acao": "manter",
         "padroes": ["LUCIANA MONTEFUSCO DE ALMEIDA"]},
        {"categoria": "ORGAO-PUBLICO", "rotulo": "empresa 2", "tipo": "empresa", "acao": "manter",
         "padroes": ["UNIÃO (FAZENDA NACIONAL)"]},
        {"categoria": "ID-EMAIL", "rotulo": "EMAIL 1", "tipo": "id", "acao": "tokenizar", "padroes": ["x@y.z"]},
    ],
    "ids": [],
}


def _finder(names, *, case_sensitive=False):
    def detect(text):
        spans = []
        hay = text if case_sensitive else text.casefold()
        for kind, value in names:
            needle = value if case_sensitive else value.casefold()
            start = hay.find(needle)
            while start >= 0:
                spans.append((start, start + len(value), kind))
                start = hay.find(needle, start + 1)
        return spans
    return detect


PERFECT = _finder([("ORGANIZATION", "ALFAVILLE METAIS LTDA"), ("PERSON", "OLAVO RICARDO TEIXEIRA"), ("PERSON", "MARLENE OLIVEIRA SAMPAIO")])
# Acha o socio uma vez so (a 2a ocorrencia escapa), marca a juiza e a Uniao, e um termo fora do gabarito.
WEAK = _finder([("PERSON", "OLAVO RICARDO TEIXEIRA foi citado."), ("PERSON", "LUCIANA MONTEFUSCO DE ALMEIDA"),
                ("ORGANIZATION", "UNIÃO (FAZENDA NACIONAL)"), ("ORGANIZATION", "PGFN"), ("ORGANIZATION", "execução fiscal")])
# So acha em Title Case: a passada-sombra e que o salva.
TITLE_ONLY = _finder([("ORGANIZATION", "Alfaville Metais Ltda"), ("PERSON", "Olavo Ricardo Teixeira"), ("PERSON", "Marlene Oliveira Sampaio")], case_sensitive=True)


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        (self.base / "textos").mkdir()
        (self.base / "textos" / "PARTE_1.md").write_text(TEXTO, encoding="utf-8")
        self.gabarito = self.base / "g.json"
        self.gabarito.write_text(json.dumps(GABARITO, ensure_ascii=False), encoding="utf-8")
        self.targets = comparar.load_gabarito(self.gabarito)
        comparar.locate(self.targets, [TEXTO])

    def test_ids_are_left_to_presidio_and_occurrences_are_located_in_any_case(self) -> None:
        by_label = {t.rotulo: t for t in self.targets}
        self.assertNotIn("EMAIL 1", by_label)
        self.assertEqual(len(by_label["empresa 1"].occurrences), 2)
        self.assertEqual(len(by_label["pessoa 1"].occurrences), 2)

    def test_a_perfect_detector_scores_full_recall_and_touches_nothing_kept(self) -> None:
        result = comparar.evaluate("perfeito", PERFECT, self.targets, [TEXTO])
        self.assertEqual(result["tokenizar"]["recall_ocorrencias"], 1.0)
        self.assertEqual(result["tokenizar"]["cobriu_todas"], 3)
        self.assertEqual(result["manter"]["marcadas_pelo_detector"], 0)
        self.assertEqual(result["sem_cobertura"], [])

    def test_a_weak_detector_shows_misses_kept_hits_and_protected_terms(self) -> None:
        result = comparar.evaluate("fraco", WEAK, self.targets, [TEXTO])
        t = result["tokenizar"]
        self.assertEqual((t["achou_alguma_vez"], t["cobriu_todas"]), (1, 0))
        self.assertEqual(t["cobertas"], 1)  # a ocorrencia do socio sobreposta >= 50%
        self.assertEqual(result["manter"]["marcadas_pelo_detector"], 2)  # juiza e Uniao
        names = {m["rotulo"] for m in result["sem_cobertura"]}
        self.assertEqual(names, {"empresa 1", "pessoa 1", "pessoa 2"})
        self.assertGreaterEqual(sum(result["fora_do_gabarito"].values()), 1)

    def test_the_shadow_pass_rescues_an_uppercase_blind_detector(self) -> None:
        plain = comparar.evaluate("title", TITLE_ONLY, self.targets, [TEXTO])
        shadow = comparar.evaluate("title+sombra", comparar.with_shadow(TITLE_ONLY), self.targets, [TEXTO])
        self.assertEqual(plain["tokenizar"]["cobertas"], 1)  # so "Alfaville Metais Ltda"
        self.assertEqual(shadow["tokenizar"]["recall_ocorrencias"], 1.0)

    def test_cli_prints_a_table_without_names_and_writes_the_private_detail(self) -> None:
        registry = {"perfeito": lambda _t, _r: PERFECT, "fraco": lambda _t, _r: WEAK}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = comparar.main(
                [str(self.gabarito), "--textos", str(self.base / "textos"), "--privados", str(self.base / "priv"),
                 "--motor", "perfeito", "--motor", "fraco", "--sombra"],
                engines=registry,
            )
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("perfeito", text)
        self.assertIn("fraco+sombra", text)
        for name in ("OLAVO", "Olavo", "MARLENE", "LUCIANA", "Alfaville"):
            self.assertNotIn(name, text)
        details = list((self.base / "priv" / "gabaritos").glob("comparacao_motores_*.json"))
        self.assertEqual(len(details), 1)

    def test_an_unknown_engine_is_refused(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = comparar.main([str(self.gabarito), "--textos", str(self.base / "textos"), "--privados", str(self.base / "priv"), "--motor", "x"], engines={"a": lambda _t, _r: PERFECT})
        self.assertEqual(code, 2)
        self.assertIn("Motor desconhecido", err.getvalue())


class _FakeLlm:
    """Descobridor do canal local simulado: devolve mencoes prontas (uma sem ancora)."""

    def __init__(self, mentions) -> None:
        self.mentions = mentions
        self.texts: list[str] = []

    def discover(self, text):
        from anonimizador_app.llm_discovery import Discovery, DiscoveryReport

        self.texts.append(text)
        return Discovery(mentions=list(self.mentions), report=DiscoveryReport())


def _mention(kind, value, start=None):
    from anonimizador_app.roster import Mention

    where = TEXTO.find(value) if start is None else start
    end = where + len(value) if where >= 0 else -1
    return Mention(kind=kind, text=value, role="", canonical=value, start=where, end=end, source_text=value)


class OllamaEngineTests(unittest.TestCase):
    def test_union_of_the_local_engine_and_the_llm_with_the_kinds_mapped(self) -> None:
        base = _finder([("PERSON", "OLAVO RICARDO TEIXEIRA")])
        llm = _FakeLlm([
            _mention("pessoa", "MARLENE OLIVEIRA SAMPAIO"),
            _mention("empresa", "ALFAVILLE METAIS LTDA"),
            _mention("endereco", "execução fiscal"),
            _mention("pessoa", "NAO ANCORADA", start=-1),
            _mention("orgao", "UNIÃO (FAZENDA NACIONAL)"),
        ])
        detect = comparar.app_local_ollama_detector(Path("."), "qwen3:4b", local=base, discoverer=llm)
        spans = detect(TEXTO)
        kinds = {TEXTO[start:end]: kind for start, end, kind in spans}
        self.assertEqual(kinds["OLAVO RICARDO TEIXEIRA"], "PERSON")
        self.assertEqual(kinds["MARLENE OLIVEIRA SAMPAIO"], "PERSON")
        self.assertEqual(kinds["ALFAVILLE METAIS LTDA"], "ORGANIZATION")
        self.assertEqual(kinds["execução fiscal"], "LOCATION")
        self.assertNotIn("NAO ANCORADA", kinds)
        self.assertNotIn("UNIÃO (FAZENDA NACIONAL)", kinds)  # tipo desconhecido fica de fora
        self.assertEqual(spans, sorted(set(spans)))
        self.assertEqual(llm.texts, [TEXTO])

    def test_the_llm_part_runs_inside_the_loopback_only_network_block(self) -> None:
        import socket

        class Probing(_FakeLlm):
            def discover(self, text):
                try:
                    socket.getaddrinfo("example.invalid", 80)
                except OSError:
                    pass
                return super().discover(text)

        err = io.StringIO()
        detect = comparar.app_local_ollama_detector(Path("."), local=lambda _t: [], discoverer=Probing([]))
        with contextlib.redirect_stderr(err):
            detect(TEXTO)
        self.assertIn("1 tentativa(s) de rede fora de loopback", err.getvalue())

    def test_the_llm_reads_the_masked_text_of_the_pipeline_and_spans_return_to_the_original(self) -> None:
        from anonimizador_app.llm_discovery import Discovery, DiscoveryReport
        from anonimizador_app.roster import Mention

        text = "CPF 529.982.247-25 de OLAVO RICARDO TEIXEIRA, sócio. Outro: 529.982.247-25 e MARLENE OLIVEIRA SAMPAIO."

        class Finder:
            texts: list[str] = []

            def discover(self, body):
                Finder.texts.append(body)
                found = []
                for value in ("OLAVO RICARDO TEIXEIRA", "MARLENE OLIVEIRA SAMPAIO"):
                    start = body.find(value)
                    found.append(Mention(kind="pessoa", text=value, role="", canonical=value, start=start,
                                         end=start + len(value), source_text=value))
                return Discovery(mentions=found, report=DiscoveryReport())

        with tempfile.TemporaryDirectory() as tmp:
            detect = comparar.app_local_ollama_detector(
                Path(tmp), local=lambda _t: [], discoverer=Finder()
            )
            spans = detect(text)
        self.assertEqual(len(Finder.texts), 1)
        self.assertNotIn("529.982.247-25", Finder.texts[0])  # o LLM le o texto com o ID mascarado
        self.assertIn("[CPF_", Finder.texts[0])
        self.assertEqual(
            sorted(text[start:end] for start, end, _kind in spans),
            ["MARLENE OLIVEIRA SAMPAIO", "OLAVO RICARDO TEIXEIRA"],  # posicoes de volta ao texto original
        )

    def test_the_cache_folder_is_the_pipelines(self) -> None:
        from anonimizador_app.pipeline_llm import LLM_CACHE_SUBDIR

        self.assertEqual(comparar.OLLAMA_CACHE_SUBDIR, LLM_CACHE_SUBDIR)
        with tempfile.TemporaryDirectory() as tmp:
            discoverer = comparar.build_ollama_discoverer(Path(tmp), "qwen3:4b", env={})
            self.assertEqual(discoverer.cache_dir, Path(tmp) / LLM_CACHE_SUBDIR)

    def test_the_real_builder_uses_the_local_channel_cache_and_the_app_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discoverer = comparar.build_ollama_discoverer(Path(tmp), "llama3.2:3b", env={})
            self.assertEqual(discoverer.cache_dir, Path(tmp) / "cache_llm")
            self.assertEqual(discoverer.config.channel, "ollama-local")
            self.assertEqual(discoverer.config.model_id, "llama3.2:3b")
            for env, model in (({"OLLAMA_HOST": "http://10.0.0.5:11434"}, "qwen3:4b"),
                               ({"HTTPS_PROXY": "http://p.example.invalid:3128"}, "qwen3:4b"),
                               ({}, "gpt-oss:120b-cloud")):
                with self.assertRaises(comparar.LlmConfigError, msg=(env, model)):
                    comparar.build_ollama_discoverer(Path(tmp), model, env=env)

    def test_cli_runs_the_new_engine_with_the_model_flag_and_prints_no_names(self) -> None:
        import tempfile as _tf

        seen = {}
        real = comparar.app_local_ollama_detector

        def fake(private_root, model_id="qwen3:4b", **_kw):
            seen["model"] = model_id
            return real(
                private_root,
                model_id,
                local=_finder([("PERSON", "OLAVO RICARDO TEIXEIRA")]),
                discoverer=_FakeLlm([_mention("empresa", "ALFAVILLE METAIS LTDA"), _mention("empresa", "Alfaville Metais Ltda")]),
            )

        base = Path(_tf.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        (base / "textos").mkdir()
        (base / "textos" / "PARTE_1.md").write_text(TEXTO, encoding="utf-8")
        gabarito = base / "g.json"
        gabarito.write_text(json.dumps(GABARITO, ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(comparar, "app_local_ollama_detector", fake), \
                mock.patch.object(comparar, "app_local_detector", lambda _r: _finder([])), \
                contextlib.redirect_stdout(out):
            code = comparar.main([
                str(gabarito), "--textos", str(base / "textos"), "--privados", str(base / "priv"),
                "--motor", "app-local", "--motor", "app-local+ollama", "--modelo-ollama", "qwen3:8b",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(seen["model"], "qwen3:8b")
        text = out.getvalue()
        self.assertIn("app-local+ollama", text)
        for name in ("OLAVO", "ALFAVILLE", "Alfaville"):
            self.assertNotIn(name, text)
        comparar.OLLAMA_MODEL = "qwen3:4b"

    def test_the_new_engine_is_registered_next_to_the_old_ones(self) -> None:
        for name in ("gliner-atual", "gliner-large", "lener", "app-local", "app-local+ollama"):
            self.assertIn(name, comparar.ENGINES)


if __name__ == "__main__":
    unittest.main()
