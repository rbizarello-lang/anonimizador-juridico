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


if __name__ == "__main__":
    unittest.main()
