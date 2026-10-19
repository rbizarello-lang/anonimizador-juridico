"""H9-i, lote B: lista do caso como chave unica (grafias adicionais, frases da empresa, mencao do LLM), processos,
CDAs e contatos da aba Caso trocados sem rotulo, raiz do CNPJ, zero a esquerda comido pela planilha e NIRE listado.

Numeros e nomes inventados (CPF/CNPJ de teste com digito verificador valido); sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.case_list import CaseRules, classify_contact, classify_process, parse_alias_lines
from anonimizador_app.settings import AppSettings
from anonimizador_app.target_ids import describe_listed_ids, find_listed_id_spans, parse_target_id_line
from anonimizador_app.target_list import TargetListEngine

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer

_TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")


def _values(text: str, spans) -> list[str]:
    return [text[span.start : span.end] for span in spans]


class AliasParsingTests(unittest.TestCase):
    def test_alias_line_splits_on_semicolon_and_skips_duplicates(self) -> None:
        parsed = parse_alias_lines(["Maria Ficticia Souza = Mari Souza; M. F. Souza; maria ficticia souza", "sem igual"])
        self.assertEqual(parsed, [("Maria Ficticia Souza", ("Mari Souza", "M. F. Souza"))])

    def test_contact_lines_are_classified(self) -> None:
        self.assertEqual(classify_contact("e-mail: fulano @ exemplo.com.br"), ("email", "fulano@exemplo.com.br"))
        self.assertEqual(classify_contact("(47) 98473-5213"), ("telefone", "(47) 98473-5213"))
        self.assertEqual(classify_contact("Rua das Acacias, 123, Bairro Alto")[0], "endereco")
        self.assertEqual(classify_contact("placa: ABC1D23"), ("placa", "ABC1D23"))
        self.assertIsNone(classify_contact("Rua"))

    def test_process_lines_are_classified(self) -> None:
        self.assertEqual(classify_process("5004321-55.2024.4.04.7102")[0], "BR_PROCESSO")
        self.assertEqual(classify_process("PA: 10983.720123/2019-11")[0], "BR_PROCESSO_ADMINISTRATIVO")
        self.assertIsNone(classify_process("123"))


class CaseRulesTests(unittest.TestCase):
    def test_listed_process_is_found_without_label_and_broken_by_a_line(self) -> None:
        rules = CaseRules(processes=["5004321-55.2024.4.04.7102"])
        text = "Ver autos 5004321-55.2024.\n4.04.7102 e tambem 50043215520244047102."
        found = rules.detect(text)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(span.entity_type == "BR_PROCESSO" for span in found))

    def test_listed_cda_is_found_without_label(self) -> None:
        rules = CaseRules(cdas=["80.6.21.000123-45"])
        found = rules.detect("Inscricoes: 80 6 21 000123 45 e 80.6.21.000123-45.")
        self.assertEqual([span.entity_type for span in found], ["BR_CDA", "BR_CDA"])

    def test_listed_contacts(self) -> None:
        rules = CaseRules(
            contacts=["fulano@exemplo.com.br", "(47) 98473-5213", "Rua das Acácias, 123, Bairro Alto"]
        )
        text = (
            "Contato FULANO @ exemplo.com.br, fone 47 98473 5213 ou 98473-5213. "
            "Reside na R. das Acacias, nº 123, Bairro Alto, Ijui."
        )
        found = rules.detect(text)
        kinds = sorted(span.entity_type for span in found)
        self.assertEqual(kinds, ["BR_PHONE", "BR_PHONE", "EMAIL_ADDRESS", "LOCATION"])
        address = next(span for span in found if span.entity_type == "LOCATION")
        self.assertEqual(text[address.start : address.end], "R. das Acacias, nº 123, Bairro Alto")

    def test_index_respects_number_boundaries(self) -> None:
        rules = CaseRules(processes=["5004321-55.2024.4.04.7102"], cdas=["80.6.21.000123-45"])
        text = "autos 50043215520244047102 2020 e 2019 80.6.21.000123-45; nao: 150043215520244047102."
        found = rules.detect(text)
        self.assertEqual([text[s.start : s.end] for s in found], ["50043215520244047102", "80.6.21.000123-45"])

    def test_value_inside_a_larger_number_is_not_found(self) -> None:
        rules = CaseRules(processes=["10983.720123/2019-11"])
        self.assertEqual(rules.detect("conta 9109837201232019110"), [])


class ListedIdTests(unittest.TestCase):
    def test_cnpj_root_covers_every_branch(self) -> None:
        self.assertEqual(parse_target_id_line("11.222.333"), "11.222.333")
        text = "Matriz 11.222.333/0001-81, filial 11.222.333/0002-62 e CNPJ raiz 11.222.333."
        hits = find_listed_id_spans(text, ["11222333"])
        self.assertEqual(
            [text[start:end] for start, end, _record in hits],
            ["11.222.333/0001-81", "11.222.333/0002-62", "11.222.333"],
        )

    def test_root_without_label_alone_is_not_found(self) -> None:
        hits = find_listed_id_spans("valor 11.222.333 reais", ["11222333"])
        self.assertEqual(hits, [])

    def test_leading_zero_eaten_by_the_spreadsheet_comes_back(self) -> None:
        self.assertEqual(parse_target_id_line("1234567890"), "012.345.678-90")
        self.assertEqual(parse_target_id_line("5432187000116"), "05.432.187/0001-16")

    def test_listed_nire(self) -> None:
        self.assertEqual(parse_target_id_line("NIRE 42201234567"), "NIRE 42201234567")
        record = describe_listed_ids(["NIRE 42201234567"])[0]
        self.assertEqual(record.entity_type, "BR_NIRE")


class TargetAliasTests(unittest.TestCase):
    def test_every_alias_gets_the_entry_key(self) -> None:
        engine = TargetListEngine(
            companies=["Madeireira Pinhal Verde Ltda"],
            people=[],
            ids=[],
            lookup_cnpj_names=False,
            aliases=["Madeireira Pinhal Verde Ltda = Pinhal Verde; Madeireira PV"],
        )
        text = "A MADEIREIRA PINHAL VERDE LTDA, dita Pinhal Verde ou Madeireira PV, vendeu."
        found = [span for span in engine.detect(text) if span.entity_type == "ORGANIZATION"]
        self.assertEqual({span.canonical_key for span in found}, {"alvo:empresa:madeireira pinhal verde"})
        self.assertEqual(len(found), 3)

    def test_alias_that_is_another_entry_does_not_merge(self) -> None:
        engine = TargetListEngine(
            companies=[],
            people=["Joao Ficticio Souza", "Pedro Ficticio Souza"],
            ids=[],
            lookup_cnpj_names=False,
            aliases=["Joao Ficticio Souza = Pedro Ficticio Souza; Joaozinho Souza"],
        )
        keys = {entry.canonical_key for entry in engine.entries}
        self.assertEqual(len(keys), 2)
        joao = next(entry for entry in engine.entries if "joao" in entry.canonical_key)
        self.assertEqual(joao.aliases, ("Joaozinho Souza",))

    def test_resolve_mention(self) -> None:
        engine = TargetListEngine(
            companies=["Madeireira Pinhal Verde Ltda"],
            people=["Gioconda Pereira Lustosa"],
            ids=[],
            lookup_cnpj_names=False,
            aliases=["Madeireira Pinhal Verde Ltda = Pinhal Verde"],
        )
        self.assertEqual(engine.resolve_mention("PINHAL VERDE").kind, "empresa")
        self.assertEqual(engine.resolve_mention("Madeireira Pinhal Verde").kind, "empresa")
        self.assertEqual(engine.resolve_mention("Gioconda Pereira").kind, "pessoa")
        self.assertEqual(engine.resolve_mention("Sra. Gioconda").kind, "pessoa")
        self.assertIsNone(engine.resolve_mention("Pereira"))  # sobrenome comum sem posicao nao basta
        self.assertIsNone(engine.resolve_mention("Outra Pessoa Qualquer"))


DOC_CASE = (
    "Execução fiscal 5004321-55.2024.4.04.7102. A executada MADEIREIRA PINHAL VERDE LTDA, nome fantasia "
    "Pinhal Verde, tem como sócia GIOCONDA PEREIRA LUSTOSA, conhecida como Gió Lustosa.\n\n"
    "A Pinhal Verde deixou de pagar a CDA 80.6.21.000123-45. Contato: gio @ pinhalverde.com.br.\n\n"
    "Os autos 50043215520244047102 seguem conclusos.\n"
)


class CaseListPipelineTests(_Pipeline):
    SETTINGS = dict(
        target_companies=["Madeireira Pinhal Verde Ltda"],
        target_people=["Gioconda Pereira Lustosa"],
        target_aliases=["Gioconda Pereira Lustosa = Gió Lustosa", "Madeireira Pinhal Verde Ltda = Pinhal Verde"],
        target_processes=["5004321-55.2024.4.04.7102"],
        target_cdas=["80.6.21.000123-45"],
        target_contacts=["gio@pinhalverde.com.br"],
        cnpj_lookup_enabled=False,
        local_ner_enabled=False,
    )

    def run_case(self, discoverer):
        return self.go(DOC_CASE, discoverer, settings=AppSettings(**self.SETTINGS))

    def test_every_form_of_the_listed_company_gets_one_token(self) -> None:
        # O LLM aponta a forma curta como entrada propria; a lista do caso a manda para a entrada da empresa.
        llm = FakeDiscoverer([("empresa", "Pinhal Verde", "parte"), ("pessoa", "GIOCONDA PEREIRA LUSTOSA", "socio")])
        _result, body, _audit = self.run_case(llm)
        self.assertNotIn("Pinhal Verde", body)
        self.assertNotIn("PINHAL VERDE", body)
        company_tokens = set(re.findall(r"\[EMPRESA_\d+\]", body))
        self.assertEqual(len(company_tokens), 1, body)

    def test_alias_of_a_person_gets_the_same_token(self) -> None:
        _result, body, _audit = self.run_case(FakeDiscoverer([]))
        self.assertNotIn("Gió", body)
        self.assertNotIn("GIOCONDA", body)
        person_tokens = set(re.findall(r"\[PESSOA_\d+\]", body))
        self.assertEqual(len(person_tokens), 1, body)

    def test_listed_process_cda_and_contact_are_replaced_everywhere(self) -> None:
        _result, body, _audit = self.run_case(FakeDiscoverer([]))
        self.assertNotIn("5004321", body)
        self.assertNotIn("50043215520244047102", body)
        self.assertNotIn("000123", body)
        self.assertNotIn("pinhalverde.com.br", body)
        self.assertEqual(len(set(re.findall(r"\[PROCESSO_\d+\]", body))), 1, body)


class CaseListBeatsProtectedListForLlmTests(_Pipeline):
    def test_listed_name_that_is_a_protected_system_is_replaced_from_the_llm_mention(self) -> None:
        # "Cresol" esta na lista protegida (instituicao financeira); aqui e o nome fantasia de uma empresa do caso.
        text = "A empresa CRESOL MADEIRAS FICTICIAS LTDA, conhecida como Cresol, vendeu o caminhão.\n"
        settings = AppSettings(
            target_companies=["Cresol Madeiras Ficticias Ltda"],
            target_aliases=["Cresol Madeiras Ficticias Ltda = Cresol Madeiras"],
            cnpj_lookup_enabled=False,
            local_ner_enabled=False,
        )
        llm = FakeDiscoverer([("empresa", "CRESOL MADEIRAS FICTICIAS LTDA", "parte")])
        _result, body, _audit = self.go(text, llm, settings=settings)
        self.assertNotIn("MADEIRAS FICTICIAS", body)
        self.assertIn("como Cresol,", body)  # a palavra solta da lista protegida segue em claro


COMBINED = {
    "Pessoas": [
        ["nome", "cpf", "papel", "papel_original", "vinculo", "apelidos"],
        ["Gioconda Pereira Lustosa", "529.982.247-25", "socio", "Sócia-gerente", "11.222.333/0001-81", "Gió Lustosa"],
        ["Rui Ficticio Prado", "123.456.789-00", "procurador", "Advogado da parte", "", ""],  # DV invalido
        ["", "", "", "", "", ""],
        ["União", "", "", "", "", ""],
    ],
    "Empresas": [
        ["razao_social", "cnpj", "nome_fantasia", "apelidos", "papel", "papel_original", "situacao", "nire"],
        ["Madeireira Pinhal Verde Ltda", "11.222.333/0001-81", "Pinhal Verde", "Madeireira PV", "devedora", "Executada", "ativa", "42201234567"],
        ["Comercial Raiz Ficticia Ltda", "05.432.187", "", "", "grupo", "", "baixada", ""],
    ],
    "Processos": [
        ["numero", "classe", "relacao"],
        ["5004321-55.2024.4.04.7102", "execução fiscal", "principal"],
        ["5004321-55.2024.4.04.7102", "execução fiscal", "principal"],
        ["sem numero", "", ""],
    ],
    "CDAs": [
        ["numero", "processo_adm"],
        ["80.6.21.000123-45", "10983.720123/2019-11"],
        ["80.6.21.000124-26", ""],
    ],
    "Contatos": [
        ["titular", "tipo", "valor"],
        ["Gioconda Pereira Lustosa", "telefone", "(47) 98473-5213"],
        ["Gioconda Pereira Lustosa", "email", "gio@pinhalverde.com.br"],
        ["Madeireira Pinhal Verde Ltda", "endereco", "Rua das Acácias, 123, Bairro Alto"],
        ["Madeireira Pinhal Verde Ltda", "telefone", "12"],
    ],
    "Veiculos": [
        ["titular", "placa", "renavam"],
        ["Madeireira Pinhal Verde Ltda", "ABC-1D23", "00345678901"],
    ],
}


class CombinedSpreadsheetTests(unittest.TestCase):
    def parse(self, sheets=COMBINED):
        import tempfile
        from pathlib import Path

        from anonimizador_app.spreadsheet_targets import parse_target_spreadsheet
        from test_spreadsheet_targets import _write_xlsx_simple

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "para_o_anonimizador.xlsx"
            _write_xlsx_simple(path, sheets)
            return parse_target_spreadsheet(path)

    def test_every_sheet_is_read(self) -> None:
        parsed = self.parse()
        self.assertEqual(parsed.people, ["Gioconda Pereira Lustosa", "Rui Ficticio Prado"])
        self.assertEqual(parsed.companies, ["Madeireira Pinhal Verde Ltda", "Comercial Raiz Ficticia Ltda"])
        self.assertEqual(
            parsed.ids, ["529.982.247-25", "11.222.333/0001-81", "NIRE 42201234567", "05.432.187"]
        )
        self.assertEqual(
            parsed.aliases,
            [
                "Gioconda Pereira Lustosa = Gió Lustosa",
                "Madeireira Pinhal Verde Ltda = Pinhal Verde; Madeireira PV",
            ],
        )
        self.assertEqual(parsed.processes, ["5004321-55.2024.4.04.7102", "PA: 10983.720123/2019-11"])
        self.assertEqual(parsed.cdas, ["80.6.21.000123-45", "80.6.21.000124-26"])
        self.assertEqual(
            parsed.contacts,
            [
                "telefone: (47) 98473-5213",
                "e-mail: gio@pinhalverde.com.br",
                "endereço: Rua das Acácias, 123, Bairro Alto",
                "placa: ABC-1D23",
                "renavam: 00345678901",
            ],
        )

    def test_nexus_role_is_translated_only_to_private_roles(self) -> None:
        parsed = self.parse()
        self.assertEqual(parsed.roles["Gioconda Pereira Lustosa"], "socio")
        self.assertEqual(parsed.roles["Rui Ficticio Prado"], "advogado")  # procurador DA PARTE, nunca o publico
        self.assertEqual(parsed.roles["Madeireira Pinhal Verde Ltda"], "parte")

    def test_discarded_rows_are_counted_by_reason(self) -> None:
        parsed = self.parse()
        self.assertEqual(
            parsed.discarded,
            {
                "documento sem dígito verificador válido ou de órgão público": 1,
                "termo protegido (União, PGFN...)": 1,
                "processo repetido": 1,
                "processo sem número válido": 1,
                "contato vazio ou fora do formato": 1,
            },
        )
        self.assertIn("Processos: 2", parsed.summary_lines())

    def test_imported_case_list_reaches_the_settings(self) -> None:
        import tempfile
        from pathlib import Path

        from anonimizador_app.settings import load_settings
        from anonimizador_app.ui import Session, _apply_spreadsheet

        parsed = self.parse()
        with tempfile.TemporaryDirectory() as tmp:
            session = Session.__new__(Session)
            session.private_dir = Path(tmp)
            session.settings = AppSettings(target_processes=["5004321-55.2024.4.04.7102"])
            message = _apply_spreadsheet(session, parsed, "para_o_anonimizador.xlsx")
            saved = load_settings(Path(tmp))
        self.assertIn("1 processo(s)", message)  # o repetido nao entra de novo
        self.assertEqual(saved.target_cdas, parsed.cdas)
        self.assertEqual(saved.target_roles["Rui Ficticio Prado"], "advogado")
        self.assertIn("80.6.21.000123-45", session.target_cdas_text)


DOC_PRECEDENT = (
    "O sócio OTAVIO FICTICIO RAMALHO responde pela dívida.\n\n"
    "Nesse sentido: REsp 1.234.567/SC, Rel. Min. OTAVIO FICTICIO RAMALHO, Segunda Turma, julgado em 01/02/2020.\n"
)


class PrecedentJudgeTests(_Pipeline):
    def test_judge_name_inside_a_citation_stays_clear(self) -> None:
        from anonimizador_app.fidelity import is_precedent_judge

        start = DOC_PRECEDENT.index("Min. OTAVIO") + len("Min. ")
        end = start + len("OTAVIO FICTICIO RAMALHO")
        self.assertTrue(is_precedent_judge(DOC_PRECEDENT, start, end))
        first = DOC_PRECEDENT.index("OTAVIO")
        self.assertFalse(is_precedent_judge(DOC_PRECEDENT, first, first + 23))

    def test_title_without_citation_is_not_enough(self) -> None:
        from anonimizador_app.fidelity import is_precedent_judge

        text = "Conforme orientou o Min. FULANO FICTICIO, a empresa deve pagar."
        start = text.index("FULANO")
        self.assertFalse(is_precedent_judge(text, start, start + len("FULANO FICTICIO")))

    def test_pipeline_keeps_the_judge_and_replaces_the_party(self) -> None:
        # O LLM confundiu: o mesmo nome e socio na peca e relator no precedente (homonimo de proposito).
        llm = FakeDiscoverer([("pessoa", "OTAVIO FICTICIO RAMALHO", "socio")])
        _result, body, audit = self.go(DOC_PRECEDENT, llm, settings=AppSettings(local_ner_enabled=False))
        self.assertIn("Rel. Min. OTAVIO FICTICIO RAMALHO, Segunda Turma", body)
        self.assertIn("O sócio [PESSOA_1] responde", body)
        self.assertIn("Nome de julgador em citacao de precedente mantido em claro: 1", audit)

    def test_listed_name_is_replaced_even_in_a_citation(self) -> None:
        llm = FakeDiscoverer([("pessoa", "OTAVIO FICTICIO RAMALHO", "socio")])
        settings = AppSettings(local_ner_enabled=False, target_people=["Otavio Ficticio Ramalho"])
        _result, body, _audit = self.go(DOC_PRECEDENT, llm, settings=settings)
        self.assertNotIn("OTAVIO", body)


class CaseListInsideKeptNameTests(_Pipeline):
    """Conferencia no lote real: a palavra do meio do nome de um servidor (mantido em claro) era trocada por casar
    com a grafia de uma pessoa da lista do caso."""

    TEXT = (
        "A sócia BEATRIZ VALDUGA assinou o contrato.\n\n"
        "Certifico que o servidor ANSELMO VALDUGA PRATES, Analista Judiciário, cumpriu o mandado.\n"
    )

    def test_list_piece_inside_a_kept_name_is_not_replaced(self) -> None:
        llm = FakeDiscoverer([("pessoa", "ANSELMO VALDUGA PRATES", "servidor_publico")])
        settings = AppSettings(local_ner_enabled=False, cnpj_lookup_enabled=False, target_people=["Beatriz Valduga"])
        _result, body, audit = self.go(self.TEXT, llm, settings=settings)
        self.assertIn("servidor ANSELMO VALDUGA PRATES, Analista", body)
        self.assertNotIn("BEATRIZ", body)
        self.assertRegex(audit, r"dentro do nome de pessoa mantida em claro \(nao trocado\): [1-9]")

    def test_full_listed_name_inside_a_longer_kept_form_is_still_replaced(self) -> None:
        # O LLM juntou o nome da socia a outras palavras e marcou a forma longa como publica: o nome listado vence.
        text = "Certidao: BEATRIZ VALDUGA PROCURADORIA SETORIAL recebeu a intimacao.\n"
        llm = FakeDiscoverer([("pessoa", "BEATRIZ VALDUGA PROCURADORIA SETORIAL", "procurador_publico")])
        settings = AppSettings(local_ner_enabled=False, cnpj_lookup_enabled=False, target_people=["Beatriz Valduga"])
        _result, body, _audit = self.go(text, llm, settings=settings)
        self.assertNotIn("BEATRIZ", body)


class FullNameJoinTests(unittest.TestCase):
    def spans(self, text: str, *parts: tuple[str, str]):
        from anonimizador_juridico import EntitySpan

        found = []
        cursor = 0
        for value, key in parts:
            start = text.index(value, cursor)
            found.append(
                EntitySpan(start=start, end=start + len(value), entity_type="PERSON", score=0.9,
                           recognizer="ElencoLLM", canonical_key=key)
            )
            cursor = start + len(value)
        return found

    def test_two_pieces_of_one_name_become_one_span(self) -> None:
        from anonimizador_app.name_join import join_person_names

        text = "A sócia Maria Fernanda Souza assinou."
        joined, count = join_person_names(text, self.spans(text, ("Maria", "maria"), ("Fernanda Souza", "fernanda souza")))
        self.assertEqual(count, 1)
        self.assertEqual([text[s.start : s.end] for s in joined], ["Maria Fernanda Souza"])

    def test_particle_and_generation_stay_inside(self) -> None:
        from anonimizador_app.name_join import join_person_names

        text = "Assinou José Carlos da Silveira Neto, sócio."
        joined, count = join_person_names(
            text, self.spans(text, ("José Carlos", "jose carlos"), ("Silveira", "silveira"))
        )
        self.assertEqual([text[s.start : s.end] for s in joined], ["José Carlos da Silveira Neto"])
        self.assertEqual(count, 2)

    def test_e_comma_and_line_break_separate(self) -> None:
        from anonimizador_app.name_join import join_person_names

        for text in ("Maria e Fernanda Souza", "Maria, Fernanda Souza", "Maria\nFernanda Souza"):
            joined, count = join_person_names(text, self.spans(text, ("Maria", "maria"), ("Fernanda Souza", "fs")))
            self.assertEqual(count, 0, text)
            self.assertEqual(len(joined), 2, text)

    def test_merged_key_rule(self) -> None:
        from anonimizador_app.name_join import join_person_names

        text = "A sócia Maria Fernanda Souza assinou."
        pieces = (("Maria", "maria"), ("Fernanda Souza", "fernanda souza"))
        joined, _count = join_person_names(text, self.spans(text, *pieces))
        self.assertEqual(joined[0].canonical_key, "fernanda souza")  # mais palavras de conteudo
        joined, _count = join_person_names(text, self.spans(text, *pieces), is_listed=lambda key: key == "maria")
        self.assertEqual(joined[0].canonical_key, "maria")  # a lista do caso vence
        text = "Assinou Joana Prado."
        joined, _count = join_person_names(text, self.spans(text, ("Joana", "joana"), ("Prado", "prado")))
        self.assertEqual(joined[0].canonical_key, "joana")  # empate: a esquerda

    def test_two_full_names_side_by_side_are_two_people(self) -> None:
        from anonimizador_app.name_join import join_person_names

        text = "| MARIA FICTICIA SOUZA JOAO FICTICIO PEREIRA |"
        joined, count = join_person_names(
            text, self.spans(text, ("MARIA FICTICIA SOUZA", "maria"), ("JOAO FICTICIO PEREIRA", "joao"))
        )
        self.assertEqual(count, 0)
        self.assertEqual(len(joined), 2)


class EmptyChunkRetryTests(unittest.TestCase):
    """H9-j: o trecho vazio volta ao LLM em DOIS PEDACOS menores, nao o mesmo texto."""

    TEXT = (
        "Parágrafo inicial sem nenhum nome proprio, apenas texto juridico corrido sobre a execucao e a penhora. " * 6
        + chr(10) * 2
        + "Mais adiante, a sócia MARTA FICTICIA BORGES assinou o contrato social da empresa. "
        + "Segue texto corrido sem nome para completar o trecho final da folha. " * 6
    )

    def _discoverer(self, answer_for_whole: bool):
        import json

        try:
            from langextract.core import base_model
            from langextract.core import types as lx_types
        except ImportError:  # pragma: no cover - ambiente sem langextract
            self.skipTest("langextract nao instalado")
        from anonimizador_app.llm_discovery import LangExtractDiscoverer, LlmConfig

        calls: list[int] = []

        class Flaky(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **kwargs):
                for prompt in batch_prompts:
                    chunk = prompt.rsplit("Q: ", 1)[-1]
                    calls.append(len(chunk))
                    items = []
                    whole = len(chunk) > 800
                    if "MARTA FICTICIA BORGES" in chunk and (not whole or answer_for_whole):
                        items = [{"pessoa": "MARTA FICTICIA BORGES",
                                  "pessoa_attributes": {"papel": "socio", "nome_canonico": "MARTA FICTICIA BORGES"}}]
                    yield [lx_types.ScoredOutput(score=1.0, output=json.dumps({"extractions": items}))]

        return LangExtractDiscoverer(LlmConfig(chunk_chars=2000, max_workers=1), model_factory=lambda: Flaky()), calls

    def test_empty_chunk_is_retried_in_halves_and_the_mention_is_anchored(self) -> None:
        discoverer, calls = self._discoverer(answer_for_whole=False)
        found = discoverer.discover(self.TEXT)
        self.assertEqual(len(calls), 3)  # o trecho inteiro (vazio) e as duas metades
        self.assertLess(max(calls[1:]), calls[0])
        self.assertEqual((found.report.empty_retried, found.report.empty_recovered), (1, 1))
        self.assertEqual(found.report.empty, [])
        mention = found.mentions[0]
        self.assertEqual(mention.text, "MARTA FICTICIA BORGES")
        self.assertEqual(self.TEXT[mention.start : mention.end], "MARTA FICTICIA BORGES")

    def test_short_empty_chunk_is_not_split_and_non_empty_is_not_retried(self) -> None:
        discoverer, calls = self._discoverer(answer_for_whole=True)
        discoverer.discover(self.TEXT)
        self.assertEqual(len(calls), 1)
        short, short_calls = self._discoverer(answer_for_whole=False)
        found = short.discover("Texto curto sem nome algum.")
        self.assertEqual((len(short_calls), found.report.empty_retried), (1, 0))


class RetractTests(_Pipeline):
    TEXT = "O executado JOAQUIM FICTICIO TAVARES foi citado. A testemunha Valdir Ficticio Brum compareceu.\n"

    class _Ner:
        """O detector de nomes local falso: acha os dois nomes."""

        def __init__(self, names):
            self.names = names

        def detect(self, text):
            from anonimizador_juridico import EntitySpan

            found = []
            for name in self.names:
                start = text.find(name)
                if start >= 0:
                    found.append(EntitySpan(start=start, end=start + len(name), entity_type="PERSON", score=0.9,
                                            recognizer="NerLocal", canonical_key=name.casefold()))
            return found

    def run_with(self, retract: bool):
        from unittest import mock

        llm = FakeDiscoverer([("pessoa", "JOAQUIM FICTICIO TAVARES", "parte")])
        engine = self._Ner(["JOAQUIM FICTICIO TAVARES", "Valdir Ficticio Brum"])
        settings = AppSettings(local_ner_enabled=False, llm_may_retract=retract, cnpj_lookup_enabled=False)
        with mock.patch("anonimizador_app.pipeline_llm.build_contextual_engine", return_value=engine):
            return self.go(self.TEXT, llm, settings=settings, local_floor=True)

    def test_off_by_default_only_counts(self) -> None:
        self.assertFalse(AppSettings().llm_may_retract)
        _result, body, audit = self.run_with(False)
        self.assertNotIn("Valdir", body)  # o detector de nomes continua valendo
        self.assertIn("Nomes so do detector de nomes que o LLM leu e nao apontou: 1; retirados: 0", audit)

    def test_on_the_llm_retracts_a_ner_only_name(self) -> None:
        _result, body, audit = self.run_with(True)
        self.assertIn("Valdir Ficticio Brum", body)
        self.assertNotIn("JOAQUIM", body)
        self.assertIn("retirados: 1", audit)


if __name__ == "__main__":
    unittest.main()
