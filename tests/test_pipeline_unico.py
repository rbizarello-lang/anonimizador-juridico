"""H5: pipeline unico. O motor local pelo pipeline do LLM sem descobridor (``run_local_job``) gera o mesmo
texto que o motor local atual (``run_job``); a deteccao passa a ser a unica diferenca entre os modos.

Nomes, numeros e enderecos ficticios.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.local_ner import CombinedEngine
from anonimizador_app.pipeline import run_job
from anonimizador_app.pipeline_llm import LEGEND_FILENAME, run_local_job
from anonimizador_app.settings import AppSettings
from anonimizador_juridico import EntitySpan

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("comparar_pipelines", ROOT / "tools" / "comparar_pipelines.py")
comparar = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = comparar  # a dataclass do modulo procura o proprio modulo em sys.modules
_SPEC.loader.exec_module(comparar)

PARTE_1 = (
    """# EXECUÇÃO FISCAL

<!-- pagina-origem: 1 -->

A UNIÃO (FAZENDA NACIONAL) ajuíza execução fiscal contra ALFAVILLE METAIS LTDA,
CNPJ 11.222.333/0001-81, e contra o sócio OLAVO RICARDO TEIXEIRA, CPF 529.982.247-25.
A ALFAVILLE METAIS LTDA não pagou. A Alfaville encerrou as atividades.
Manifestou-se o Procurador da Fazenda Nacional RAIMUNDO NONATO FURTADO.

<!-- pagina-origem: 2 -->

Consta que MARLENE OLIVEIRA SAMPAIO, sócia, reside na Rua Olavo Bilac, nº 50.
Valor: R$ 1.234.567,89 em 12/03/2024. Conforme o evento 12, a Alfaville foi citada.
Documento eletrônico assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.
"""
    # Nome completo repetido: a marca de uma palavra ("Alfaville") so e aprovada com uso suficiente (E13).
    + "".join(f"\nNo item {index}, a ALFAVILLE METAIS LTDA declarou o débito.\n" for index in range(22))
    + """
A Alfaville respondeu. O CPF 111.444.777-35 da sócia consta também na PARTE 2.
"""
)

PARTE_2 = """# PARTE 2

<!-- pagina-origem: 1 -->

O executado Olavo Teixeira apresentou defesa. Olavo Teixeira alega ilegitimidade.

Ordem SISBAJUD às instituições:
00341 - BANCO PALMEIRAS DO SUL S A
Bloqueio na conta de Olavo Ricardo Teixeira, CPF 52998224725, e da Alfaville.
Nova citação de ALFAVILLE METAIS LTDA na Rua Olavo Bilac, nº 52.
A sócia BEATRIZ HELENA CAVALCANTI, CPF 111.444.777-35, foi citada. CNPJ 11222333000181.
"""

NER_NAMES = [
    ("ORGANIZATION", "ALFAVILLE METAIS LTDA"),
    ("PERSON", "OLAVO RICARDO TEIXEIRA"),
    ("PERSON", "Olavo Teixeira"),  # forma curta: o lote agrupa com o nome completo (E13)
    ("PERSON", "RAIMUNDO NONATO FURTADO"),
    ("PERSON", "MARLENE OLIVEIRA SAMPAIO"),
    ("PERSON", "Olavo Bilac"),
    ("PERSON", "LUCIANA MONTEFUSCO DE ALMEIDA"),
    ("ORGANIZATION", "BANCO PALMEIRAS DO SUL S A"),
    ("PERSON", "BEATRIZ HELENA CAVALCANTI"),
]


class FakeNer:
    """O NER do teste: acha os nomes que conhece, em qualquer caixa (o GLiNER nao roda na suite)."""

    def __init__(self, *, boom: Exception | None = None, residual=()) -> None:
        self.boom = boom
        self.residual = list(residual)

    def detect(self, text: str) -> list[EntitySpan]:
        if self.boom is not None:
            raise self.boom
        spans = []
        folded = text.casefold()
        for kind, value in NER_NAMES:
            start = folded.find(value.casefold())
            while start >= 0:
                spans.append(
                    EntitySpan(start=start, end=start + len(value), entity_type=kind, score=0.9, recognizer="FakeNer")
                )
                start = folded.find(value.casefold(), start + 1)
        return spans

    def scan_residual(self, text: str) -> list[str]:
        return list(self.residual)


def _engine(**kwargs) -> CombinedEngine:
    """As heuristicas reais do motor local (lista de alvos, eproc, capa, tabela, formulario, endereco) e o NER falso."""

    settings = AppSettings(local_ner_enabled=False)
    return CombinedEngine([build_contextual_engine(settings, use_ner_cache=False), FakeNer(**kwargs)])


class _Lote(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.files = []
        for name, body in (("PARTE_1.md", PARTE_1), ("PARTE_2.md", PARTE_2)):
            path = self.base / name
            path.write_text(body, encoding="utf-8")
            self.files.append(path)
        self.settings = AppSettings(local_ner_enabled=False)

    def _current(self, engine):
        return run_job(
            self.files,
            export_root=self.base / "a_exp",
            private_root=self.base / "a_priv",
            contextual=engine,
            settings=self.settings,
            use_cache=False,
        )

    def _unified(self, engine):
        return run_local_job(
            self.files,
            export_root=self.base / "b_exp",
            private_root=self.base / "b_priv",
            fallback_engine=engine,
            settings=self.settings,
            use_cache=False,
        )


class ParityTests(_Lote):
    def test_the_unified_pipeline_writes_the_same_text_as_the_local_engine(self) -> None:
        engine = _engine()
        current = self._current(engine)
        unified = self._unified(engine).job

        pairs = [(a.markdown_path, b.markdown_path) for a, b in zip(current.outcomes, unified.outcomes)]
        self.assertEqual(len(pairs), 2)
        for item in comparar.compare_outcomes(pairs):
            self.assertTrue(item.same, f"{item.name}: {item.diff[:12]}")

    def test_the_lote_rules_apply_in_the_unified_pipeline(self) -> None:
        # O que so o elenco do lote do motor local fazia: marca de uma palavra, procurador e banco em claro,
        # rua com nome de pessoa como endereco, CPF sem pontuacao com o mesmo token.
        result = self._unified(_engine())
        parte_1, parte_2 = (outcome.markdown_path.read_text(encoding="utf-8") for outcome in result.job.outcomes)

        self.assertNotIn("Alfaville", parte_1)
        self.assertNotIn("Alfaville", parte_2)
        self.assertIn("Procurador da Fazenda Nacional RAIMUNDO NONATO FURTADO", parte_1)
        self.assertIn("LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal", parte_1)
        self.assertIn("00341 - BANCO PALMEIRAS DO SUL S A", parte_2)
        self.assertNotIn("Olavo Bilac", parte_1 + parte_2)
        self.assertNotIn("52998224725", parte_2)
        for kept in ("R$ 1.234.567,89", "12/03/2024", "Conforme o evento 12,", "SISBAJUD"):
            self.assertIn(kept, parte_1 + parte_2)


class GroupingTests(_Lote):
    def test_a_short_form_shares_the_token_of_the_full_name_in_both_pipelines(self) -> None:
        import json

        engine = _engine()
        current = self._current(engine)
        unified = self._unified(engine).job
        for job in (current, unified):
            records = json.loads(job.map_path.read_text(encoding="utf-8"))["records"]
            people = [r for r in records if r["entity_type"] == "PERSON" and "teixeira" in r["canonical"].casefold()]
            self.assertEqual(len(people), 1, [r["canonical"] for r in people])
            self.assertIn("Olavo Teixeira", people[0]["originals"])
        texts = [o.markdown_path.read_text(encoding="utf-8") for o in unified.outcomes]
        self.assertNotIn("Olavo Teixeira", "".join(texts))


class KeptEntryTests(_Lote):
    def test_a_company_kept_in_the_roster_keeps_its_one_word_brand_in_clear(self) -> None:
        import json

        first = self._unified(_engine())
        payload = json.loads(first.roster_path.read_text(encoding="utf-8"))
        entries = payload["entradas"]
        for entry in entries:
            if entry["tipo"] == "empresa" and "alfaville" in entry["nome"].casefold():
                entry["acao"] = "manter"
                entry["fixo"] = True
        edited = self.base / "elenco.json"
        edited.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        again = run_local_job(
            self.files,
            export_root=self.base / "c_exp",
            private_root=self.base / "c_priv",
            fallback_engine=_engine(),
            settings=self.settings,
            use_cache=False,
            roster_path=edited,
        )
        text = again.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("ALFAVILLE METAIS LTDA não pagou", text)
        self.assertIn("A Alfaville encerrou", text)


class LocalOnlyTests(_Lote):
    def test_nothing_is_attributed_to_an_llm(self) -> None:
        result = self._unified(_engine())

        self.assertFalse(result.job.gemini_used)
        self.assertEqual(result.engine, "local")
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("Motor: local (pipeline unico, sem LLM)", audit)
        self.assertIn("nada saiu desta maquina", audit)
        # H9-d: a linha de rede existe tambem sem LLM, com zero.
        self.assertIn("Tentativas de rede fora de loopback bloqueadas: 0", audit)
        self.assertNotIn("Motor LLM", audit)
        self.assertNotIn("RAIMUNDO", audit)
        legend = result.job.export_dir / LEGEND_FILENAME
        if legend.is_file():
            self.assertNotIn("atribuídos pelo LLM", legend.read_text(encoding="utf-8"))
        self.assertTrue(result.job.export_allowed, result.job.export_block_reason)

    def test_the_local_engine_failing_blocks_the_export(self) -> None:
        # Sem LLM, o motor local e a unica fonte de nomes: se ele nao roda, o texto so teria os IDs trocados.
        result = self._unified(_engine(boom=RuntimeError("modelo ausente: OLAVO")))

        self.assertFalse(result.job.export_allowed)
        self.assertIn("motor local nao rodou", result.job.export_block_reason)
        self.assertNotIn("OLAVO", result.job.export_block_reason)

    def test_the_residual_review_is_reported_as_in_the_local_engine(self) -> None:
        result = self._unified(_engine(residual=["Fulano Residual"]))

        self.assertIn("Fulano Residual", result.job.residual_hits)
        self.assertIn("revisão residual", result.job.export_warning)


class CompareToolTests(unittest.TestCase):
    def test_token_numbers_do_not_count_as_a_difference(self) -> None:
        item = comparar.compare_texts("x.md", "[PESSOA_1] e [PESSOA_2]\n", "[PESSOA_2] e [PESSOA_1]\n")
        self.assertTrue(item.same)

    def test_a_name_left_in_clear_is_a_difference(self) -> None:
        item = comparar.compare_texts("x.md", "A [EMPRESA_1] pagou.\n", "A Alfaville pagou.\n")
        self.assertEqual(item.changed, 2)
        self.assertFalse(item.same)

    def test_two_entities_merged_on_one_side_are_a_regrouping(self) -> None:
        item = comparar.compare_texts(
            "x.md", "[PESSOA_1] citou\n[PESSOA_2] citou\n", "[PESSOA_1] citou\n[PESSOA_1] citou\n"
        )
        self.assertEqual(item.changed, 0)
        self.assertGreater(item.regrouped, 0)
        self.assertFalse(item.same)


if __name__ == "__main__":
    unittest.main()
