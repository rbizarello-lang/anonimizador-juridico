from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "auditar_lote.py"


def _load_auditar():
    spec = importlib.util.spec_from_file_location("auditar_lote_tool", _TOOLS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuditarLoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_auditar()

    def test_synthetic_lote_passes_gabarito(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lote = root / "lote_sintetico"
            lote.mkdir()
            (lote / "peca.md").write_text(
                "A Uniao citou [PESSOA_1] da [EMPRESA_1]. "
                "CPF [CPF_1]. SISBAJUD e execucao fiscal.\n",
                encoding="utf-8",
            )
            (lote / "mapa.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "records": [
                            {
                                "token": "[PESSOA_1]",
                                "entity_type": "PERSON",
                                "canonical": "maria ficticia da silva",
                                "originals": ["Maria Ficticia da Silva"],
                            },
                            {
                                "token": "[EMPRESA_1]",
                                "entity_type": "ORGANIZATION",
                                "canonical": "horizonte ficticio ltda",
                                "originals": ["Horizonte Ficticio Ltda"],
                            },
                            {
                                "token": "[CPF_1]",
                                "entity_type": "BR_CPF",
                                "canonical": "52998224725",
                                "originals": ["529.982.247-25"],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            gabarito = root / "gabarito.json"
            gabarito.write_text(
                json.dumps(
                    {
                        "documento": "sintetico",
                        "nao_pode_aparecer": [
                            {"texto": "Maria Ficticia da Silva", "tipo": "PESSOA"},
                            {"texto": "529.982.247-25", "tipo": "CPF"},
                        ],
                        "deve_permanecer": [
                            {"texto": "Uniao"},
                            {"texto": "SISBAJUD"},
                        ],
                        "entidades": [
                            {
                                "nome": "Maria Ficticia da Silva",
                                "tipo": "PESSOA",
                                "variantes": ["Maria Ficticia"],
                            }
                        ],
                        "observar": [{"texto": "[EMPRESA_1]", "obs": "token"}],
                        "escopo_ciclo4": [{"texto": "ABC1D23", "tipo": "PLACA"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.mod.audit_lote(lote, gabarito=gabarito, project_root=root)
            self.assertEqual(result["ocorrencias"]["PESSOA"], 1)
            self.assertEqual(result["tokens_unicos"]["PESSOA"], 1)
            self.assertIn("sisbajud", result["termos_protegidos"])
            self.assertEqual(result["veredito"], "APROVADO")
            self.assertEqual(result["contagem_falhas"], 0)

    def test_leftover_name_and_fragmentation_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lote = root / "lote_ruim"
            lote.mkdir()
            (lote / "peca.md").write_text(
                "Joao Ficticio Alves e [PESSOA_1] e [PESSOA_2]. Uniao.\n",
                encoding="utf-8",
            )
            (lote / "mapa.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "records": [
                            {
                                "token": "[PESSOA_1]",
                                "entity_type": "PERSON",
                                "canonical": "joao ficticio alves",
                                "originals": ["Joao Ficticio Alves"],
                            },
                            {
                                "token": "[PESSOA_2]",
                                "entity_type": "PERSON",
                                "canonical": "joao",
                                "originals": ["Joao"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            gabarito = root / "gabarito.json"
            gabarito.write_text(
                json.dumps(
                    {
                        "nao_pode_aparecer": [
                            {"texto": "Joao Ficticio Alves", "tipo": "PESSOA"}
                        ],
                        "deve_permanecer": [{"texto": "TRF4"}],
                        "entidades": [
                            {
                                "nome": "Joao Ficticio Alves",
                                "tipo": "PESSOA",
                                "variantes": ["Joao"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.mod.audit_lote(lote, gabarito=gabarito, project_root=root)
            self.assertEqual(result["veredito"], "REPROVADO")
            joined = " ".join(result["falhas"])
            self.assertIn("nao_pode_aparecer", joined)
            self.assertIn("deve_permanecer", joined)
            self.assertIn("entidades", joined)
            self.assertGreaterEqual(result["contagem_falhas"], 3)

    def test_compare_table_has_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "lote_old"
            new = root / "lote_new"
            old.mkdir()
            new.mkdir()
            (old / "peca.md").write_text("[PESSOA_1] [PESSOA_1]\n", encoding="utf-8")
            (new / "peca.md").write_text("[PESSOA_1]\n", encoding="utf-8")
            mapa = {
                "schema_version": 1,
                "records": [
                    {
                        "token": "[PESSOA_1]",
                        "entity_type": "PERSON",
                        "canonical": "ana",
                        "originals": ["Ana"],
                    }
                ],
            }
            (old / "mapa.json").write_text(json.dumps(mapa), encoding="utf-8")
            (new / "mapa.json").write_text(json.dumps(mapa), encoding="utf-8")
            result = self.mod.audit_lote(new, comparar=old, project_root=root)
            occ_rows = {row[0]: row for row in result["comparacao"]["ocorrencias"]}
            self.assertEqual(occ_rows["PESSOA"][1:], (2, 1, -1))


if __name__ == "__main__":
    unittest.main()
