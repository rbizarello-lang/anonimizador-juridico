"""v0.4.11 (deteccao 2.17.0): regras do motor e do pos-LLM reveladas pela leitura cega do caso real (v0.4.10) e pelo
avaliador novo (colisao, tipo impossivel, agente publico). Casos sinteticos curtos; nomes, CPFs e CNPJs inventados.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico import EntitySpan

from anonimizador_app.address_guard import initial_not_street, not_an_address
from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.llm_hygiene import LlmHygieneStats, filter_llm_mentions
from anonimizador_app.local_ner import CombinedEngine
from anonimizador_app.lote_elenco import build_cast, same_cpf_rows
from anonimizador_app.pipeline_llm import run_llm_job, run_local_job
from anonimizador_app.public_agents import lot_public_agent, private_evidence, public_agent_context
from anonimizador_app.roster import Mention, Roster
from anonimizador_app.roster_identity import fuse_identities
from anonimizador_app.settings import AppSettings
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

from test_pipeline_llm import FakeDiscoverer


class _Ner:
    """NER de mentira: o tipo dado em cada ocorrencia exata dos nomes."""

    provider = "ner"

    def __init__(self, names, kind: str = "PERSON") -> None:
        self.names = [(name, kind) if isinstance(name, str) else name for name in names]

    def detect(self, text: str) -> list[EntitySpan]:
        found = []
        for name, kind in self.names:
            start = text.find(name)
            while start >= 0:
                found.append(EntitySpan(start, start + len(name), kind, 0.9, "NerLocal"))
                start = text.find(name, start + 1)
        return found

    def scan_residual(self, text: str) -> list[str]:
        return []


class _Lote(unittest.TestCase):
    def _paths(self, text: str) -> tuple[Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(text, encoding="utf-8")
        return base, source

    def local(self, text: str, names, *, people=(), companies=()) -> tuple[str, str]:
        base, source = self._paths(text)
        settings = AppSettings(
            local_ner_enabled=False, cnpj_lookup_enabled=False, target_people=list(people),
            target_companies=list(companies),
        )
        engine = CombinedEngine([build_contextual_engine(settings, use_ner_cache=False), _Ner(names)])
        result = run_local_job(
            [source], export_root=base / "e", private_root=base / "p", fallback_engine=engine, settings=settings,
            use_cache=False,
        )
        return self._read(result)

    def llm(self, text: str, entities, *, people=()) -> tuple[str, str]:
        base, source = self._paths(text)
        settings = AppSettings(local_ner_enabled=False, cnpj_lookup_enabled=False, target_people=list(people))
        result = run_llm_job(
            [source], export_root=base / "e", private_root=base / "p", discoverer=FakeDiscoverer(entities),
            use_cache=False, local_floor=False, verification_rounds=0, settings=settings,
        )
        return self._read(result)

    @staticmethod
    def _read(result) -> tuple[str, str]:
        body = result.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        cast = result.job.export_dir / "ELENCO.md"
        return body, cast.read_text(encoding="utf-8") if cast.is_file() else ""


def _span(text: str, value: str, nth: int = 0) -> tuple[int, int]:
    start = -1
    for _ in range(nth + 1):
        start = text.index(value, start + 1)
    return start, start + len(value)


class VersaoTest(unittest.TestCase):
    def test_versoes(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.16")  # v0.4.16 sobe as duas
        self.assertEqual(DETECTION_VERSION, "2.22.0")


# ------------------------------------------------------------------------------------------- 1. agentes publicos


AGENTES = (
    "<!-- pagina-origem: 1 -->\n\n"
    "O sócio LAURO PIRES FAGUNDES, CPF 529.982.247-25, foi citado.\n"
    "A Procuradora da Fazenda Nacional MARINA SOARES QUEIROZ requereu a penhora.\n"
    "Documento assinado eletronicamente por HELIO CAMPOS DUARTE, Procurador da Fazenda Nacional, em 12/03/2024.\n"
    "Conforme a manifestação de HELIO CAMPOS DUARTE, o crédito é exigível.\n"
    "Documento assinado eletronicamente por TERESA LINS MOURA, Juíza Federal, em 13/03/2024.\n"
    "(TRF4, AG 5007318-28.2022.4.04.0000, Relator Desembargador Federal OTAVIANO BRUM LEAL, Primeira Turma)\n"
    "tendo o Eminente Desembargador Federal Otaviano Brum Leal fixado a tese.\n"
    "Certifico que intimei o executado. RUBENS ALVES TORRES, Oficial de Justiça.\n"
    "Diretora de Secretaria: CLÉLIA NUNES VIANA\n"
    "A analista judiciária Sônia Prates Lobo certificou o decurso.\n"
    "O Procurador da União AFONSO REIS BRAGA manifestou-se.\n"
    "Advogado: CARLOS EDUARDO GRANADO, OAB/SC 12.345, pela executada.\n"
    "Documento assinado eletronicamente por GUMERCINDO TAVEIRA BRUM, Juiz Federal Substituto.\n"
    "O sócio GUMERCINDO TAVEIRA BRUM, CPF 111.444.777-35, retirou-se da sociedade.\n"
)
AGENTES_NOMES = [
    "LAURO PIRES FAGUNDES", "MARINA SOARES QUEIROZ", "HELIO CAMPOS DUARTE", "TERESA LINS MOURA", "OTAVIANO BRUM LEAL",
    "Otaviano Brum Leal", "RUBENS ALVES TORRES", "CLÉLIA NUNES VIANA", "Sônia Prates Lobo", "AFONSO REIS BRAGA",
    "CARLOS EDUARDO GRANADO", "GUMERCINDO TAVEIRA BRUM",
]
EM_CLARO = [
    "Procuradora da Fazenda Nacional MARINA SOARES QUEIROZ",
    "por HELIO CAMPOS DUARTE, Procurador",
    "manifestação de HELIO CAMPOS DUARTE,",  # o nome inteiro no lote: sem prova de particular em nenhuma ocorrencia
    "por TERESA LINS MOURA, Juíza Federal",
    "Desembargador Federal OTAVIANO BRUM LEAL",
    "Desembargador Federal Otaviano Brum Leal fixado",
    "RUBENS ALVES TORRES, Oficial de Justiça",
    "Diretora de Secretaria: CLÉLIA NUNES VIANA",
    "analista judiciária Sônia Prates Lobo",
    "Procurador da União AFONSO REIS BRAGA",
    "por GUMERCINDO TAVEIRA BRUM, Juiz Federal Substituto",
]


class AgentePublicoRegraTests(unittest.TestCase):
    def test_cargo_colado_antes_ou_depois(self) -> None:
        for value, needle in (
            ("Procuradora da Fazenda Nacional MARINA SOARES QUEIROZ requereu.", "MARINA SOARES QUEIROZ"),
            ("Documento assinado eletronicamente por HELIO DUARTE, Procurador da Fazenda Nacional.", "HELIO DUARTE"),
            ("Relator:\nOTAVIANO BRUM LEAL\n", "OTAVIANO BRUM LEAL"),
            ("HELIO DUARTE\nProcurador da Fazenda Nacional\n", "HELIO DUARTE"),
            ("O Procurador da República JOSUÉ LIMA BRAGA opinou.", "JOSUÉ LIMA BRAGA"),
            ("A servidora Ivone Matos Lacerda certificou.", "Ivone Matos Lacerda"),
        ):
            with self.subTest(value=value):
                self.assertTrue(public_agent_context(value, *_span(value, needle)))

    def test_advogado_e_parte_nao_sao_agente_publico(self) -> None:
        for value, needle in (
            ("Advogado: CARLOS EDUARDO GRANADO, OAB/SC 12.345.", "CARLOS EDUARDO GRANADO"),
            ("Procurador(es): CARLOS EDUARDO GRANADO (OAB SC012345)", "CARLOS EDUARDO GRANADO"),
            ("Usuário: XYZ12 - Heitor Saldanha Brito - ADVOGADO", "Heitor Saldanha Brito"),
            ("O sócio LAURO PIRES FAGUNDES, CPF 529.982.247-25, foi citado.", "LAURO PIRES FAGUNDES"),
            ("Assinado por LAURO PIRES FAGUNDES, sócio-administrador.", "LAURO PIRES FAGUNDES"),
        ):
            with self.subTest(value=value):
                self.assertFalse(public_agent_context(value, *_span(value, needle)))

    def test_socio_com_cpf_na_linha_de_cima_do_procurador_nao_e_procurador(self) -> None:
        text = AGENTES
        self.assertTrue(private_evidence(text, *_span(text, "LAURO PIRES FAGUNDES")))
        self.assertFalse(private_evidence(text, *_span(text, "MARINA SOARES QUEIROZ")))

    def test_lote_inteiro_so_sem_prova_de_particular(self) -> None:
        text = AGENTES
        helio = [(text, *_span(text, "HELIO CAMPOS DUARTE", n)) for n in range(2)]
        self.assertTrue(lot_public_agent(helio))
        judge = [(text, *_span(text, "GUMERCINDO TAVEIRA BRUM", n)) for n in range(2)]
        self.assertFalse(lot_public_agent(judge))  # homonimo: decide a ocorrencia


class AgentePublicoNoLoteTests(_Lote):
    def check(self, body: str) -> None:
        for snippet in EM_CLARO:
            self.assertIn(snippet, body)
        self.assertIn("O sócio [PESSOA_", body)
        self.assertNotIn("LAURO", body)
        self.assertRegex(body, r"Advogado: \[PESSOA_\d+\], OAB")  # advogado das partes segue trocado
        # juiz homonimo de um socio: em claro onde assina com o cargo, trocado onde e socio
        self.assertRegex(body, r"O sócio \[PESSOA_\d+\], CPF \[CPF_\d+\], retirou-se")

    def test_motor_local(self) -> None:
        body, _cast = self.local(AGENTES, AGENTES_NOMES)
        self.check(body)

    def test_mencoes_da_llm(self) -> None:
        body, _cast = self.llm(AGENTES, [("pessoa", name, "parte") for name in AGENTES_NOMES])
        self.check(body)

    def test_homonimo_da_lista_do_caso_tambem_por_ocorrencia(self) -> None:
        body, _cast = self.local(AGENTES, AGENTES_NOMES, people=["GUMERCINDO TAVEIRA BRUM", "LAURO PIRES FAGUNDES"])
        self.check(body)


# ------------------------------------------------------------------------------------------- 2. empresas


EMPRESAS = (
    "<!-- pagina-origem: 1 -->\n\n"
    "A requerida VALDOBENS ADMINISTRADORA DE BENS LTDA, CNPJ 11.222.333/0001-81, contestou.\n"
    "A Valdobens alega prescrição. A VALDOBENS não pertence ao grupo.\n"
    "Executadas: BRONZECARGAS TRANSPORTES LTDA, CNPJ 48.213.579/0001-05, e BRONZETEC COMÉRCIO E METALURGIA LTDA, "
    "CNPJ 73.905.146/0001-50.\n"
    "A Bronzecargas e a Bronzetec foram dissolvidas. A Bronzecargas tem dívida.\n"
    "A ZETAFORT INDÚSTRIA METALÚRGICA LTDA (doravante “METALFORT”) adquiriu imóveis.\n"
    "Consta no RENAJUD: 11.222.333/0001-81 | VALDOBENS TRANSPORTADORA E ADMINISTRADORA LTDA\n"
    "A KAPPA LOGÍSTICA LTDA, atual denominação de OMEGA FRETES LTDA, foi citada. A Omega Fretes recorreu.\n"
    "LUMEN TRANSPORTES LTDA, CNPJ 33.000.167/0001-01, e LUMEN COMÉRCIO LTDA, CNPJ 60.746.948/0001-12. A Lumen pagou.\n"
)
EMPRESAS_NOMES = [
    "VALDOBENS ADMINISTRADORA DE BENS LTDA", "Valdobens", "VALDOBENS", "BRONZECARGAS TRANSPORTES LTDA",
    "BRONZETEC COMÉRCIO E METALURGIA LTDA", "Bronzecargas", "Bronzetec", "ZETAFORT INDÚSTRIA METALÚRGICA LTDA", "METALFORT",
    "VALDOBENS TRANSPORTADORA E ADMINISTRADORA LTDA", "KAPPA LOGÍSTICA LTDA", "OMEGA FRETES LTDA", "Omega Fretes",
    "LUMEN TRANSPORTES LTDA", "LUMEN COMÉRCIO LTDA", "Lumen",
]


def _token_at(body: str, prefix: str) -> str:
    match = re.search(re.escape(prefix) + r"(\[EMPRESA_\d+(?:·G\d+)?\])", body)
    assert match is not None, (prefix, body)
    return match.group(1)


class UnificacaoDeEmpresaTests(_Lote):
    def test_mencoes_da_llm(self) -> None:
        body, cast = self.llm(EMPRESAS, [("empresa", name, "parte") for name in EMPRESAS_NOMES])
        requerida = _token_at(body, "A requerida ")
        self.assertEqual(_token_at(body, "contestou.\nA "), requerida)  # "A Valdobens alega"
        self.assertEqual(_token_at(body, "prescrição. A "), requerida)
        self.assertEqual(_token_at(body, "RENAJUD: [CNPJ_1] | "), requerida)  # nome antigo, mesmo CNPJ
        # empresas distintas com o mesmo radical "Bronze" nunca se unem
        self.assertNotEqual(_token_at(body, "Executadas: "), _token_at(body, "[CNPJ_2], e "))
        self.assertNotEqual(_token_at(body, "Executadas: "), requerida)
        # declaracao no texto
        declared = re.search(r"A (\[EMPRESA_\d+\]) \(doravante “(\[EMPRESA_\d+\])”\)", body)
        self.assertIsNotNone(declared, body)
        self.assertEqual(declared.group(1), declared.group(2))
        kappa = re.search(r"A (\[EMPRESA_\d+\]), atual denominação de (\[EMPRESA_\d+\])", body)
        self.assertIsNotNone(kappa)
        self.assertEqual(kappa.group(1), kappa.group(2))
        # forma curta de duas razoes sociais distintas (Lumen Transportes x Lumen Comercio): separada, em claro
        self.assertIn("A Lumen pagou", body)
        self.assertNotIn("não forma grupo", cast)

    def test_motor_local_nao_une_radical_comum(self) -> None:
        body, _cast = self.local(EMPRESAS, [(name, "ORGANIZATION") for name in EMPRESAS_NOMES])
        self.assertNotEqual(_token_at(body, "Executadas: "), _token_at(body, "[CNPJ_2], e "))
        self.assertIn("A Lumen pagou", body)

    def test_nome_antigo_de_uma_e_forma_curta_de_outra_com_o_mesmo_radical(self) -> None:
        # Padrao do avaliador (colisao E02 x E03): o nome ANTIGO de uma empresa abre com o radical da forma curta de
        # OUTRA. A forma curta nunca pode cair no token da primeira.
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Executadas: BRONZECARGAS TRANSPORTES LTDA, CNPJ 48.213.579/0001-05, e BRONZETEC COMÉRCIO E METALURGIA LTDA, "
            "CNPJ 73.905.146/0001-50.\n"
            "A BRONZECARGAS TRANSPORTES LTDA, antiga BRONZETEC TRANSPORTES LTDA, foi dissolvida.\n"
            "Consta no RENAJUD: 48.213.579/0001-05 | BRONZETEC TRANSPORTES LTDA\n"
            "A Bronzetec foi dissolvida. A Bronzetec tem dívida. A BRONZETEC COMÉRCIO E METALURGIA LTDA pagou.\n"
        )
        names = ["BRONZECARGAS TRANSPORTES LTDA", "BRONZETEC COMÉRCIO E METALURGIA LTDA", "BRONZETEC TRANSPORTES LTDA", "Bronzetec"]
        for body, _cast in (
            self.llm(text, [("empresa", name, "parte") for name in names]),
            self.local(text, [(name, "ORGANIZATION") for name in names]),
        ):
            first = _token_at(body, "Executadas: ")
            self.assertEqual(_token_at(body, ", antiga "), first)  # a denominacao antiga e a mesma empresa
            self.assertNotEqual(_token_at(body, "[CNPJ_1], e "), first)
            self.assertNotRegex(body, re.escape(f"A {first} foi dissolvida. A {first} tem"))

    def test_fusao_por_forma_curta_e_declaracao_no_elenco(self) -> None:
        roster = Roster()
        full = roster.add_mention(Mention(kind="empresa", text="Quartzo Mineração Ltda", canonical="Quartzo Mineração Ltda"))
        short = roster.add_mention(Mention(kind="empresa", text="Quartzo", canonical="Quartzo"))
        removed = fuse_identities(roster, ["A Quartzo Mineração Ltda e a Quartzo."])
        self.assertEqual(removed.get(short.key), full.key)
        # com outra razao social escrita no lote abrindo com a mesma palavra: separado
        roster = Roster()
        roster.add_mention(Mention(kind="empresa", text="Quartzo Mineração Ltda", canonical="Quartzo Mineração Ltda"))
        short = roster.add_mention(Mention(kind="empresa", text="Quartzo", canonical="Quartzo"))
        removed = fuse_identities(roster, ["A Quartzo Mineração Ltda e a Quartzo. A QUARTZO TÊXTIL LTDA é outra."])
        self.assertNotIn(short.key, removed)

    def test_elenco_mesmo_cnpj_no_corpo_e_firme(self) -> None:
        cast = build_cast(
            [
                (
                    "a.md",
                    "A [EMPRESA_1], CNPJ [CNPJ_1], contestou.\nRENAJUD: [CNPJ_1] | [EMPRESA_4]\n"
                    "Alteração do nome social de [EMPRESA_1] para [EMPRESA_4].\n",
                )
            ]
        )
        self.assertIn("| [EMPRESA_1·G1] | mesmo CNPJ | [EMPRESA_4·G1] | [CNPJ_1] colado aos dois no texto |", cast)
        self.assertNotIn("não forma grupo", cast)


# ------------------------------------------------------------------------------------------- 3. holding


class HoldingTests(_Lote):
    def test_holding_x_e_a_empresa_x_e_a_palavra_fica(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "A requerida VALDOBENS ADMINISTRADORA DE BENS LTDA, CNPJ 11.222.333/0001-81, é a holding da família.\n"
            "Se a holding Valdobens detém bens, responde. A holding patrimonial Valdobens também.\n"
        )
        entities = [
            ("empresa", "VALDOBENS ADMINISTRADORA DE BENS LTDA", "parte"),
            ("empresa", "holding Valdobens", "parte"),
            ("empresa", "holding patrimonial Valdobens", "parte"),
        ]
        body, _cast = self.llm(text, entities)
        token = _token_at(body, "A requerida ")
        self.assertIn(f"Se a holding {token} detém", body)
        self.assertIn(f"A holding patrimonial {token} também", body)
        self.assertEqual(len(set(re.findall(r"\[EMPRESA_\d+\]", body))), 1)

    def test_holding_com_nome_registrado_continua(self) -> None:
        text = "A HOLDING ALFAMAR S.A. foi citada."
        stats = LlmHygieneStats()
        start = text.index("HOLDING")
        mention = Mention(kind="empresa", text="HOLDING ALFAMAR S.A.", start=start, end=start + 20,
                          source_text="HOLDING ALFAMAR S.A.")
        kept = filter_llm_mentions([mention], text, stats)
        self.assertEqual([item.text for item in kept], ["HOLDING ALFAMAR S.A."])


# ------------------------------------------------------------------------------------------- 4. familia


FAMILIA = (
    "<!-- pagina-origem: 1 -->\n\n"
    "O executado ODORICO TRAVASSOS QUARESMINI, CPF 731.952.648-46, e a ex-esposa LUCIMARA RIZZOTTO QUARESMINI, "
    "CPF 604.318.275-08.\n"
    "A empresa é o patrimônio da família Quaresmini. O patronímico Quaresmini consta do nome dos filhos.\n"
    "Odorico Travassos Quaresmini transferiu as quotas.\n"
)


class SobrenomeDeFamiliaTests(_Lote):
    def check(self, body: str, cast: str) -> None:
        self.assertIn("da família [SOBRENOME_1].", body)
        self.assertIn("O patronímico [SOBRENOME_1] consta", body)
        self.assertNotIn("Quaresmini", body)
        self.assertNotRegex(body, r"família \[PESSOA_")
        self.assertIn("## Sobrenomes de família", cast)
        self.assertRegex(cast, r"\| \[SOBRENOME_1\] \| \[PESSOA_\d+\], \[PESSOA_\d+\] \|")

    def test_motor_local(self) -> None:
        names = ["ODORICO TRAVASSOS QUARESMINI", "LUCIMARA RIZZOTTO QUARESMINI", "Odorico Travassos Quaresmini"]
        self.check(*self.local(FAMILIA, names))

    def test_llm_aponta_o_sobrenome_como_pessoa(self) -> None:
        names = ["ODORICO TRAVASSOS QUARESMINI", "LUCIMARA RIZZOTTO QUARESMINI", "Odorico Travassos Quaresmini"]
        entities = [("pessoa", name, "parte") for name in names] + [("pessoa", "Quaresmini", "parte")]
        self.check(*self.llm(FAMILIA, entities))


# ------------------------------------------------------------------------------------------- 5. ENDERECO


class GuardaDeEnderecoTests(_Lote):
    def test_regra(self) -> None:
        self.assertEqual(not_an_address("2º CRI de Itajaí/SC"), "endereco_cartorio")
        self.assertEqual(not_an_address("Registro de Imóveis de Itajaí"), "endereco_cartorio")
        self.assertEqual(not_an_address("protocolo nº 212.713"), "endereco_protocolo")
        self.assertEqual(not_an_address("AUTO DE PENHORA"), "endereco_titulo_documento")
        self.assertEqual(not_an_address("CERTIDÃO"), "endereco_titulo_documento")
        self.assertEqual(not_an_address("VALDOBENS", {"valdobens"}), "endereco_empresa")
        self.assertEqual(not_an_address("Jardim das Flores Itajaí"), "")  # "jardim" e marcador
        self.assertEqual(not_an_address("Morro do Cristo"), "endereco_sem_marcador")
        for real in ("Rua das Palmeiras, nº 120", "Av. Brasil, 1500", "Rodovia SC-412, km 5", "Lote 12, Quadra B",
                     "Bairro Centro, CEP 88301-000", "Estrada Geral s/n"):
            with self.subTest(real=real):
                self.assertEqual(not_an_address(real), "")

    def test_mencoes_da_llm(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "A requerida VALDOBENS ADMINISTRADORA DE BENS LTDA, CNPJ 11.222.333/0001-81, contestou.\n"
            "A VALDOBENS não pertence ao grupo.\n"
            "O imóvel da matrícula 476 do 2º CRI de Itajaí/SC foi penhorado (protocolo nº 212.713).\n"
            "AUTO DE PENHORA\n"
            "O sócio reside na Rua das Palmeiras, nº 120, Centro, Itajaí/SC.\n"
        )
        entities = [
            ("empresa", "VALDOBENS ADMINISTRADORA DE BENS LTDA", "parte"),
            ("endereco", "VALDOBENS", "parte"),
            ("endereco", "2º CRI de Itajaí/SC", "terceiro"),
            ("endereco", "protocolo nº 212.713", "terceiro"),
            ("endereco", "AUTO DE PENHORA", "terceiro"),
            ("endereco", "Rua das Palmeiras, nº 120", "terceiro"),
        ]
        body, _cast = self.llm(text, entities)
        token = _token_at(body, "A requerida ")
        self.assertRegex(body, r"\nA (VALDOBENS|\[EMPRESA_\d+\]) não pertence")  # empresa (ou em claro), nunca ENDERECO
        self.assertIn("do 2º CRI de Itajaí/SC foi penhorado (protocolo nº 212.713)", body)
        self.assertIn("\nAUTO DE PENHORA\n", body)
        self.assertRegex(body, r"reside na \[ENDERECO_1\], Centro")  # endereco real com "nº" continua ENDERECO
        self.assertEqual(len(re.findall(r"\[ENDERECO_\d+\]", body)), 1)

    def test_inicial_de_prenome_nao_e_rua(self) -> None:
        for text in ("Assinatura: A. R. Quaresmini Neto\n", "LUCIMARA M. R. QUARESMINI assinou.\n"):
            with self.subTest(text=text):
                self.assertTrue(initial_not_street(text, text.index("R. Q")))
        self.assertFalse(initial_not_street("Reside na R. Quintino Bocaiúva, 12.", 10))
        text = "<!-- pagina-origem: 1 -->\n\nAssinatura: A. R. Quaresmini Neto\nReside na R. Quintino Bocaiúva, 12.\n"
        body, _cast = self.local(text, [])
        self.assertIn("A. R. Quaresmini Neto", body)
        self.assertIn("[ENDERECO_1]", body)
        self.assertNotIn("Quintino", body)


# ------------------------------------------------------------------------------------------- 6. pessoas


PESSOAS = (
    "<!-- pagina-origem: 1 -->\n\n"
    "A sócia LUCIMARA MARIA RIZZOTTO QUARESMINI, CPF 604.318.275-08, retirou-se. Lucimara Rizzotto Quaresmini assinou. "
    "LUCIMARA M. R. QUARESMINI também.\n"
    "O filho WENDEL RIZZOTTO QUARESMINI entrou em 2009; Wendel Quaresmini é administrador.\n"
    "O sócio GERALDO MOTTA PIRES retirou-se. O filho, GERALDO MOTTA PIRES FILHO, administra. Geraldo Pires Filho "
    "assinou.\n"
    "IRENE SOUZA LIMA é a esposa; IRENE SOUZA LIMA BARCELOS é a filha.\n"
    "O avô ANTERO NUNES VAZ fundou a empresa; o neto ANTERO NUNES VAZ NETO é sócio.\n"
    "A solteira CLARA DUTRA BORBA casou e passou a CLARA DUTRA BORBA FONTANA.\n"
    "JOÃO CARLOS SILVEIRA e JOÃO PEDRO SILVEIRA são irmãos; João Silveira assinou.\n"
)
PESSOAS_NOMES = [
    "LUCIMARA MARIA RIZZOTTO QUARESMINI", "Lucimara Rizzotto Quaresmini", "LUCIMARA M. R. QUARESMINI", "WENDEL RIZZOTTO QUARESMINI",
    "Wendel Quaresmini", "GERALDO MOTTA PIRES FILHO", "GERALDO MOTTA PIRES", "Geraldo Pires Filho",
    "IRENE SOUZA LIMA BARCELOS", "IRENE SOUZA LIMA", "ANTERO NUNES VAZ NETO", "ANTERO NUNES VAZ", "CLARA DUTRA BORBA FONTANA",
    "CLARA DUTRA BORBA", "JOÃO CARLOS SILVEIRA", "JOÃO PEDRO SILVEIRA", "João Silveira",
]


class UnificacaoDePessoaTests(_Lote):
    def check(self, body: str) -> None:
        tokens = re.findall(r"\[PESSOA_\d+\]", body)
        socia = re.search(r"sócia (\[PESSOA_\d+\]), CPF \[CPF_\d+\], retirou-se\. (\[PESSOA_\d+\]) assinou\. (\[PESSOA_\d+\])", body)
        self.assertIsNotNone(socia, body)
        self.assertEqual(len(set(socia.groups())), 1)  # com e sem o nome do meio, nome do meio abreviado
        filho = re.search(r"filho (\[PESSOA_\d+\]) entrou em 2009; (\[PESSOA_\d+\]) é", body)
        self.assertEqual(filho.group(1), filho.group(2))
        # protecoes da v0.4.10 (nao-regressao): pai x filho, mae x filha, avo x neto, casada x solteira
        father = re.search(r"O sócio (\[PESSOA_\d+\]) retirou-se\. O filho, (\[PESSOA_\d+\]), administra\. (\[PESSOA_\d+\])", body)
        self.assertNotEqual(father.group(1), father.group(2))
        self.assertEqual(father.group(2), father.group(3))  # o filho sem o nome do meio continua o filho
        for pattern in (
            r"(\[PESSOA_\d+\]) é a esposa; (\[PESSOA_\d+\]) é a filha",
            r"avô (\[PESSOA_\d+\]) fundou a empresa; o neto (\[PESSOA_\d+\])",
            r"solteira (\[PESSOA_\d+\]) casou e passou a (\[PESSOA_\d+\])",
        ):
            match = re.search(pattern, body)
            self.assertIsNotNone(match, pattern)
            self.assertNotEqual(match.group(1), match.group(2), pattern)
        # "João Silveira" cabe em dois irmaos: separado
        brothers = re.search(r"(\[PESSOA_\d+\]) e (\[PESSOA_\d+\]) são irmãos; (\[PESSOA_\d+\]) assinou", body)
        self.assertEqual(len(set(brothers.groups())), 3)
        self.assertTrue(tokens)

    def test_mencoes_da_llm(self) -> None:
        body, _cast = self.llm(PESSOAS, [("pessoa", name, "socio") for name in PESSOAS_NOMES])
        self.check(body)

    def test_motor_local(self) -> None:
        body, _cast = self.local(PESSOAS, PESSOAS_NOMES)
        self.check(body)

    def test_cpf_diferente_separa(self) -> None:
        roster = Roster()
        for name in ("Joana Prado Siqueira", "Joana Siqueira"):
            roster.add_mention(Mention(kind="pessoa", text=name, canonical=name, role="socio"))
        text = "Joana Prado Siqueira, CPF 529.982.247-25, e Joana Siqueira, CPF 111.444.777-35."
        self.assertEqual(fuse_identities(roster, [text]), {})


# ------------------------------------------------------------------------------------------- 7. nome partido e ELENCO


class RazaoSocialPartidaTests(_Lote):
    def test_fim_do_nome_depois_da_quebra_de_pagina(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "A requerida VALDOBENS ADMINISTRADORA DE BENS LTDA, CNPJ 11.222.333/0001-81, contestou. Requer a intimação "
            "da VALDOBENS ADMINISTRADORA\n\n<!-- pagina-origem: 2 -->\n\nDE BENS LTDA. para pagar.\n"
        )
        entities = [
            ("empresa", "VALDOBENS ADMINISTRADORA DE BENS LTDA", "parte"),
            ("empresa", "VALDOBENS ADMINISTRADORA", "parte"),
            ("empresa", "BENS LTDA", "parte"),
        ]
        body, _cast = self.llm(text, entities)
        token = _token_at(body, "A requerida ")
        self.assertIn(f"\n{token}. para pagar", body)
        self.assertNotIn("DE [EMPRESA", body)


class ElencoCpfAoLadoTests(unittest.TestCase):
    def test_cpf_so_do_nome_colado(self) -> None:
        documents = [
            ("a.md", "[PESSOA_1] e [PESSOA_2], CPFs [CPF_1] e [CPF_2], casados.\n[PESSOA_1], CPF [CPF_1], foi citado.\n")
        ]
        self.assertEqual(same_cpf_rows(documents), [])
        documents = [("a.md", "[PESSOA_1], CPF [CPF_1], foi citado.\n[PESSOA_3], inscrito no CPF sob o nº [CPF_1].\n")]
        # v0.4.16: so com os nomes, e so quando podem ser a mesma pessoa.
        self.assertEqual(same_cpf_rows(documents), [])
        names = {"[PESSOA_1]": "Osvaldo Tibério Cunha", "[PESSOA_3]": "Osvaldo T. Cunha"}
        self.assertEqual([row[0] for row in same_cpf_rows(documents, names)], ["[PESSOA_1]", "[PESSOA_3]"])
        names["[PESSOA_3]"] = "Osvaldo Tibério Cunha Filho"
        self.assertEqual(same_cpf_rows(documents, names), [])


if __name__ == "__main__":
    unittest.main()
