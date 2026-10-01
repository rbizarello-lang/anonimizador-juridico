"""Revisao do elenco (L3) e reaplicacao sem rede. Dados inventados; rede bloqueada."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.llm_discovery import LangExtractDiscoverer, LlmConfig, LlmConfigError
from anonimizador_app.llm_engine import build_offline_discoverer
from anonimizador_app.netblock import block_network
from anonimizador_app.pipeline_llm import ROSTER_FILENAME, run_llm_job
from anonimizador_app.roster import KEEP, TOKENIZE, Mention, Roster
from anonimizador_app.roster_review import (
    REVIEWED_FILENAME,
    apply_decisions,
    build_rows,
    reapply_offline,
    summarize,
)
from test_pipeline_llm import DOC, ENTITIES, FakeDiscoverer

HAS_LANGEXTRACT = importlib.util.find_spec("langextract") is not None


def _roster() -> Roster:
    roster = Roster()
    for kind, name, role in (
        ("empresa", "ACME COMERCIO LTDA", "parte"),
        ("pessoa", "ANA LIMA", "socio"),
        ("pessoa", "ANA LIMA", "advogado"),
        ("pessoa", "BRUNO SOUZA", "magistrado"),
        ("endereco", "Rua das Flores, 10", "parte"),
    ):
        roster.add_mention(Mention(kind=kind, text=name, role=role, canonical=name))
    return roster


class RowsTests(unittest.TestCase):
    def test_rows_carry_type_name_role_count_token_and_action(self) -> None:
        roster = _roster()
        rows = build_rows(
            roster,
            occurrences={"pessoa:ana lima": 7, "pessoa:bruno souza": 2, "empresa:acme comercio": 9},
            tokens={"pessoa:ana lima": "[PESSOA_1]", "empresa:acme comercio": "[EMPRESA_1]"},
        )
        self.assertEqual([row.kind for row in rows], ["empresa", "pessoa", "pessoa", "endereco"])
        ana = next(row for row in rows if row.name == "ANA LIMA")
        self.assertEqual((ana.occurrences, ana.token, ana.action), (7, "[PESSOA_1]", TOKENIZE))
        self.assertEqual(ana.role_label, "sócio, advogado")
        bruno = next(row for row in rows if row.name == "BRUNO SOUZA")
        self.assertEqual((bruno.action, bruno.default_action, bruno.token), (KEEP, KEEP, ""))
        self.assertFalse(bruno.changed)
        # Mais mencoes primeiro, dentro do tipo.
        self.assertEqual([row.name for row in rows if row.kind == "pessoa"], ["ANA LIMA", "BRUNO SOUZA"])

    def test_summary_counts_decisions(self) -> None:
        rows = build_rows(_roster())
        self.assertEqual(summarize(rows), "3 a tokenizar, 1 a manter")
        self.assertIn("1 decisão(ões) sua(s)", summarize(rows, {"pessoa:ana lima": KEEP}))


class DecisionTests(unittest.TestCase):
    def test_decisions_are_pinned_and_beat_the_role_table(self) -> None:
        roster = _roster()
        changed = apply_decisions(
            roster, {"pessoa:bruno souza": TOKENIZE, "pessoa:ana lima": KEEP, "empresa:acme comercio": TOKENIZE}
        )
        self.assertEqual(changed, 2)
        self.assertEqual(roster.get("pessoa:bruno souza").action, TOKENIZE)
        self.assertTrue(roster.get("pessoa:bruno souza").pinned)
        # O LLM voltar a apontar a pessoa (com outro papel) nao desfaz a decisao.
        roster.add_mention(Mention(kind="pessoa", text="ANA LIMA", role="parte", canonical="ANA LIMA"))
        self.assertEqual(roster.get("pessoa:ana lima").action, KEEP)

    def test_unknown_key_and_bad_action_are_errors(self) -> None:
        roster = _roster()
        with self.assertRaises(KeyError):
            apply_decisions(roster, {"pessoa:ninguem": KEEP})
        with self.assertRaises(ValueError):
            apply_decisions(roster, {"pessoa:ana lima": "apagar"})

    def test_round_trip_through_the_private_json(self) -> None:
        roster = _roster()
        apply_decisions(roster, {"pessoa:ana lima": KEEP})
        with tempfile.TemporaryDirectory() as tmp:
            path = roster.dump(Path(tmp) / "elenco.json", annotations={"pessoa:ana lima": {"token": "[PESSOA_1]", "ocorrencias": 4}})
            payload = json.loads(path.read_text(encoding="utf-8"))
            again = Roster.load(path)
        ana = next(item for item in payload["entradas"] if item["chave"] == "pessoa:ana lima")
        self.assertEqual((ana["token"], ana["ocorrencias"], ana["fixo"], ana["acao"]), ("[PESSOA_1]", 4, True, KEEP))
        self.assertEqual(again.get("pessoa:ana lima").action, KEEP)
        self.assertTrue(again.get("pessoa:ana lima").pinned)


class _Base(unittest.TestCase):
    def _workspace(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(DOC, encoding="utf-8")
        return base, source


class ReapplyWithFakeDiscovererTests(_Base):
    def test_reapply_applies_pinned_decisions_in_a_new_lote(self) -> None:
        base, source = self._workspace()
        paths = dict(export_root=base / "exp", private_root=base / "priv")
        first = run_llm_job([source], discoverer=FakeDiscoverer(), use_cache=False, **paths)
        first_text = first.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertNotIn("MARLENE OLIVEIRA SAMPAIO", first_text)

        with block_network() as attempts:
            done = reapply_offline(
                first,
                {"pessoa:marlene oliveira sampaio": KEEP, "pessoa:luciana montefusco de almeida": TOKENIZE},
                [source],
                discoverer=FakeDiscoverer(),
                **paths,
            )
        self.assertEqual(attempts, [])
        self.assertEqual(done.changed, 2)
        new = done.result
        self.assertNotEqual(new.job.export_dir, first.job.export_dir)
        text = new.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("MARLENE OLIVEIRA SAMPAIO", text)
        self.assertNotIn("LUCIANA MONTEFUSCO DE ALMEIDA", text)
        self.assertNotIn("OLAVO RICARDO TEIXEIRA", text)
        # Rodada anterior intacta.
        self.assertEqual(first.job.outcomes[0].markdown_path.read_text(encoding="utf-8"), first_text)
        # Decisao guardada so na pasta privada, fixa.
        self.assertEqual(done.reviewed_path.name, REVIEWED_FILENAME)
        self.assertEqual(done.reviewed_path.parent, first.roster_path.parent)
        reviewed = Roster.load(done.reviewed_path)
        self.assertEqual(reviewed.get("pessoa:marlene oliveira sampaio").action, KEEP)
        self.assertTrue(reviewed.get("pessoa:marlene oliveira sampaio").pinned)
        self.assertFalse((first.job.export_dir / REVIEWED_FILENAME).exists())
        self.assertFalse((new.job.export_dir / REVIEWED_FILENAME).exists())
        self.assertFalse((new.job.export_dir / ROSTER_FILENAME).exists())

    def test_two_rounds_in_the_same_second_do_not_overwrite_each_other(self) -> None:
        base, source = self._workspace()
        paths = dict(export_root=base / "exp", private_root=base / "priv")
        one = run_llm_job([source], discoverer=FakeDiscoverer(), use_cache=False, **paths)
        two = run_llm_job([source], discoverer=FakeDiscoverer(), use_cache=False, **paths)
        self.assertNotEqual(one.job.job_id, two.job.job_id)
        self.assertTrue(one.job.outcomes[0].markdown_path.is_file())

    def test_elenco_json_carries_token_and_occurrences_for_hand_review(self) -> None:
        base, source = self._workspace()
        first = run_llm_job(
            [source], export_root=base / "e", private_root=base / "p",
            discoverer=FakeDiscoverer(), use_cache=False,
        )
        payload = json.loads(first.roster_path.read_text(encoding="utf-8"))
        by_name = {item["nome"]: item for item in payload["entradas"]}
        self.assertEqual(by_name["OLAVO RICARDO TEIXEIRA"]["ocorrencias"], 2)
        self.assertEqual(by_name["OLAVO RICARDO TEIXEIRA"]["token"], first.tokens["pessoa:olavo ricardo teixeira"])
        self.assertEqual(by_name["LUCIANA MONTEFUSCO DE ALMEIDA"]["acao"], KEEP)
        self.assertEqual(by_name["LUCIANA MONTEFUSCO DE ALMEIDA"]["ocorrencias"], 1)


def _fake_langextract_model(names: dict):
    from langextract.core import base_model
    from langextract.core import types as lx_types

    class FakeModel(base_model.BaseLanguageModel):
        def infer(self, batch_prompts, **kwargs):
            for prompt in batch_prompts:
                chunk = prompt.rsplit("Q: ", 1)[-1]
                items = []
                for value, (kind, role) in names.items():
                    if value.casefold() in chunk.casefold():
                        items.append(
                            {kind: value, f"{kind}_attributes": {"papel": role, "nome_canonico": value}}
                        )
                yield [lx_types.ScoredOutput(score=1.0, output=json.dumps({"extractions": items}))]

    return FakeModel()


@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class ReapplyOfflineEndToEndTests(_Base):
    """LangExtract de verdade, modelo falso, rede bloqueada o tempo todo."""

    def test_reviewed_roster_is_reapplied_from_the_cache_alone(self) -> None:
        base, source = self._workspace()
        paths = dict(export_root=base / "exp", private_root=base / "priv")
        names = {value: (kind, role) for kind, value, role in ENTITIES}
        created: list[int] = []

        def factory():
            created.append(1)
            return _fake_langextract_model(names)

        with block_network() as attempts:
            live = LangExtractDiscoverer(
                LlmConfig(channel="gemini-pago", chunk_chars=4000),
                cache_dir=base / "priv" / "cache_llm",
                model_factory=factory,
            )
            first = run_llm_job([source], discoverer=live, verification_rounds=1, **paths)
            made_first = len(created)
            self.assertGreaterEqual(made_first, 1)
            self.assertEqual(first.engine, "gemini-pago")

            rows = build_rows(first.roster, occurrences=first.occurrences, tokens=first.tokens)
            maria = next(row for row in rows if row.name == "MARLENE OLIVEIRA SAMPAIO")
            self.assertEqual((maria.action, maria.token, maria.occurrences), (TOKENIZE, "[PESSOA_2]", 1))

            done = reapply_offline(first, {maria.key: KEEP}, [source], **paths)
        self.assertEqual(attempts, [])
        self.assertEqual(len(created), made_first, "reaplicar nao pode criar modelo")
        text = done.result.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("MARLENE OLIVEIRA SAMPAIO", text)
        self.assertNotIn("OLAVO RICARDO TEIXEIRA", text)
        self.assertTrue(done.result.job.export_allowed, done.result.job.export_block_reason)
        self.assertEqual(done.result.engine, "gemini-pago")
        audit = (done.result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertIn("sem rede", audit)
        self.assertIn("motor LLM: gemini-pago", audit)

    def test_chunk_missing_from_the_cache_blocks_the_export(self) -> None:
        base, source = self._workspace()
        paths = dict(export_root=base / "exp", private_root=base / "priv")
        names = {value: (kind, role) for kind, value, role in ENTITIES}
        with block_network() as attempts:
            # Sem cache de LLM na primeira rodada: nada fica guardado para reaproveitar.
            live = LangExtractDiscoverer(
                LlmConfig(channel="gemini-pago"),
                cache_dir=None,
                model_factory=lambda: _fake_langextract_model(names),
            )
            first = run_llm_job([source], discoverer=live, verification_rounds=0, **paths)
            done = reapply_offline(first, {}, [source], **paths)
        self.assertEqual(attempts, [])
        self.assertFalse(done.result.job.export_allowed)
        self.assertIn("nao conseguiu ler", done.result.job.export_block_reason)

    def test_unknown_engine_has_no_cache_to_reuse(self) -> None:
        base, source = self._workspace()
        paths = dict(export_root=base / "exp", private_root=base / "priv")
        first = run_llm_job([source], discoverer=FakeDiscoverer(), use_cache=False, **paths)
        with self.assertRaises(LlmConfigError):
            reapply_offline(first, {}, [source], **paths)

    def test_offline_discoverer_uses_the_same_cache_key_as_the_online_one(self) -> None:
        offline = build_offline_discoverer("gemini-pago", cache_dir=Path("x"))
        online = LangExtractDiscoverer(LlmConfig(provider="gemini", channel="gemini-pago"), cache_dir=Path("x"))
        from anonimizador_app.llm_discovery import Chunk

        chunk = Chunk(0, 5, "texto")
        self.assertEqual(offline._cache_path(chunk), online._cache_path(chunk))


if __name__ == "__main__":
    unittest.main()
