"""Gabarito pre-classificado a partir da uniao dos mapas (tools/montar_gabarito.py). Dados ficticios."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("montar_gabarito", ROOT / "tools" / "montar_gabarito.py")
montar = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = montar
_SPEC.loader.exec_module(montar)

TEXTO = """# EXECUÇÃO FISCAL

<!-- pagina-origem: 1 -->

A UNIÃO (FAZENDA NACIONAL) ajuíza execução fiscal contra ALFAVILLE METAIS LTDA, CNPJ 11.222.333/0001-81,
e contra o sócio OLAVO RICARDO TEIXEIRA, CPF 529.982.247-25. A Alfaville Metais Ltda não pagou.
Manifestou-se o Procurador da Fazenda Nacional RAIMUNDO NONATO FURTADO pelo prosseguimento.
Consta que MARLENE OLIVEIRA SAMPAIO, sócia, reside na Rua Olavo Bilac, nº 50. Penhora na Rua Olavo Bilac, nº 52.
Ordem SISBAJUD às instituições:
00341 - BANCO PALMEIRAS DO SUL S A
A sócia Beatriz Helena Cavalcanti não foi citada. O sócio OLAVO RICARDO TEIXEIRA foi citado de novo.
Documento eletrônico assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.
"""


def _record(token, kind, canonical, *originals):
    return {"token": token, "entity_type": kind, "canonical": canonical, "originals": list(originals)}


MAPA_LOCAL = {
    "schema_version": 2,
    "records": [
        _record("[EMPRESA_1]", "ORGANIZATION", "alfaville metais ltda", "ALFAVILLE METAIS LTDA", "Alfaville Metais Ltda"),
        _record("[PESSOA_1]", "PERSON", "olavo ricardo teixeira", "OLAVO RICARDO TEIXEIRA"),
        _record("[PESSOA_2]", "PERSON", "marlene oliveira sampaio", "MARLENE OLIVEIRA SAMPAIO"),
        _record("[PESSOA_3]", "PERSON", "olavo bilac", "Olavo Bilac"),
        _record("[CPF_1]", "BR_CPF", "52998224725", "529.982.247-25"),
        _record("[CNPJ_1]", "BR_CNPJ", "11222333000181", "11.222.333/0001-81"),
    ],
}
MAPA_LLM = {
    "schema_version": 2,
    "records": [
        _record("[EMPRESA_1]", "ORGANIZATION", "alfaville metais ltda", "ALFAVILLE METAIS LTDA"),
        _record("[EMPRESA_2]", "ORGANIZATION", "banco palmeiras do sul s a", "BANCO PALMEIRAS DO SUL S A"),
        _record("[PESSOA_1]", "PERSON", "olavo ricardo teixeira", "OLAVO RICARDO TEIXEIRA"),
        _record("[PESSOA_2]", "PERSON", "marlene oliveira sampaio", "MARLENE OLIVEIRA SAMPAIO"),
        _record("[PESSOA_3]", "PERSON", "raimundo nonato furtado", "RAIMUNDO NONATO FURTADO"),
        _record("[PESSOA_4]", "PERSON", "luciana montefusco de almeida", "LUCIANA MONTEFUSCO DE ALMEIDA"),
        _record("[EMPRESA_3]", "ORGANIZATION", "uniao (fazenda nacional)", "UNIÃO (FAZENDA NACIONAL)"),
        _record("[CPF_1]", "BR_CPF", "52998224725", "529.982.247-25"),
    ],
}
ELENCO_LLM = {
    "entradas": [
        {"chave": "pessoa:luciana montefusco de almeida", "tipo": "pessoa", "nome": "LUCIANA MONTEFUSCO DE ALMEIDA",
         "papeis": ["magistrado"], "variantes": [], "acao": "manter", "origem": "llm", "fixo": False},
        {"chave": "pessoa:olavo ricardo teixeira", "tipo": "pessoa", "nome": "OLAVO RICARDO TEIXEIRA",
         "papeis": ["socio"], "variantes": [], "acao": "tokenizar", "origem": "llm", "fixo": False},
    ]
}


class GabaritoTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.private = base / "privados"
        self.lote_local = self.private / "lote_1"
        self.lote_llm = self.private / "lote_2"
        for lote, mapa, engine in ((self.lote_local, MAPA_LOCAL, "motor local"), (self.lote_llm, MAPA_LLM, "motor LLM: gemini-pago")):
            lote.mkdir(parents=True)
            (lote / "mapa.json").write_text(json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
            (lote / "LEIA-ME.txt").write_text(
                f"Gerado por: v0.4.1 · extração x · detecção 2.8.3 · {engine} · código abc\n"
                "A configuracao do app (regras, motor local) fica em config.json.\n",
                encoding="utf-8",
            )
        (self.lote_llm / "elenco.json").write_text(json.dumps(ELENCO_LLM, ensure_ascii=False), encoding="utf-8")
        self.texts = base / "textos"
        self.texts.mkdir()
        (self.texts / "PARTE_1.md").write_text(TEXTO, encoding="utf-8")
        self.payload = montar.build([self.lote_local, self.lote_llm], self.private, None, self.texts)
        self.rows = {row["padroes"][0].casefold(): row for row in self.payload["entidades"]}

    def test_engines_are_read_from_the_version_line_only(self) -> None:
        self.assertEqual(self.payload["motores"], ["gemini-pago", "local"])

    def test_union_keeps_what_only_one_engine_found_and_flags_it(self) -> None:
        row = self.rows["luciana montefusco de almeida"]
        self.assertEqual(row["motores"], ["gemini-pago"])
        self.assertTrue(row["revisar"])
        self.assertIn("so um motor", row["motivo_revisao"])

    def test_public_roles_are_kept(self) -> None:
        self.assertEqual(self.rows["raimundo nonato furtado"]["acao"], "manter")
        self.assertEqual(self.rows["raimundo nonato furtado"]["categoria"], "AGENTE-PUBLICO")
        self.assertEqual(self.rows["luciana montefusco de almeida"]["papel"], "agente_publico")
        self.assertEqual(self.rows["união (fazenda nacional)"]["acao"], "manter")
        self.assertEqual(self.rows["união (fazenda nacional)"]["categoria"], "ORGAO-PUBLICO")
        self.assertEqual(self.rows["banco palmeiras do sul s a"]["papel"], "instituicao_financeira")
        self.assertEqual(self.rows["banco palmeiras do sul s a"]["acao"], "manter")

    def test_partners_and_the_company_are_tokenized_with_roles_from_cues_and_llm(self) -> None:
        olavo = self.rows["olavo ricardo teixeira"]
        self.assertEqual((olavo["acao"], olavo["papel"]), ("tokenizar", "socio"))
        self.assertEqual(olavo["motores"], ["gemini-pago", "local"])
        self.assertEqual(olavo["ocorrencias"], 2)
        self.assertEqual(self.rows["marlene oliveira sampaio"]["papel"], "socio")
        company = self.rows["alfaville metais ltda"]
        self.assertEqual(company["acao"], "tokenizar")
        self.assertEqual(company["ocorrencias"], 2)  # as duas grafias, uma busca sem acento e sem caixa

    def test_a_street_named_after_a_person_is_flagged_as_address(self) -> None:
        row = self.rows["olavo bilac"]
        self.assertEqual(row["papel"], "logradouro")
        self.assertTrue(row["revisar"])

    def test_the_sweep_finds_the_partner_no_engine_caught(self) -> None:
        candidates = [row for row in self.payload["entidades"] if row["categoria"] == "CANDIDATO-sem-motor"]
        names = {row["padroes"][0] for row in candidates}
        self.assertIn("Beatriz Helena Cavalcanti", names)
        self.assertTrue(all(row["revisar"] for row in candidates))

    def test_ids_go_to_the_ids_list_in_medir_vazamento_format(self) -> None:
        ids = {row["categoria"]: row for row in self.payload["ids"]}
        self.assertIn("ID-CPF", ids)
        self.assertIn("ID-CNPJ", ids)
        self.assertEqual(ids["ID-CPF"]["valor"], "52998224725")
        self.assertEqual(ids["ID-CPF"]["motores"], ["gemini-pago", "local"])

    def test_output_is_readable_by_medir_vazamento_and_review_lists_doubts_first(self) -> None:
        for row in self.payload["entidades"]:
            self.assertTrue(row["categoria"] and row["rotulo"] and row["padroes"])
        review = montar.review_markdown(self.payload)
        self.assertIn("NAO ENVIAR", review)
        first_rows = [row for row in self.payload["entidades"][:3]]
        self.assertTrue(all(row["revisar"] for row in first_rows))

    def test_cli_prints_only_counts(self) -> None:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = montar.main([str(self.lote_local), str(self.lote_llm), "--textos", str(self.texts), "--privados", str(self.private), "--nome", "g1"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        for name in ("OLAVO", "Olavo", "Beatriz", "Alfaville", "RAIMUNDO"):
            self.assertNotIn(name, text)
        self.assertTrue((self.private / "gabaritos" / "g1.json").is_file())
        self.assertTrue((self.private / "gabaritos" / "g1.REVISAO.md").is_file())


if __name__ == "__main__":
    unittest.main()
