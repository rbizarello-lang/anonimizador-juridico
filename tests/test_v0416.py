"""v0.4.16 (deteccao 2.22.0): auditoria independente de 27/10.

Todos os nomes e numeros sao inventados.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.pipeline import run_job
from anonimizador_app.settings import AppSettings, case_item_key
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer

OPERATION_NAME = "Quimera Pantanosa"


class VersaoTest(unittest.TestCase):
    def test_versoes(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.17")
        self.assertEqual(DETECTION_VERSION, "2.22.0")


def _operation_settings(**extra) -> AppSettings:
    return AppSettings(
        local_ner_enabled=False,
        target_people=["Teodoro Quirino Valadares"],
        case_operations={case_item_key("target_people", "Teodoro Quirino Valadares"): ["OP-7"]},
        case_operation_names={"OP-7": OPERATION_NAME},
        case_operation="OP-7",
        **extra,
    )


def _exported_text(export_dir: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in export_dir.rglob("*") if path.is_file())


# ------------------------------------------------------------------------------- 1. nome da operacao do Nexus
class OperacaoForaDoExportavelTests(_Pipeline):
    TEXT = "O sócio Teodoro Quirino Valadares assinou o contrato.\n"

    def test_motor_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text(self.TEXT, encoding="utf-8")
            result = run_job([source], export_root=base / "exp", private_root=base / "priv", settings=_operation_settings())
            exported = _exported_text(result.export_dir)
            self.assertIn("uma operacao escolhida", exported)
            self.assertNotIn("Quimera", exported)
            self.assertNotIn("OP-7", exported)
            private = (result.map_path.parent / "AUDITORIA_PRIVADA.txt").read_text(encoding="utf-8")
            self.assertIn(f"{OPERATION_NAME} (OP-7)", private)

    def test_motor_com_llm(self) -> None:
        result, _body, audit = self.go(self.TEXT, FakeDiscoverer([]), settings=_operation_settings())
        exported = _exported_text(result.job.export_dir)
        self.assertIn("uma operacao escolhida", audit)
        self.assertNotIn("Quimera", exported)
        self.assertNotIn("OP-7", exported)
        private = (result.job.map_path.parent / "AUDITORIA_PRIVADA.txt").read_text(encoding="utf-8")
        self.assertIn(OPERATION_NAME, private)


# ------------------------------------------------- 3 (e 4c). vinculo firme falso no ELENCO (CPF ao lado, renomeacao)
from anonimizador_app.lote_elenco import build_cast, compatible_person_names, same_cpf_rows  # noqa: E402


class VinculoFalsoTests(unittest.TestCase):
    def test_nomes_compativeis(self) -> None:
        self.assertTrue(compatible_person_names("Osvaldo Tibério Cunha", "Osvaldo T. Cunha"))
        self.assertTrue(compatible_person_names("Osvaldo Tibério Cunha", "Osvaldo"))
        self.assertFalse(compatible_person_names("Osvaldo Tibério Cunha", "Osvaldo Tibério Cunha Filho"))
        self.assertFalse(compatible_person_names("Osvaldo Tibério Cunha", "Marta Cunha Prado"))

    def test_cpf_ao_lado_de_duas_pessoas_diferentes_nao_liga(self) -> None:
        text = "Assina [PESSOA_1], CPF [CPF_1].\nTestemunha: [PESSOA_2], CPF [CPF_1].\n"
        names = {"[PESSOA_1]": "Osvaldo Tibério Cunha", "[PESSOA_2]": "Marta Cunha Prado"}
        self.assertEqual(same_cpf_rows([("doc_1.md", text)], names), [])

    def test_renomeacao_entre_cnpj_diferentes_nao_entra_na_tabela_de_vinculos(self) -> None:
        text = (
            "A [EMPRESA_1], atualmente denominada [EMPRESA_2], deve.\n"
            "Vendedora: [EMPRESA_1], CNPJ [CNPJ_1].\nCompradora: [EMPRESA_2], CNPJ [CNPJ_2].\n"
        )
        cast = build_cast([("doc_1.md", text)])
        self.assertNotIn("denominação posterior", cast)
        self.assertNotIn("antiga denominação |", cast)
        same = text.replace("[CNPJ_2]", "[CNPJ_1]")
        self.assertIn("denominação posterior", build_cast([("doc_1.md", same)]))


# ----------------------------------------------------------------------- 2. denominacao usada por mais de uma empresa
from types import SimpleNamespace  # noqa: E402

from anonimizador_app.lote_elenco import shared_denomination_rows  # noqa: E402
from anonimizador_app.target_list import TargetListEngine  # noqa: E402

LISTED = "Alfa Beta Gama Comercio de Pecas Ltda"
SHARED_TEXT = (
    "A Alfa Beta Gama Comercio de Pecas Ltda, CNPJ 11.222.333/0001-81, vendeu. "
    "A Alfa Beta Ltda, CNPJ 44.555.666/0001-00, comprou. A marca Alfa Beta e famosa. "
    "A Alfa Beta (CNPJ 11.222.333/0001-81) pagou. A Alfa Beta, CNPJ 44.555.666/0001-00, fechou."
)


class DenominacaoCompartilhadaTests(unittest.TestCase):
    def _keys(self, text: str) -> list[tuple[str, str]]:
        engine = TargetListEngine(companies=[LISTED], lookup_cnpj_names=False)
        return [(text[s.start : s.end], s.canonical_key) for s in engine.detect(text) if s.entity_type == "ORGANIZATION"]

    def test_ancora_razao_social_completa_e_cnpj_na_frase(self) -> None:
        listed = "alvo:empresa:alfa beta gama comercio de pecas"
        self.assertEqual(
            self._keys(SHARED_TEXT),
            [
                ("Alfa Beta Gama Comercio de Pecas Ltda", listed),
                ("Alfa Beta Ltda", "razao:alfa beta"),
                ("Alfa Beta", "denominacao:alfa beta"),  # sem ancora: um token so, nunca a empresa da lista
                ("Alfa Beta", listed),  # CNPJ da empresa da lista na frase
                ("Alfa Beta", "razao:alfa beta"),  # CNPJ da outra razao social na frase
            ],
        )

    def test_sem_outra_razao_social_a_forma_curta_continua_da_lista(self) -> None:
        text = "A Alfa Beta Gama Comercio de Pecas Ltda vendeu. Depois a Alfa Beta pagou."
        self.assertEqual({key for _value, key in self._keys(text)}, {"alvo:empresa:alfa beta gama comercio de pecas"})

    def test_elenco_declara_a_denominacao(self) -> None:
        records = [
            SimpleNamespace(token="[EMPRESA_1]", entity_type="ORGANIZATION", canonical="alvo:empresa:alfa beta gama comercio de pecas"),
            SimpleNamespace(token="[EMPRESA_2]", entity_type="ORGANIZATION", canonical="razao:alfa beta"),
            SimpleNamespace(token="[EMPRESA_3]", entity_type="ORGANIZATION", canonical="denominacao:alfa beta"),
        ]
        documents = [("doc_1.md", "A [EMPRESA_1], CNPJ [CNPJ_1], vendeu. A [EMPRESA_3] e famosa.\n")]
        self.assertEqual(
            shared_denomination_rows(records, documents),
            [("[EMPRESA_3]", [("[EMPRESA_1]", ["[CNPJ_1]"]), ("[EMPRESA_2]", [])])],
        )


# --------------------------------------------------------------------- 4. fragmentacao e [PRENOME_n] de uma pessoa so
from anonimizador_app.first_names import owners_by_first_name  # noqa: E402
from anonimizador_app.lote_elenco import ocr_variant_rows, set_cnpj_roots  # noqa: E402


class FragmentacaoTests(unittest.TestCase):
    def test_prenome_da_mesma_pessoa_em_grafias_de_ocr_leva_o_token_dela(self) -> None:
        people = [
            ("alvo:pessoa:osvaldo tiberio cunha", "OSVALDO TIBERIO CUNHA"),
            ("alvo:pessoa:osvaldo tiberio cunha", "OSVALDO TI/ERIO CUNHA"),
            ("osvaldo tiberlo cvnha", "OSVALDO TIBERLO CVNHA"),
            ("osvaldo t. cunha", "OSVALDO T. CUNHA"),
        ]
        self.assertEqual(owners_by_first_name(people)["osvaldo"], {"alvo:pessoa:osvaldo tiberio cunha"})

    def test_duas_pessoas_de_verdade_seguem_com_prenome_ambiguo(self) -> None:
        people = [("a", "Osvaldo Tiberio Cunha"), ("b", "Osvaldo Prado Lemos"), ("c", "Osvaldo Tiberio Cunha Filho")]
        self.assertEqual(owners_by_first_name(people)["osvaldo"], {"a", "b", "c"})

    def test_elenco_diz_que_grafias_de_ocr_podem_ser_a_mesma_pessoa(self) -> None:
        rows = ocr_variant_rows([("[PESSOA_1]", "Osvaldo Tiberio Cunha"), ("[PESSOA_2]", "Osvaldo Tiberlo Cvnha"),
                                 ("[PESSOA_3]", "Marta Cunha Prado")])
        self.assertEqual([(token, others) for token, others, _how in rows],
                         [("[PESSOA_1]", ["[PESSOA_2]"]), ("[PESSOA_2]", ["[PESSOA_1]"])])

    def test_denominacao_anterior_liga_e_marca_nao(self) -> None:
        text = "Outra empresa do grupo, a [EMPRESA_1]. (denominação anterior: [EMPRESA_2].), teve sede ali.\n"
        try:
            set_cnpj_roots(None)
            self.assertIn("denominação posterior", build_cast([("doc_1.md", text)]))
            marca = SimpleNamespace(records=[SimpleNamespace(token="[EMPRESA_2]", entity_type="ORGANIZATION",
                                                             canonical="marca:gaivota kids")])
            set_cnpj_roots(marca)
            self.assertNotIn("denominação posterior", build_cast([("doc_1.md", text)]))
        finally:
            set_cnpj_roots(None)


# ---------------------------------------------------------------------------------------------- 5. ramo + marca
class RamoMaisMarcaTests(unittest.TestCase):
    def test_iniciais_da_sigla_da_lista_levam_o_token_da_empresa(self) -> None:
        engine = TargetListEngine(companies=["TAB Imoveis Ltda", "Delta Comercio Ltda"], lookup_cnpj_names=False)
        text = "A sigla TAB remete a Têxtil Alfa Beta. A Têxtil Gama Beta e outra. A Industria Alfa vendeu."
        found = {text[s.start : s.end]: s.canonical_key for s in engine.detect(text)}
        self.assertEqual(found.get("Têxtil Alfa Beta"), "alvo:empresa:tab imoveis")
        self.assertNotIn("Têxtil Gama Beta", found)
        self.assertNotIn("Industria Alfa", found)


# ------------------------------------------------------------- 6. razao social curta de outra empresa (sint3)
class RazaoCurtaDeOutraEmpresaTests(unittest.TestCase):
    def test_razao_curta_nao_herda_o_token_da_lista_pelo_prefixo(self) -> None:
        engine = TargetListEngine(companies=["Quilate Prime Vestuario Infantil Ltda"], lookup_cnpj_names=False)
        text = (
            "A QUILATE PRIME VESTUARIO INFANTIL LTDA vendeu. A QUILATE PRIME LTDA. comprou. "
            "Outra, a QUILATE PRIME MALHARIA LTDA, fechou. A loja Quilate Prime abriu."
        )
        found = [(text[s.start : s.end], s.canonical_key) for s in engine.detect(text)]
        self.assertIn(("QUILATE PRIME VESTUARIO INFANTIL LTDA", "alvo:empresa:quilate prime vestuario infantil"), found)
        self.assertIn(("QUILATE PRIME LTDA.", "razao:quilate prime"), found)
        self.assertIn(("Quilate Prime", "denominacao:quilate prime"), found)
        self.assertEqual(sum(1 for _value, key in found if key.startswith("alvo:")), 1)


# ----------------------------------------------------------- decisao do Operador: OAB e CRECI em claro
class OabEmClaroTests(unittest.TestCase):
    def test_oab_e_creci_ficam_em_claro(self) -> None:
        from anonimizador_juridico import PseudonymMap, StructuredDetector, pseudonymize, resolve_overlaps, recognizers

        from anonimizador_app.known_forms import KnownForms

        self.assertIs(recognizers.OAB_EM_CLARO, True)
        text = "Advogado OAB/SC 12.345, corretor CRECI 9876-F."
        out = pseudonymize(text, resolve_overlaps(StructuredDetector().detect(text)), PseudonymMap()).text
        self.assertEqual(out, text)
        forms = KnownForms()
        forms.add("BR_OAB", "", "OAB/SC 12.345")
        self.assertEqual(forms.spans(text, []), [])


if __name__ == "__main__":
    unittest.main()
