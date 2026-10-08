"""Mencoes em claro por entidade e por lote (tools/medir_vazamento.py). Dados ficticios."""

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
_SPEC = importlib.util.spec_from_file_location("medir_vazamento", ROOT / "tools" / "medir_vazamento.py")
medir = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = medir
_SPEC.loader.exec_module(medir)

LOTE_A = """A UNIÃO (FAZENDA NACIONAL) executa [EMPRESA_1]. O sócio [PESSOA_1] foi citado; [PESSOA_1] não pagou.
Manifestou-se o Procurador RAIMUNDO NONATO FURTADO. Ordem SISBAJUD ao BANCO PALMEIRAS DO SUL S A.
Documento assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal. CPF [CPF_1].
"""
LOTE_B = """A UNIÃO (FAZENDA NACIONAL) executa Alfaville Metais Ltda. O sócio OLAVO RICARDO TEIXEIRA foi citado.
Manifestou-se o Procurador [PESSOA_2]. Ordem SISBAJUD ao [EMPRESA_2].
Documento assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal. CPF 529.982.247-25.
"""
ENTIDADES = [
    {"categoria": "PESSOA-socio", "rotulo": "pessoa 1", "padroes": ["OLAVO RICARDO TEIXEIRA"], "acao": "tokenizar"},
    {"categoria": "EMPRESA-empresa_privada", "rotulo": "empresa 1", "padroes": ["Alfaville Metais Ltda"], "acao": "tokenizar"},
    {"categoria": "AGENTE-PUBLICO", "rotulo": "pessoa 2", "padroes": ["RAIMUNDO NONATO FURTADO"], "acao": "manter"},
    {"categoria": "AGENTE-PUBLICO", "rotulo": "pessoa 3", "padroes": ["LUCIANA MONTEFUSCO DE ALMEIDA"], "acao": "manter"},
    {"categoria": "INSTITUICAO-FINANCEIRA", "rotulo": "empresa 2", "padroes": ["BANCO PALMEIRAS DO SUL S A"], "acao": "manter"},
    # O Operador mudou a acao, mas a categoria antiga ficou: a acao manda.
    {"categoria": "PESSOA-socio", "rotulo": "pessoa 4", "padroes": ["UNIÃO (FAZENDA NACIONAL)"], "acao": "manter"},
]
IDS = [{"categoria": "ID-CPF", "rotulo": "CPF 1", "valor": "52998224725", "acao": "tokenizar"}]


class MedirVazamentoTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.lotes = []
        for name, text in (("lote_a", LOTE_A), ("lote_b", LOTE_B)):
            lote = self.base / name
            lote.mkdir()
            (lote / "PARTE_1.md").write_text(text, encoding="utf-8")
            self.lotes.append(lote)

    def _run(self, gabarito: dict) -> str:
        path = self.base / "gabarito.json"
        path.write_text(json.dumps(gabarito, ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = medir.main(["medir", str(path), *map(str, self.lotes)])
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_reviewed_gabarito_prints_two_blocks_by_action_sorted_by_category(self) -> None:
        text = self._run({"entidades": ENTIDADES, "ids": IDS})
        lines = text.splitlines()
        tokenizar = lines.index("== DEVEM SER TROCADAS (acao=tokenizar): contagem alta = vazamento ==")
        manter = lines.index("== DEVEM FICAR EM CLARO (acao=manter): contagem ZERO = tokenizacao indevida ==")
        self.assertLess(tokenizar, manter)
        first = [line.split()[0] for line in lines[tokenizar + 2 : manter] if line and not line.startswith(("RESUMO", "  "))]
        self.assertEqual(first, ["EMPRESA-empresa_pr", "ID-CPF", "PESSOA-socio"])
        second = [line.split()[0] for line in lines[manter + 2 :] if line and not line.startswith(("RESUMO", "  "))]
        self.assertEqual(second, ["AGENTE-PUBLICO", "AGENTE-PUBLICO", "INSTITUICAO-FINANC", "PESSOA-socio"])
        # pessoa 1 vazou no lote_b (1), nao no lote_a (0); pessoa 4 (acao manter) esta no segundo bloco.
        row = next(line for line in lines if " pessoa 1 " in line)
        self.assertEqual(row.split()[-2:], ["0", "1"])
        self.assertGreater(lines.index(next(line for line in lines if " pessoa 4 " in line)), manter)

    def test_each_block_ends_with_a_summary_per_category_and_lote(self) -> None:
        text = self._run({"entidades": ENTIDADES, "ids": IDS})
        self.assertEqual(text.count("RESUMO por categoria"), 2)
        self.assertIn("RESUMO por categoria (3 entidades)", text)
        self.assertIn("RESUMO por categoria (4 entidades)", text)
        lines = text.splitlines()
        # Bloco "manter": AGENTE-PUBLICO tem 2 entidades; no lote_a as duas estao em claro, no lote_b so uma.
        agente = [line for line in lines if line.startswith("  AGENTE-PUBLICO")]
        self.assertEqual(len(agente), 2)
        self.assertRegex(agente[0], r"lote_a\s+zero:\s+0\s+alguma:\s+2")
        self.assertRegex(agente[1], r"lote_b\s+zero:\s+1\s+alguma:\s+1")
        # Bloco "tokenizar": o socio ficou em claro so no lote_b.
        socio = [line for line in lines if line.startswith("  PESSOA-socio")]
        self.assertRegex(socio[0], r"lote_a\s+zero:\s+1\s+alguma:\s+0")
        self.assertRegex(socio[1], r"lote_b\s+zero:\s+0\s+alguma:\s+1")

    def test_summary_helper_counts_zero_and_some_per_category_and_lote(self) -> None:
        rows = [({"categoria": "X"}, [0, 2]), ({"categoria": "X"}, [0, 0]), ({"categoria": "Y"}, [3, 0])]
        self.assertEqual(medir.summary(rows, ["a", "b"]),
                         [("X", "a", 2, 0), ("X", "b", 1, 1), ("Y", "a", 0, 1), ("Y", "b", 1, 0)])

    def test_only_labels_and_counts_reach_the_screen(self) -> None:
        text = self._run({"entidades": ENTIDADES, "ids": IDS})
        for name in ("OLAVO", "Alfaville", "RAIMUNDO", "LUCIANA", "PALMEIRAS", "529", "52998224725"):
            self.assertNotIn(name, text)

    def test_gabarito_without_action_keeps_the_single_table(self) -> None:
        legacy = [{k: v for k, v in row.items() if k != "acao"} for row in ENTIDADES]
        ids = [{k: v for k, v in row.items() if k != "acao"} for row in IDS]
        text = self._run({"entidades": legacy, "ids": ids})
        self.assertNotIn("==", text)
        self.assertNotIn("RESUMO", text)
        lines = text.splitlines()
        self.assertEqual(len(lines), 1 + len(legacy) + len(ids))
        self.assertTrue(lines[0].startswith("categoria"))
        self.assertTrue(lines[1].startswith("PESSOA-socio"))  # ordem do arquivo, como antes


if __name__ == "__main__":
    unittest.main()
