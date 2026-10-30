"""H9-l: seletor de operacao (lista do caso restrita a operacao do Nexus). Nomes, documentos e operacoes INVENTADOS."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.settings import (
    AppSettings,
    carry_case_list,
    case_item_key,
    case_list_lines,
    operation_private_lines,
    load_settings,
    save_settings,
    settings_for_operation,
)
from anonimizador_app.spreadsheet_targets import parse_target_spreadsheet
from anonimizador_app.ui import Session, _apply_spreadsheet, _operation_note, _operation_options

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer
from test_spreadsheet_targets import _write_xlsx_simple


def _cpf(base: str) -> str:
    digits = [int(char) for char in base]
    for size in (9, 10):
        total = sum(value * weight for value, weight in zip(digits, range(size + 1, 1, -1)))
        digits.append((total * 10 % 11) % 10)
    text = "".join(map(str, digits))
    return f"{text[:3]}.{text[3:6]}.{text[6:9]}-{text[9:]}"


CPF_A = _cpf("123456780")
CPF_B = _cpf("987654320")


def _sheets() -> dict[str, list[list[str]]]:
    return {
        "Operacoes": [["id", "nome"], ["OP-1", "Operação Ficticia Norte"], ["OP-2", "Operação Ficticia Sul"]],
        "Pessoas": [
            ["nome", "cpf", "papel", "operacoes"],
            ["Teodoro Quirino Valadares", CPF_A, "socio", "OP-1"],
            ["Bartolomeu Pires Mendonça", CPF_B, "socio", "OP-2"],
            ["Clotilde Arruda Fictícia", "", "socio", "OP-1; OP-2"],
        ],
        "Empresas": [
            ["nome", "cnpj", "papel", "operacoes"],
            ["Madeireira Ficticia Norte Ltda", "", "devedora", "OP-1"],
        ],
        "Processos": [["numero", "operacoes"], ["5000001-00.2024.4.04.7000", "OP-1"], ["5000002-00.2024.4.04.7000", "OP-2"]],
    }


class ImportadorTest(unittest.TestCase):
    def _parse(self, sheets):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "nexus.xlsx"
        _write_xlsx_simple(path, sheets)
        return parse_target_spreadsheet(path)

    def test_operacao_de_cada_linha_e_nomes_das_operacoes(self):
        parsed = self._parse(_sheets())
        ops = parsed.operations
        self.assertEqual(ops[case_item_key("target_people", "Teodoro Quirino Valadares")], ["OP-1"])
        self.assertEqual(ops[case_item_key("target_people", "Clotilde Arruda Fictícia")], ["OP-1", "OP-2"])
        self.assertEqual(ops[case_item_key("target_ids", CPF_B)], ["OP-2"])
        self.assertEqual(ops[case_item_key("target_companies", "Madeireira Ficticia Norte Ltda")], ["OP-1"])
        self.assertEqual(ops[case_item_key("target_processes", "5000002-00.2024.4.04.7000")], ["OP-2"])
        self.assertEqual(parsed.operation_names["OP-2"], "Operação Ficticia Sul")
        self.assertTrue(any(line.startswith("Operações: 2") for line in parsed.summary_lines()))

    def test_mesma_pessoa_em_duas_linhas_soma_as_operacoes_sem_descarte(self):
        sheets = {
            "Pessoas": [
                ["nome", "cpf", "operacoes"],
                ["Teodoro Quirino Valadares", CPF_A, "OP-1"],
                ["Teodoro Quirino Valadares", CPF_A, "OP-3"],
            ]
        }
        parsed = self._parse(sheets)
        self.assertEqual(parsed.people, ["Teodoro Quirino Valadares"])
        self.assertEqual(parsed.operations[case_item_key("target_people", "Teodoro Quirino Valadares")], ["OP-1", "OP-3"])
        self.assertEqual(parsed.operations[case_item_key("target_ids", CPF_A)], ["OP-1", "OP-3"])
        self.assertNotIn("nome repetido", parsed.discarded)

    def test_planilha_sem_coluna_de_operacao_continua_valendo(self):
        parsed = self._parse({"Pessoas": [["nome", "cpf"], ["Teodoro Quirino Valadares", CPF_A]]})
        self.assertEqual(parsed.people, ["Teodoro Quirino Valadares"])
        self.assertEqual(parsed.operations, {})


class FiltroTest(unittest.TestCase):
    def _settings(self, chosen=""):
        settings = AppSettings(
            target_people=["Teodoro Quirino Valadares", "Bartolomeu Pires Mendonça", "Pessoa Digitada à Mão"],
            target_ids=[CPF_A, CPF_B],
            target_processes=["5000001-00.2024.4.04.7000", "5000002-00.2024.4.04.7000"],
            target_roles={"Teodoro Quirino Valadares": "socio", "Bartolomeu Pires Mendonça": "socio"},
            case_operations={
                case_item_key("target_people", "Teodoro Quirino Valadares"): ["OP-1"],
                case_item_key("target_people", "Bartolomeu Pires Mendonça"): ["OP-2"],
                case_item_key("target_ids", CPF_A): ["OP-1"],
                case_item_key("target_ids", CPF_B): ["OP-2"],
                case_item_key("target_processes", "5000001-00.2024.4.04.7000"): ["OP-1"],
                case_item_key("target_processes", "5000002-00.2024.4.04.7000"): ["OP-2"],
            },
            case_operation_names={"OP-1": "Operação Ficticia Norte"},
            case_operation=chosen,
        )
        return settings

    def test_sem_escolha_a_lista_inteira(self):
        settings = self._settings()
        self.assertIs(settings_for_operation(settings), settings)

    def test_escolha_filtra_e_mantem_o_digitado_a_mao(self):
        active = settings_for_operation(self._settings("OP-1"))
        self.assertEqual(active.target_people, ["Teodoro Quirino Valadares", "Pessoa Digitada à Mão"])
        self.assertEqual(active.target_ids, [CPF_A])
        self.assertEqual(active.target_processes, ["5000001-00.2024.4.04.7000"])
        self.assertEqual(active.target_roles, {"Teodoro Quirino Valadares": "socio"})

    def test_auditoria_diz_a_operacao(self):
        lines = case_list_lines(settings_for_operation(self._settings("OP-1")))
        self.assertIn("pessoas 2", lines[0])
        # v0.4.16: o nome da operacao so na AUDITORIA privada; a exportavel diz que ha uma operacao escolhida.
        self.assertIn("Operacao: uma operacao escolhida na aba Caso", lines[1])
        self.assertNotIn("Norte", " ".join(lines))
        private = operation_private_lines(settings_for_operation(self._settings("OP-1")))
        self.assertEqual(private, ["Operacao escolhida na aba Caso: Operação Ficticia Norte (OP-1)"])
        self.assertIn("Operacao: todas", case_list_lines(self._settings())[1])

    def test_grava_e_le_e_leva_ao_trocar_de_pasta(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_settings(Path(tmp), self._settings("OP-2"))
            loaded = load_settings(Path(tmp))
        self.assertEqual(loaded.case_operation, "OP-2")
        self.assertEqual(loaded.case_operations[case_item_key("target_ids", CPF_B)], ["OP-2"])
        target = AppSettings()
        self.assertTrue(carry_case_list(loaded, target))
        self.assertEqual((target.case_operation, target.case_operation_names), ("OP-2", {"OP-1": "Operação Ficticia Norte"}))

    def test_opcoes_e_nota_da_aba_caso(self):
        settings = self._settings("OP-1")
        options = _operation_options(settings)
        self.assertEqual(list(options)[:1], [""])
        self.assertEqual(options["OP-1"], "Operação Ficticia Norte (OP-1)")
        self.assertIn("OP-2", options)
        self.assertIn("2 pessoa(s)", _operation_note(settings))


class ImportarNaSessaoTest(unittest.TestCase):
    def test_importar_soma_operacoes_e_nomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "nexus.xlsx"
            _write_xlsx_simple(path, _sheets())
            # Sessao sem ler a pasta privada padrao (que tem a lista real): so a pasta temporaria.
            session = Session.__new__(Session)
            session.private_dir = base / "privados"
            session.settings = AppSettings()
            _apply_spreadsheet(session, parse_target_spreadsheet(path), "nexus.xlsx")
            saved = load_settings(base / "privados")
        self.assertEqual(saved.case_operations[case_item_key("target_people", "Bartolomeu Pires Mendonça")], ["OP-2"])
        self.assertEqual(saved.case_operation_names["OP-1"], "Operação Ficticia Norte")
        self.assertEqual(saved.case_operation, "")


class RodadaTest(_Pipeline):
    TEXT = "O sócio Teodoro Quirino Valadares e o sócio Bartolomeu Pires Mendonça assinaram o contrato.\n"

    def _settings(self, chosen):
        return AppSettings(
            local_ner_enabled=False,
            target_people=["Teodoro Quirino Valadares", "Bartolomeu Pires Mendonça"],
            case_operations={
                case_item_key("target_people", "Teodoro Quirino Valadares"): ["OP-1"],
                case_item_key("target_people", "Bartolomeu Pires Mendonça"): ["OP-2"],
            },
            case_operation=chosen,
        )

    def test_so_a_operacao_escolhida_e_trocada(self):
        _result, body, audit = self.go(self.TEXT, FakeDiscoverer([]), settings=self._settings("OP-1"))
        self.assertNotIn("Teodoro", body)
        self.assertIn("Bartolomeu Pires Mendonça", body)
        self.assertIn("Operacao: uma operacao escolhida na aba Caso", audit)
        self.assertNotIn("OP-1", audit)  # v0.4.16: nem o codigo da operacao vai ao exportavel

    def test_todas_troca_as_duas(self):
        _result, body, _audit = self.go(self.TEXT, FakeDiscoverer([]), settings=self._settings(""))
        self.assertNotIn("Teodoro", body)
        self.assertNotIn("Bartolomeu", body)


if __name__ == "__main__":
    unittest.main()
