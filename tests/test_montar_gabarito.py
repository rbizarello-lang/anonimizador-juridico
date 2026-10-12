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

    def test_a_lone_engine_is_a_reason_only_with_three_engines_or_a_rare_entity(self) -> None:
        def entity(occurrences: int) -> montar.Entity:
            item = montar.Entity(kind="pessoa", core="fulano de tal", originals=["Fulano de Tal"], engines={"local"})
            item.occurrences = occurrences
            return item

        # Dois motores e a entidade aparece 2+ vezes: fato registrado em sinais, nao motivo de revisao.
        row = montar.classify(entity(2), total_engines=2)
        self.assertFalse(row["revisar"])
        self.assertTrue(row["sinais"]["so_um_motor"])
        self.assertEqual(row["certeza"], "media")
        # Dois motores, mas so uma ocorrencia (ou nenhuma): continua a revisar.
        self.assertIn("so um motor", montar.classify(entity(1), total_engines=2)["motivo_revisao"])
        self.assertIn("so um motor", montar.classify(entity(0), total_engines=2)["motivo_revisao"])
        # Tres motores: um so achou e sempre suspeito.
        self.assertIn("so um motor", montar.classify(entity(5), total_engines=3)["motivo_revisao"])
        # Dois motores acharam: sem sinal.
        both = entity(2)
        both.engines = {"local", "gemini-pago"}
        self.assertFalse(montar.classify(both, total_engines=2)["sinais"]["so_um_motor"])
        # O REVISAO.md mostra o fato na coluna de motores.
        payload = {**self.payload, "entidades": [{**row, "rotulo": "pessoa 9"}]}
        line = next(item for item in montar.review_markdown(payload).splitlines() if "pessoa 9" in item)
        self.assertIn("local (so este)", line)

    def test_prefixed_canonical_keys_merge_with_plain_ones(self) -> None:
        # Mapas reais: o pipeline com elenco grava "pessoa:<nucleo>" / "empresa:<nucleo sem LTDA>" e a lista
        # de alvos "alvo:pessoa:<nucleo>"; o motor local antigo grava so o nome dobrado.
        mapa_llm = {"schema_version": 2, "records": [
            _record("[EMPRESA_1]", "ORGANIZATION", "empresa:alfaville metais", "ALFAVILLE METAIS LTDA"),
            _record("[PESSOA_1]", "PERSON", "pessoa:olavo ricardo teixeira", "OLAVO RICARDO TEIXEIRA"),
        ]}
        mapa_local = {"schema_version": 2, "records": [
            _record("[EMPRESA_1]", "ORGANIZATION", "alfaville metais ltda", "Alfaville Metais Ltda"),
            _record("[PESSOA_1]", "PERSON", "alvo:pessoa:olavo ricardo teixeira", "OLAVO RICARDO TEIXEIRA"),
        ]}
        (self.lote_local / "mapa.json").write_text(json.dumps(mapa_local, ensure_ascii=False), encoding="utf-8")
        (self.lote_llm / "mapa.json").write_text(json.dumps(mapa_llm, ensure_ascii=False), encoding="utf-8")
        payload = montar.build([self.lote_local, self.lote_llm], self.private, None, self.texts)
        names = [row for row in payload["entidades"] if row.get("motores")]
        self.assertEqual(len(names), 2)
        for row in names:
            self.assertEqual(row["motores"], ["gemini-pago", "local"])
        # O papel do elenco (chave com prefixo) continua chegando na entidade.
        olavo = next(row for row in names if row["tipo"] == "pessoa")
        self.assertEqual(olavo["acao"], "tokenizar")

    def test_same_spelling_under_different_canonicals_is_one_entity_but_a_lone_surname_is_not(self) -> None:
        mapa_llm = {"schema_version": 2, "records": [
            _record("[PESSOA_1]", "PERSON", "pessoa:marlene oliveira sampaio", "MARLENE OLIVEIRA SAMPAIO"),
            _record("[PESSOA_2]", "PERSON", "pessoa:olavo ricardo teixeira", "OLAVO RICARDO TEIXEIRA", "Teixeira"),
        ]}
        mapa_local = {"schema_version": 2, "records": [
            _record("[PESSOA_1]", "PERSON", "marlene sampaio", "Marlene Sampaio", "MARLENE OLIVEIRA SAMPAIO"),
            _record("[PESSOA_2]", "PERSON", "beatriz teixeira", "Teixeira"),
        ]}
        (self.lote_local / "mapa.json").write_text(json.dumps(mapa_local, ensure_ascii=False), encoding="utf-8")
        (self.lote_llm / "mapa.json").write_text(json.dumps(mapa_llm, ensure_ascii=False), encoding="utf-8")
        payload = montar.build([self.lote_local, self.lote_llm], self.private, None, self.texts)
        people = [row for row in payload["entidades"] if row.get("tipo") == "pessoa" and row.get("motores")]
        self.assertEqual(len(people), 3)
        marlene = next(row for row in people if "MARLENE OLIVEIRA SAMPAIO" in row["padroes"])
        self.assertEqual(marlene["motores"], ["gemini-pago", "local"])

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

    def test_reclassify_recomputes_only_the_category_from_the_reviewed_action(self) -> None:
        import contextlib
        import io

        gabarito = {
            "aviso": "x",
            "entidades": [
                {"categoria": "PESSOA-socio", "rotulo": "pessoa 1", "padroes": ["Fulano"], "tipo": "pessoa", "papel": "socio",
                 "acao": "manter", "certeza": "media", "revisar": True, "motivo_revisao": "nome de uma palavra"},
                {"categoria": "AGENTE-PUBLICO", "rotulo": "pessoa 2", "padroes": ["Beltrano"], "tipo": "pessoa",
                 "papel": "agente_publico", "acao": "manter"},
                {"categoria": "AGENTE-PUBLICO", "rotulo": "pessoa 3", "padroes": ["Sicrano"], "tipo": "pessoa",
                 "papel": "agente_publico", "acao": "tokenizar"},
                {"categoria": "MANTER-empresa-empresa_privada", "rotulo": "empresa 1", "padroes": ["Acme"], "tipo": "empresa",
                 "papel": "empresa_privada", "acao": "tokenizar"},
                {"categoria": "EMPRESA-empresa_privada", "rotulo": "empresa 2", "padroes": ["Beta"], "tipo": "empresa",
                 "papel": "empresa_privada", "acao": "tokenizar"},
                {"categoria": "CANDIDATO-sem-motor", "rotulo": "candidato 1", "padroes": ["Zeta Ypsilon"], "tipo": "pessoa",
                 "papel": "revisar", "acao": "tokenizar"},
            ],
            "ids": [{"categoria": "ID-CPF", "rotulo": "CPF 1", "valor": "52998224725", "acao": "tokenizar"}],
        }
        path = self.private / "gabaritos" / "g2.json"
        path.parent.mkdir(parents=True)
        original = json.dumps(gabarito, ensure_ascii=False, indent=2) + "\n"
        path.write_text(original, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = montar.main(["--reclassificar", str(path)])
        self.assertEqual(code, 0)
        for name in ("Fulano", "Beltrano", "Sicrano", "Acme", "Zeta"):
            self.assertNotIn(name, out.getvalue())
        backup = path.with_name("g2.antes-reclassificar.json")
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        result = json.loads(path.read_text(encoding="utf-8"))
        categories = [row["categoria"] for row in result["entidades"]]
        self.assertEqual(categories, ["MANTER-pessoa-socio", "AGENTE-PUBLICO", "PESSOA-agente_publico",
                                      "EMPRESA-empresa_privada", "EMPRESA-empresa_privada", "CANDIDATO-sem-motor"])
        # Nada alem da categoria muda: o resto do item e o restante do arquivo ficam como estavam.
        first = result["entidades"][0]
        self.assertEqual({k: v for k, v in first.items() if k != "categoria"},
                         {k: v for k, v in gabarito["entidades"][0].items() if k != "categoria"})
        self.assertEqual(result["ids"], gabarito["ids"])
        self.assertEqual(result["aviso"], "x")


if __name__ == "__main__":
    unittest.main()
