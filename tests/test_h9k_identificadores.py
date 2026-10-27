"""H9-k: identificadores que vazavam num lote real (so contagens la; aqui so valores INVENTADOS).

PIS/NIT e CTPS (CNIS), titulo de eleitor, RG com rotulo colado ou orgao emissor depois, CEP e telefone em bloco de
colunas, data de nascimento com ponto, codigo verificador do eproc, CNPJ so com DV (protocolo do SISBAJUD nao e CNPJ)
e RG/CEP na passada final de formas conhecidas.

Todo numero daqui e inventado; os digitos verificadores sao calculados pelas funcoes abaixo. Sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_app.known_forms import KnownForms
from anonimizador_app.table_labels import TableLabelsEngine
from anonimizador_juridico import PseudonymMap, StructuredDetector, propagate_mapped_ids, pseudonymize, resolve_overlaps
from anonimizador_juridico.validators import is_valid_cnpj, is_valid_cpf, is_valid_pis

_DETECTOR = StructuredDetector()
_TABLES = TableLabelsEngine()


# --- digitos verificadores (valores inventados) ---------------------------------------------------------------------
def _pis(body: str) -> str:
    weights = (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    rest = sum(int(d) * w for d, w in zip(body, weights)) % 11
    return body + str(0 if rest < 2 else 11 - rest)


def _cpf(body: str) -> str:
    digits = [int(d) for d in body]
    for size in (10, 11):
        total = sum(d * w for d, w in zip(digits, range(size, 1, -1)))
        check = (total * 10) % 11
        digits.append(0 if check == 10 else check)
    return "".join(map(str, digits))


def _titulo(sequence: str, uf: str) -> str:
    first = sum(int(d) * w for d, w in zip(sequence, range(2, 10))) % 11
    first = 0 if first == 10 else first
    second = sum(int(d) * w for d, w in zip(uf + str(first), (7, 8, 9))) % 11
    second = 0 if second == 10 else second
    return f"{sequence}{uf}{first}{second}"


def _cnpj(body: str) -> str:
    def check(chars: str, weights: tuple[int, ...]) -> int:
        rest = sum((ord(c) - 48) * w for c, w in zip(chars, weights)) % 11
        return 0 if rest < 2 else 11 - rest

    first = check(body, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    second = check(body + str(first), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    return f"{body}{first}{second}"


def _cnj(seq: str, year: str, j: str, tr: str, org: str) -> str:
    rest = f"{year}{j}{tr}{org}"
    check = 98 - (int(f"{seq}{rest}00") % 97)
    return f"{seq}-{check:02d}.{year}.{j}.{tr}.{org}"


PIS_A = _pis("1700000012")  # 11 digitos com DV
PIS_B = _pis("2090000034")
PIS_A_FMT = f"{PIS_A[:3]}.{PIS_A[3:8]}.{PIS_A[8:10]}-{PIS_A[10]}"
PIS_B_FMT = f"{PIS_B[:3]}.{PIS_B[3:8]}.{PIS_B[8:10]}-{PIS_B[10]}"
PIS_INVALIDO = PIS_A[:10] + str((int(PIS_A[10]) + 1) % 10)
CPF_A = _cpf("900000012")
CPF_A_FMT = f"{CPF_A[:3]}.{CPF_A[3:6]}.{CPF_A[6:9]}-{CPF_A[9:]}"
TITULO_A = _titulo("90000012", "08")
TITULO_B = _titulo("00001234", "24")
TITULO_INVALIDO = TITULO_A[:11] + str((int(TITULO_A[11]) + 1) % 10)
PROCESSO = _cnj("5000001", "2024", "4", "04", "7200")


def _found(text: str, *types: str) -> list[tuple[str, str]]:
    spans = resolve_overlaps([*_DETECTOR.detect(text), *_TABLES.detect(text)])
    items = [(span.entity_type, text[span.start : span.end]) for span in spans]
    return [item for item in items if not types or item[0] in types]


def _spans(text: str, *types: str):
    spans = resolve_overlaps([*_DETECTOR.detect(text), *_TABLES.detect(text)])
    return [span for span in spans if not types or span.entity_type in types]


class ValoresInventadosTests(unittest.TestCase):
    def test_digitos_verificadores_conferem(self) -> None:
        self.assertTrue(is_valid_pis(PIS_A) and is_valid_pis(PIS_B))
        self.assertFalse(is_valid_pis(PIS_INVALIDO))
        self.assertTrue(is_valid_cpf(CPF_A))


# --- 1. PIS/PASEP/NIT e CTPS -----------------------------------------------------------------------------------------
class PisNitTests(unittest.TestCase):
    def test_rotulos_do_cnis_na_mesma_linha(self) -> None:
        for label in ("PIS", "PIS Base", "PIS Convertido", "NIT", "PIS/PASEP", "NIS", "PIS/PASEP/NIT", "NIT/PIS"):
            for value in (PIS_A_FMT, PIS_A):
                text = f"Segurado: Fulano Ficticio. {label}: {value}. Situação: ativo."
                with self.subTest(text=text):
                    self.assertEqual(_found(text, "BR_PIS"), [("BR_PIS", value)])

    def test_sob_o_numero_e_par_rotulo_valor_de_tabela(self) -> None:
        self.assertEqual(_found(f"inscrito no PIS sob o nº {PIS_A_FMT}.", "BR_PIS"), [("BR_PIS", PIS_A_FMT)])
        text = f"| NIT | {PIS_A_FMT} | Nome | Fulano Ficticio |\n"
        self.assertEqual(_found(text, "BR_PIS"), [("BR_PIS", PIS_A_FMT)])

    def test_cabecalho_markdown_e_bloco_de_colunas(self) -> None:
        markdown = f"| Nome | PIS Base | Competência |\n|---|---|---|\n| Fulano | {PIS_A} | 01/2020 |\n"
        self.assertEqual(_found(markdown, "BR_PIS"), [("BR_PIS", PIS_A)])
        block = (
            "NIT              Nome                     Competência\n"
            f"{PIS_A_FMT}   FULANO FICTICIO          01/2020\n"
            f"{PIS_B_FMT}   BELTRANO FICTICIO        02/2020\n"
        )
        self.assertEqual(_found(block, "BR_PIS"), [("BR_PIS", PIS_A_FMT), ("BR_PIS", PIS_B_FMT)])

    def test_mesmo_pis_formatos_diferentes_mesma_chave(self) -> None:
        text = f"PIS: {PIS_A_FMT}. Em outra folha, NIT {PIS_A}."
        keys = {span.canonical_key or "".join(c for c in text[span.start : span.end] if c.isdigit())
                for span in _spans(text, "BR_PIS")}
        self.assertEqual(keys, {PIS_A})

    def test_negativos(self) -> None:
        # Sem rotulo, DV errado, CPF valido no formato do CPF, coluna de tributo com valor, data, processo.
        self.assertEqual(_found(f"O número {PIS_A} consta da planilha.", "BR_PIS"), [])
        self.assertEqual(_found(f"PIS: {PIS_INVALIDO}.", "BR_PIS"), [])
        self.assertEqual(_found(f"PIS/CPF: {CPF_A_FMT}.", "BR_PIS"), [])
        self.assertEqual(_found("Valor PIS       COFINS\n1.234,56        2.000,00\n", "BR_PIS"), [])
        self.assertEqual(_found(f"NIT 01/02/2020; processo {PROCESSO}; R$ 1.234,56; evento 12.", "BR_PIS"), [])


class CtpsTests(unittest.TestCase):
    def test_numero_serie_e_uf_com_rotulo(self) -> None:
        cases = {
            "CTPS nº 1234567 série 0012-PR, admitido em 01/02/2010.": "1234567 série 0012-PR",
            "CTPS/Série: 54321/00123-SC": "54321/00123-SC",
            "Carteira de Trabalho nº 765432, série 00456 RS": "765432, série 00456 RS",
            "CTPS 7654321": "7654321",
        }
        for text, value in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_CTPS"), [("BR_CTPS", value)])

    def test_mesma_ctps_mesma_chave(self) -> None:
        keys = {span.canonical_key for span in _spans("CTPS 0054321 série 0123 e CTPS/Série 54321/123.", "BR_CTPS")}
        self.assertEqual(keys, {"54321/123"})

    def test_tabela_e_coluna(self) -> None:
        self.assertEqual(_found("| CTPS/Série | 54321/00123-SC |\n", "BR_CTPS"), [("BR_CTPS", "54321/00123-SC")])
        block = "Nome                 CTPS/Série        Admissão\nFULANO FICTICIO      54321/00123-SC    01/02/2010\n"
        self.assertEqual(_found(block, "BR_CTPS"), [("BR_CTPS", "54321/00123-SC")])

    def test_negativos(self) -> None:
        self.assertEqual(_found("CTPS anotada em 01/02/2020, R$ 1.234,56, evento 12.", "BR_CTPS"), [])
        self.assertEqual(_found("O empregado 1234567 série 0012 foi admitido.", "BR_CTPS"), [])


# --- 2. Titulo de eleitor --------------------------------------------------------------------------------------------
class TituloEleitorTests(unittest.TestCase):
    def test_rotulos_da_consulta_de_cpf(self) -> None:
        spaced = f"{TITULO_A[:4]} {TITULO_A[4:8]} {TITULO_A[8:]}"
        for text, value in (
            (f"Título de Eleitor: {TITULO_A}", TITULO_A),
            (f"Título Eleitoral nº {spaced}", spaced),
            (f"Tít. Eleitor {TITULO_A}", TITULO_A),
            (f"Tit. Eleitoral: {TITULO_B[2:]}", TITULO_B[2:]),  # zeros a esquerda omitidos
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_TITULO_ELEITOR"), [("BR_TITULO_ELEITOR", value)])
                self.assertEqual({s.canonical_key for s in _spans(text, "BR_TITULO_ELEITOR")},
                                 {TITULO_A if value.replace(" ", "").endswith(TITULO_A[-4:]) else TITULO_B})

    def test_em_coluna(self) -> None:
        block = f"Nome                 Título de Eleitor   Zona\nFULANO FICTICIO      {TITULO_A}        0012\n"
        self.assertEqual(_found(block, "BR_TITULO_ELEITOR"), [("BR_TITULO_ELEITOR", TITULO_A)])
        self.assertEqual(_found(f"| Tít. Eleitor | {TITULO_A} |\n", "BR_TITULO_ELEITOR"), [("BR_TITULO_ELEITOR", TITULO_A)])

    def test_negativos(self) -> None:
        self.assertEqual(_found(f"Título de Eleitor: {TITULO_INVALIDO}", "BR_TITULO_ELEITOR"), [])
        self.assertEqual(_found(f"O número {TITULO_A} sem rótulo.", "BR_TITULO_ELEITOR"), [])
        self.assertEqual(_found(f"Título de Eleitor: {CPF_A}", "BR_TITULO_ELEITOR"), [])


# --- 3. RG ---------------------------------------------------------------------------------------------------------
class RgTests(unittest.TestCase):
    def test_rotulo_colado_e_orgao_emissor(self) -> None:
        cases = {
            "portador do RG-4.000.000-SSP/SP, residente": "4.000.000",
            "RG nº 1.234.567-8 SSP/PR": "1.234.567-8",
            "Carteira de Identidade n.º 6.000.000-0 PR": "6.000.000-0",
            "cédula de identidade nº 7.000.001-2": "7.000.001-2",
            "port. ident. 12345678, inscrito": "12345678",
            "portador da identidade nº 7654321-0": "7654321-0",
            "ident. nº 98765432": "98765432",
            "RG 123456789 expedido": "123456789",
            "documento 1.234.567-8 SSP/PR": "1.234.567-8",
            "documento 6543210-9-SESP/PR": "6543210-9",
            "inscrito sob 3.210.987 SSP-SC": "3.210.987",
        }
        for text, value in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_RG"), [("BR_RG", value)])

    def test_negativos(self) -> None:
        for text in (
            f"RG {CPF_A}",  # CPF valido nao vira RG
            "Valor de R$ 1.234.567,89 pago em 01/02/2020, evento 12.",
            f"Processo {PROCESSO}.",
            "Protocolo 20240000000001 do SISBAJUD.",
            "identidade de propósitos entre as partes",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_RG"), [])
        self.assertIn(("BR_CPF", CPF_A), _found(f"RG {CPF_A}"))


# --- 4. CEP e telefone em bloco de colunas -------------------------------------------------------------------------------
class CepTelefoneColunaTests(unittest.TestCase):
    def test_bloco_alinhado(self) -> None:
        block = (
            "Logradouro                      Bairro       CEP          Telefone\n"
            "RUA FICTICIA, 100               CENTRO       88330-000    (41) 3000-0000\n"
            "AV. INVENTADA, 20               JARDIM       80.000-000   (41) 3000 – 0001\n"
        )
        self.assertEqual(
            _found(block, "BR_CEP", "BR_PHONE"),
            [("BR_CEP", "88330-000"), ("BR_PHONE", "(41) 3000-0000"),
             ("BR_CEP", "80.000-000"), ("BR_PHONE", "(41) 3000 – 0001")],
        )

    def test_bloco_em_cerca_de_codigo(self) -> None:
        block = "```text\nEndereço                        CEP        Fone\nRua Ficticia, 100               88330000   47 3333-4444\n```\n"
        self.assertEqual(_found(block, "BR_CEP", "BR_PHONE"), [("BR_CEP", "88330000"), ("BR_PHONE", "47 3333-4444")])

    def test_telefone_com_travessao_e_rotulo(self) -> None:
        for text, value in (("Fone: (41) 3000 – 0000", "(41) 3000 – 0000"), ("Tel. 41 3000—0000", "41 3000—0000")):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_PHONE"), [("BR_PHONE", value)])

    def test_negativos(self) -> None:
        # Fora da coluna, sem cabecalho, data e valor na coluna CEP/Telefone.
        block = (
            "Nome              Data         CEP          Telefone\n"
            "FULANO            01/02/2020   01/02/2020   R$ 1.234,56\n"
        )
        self.assertEqual(_found(block, "BR_CEP", "BR_PHONE"), [])
        self.assertEqual(_found("Prazo 3000 – 0000 dias.", "BR_PHONE"), [])
        self.assertEqual(_found("Data         Valor\n88330000     12.345,00\n", "BR_CEP"), [])


# --- 5. Data de nascimento -------------------------------------------------------------------------------------------
class NascimentoTests(unittest.TestCase):
    def test_rotulos_e_data_com_ponto(self) -> None:
        cases = {
            "Fulano, nascido aos 01/02/1960, residente": "01/02/1960",
            "Fulana, nascida em 01.02.1960": "01.02.1960",
            "nasc. 1.2.1960": "1.2.1960",
            "Data Nasc.: 05-06-1970": "05-06-1970",
            "Data de Nascimento: 01.02.1960": "01.02.1960",
            "Dt. Nasc. 01/02/1960": "01/02/1960",
        }
        for text, value in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_NASCIMENTO"), [("BR_NASCIMENTO", value)])

    def test_mesma_data_mesma_chave_e_coluna(self) -> None:
        text = "nascida em 01.02.1960; Data de Nascimento: 01/02/1960; nasc. 1.2.1960"
        self.assertEqual({span.canonical_key for span in _spans(text, "BR_NASCIMENTO")}, {"01021960"})
        block = "Nome               Data Nasc.     Sexo\nFULANA FICTICIA    01.02.1960     F\n"
        self.assertEqual(_found(block, "BR_NASCIMENTO"), [("BR_NASCIMENTO", "01.02.1960")])
        self.assertEqual(_found(f"| Nascimento | 01.02.1960 |\n", "BR_NASCIMENTO"), [("BR_NASCIMENTO", "01.02.1960")])

    def test_negativos(self) -> None:
        for text in (
            "Contrato firmado em 01.02.2020 no valor de R$ 1.234,56.",
            "Julgado em 01/02/2020, evento 12.",
            "certidão de nascimento: 01/02/2000",
            "Data          Evento\n01.02.2020    12\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_NASCIMENTO"), [])


# --- 6. Codigo verificador / chave de acesso ---------------------------------------------------------------------------
class CodigoVerificadorTests(unittest.TestCase):
    def test_eproc_codigo_verificador_trocado_crc_em_claro(self) -> None:
        text = (
            "A conferência da autenticidade do documento está disponível no endereço eletrônico do tribunal, "
            "informando o código verificador 710000012345v2 e o código CRC 1A2B3C4D."
        )
        self.assertEqual(_found(text, "BR_URL"), [("BR_URL", "710000012345v2")])
        out = pseudonymize(text, _spans(text), PseudonymMap()).text
        self.assertNotIn("710000012345v2", out)
        self.assertIn("1A2B3C4D", out)

    def test_chave_de_acesso(self) -> None:
        self.assertEqual(_found("Chave de acesso ao processo: 123456789012.", "BR_URL"), [("BR_URL", "123456789012")])
        self.assertEqual(
            _found("chave de consulta 900000123456 do documento", "BR_URL"), [("BR_URL", "900000123456")]
        )

    def test_negativos(self) -> None:
        for text in (
            "a chave de acesso informada pelo usuário",
            "o código verificador e o código CRC estão no rodapé",
            "documento 710000012345 sem rótulo",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_URL"), [])


# --- 7. CNPJ so com DV; protocolo do SISBAJUD nao e CNPJ -------------------------------------------------------------
def _protocolos_com_um_dv_valido() -> tuple[list[str], str]:
    """Protocolos inventados do SISBAJUD (ano + sequencial, mesma raiz de 8 digitos); um deles fecha o DV de CNPJ."""

    numbers = [f"202400000000{n:02d}" for n in range(80, 100)]
    valid = [n for n in numbers if is_valid_cnpj(n)]
    return numbers, valid[0]


class CnpjProtocoloTests(unittest.TestCase):
    def test_protocolo_com_dv_valido_por_acaso_nao_e_cnpj(self) -> None:
        numbers, valid = _protocolos_com_um_dv_valido()
        for text in (
            f"Protocolo: {valid}",
            f"Nº do protocolo {valid} - ordem judicial de bloqueio",
            f"SISBAJUD - minuta {valid}",
        ):
            with self.subTest(text=text):
                self.assertEqual(_found(text, "BR_CNPJ"), [])

    def test_coluna_protocolo_markdown_e_alinhada(self) -> None:
        numbers, _valid = _protocolos_com_um_dv_valido()
        markdown = "| Protocolo | Data | Valor |\n|---|---|---|\n" + "".join(
            f"| {n} | 01/02/2024 | R$ 10,00 |\n" for n in numbers
        )
        aligned = "Protocolo         Data          Valor\n" + "".join(f"{n}    01/02/2024    R$ 10,00\n" for n in numbers)
        for text in (markdown, aligned):
            with self.subTest(kind=text[:10]):
                spans = _DETECTOR.detect(text)
                self.assertEqual([s for s in spans if s.entity_type == "BR_CNPJ"], [])
                merged = propagate_mapped_ids(text, spans)
                self.assertEqual([s for s in merged if s.entity_type == "BR_CNPJ"], [])

    def test_cnpj_valido_continua_e_rotulado_sem_dv_nao(self) -> None:
        cnpj = _cnpj("900000120001")
        formatted = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        self.assertEqual(_found(f"Empresa Ficticia, CNPJ {formatted}.", "BR_CNPJ"), [("BR_CNPJ", formatted)])
        self.assertEqual(_found(f"Empresa Ficticia {cnpj}.", "BR_CNPJ"), [("BR_CNPJ", cnpj)])
        invalid = cnpj[:12] + f"{(int(cnpj[12:]) + 1) % 100:02d}"
        self.assertEqual(_found(f"CNPJ: {invalid}", "BR_CNPJ"), [])
        # Nome entre "ordem" e o numero: e o CNPJ da empresa, nao o protocolo.
        self.assertEqual(
            _found(f"Ordem de bloqueio contra Empresa Ficticia {cnpj}.", "BR_CNPJ"), [("BR_CNPJ", cnpj)]
        )

    def test_valores_diferentes_nunca_no_mesmo_token(self) -> None:
        # Raiz de CNPJ valido (matriz e filial) segue com um token so; protocolos de mesma raiz, nao.
        matriz = _cnpj("900000120001")
        filial = _cnpj("900000120002")
        text = f"CNPJ {matriz} matriz; filial {filial}."
        merged = propagate_mapped_ids(text, _DETECTOR.detect(text))
        self.assertEqual({s.canonical_key for s in merged if s.entity_type == "BR_CNPJ"}, {matriz})

    def test_raiz_nao_alcanca_numero_sem_dv(self) -> None:
        numbers, valid = _protocolos_com_um_dv_valido()
        text = f"Empresa Ficticia {valid} citada.\n" + "\n".join(f"Registro {n}" for n in numbers if n != valid)
        merged = propagate_mapped_ids(text, _DETECTOR.detect(text))
        cnpjs = [s for s in merged if s.entity_type == "BR_CNPJ"]
        self.assertEqual([text[s.start : s.end] for s in cnpjs], [valid])


# --- 8. RG e CEP na passada final de formas conhecidas ----------------------------------------------------------------
class FormasConhecidasRgCepTests(unittest.TestCase):
    def _forms(self, *pairs: tuple[str, str]) -> KnownForms:
        forms = KnownForms()
        for entity_type, value in pairs:
            forms.add(entity_type, "", value)
        return forms

    def _hits(self, forms: KnownForms, text: str) -> list[tuple[str, str, str]]:
        return [(s.entity_type, text[s.start : s.end], s.canonical_key) for s in forms.spans(text, [])]

    def test_rg_com_e_sem_pontuacao_e_dv(self) -> None:
        forms = self._forms(("BR_RG", "1.234.567-8"))
        text = "Consta 12345678; adiante 1.234.567 e 1234567-8."
        self.assertEqual(
            self._hits(forms, text),
            [("BR_RG", "12345678", "12345678"), ("BR_RG", "1.234.567", "12345678"), ("BR_RG", "1234567-8", "12345678")],
        )
        sem_dv = self._forms(("BR_RG", "7.654.321"))
        self.assertEqual(self._hits(sem_dv, "Outra folha: 7.654.321-0."), [("BR_RG", "7.654.321-0", "7654321")])

    def test_rg_negativos(self) -> None:
        forms = self._forms(("BR_RG", "1.234.567-8"))
        for text in ("Valor R$ 1.234.567,00.", "Número 1234567 corrido.", "Processo 912345678 da vara."):
            with self.subTest(text=text):
                self.assertEqual(self._hits(forms, text), [])
        self.assertEqual(len(self._forms(("BR_RG", "12.345"))), 0)  # menos de 7 digitos nao entra

    def test_cep_forma_pontuada_e_contexto_de_endereco(self) -> None:
        forms = self._forms(("BR_CEP", "88.330-000"))
        text = "Mora na Rua Ficticia, 10 - 88330000. Correspondência para 88.330-000 ou 88330-000; CEP 88330000."
        hits = self._hits(forms, text)
        self.assertEqual([h[1] for h in hits], ["88330000", "88.330-000", "88330-000", "88330000"])
        self.assertEqual({h[2] for h in hits}, {"88330000"})

    def test_cep_negativos_oito_digitos_fora_de_endereco(self) -> None:
        forms = self._forms(("BR_CEP", "88.330-000"))
        for text in ("Protocolo 88330000 de 01/02/2020.", "Conta 88330000; evento 12.", "Valor R$ 88.330.000,00."):
            with self.subTest(text=text):
                self.assertEqual(self._hits(forms, text), [])

    def test_mapa_alimenta_rg_e_cep(self) -> None:
        mapping = PseudonymMap()
        mapping.resolve("BR_RG", "12345678")
        mapping.resolve("BR_CEP", "88330000")
        forms = KnownForms()
        forms.add_mapping(mapping)
        text = "RG 1.234.567-8 e CEP 88.330-000."
        self.assertEqual({h[0] for h in self._hits(forms, text)}, {"BR_RG", "BR_CEP"})


if __name__ == "__main__":
    unittest.main()
