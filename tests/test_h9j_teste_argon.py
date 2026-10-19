"""H9-j: correcoes do teste ponta a ponta de 16/10/2026 (anonimizacao local -> peca no Argon -> desanonimizacao).

Numeros e nomes inventados (CPF/CNPJ de teste com digito verificador valido); sem rede, sem modelo.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.invisible_chars import count_invisible, normalize_invisible

ZW = "​"


class InvisibleCharsTests(unittest.TestCase):
    def test_zero_width_between_words_becomes_a_space(self) -> None:
        text = f"o{ZW}{ZW}Incidente{ZW}{ZW}de{ZW}{ZW}Desconsideração"
        self.assertEqual(normalize_invisible(text), "o Incidente de Desconsideração")

    def test_zero_width_around_spaces_collapses_to_one_space(self) -> None:
        text = f"sobre{ZW} {ZW}oito{ZW} {ZW}veículos{ZW} {ZW}—{ZW} {ZW}de placas ABC-1234,{ZW} {ZW}DEF-5678"
        self.assertEqual(normalize_invisible(text), "sobre oito veículos — de placas ABC-1234, DEF-5678")

    def test_inside_word_marks_are_removed_and_lines_kept(self) -> None:
        text = "Desconsi­deração\n<!-- pagina-origem: 2 -->\n﻿Texto‍\n  | a | b |"
        self.assertEqual(normalize_invisible(text), "Desconsideração\n<!-- pagina-origem: 2 -->\nTexto\n  | a | b |")
        self.assertEqual(count_invisible(normalize_invisible(text)), 0)

    def test_text_without_invisible_is_untouched(self) -> None:
        text = "Linha  com  dois espaços\n\n| tabela |"
        self.assertIs(normalize_invisible(text), text)

    def test_cached_extraction_comes_back_normalized(self) -> None:
        from anonimizador_app.extraction_cache import cache_key, load_or_extract

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text("x", encoding="utf-8")
            version = "teste|1"
            digest = cache_key(source.read_bytes(), version)
            (base / f"{digest}.md").write_text(f"CDA{ZW}{ZW}80.6.21.000123-45", encoding="utf-8")
            (base / f"{digest}.json").write_text('{"paginas": 1, "extrator": "teste"}', encoding="utf-8")
            got = load_or_extract(source, cache_dir=base, version_string=version, extract_fn=lambda _p: None)
        self.assertEqual(got.document.markdown, "CDA 80.6.21.000123-45")


def _structured(text: str) -> list[tuple[str, str]]:
    from anonimizador_app.fidelity import drop_protected_spans
    from anonimizador_app.rules import build_recognizers
    from anonimizador_app.settings import AppSettings
    from anonimizador_juridico import StructuredDetector, resolve_overlaps

    detector = StructuredDetector(recognizers=build_recognizers(AppSettings()))
    spans = drop_protected_spans(text, resolve_overlaps(detector.detect(text)), [])
    return [(span.entity_type, text[span.start : span.end]) for span in spans]


def _tables(text: str) -> list[tuple[str, str]]:
    from anonimizador_app.table_labels import TableLabelsEngine

    return [(span.entity_type, text[span.start : span.end]) for span in TableLabelsEngine().detect(text)]


class NewNumberClassesTests(unittest.TestCase):
    def test_cda_with_space_separated_groups_after_label(self) -> None:
        text = "Os créditos integram a CDA nº 80 2 19 012345-67, oriunda do PAF"
        self.assertIn(("BR_CDA", "80 2 19 012345-67"), _structured(text))

    def test_process_with_typo_in_check_digit_after_execucao_fiscal(self) -> None:
        text = "apenas em relação à Execução Fiscal n. 5001234-56.2019.4.04.7200.\nFoi deferida"
        self.assertIn(("BR_PROCESSO", "5001234-56.2019.4.04.7200"), _structured(text))

    def test_old_federal_court_process_number(self) -> None:
        text = "autuada originariamente sob o nº\n2005.71.00.012345-0. Registre-se"
        self.assertIn(("BR_PROCESSO", "2005.71.00.012345-0"), _structured(text))

    def test_civil_registry_certificate_number(self) -> None:
        text = "matrícula 123456 01 55 2015 1 00012 123\n0012345 12 e outra"
        self.assertEqual(_structured(text), [("BR_REGISTRO_CIVIL", "123456 01 55 2015 1 00012 123\n0012345 12")])

    def test_fiscal_procedure_warrant(self) -> None:
        text = "Mandado de Procedimento Fiscal nº 08.1.23.00-2005-00012-3 de 2004"
        self.assertIn(("BR_PROCESSO_ADMINISTRATIVO", "08.1.23.00-2005-00012-3"), _structured(text))

    def test_url_with_numeric_id_but_not_institutional_url(self) -> None:
        self.assertEqual(_structured("ver https://siscom.exemplo.gov.br/p?acao=x&IdSiscomMensagem=123456")[0][0], "BR_URL")
        self.assertEqual(_structured("veja https://www.trf4.jus.br/trf4/ e https://www.gov.br/pgfn?pagina=2"), [])

    def test_values_and_page_numbers_are_not_caught(self) -> None:
        self.assertEqual(_structured("R$ 12.345,67 em 2007 e fls. 12 13 14"), [])


class TableLabelFormatsTests(unittest.TestCase):
    def test_label_value_pairs_in_one_table_row(self) -> None:
        text = (
            "| Placa | DEF5678 | Renavam | 00345678901 | Ano Fabricação | 2014 |\n|---|---|---|---|---|---|\n"
            "| Chassi | 9BWZZZ377VT004251 | Marca/Modelo | I/MMC PAJERO |"
        )
        self.assertEqual(
            _tables(text),
            [("BR_PLACA", "DEF5678"), ("BR_RENAVAM", "00345678901"), ("BR_CHASSI", "9BWZZZ377VT004251")],
        )

    def test_numbered_label_and_plate_list(self) -> None:
        self.assertEqual(
            _tables("chassi nº 93HGM2650YZ012345 e placa n. ABC-1D23"),
            [("BR_CHASSI", "93HGM2650YZ012345"), ("BR_PLACA", "ABC-1D23")],
        )
        found = _tables("de placas ABC-1234, DEF-5678, JKL-3456 e MNO-7890 — penhora")
        self.assertEqual([value for _kind, value in found], ["ABC-1234", "DEF-5678", "JKL-3456", "MNO-7890"])

    def test_bank_receipt_in_columns_needs_both_labels(self) -> None:
        text = "Tipo de Justiça: FEDERAL\nAgencia\nConta\nValor\nProduto\n3456\n7890-1\n264.101,07\nConta Facil PJ"
        self.assertEqual(_tables(text), [("BR_CONTA_BANCARIA", "3456"), ("BR_CONTA_BANCARIA", "7890-1")])
        self.assertEqual(_tables("Valor\nProduto\n3456\n7890-1"), [])


class RoleWordsAndPersonSpanTests(unittest.TestCase):
    def test_procedural_role_is_never_a_name(self) -> None:
        from anonimizador_app.role_labels import is_role_label

        for value in ("Requerida", "REQUERIDA", "requerida e agravante", "Executada", "embargante"):
            self.assertTrue(is_role_label(value), value)
        self.assertFalse(is_role_label("Maria Requerida"))

    def test_person_span_stops_at_comma_and_lowercase_word(self) -> None:
        from anonimizador_app.span_hygiene import sanitize_spans
        from anonimizador_juridico import EntitySpan

        def clip(text: str, value: str) -> list[str]:
            start = text.index(value)
            span = EntitySpan(start=start, end=start + len(value), entity_type="PERSON", score=0.9,
                              recognizer="NerLocal", canonical_key="x")
            return [text[s.start : s.end] for s in sanitize_spans(text, [span])]

        self.assertEqual(
            clip("cadeia: a Marta Maria Silveira, corroborando a cadeia.", "a Marta Maria Silveira, corroborando a cadeia"),
            ["Marta Maria Silveira"],
        )
        self.assertEqual(clip("o socio José de Souza e Silva compareceu", "José de Souza e Silva compareceu"),
                         ["José de Souza e Silva"])
        self.assertEqual(clip("ocr: maria fictícia souza assinou", "maria fictícia souza"), ["maria fictícia souza"])


class PublicContactAndAgentTests(unittest.TestCase):
    def test_institutional_contacts_stay_clear_and_private_ones_do_not(self) -> None:
        from anonimizador_app.fidelity import is_institutional_contact

        def check(text: str, value: str, kind: str) -> bool:
            start = text.index(value)
            return is_institutional_contact(text, start, start + len(value), kind)

        self.assertTrue(check("Contato: scvfl10@jfsc.jus.br", "scvfl10@jfsc.jus.br", "EMAIL_ADDRESS"))
        self.assertFalse(check("Contato: fulano@gmail.com", "fulano@gmail.com", "EMAIL_ADDRESS"))
        self.assertTrue(check("10ª Vara Federal de Florianópolis - Fone (48) 3251-2500", "(48) 3251-2500", "BR_PHONE"))
        self.assertFalse(check("O executado reside na Rua Exemplo, 100; telefone (48) 99999-0000", "(48) 99999-0000", "BR_PHONE"))
        long_line = "Requer ao tribunal " + "que determine a citação da parte no endereço indicado " * 3 + "Rua Exemplo, 100."
        self.assertFalse(check(long_line, "Rua Exemplo, 100", "LOCATION"))

    def test_eproc_server_code_is_public_agent_cue_but_oab_is_not(self) -> None:
        from anonimizador_app.batch_propagation import _ROLE_BEFORE_STRICT

        self.assertTrue(_ROLE_BEFORE_STRICT.search("— Evento: 12 — Usuário: MAC99 - "))
        self.assertFalse(_ROLE_BEFORE_STRICT.search("— Usuário: SC015145 - "))


class IdentityTests(unittest.TestCase):
    def test_same_cnpj_root_next_to_two_names_is_one_company(self) -> None:
        from anonimizador_app.roster import Mention, Roster
        from anonimizador_app.roster_identity import fuse_identities

        roster = Roster()
        for name in ("Alfa Holding Patrimonial Ltda", "Beta Comercio e Transportes Ltda", "Outra Empresa Ficticia Ltda"):
            roster.add_mention(Mention(kind="empresa", text=name, role="parte", canonical=name), source="llm")
        texts = [
            "| Proprietário | BETA COMERCIO E TRANSPORTES LTDA | CNPJ | 11.222.333/0001-81 |\n",
            "A requerida Alfa Holding Patrimonial Ltda, inscrita no CNPJ nº 11.222.333/0002-62, alterou o nome.\n",
            "Outra Empresa Ficticia Ltda (CNPJ 05.432.187/0001-16).",
        ]
        removed = fuse_identities(roster, texts)
        self.assertEqual(len(removed), 1)
        self.assertEqual(roster.identity_reasons.get("cnpj"), 1)

    def test_cnpj_far_from_the_name_does_not_link(self) -> None:
        from anonimizador_app.roster import Mention, Roster
        from anonimizador_app.roster_identity import fuse_identities

        roster = Roster()
        for name in ("Alfa Ficticia Comercio Ltda", "Beta Ficticia Industria Ltda"):
            roster.add_mention(Mention(kind="empresa", text=name, role="parte", canonical=name), source="llm")
        texts = [
            "Alfa Ficticia Comercio Ltda vendeu a Beta Ficticia Industria Ltda, que tem CNPJ 11.222.333/0001-81.\n",
            "Beta Ficticia Industria Ltda, CNPJ 11.222.333/0001-81.\n",
        ]
        self.assertEqual(fuse_identities(roster, texts), {})

    def test_partial_name_section_lists_candidates_without_names(self) -> None:
        from anonimizador_app.pipeline_llm import _partial_name_rows
        from anonimizador_app.roster import Mention, Roster

        roster = Roster()
        for name in ("Alvaro Ficticio Prado", "Prado", "Marta Maria Prado", "Alvaro"):
            roster.add_mention(Mention(kind="pessoa", text=name, role="parte", canonical=name), source="llm")
        tokens = {entry.key: f"[PESSOA_{index + 1}]" for index, entry in enumerate(roster.to_tokenize())}
        rows = _partial_name_rows(roster, tokens)
        self.assertIn(("[PESSOA_2]", ["[PESSOA_1]", "[PESSOA_3]"], "sobrenome"), rows)
        self.assertIn(("[PESSOA_4]", ["[PESSOA_1]"], "prenome"), rows)


class ReversalTests(unittest.TestCase):
    def test_preferred_form_keeps_accents_and_mixed_case(self) -> None:
        from anonimizador_juridico.pseudonymizer import MapRecord

        record = MapRecord(
            "[PESSOA_6]", "PERSON", "x", ["HELENA FICTICIA", "Helena Ficticia", "Helena Fictícia"],
            counts={"HELENA FICTICIA": 5, "Helena Ficticia": 2, "Helena Fictícia": 1},
        )
        self.assertEqual(record.preferred(), "Helena Fictícia")

    def test_map_dump_has_preferred_form(self) -> None:
        from anonimizador_juridico import PseudonymMap

        mapping = PseudonymMap()
        for value in ("ACOBENS FICTICIA LTDA", "ACOBENS FICTICIA LTDA", "Açobens Fictícia Ltda"):
            mapping.resolve("ORGANIZATION", value, canonical_key="empresa:acobens")
        record = mapping.to_dict()["records"][0]
        self.assertEqual(record["preferida"], "Açobens Fictícia Ltda")

    def test_reverter_reverts_and_checks_another_ai(self) -> None:
        import json
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import reverter_lote

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mapa = base / "mapa.json"
            mapa.write_text(json.dumps({"schema_version": 2, "records": [
                {"token": "[PESSOA_1]", "entity_type": "PERSON", "canonical": "x",
                 "originals": ["HELENA FICTICIA", "Helena Fictícia"], "preferida": "Helena Fictícia"},
                {"token": "[EMPRESA_1]", "entity_type": "ORGANIZATION", "canonical": "y", "originals": ["Alfa Ficticia Ltda"]},
            ]}), encoding="utf-8")
            anon = base / "peca.md"
            anon.write_text("A sócia [PESSOA_1] da [EMPRESA_1·G1].\nSem token.", encoding="utf-8")
            good = base / "boa.md"
            good.write_text("A sócia Helena Ficticia da Alfa Ficticia Ltda.\nSem token.", encoding="utf-8")
            bad = base / "ruim.md"
            bad.write_text("A sócia Alfa Ficticia Ltda da Helena Fictícia.\nSem token alterado.", encoding="utf-8")
            self.assertEqual(reverter_lote.main([str(mapa), str(anon), "--saida", str(base / "out.txt")]), 0)
            self.assertEqual((base / "out.txt").read_text(encoding="utf-8"),
                             "A sócia Helena Fictícia da Alfa Ficticia Ltda.\nSem token.")
            records = reverter_lote.load_map(mapa)
            ok = reverter_lote.check(anon.read_text(encoding="utf-8"), good.read_text(encoding="utf-8"), records)
            self.assertEqual((ok["iguais_ao_mapa"], ok["diferentes_do_mapa"]), (2, 0))
            wrong = reverter_lote.check(anon.read_text(encoding="utf-8"), bad.read_text(encoding="utf-8"), records)
            self.assertEqual(wrong["diferentes_do_mapa"], 2)
            self.assertEqual(wrong["linhas_sem_token_alteradas"], 1)


class LoteAlertTests(unittest.TestCase):
    TEXT = (
        "<!-- pagina-origem: 1 -->\nProcesso 5000000-00.2024.4.04.7200, Evento 125, PET1, Página 1\n"
        "REFER. AO EVENTO: 121\nTexto da petição.\n"
        "<!-- pagina-origem: 2 -->\nProcesso 5000000-00.2024.4.04.7200, Evento 113, DECISÃO2, Página 1\n"
        "Decrete-se o nível de sigilo 2 nestes autos. Segredo de justiça.\n"
        "<!-- pagina-origem: 3 -->\nREFER. AO EVENTO: 113\n"
    )

    def test_secrecy_pages_and_missing_events(self) -> None:
        from anonimizador_app import lote_alerts

        alerts = lote_alerts.analyze([("doc_1.md", self.TEXT)])
        self.assertEqual([page for page, _label in alerts.secrecy["doc_1.md"]], [2])
        self.assertEqual(alerts.missing_events, {"doc_1.md": [121]})
        lines = lote_alerts.alert_lines(alerts)
        self.assertIn("nível de sigilo citado: 2", lines[0])

    def test_instructions_file_mentions_missing_events_and_secrecy(self) -> None:
        from anonimizador_app import lote_alerts

        with tempfile.TemporaryDirectory() as tmp:
            path = lote_alerts.write_ai_instructions(Path(tmp), lote_alerts.analyze([("doc_1.md", self.TEXT)]))
            body = path.read_text(encoding="utf-8")
        self.assertIn("Eventos citados e ausentes deste lote:** 121", body)
        self.assertIn("marca de sigilo nas folhas 2", body)
        self.assertIn("Não deduza parentesco", body)


class Round2FixesTests(unittest.TestCase):
    """Ajustes da avaliacao da rodada 2 (18/10/2026)."""

    def test_cnpj_fusion_reads_masked_text_in_both_orders(self) -> None:
        from anonimizador_app.roster import Mention, Roster
        from anonimizador_app.roster_identity import fuse_identities

        roster = Roster()
        for name in ("Alfa Holding Patrimonial Ltda", "Beta Comercio e Transportes Ltda", "Outra Empresa Ficticia Ltda"):
            roster.add_mention(Mention(kind="empresa", text=name, role="parte", canonical=name), source="llm")
        tokens = {"[CNPJ_1]": "11222333000181", "[CNPJ_2]": "11222333000262", "[CNPJ_3]": "05432187000116"}
        texts = [
            "| Proprietário | BETA COMERCIO E TRANSPORTES LTDA | CNPJ | [CNPJ_1] |\n",
            "| [CNPJ_2]: Alfa Holding Patrimonial Ltda | R$ 10,00 |\n",
            "Outra Empresa Ficticia Ltda ([CNPJ_3]).",
        ]
        self.assertEqual(len(fuse_identities(roster, texts, tokens)), 1)
        self.assertEqual(roster.identity_reasons.get("cnpj"), 1)

    def test_bank_header_with_table_pipe_is_a_financial_position(self) -> None:
        from anonimizador_app.batch_propagation import _financial_position

        text = "Respostas\nBCO BRADESCO S.A.\n\n| Data/hora protocolo | Tipo de ordem |"
        start = text.index("BCO")
        self.assertTrue(_financial_position(text, start, text.index("S.A") + 3))

    def test_role_with_article_and_lowercase_word_and_comment(self) -> None:
        from anonimizador_app.role_labels import is_role_label
        from anonimizador_app.span_hygiene import sanitize_spans
        from anonimizador_juridico import EntitySpan

        for value in ("A REQUERIDA", "requerida e ora agravante"):
            self.assertTrue(is_role_label(value), value)
        self.assertFalse(is_role_label("A Maria"))
        text = "<!-- cobertura: em-branco=0 -->\nO campo esta em branco hoje"

        def span(value: str, after: int = 0) -> EntitySpan:
            start = text.index(value, after)
            return EntitySpan(start=start, end=start + len(value), entity_type="PERSON", score=0.9,
                              recognizer="NerLocal", canonical_key="x")

        self.assertEqual(sanitize_spans(text, [span("branco"), span("branco", 40)]), [])

    def test_cda_broken_by_newline_plate_with_zero_and_repeated_account_rows(self) -> None:
        text = "as inscrições derivadas da CDA nº 91 6 06\n003142-93 — de nºs 2 e 3"
        self.assertIn(("BR_CDA", "91 6 06\n003142-93"), _structured(text))
        self.assertIn(("BR_PLACA", "XY0-4321"), _tables("de placas ABC-1234, DEF-5678 e XY0-4321 — penhora"))
        rows = (
            "Agencia\nConta\nValor\nProduto\n3456\n7890-1\n264.101,07\nConta Facil PJ\n"
            "1.00\nConta Facil PJ\n3456\n7890-1\n264.100.07\nINVESTFACIL\n"
        )
        found = [value for kind, value in _tables(rows) if kind == "BR_CONTA_BANCARIA"]
        self.assertEqual(found, ["3456", "7890-1", "3456", "7890-1"])
        self.assertEqual(_tables("Valor\nProduto\n3456\n7890-1"), [])

    def test_split_in_halves(self) -> None:
        from anonimizador_app.llm_discovery import Chunk, split_in_halves

        text = ("Primeiro paragrafo com varias palavras. " * 12) + "\n\n" + ("Segundo paragrafo com outras palavras. " * 12)
        halves = split_in_halves(Chunk(start=100, end=100 + len(text), text=text))
        self.assertEqual(len(halves), 2)
        self.assertEqual(halves[0].text + halves[1].text, text)
        self.assertEqual((halves[0].start, halves[1].start), (100, 100 + len(halves[0].text)))
        self.assertEqual(split_in_halves(Chunk(start=0, end=20, text="curto")), [])

    def test_leiame_has_group_note_only_when_there_is_a_group(self) -> None:
        from anonimizador_app.pipeline import write_lote_guides

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            export, private = base / "exp", base / "priv"
            export.mkdir()
            (export / "doc_1.md").write_text("Empresa [EMPRESA_1] e [EMPRESA_2].", encoding="utf-8")
            write_lote_guides(job_id="j", export_dir=export, map_path=private / "mapa.json", markdown_names=["doc_1.md"])
            plain = (export / "LEIA-ME.txt").read_text(encoding="utf-8")
            (export / "doc_1.md").write_text("Empresa [EMPRESA_1·G1] e [EMPRESA_2·G1].", encoding="utf-8")
            write_lote_guides(job_id="j", export_dir=export, map_path=private / "mapa.json", markdown_names=["doc_1.md"])
            grouped = (export / "LEIA-ME.txt").read_text(encoding="utf-8")
        self.assertNotIn("Sufixo de grupo", plain)
        self.assertIn("Sufixo de grupo", grouped)
        self.assertIn("reverter_lote.py", plain)


class CaseListFolderTests(unittest.TestCase):
    def test_case_list_is_carried_to_an_empty_folder_only(self) -> None:
        from anonimizador_app.settings import AppSettings, carry_case_list

        old = AppSettings(target_people=["Maria Ficticia"], target_cdas=["80.6.21.000123-45"], target_roles={"Maria Ficticia": "socio"})
        empty = AppSettings()
        self.assertTrue(carry_case_list(old, empty))
        self.assertEqual(empty.target_people, ["Maria Ficticia"])
        self.assertEqual(empty.target_roles, {"Maria Ficticia": "socio"})
        own = AppSettings(target_people=["Outra Pessoa"])
        self.assertFalse(carry_case_list(old, own))
        self.assertEqual(own.target_people, ["Outra Pessoa"])

    def test_audit_warns_when_case_list_is_empty(self) -> None:
        from anonimizador_app.settings import AppSettings, case_list_lines

        self.assertTrue(case_list_lines(AppSettings())[0].startswith("ALERTA: lista do caso VAZIA"))
        self.assertIn("pessoas 1", case_list_lines(AppSettings(target_people=["Maria Ficticia"]))[0])


if __name__ == "__main__":
    unittest.main()
