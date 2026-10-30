"""v0.4.10 (deteccao 2.16.0): correcoes do motor reveladas pela matriz v0.4.9 (testes de 20-22/10/2026).

Nomes, CPFs, CNPJs e textos inventados.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anonimizador_juridico import EntitySpan, resolve_overlaps

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.credentials import ApiKey
from anonimizador_app.llm_discovery import LangExtractDiscoverer, LlmConfig, LlmConfigError
from anonimizador_app.llm_gate import SYNTHETIC_SHA256, gemma_lock_refusals, is_free_tier_gemma
from anonimizador_app.llm_hygiene import LlmHygieneStats, filter_llm_mentions
from anonimizador_app.local_ner import CombinedEngine
from anonimizador_app.lote_elenco import build_cast, partial_name_rows
from anonimizador_app.name_generation import conflicting_generation, generation_of, strip_generation
from anonimizador_app.name_join import join_person_names
from anonimizador_app.pipeline_llm import _generation_key, _role_summary, _roster_keys, run_local_job
from anonimizador_app.roster import Mention, Roster
from anonimizador_app.roster_identity import fuse_identities
from anonimizador_app.settings import AppSettings
from anonimizador_app.target_list import TargetListEngine, restore_orphan_partials
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer


class _Ner:
    """NER de mentira: PERSON em cada ocorrencia exata dos nomes dados."""

    provider = "ner"

    def __init__(self, names) -> None:
        self.names = list(names)

    def detect(self, text: str) -> list[EntitySpan]:
        found = []
        for name in self.names:
            start = text.find(name)
            while start >= 0:
                found.append(EntitySpan(start, start + len(name), "PERSON", 0.9, "NerLocal"))
                start = text.find(name, start + 1)
        return found

    def scan_residual(self, text: str) -> list[str]:
        return []


def _values(text, spans):
    return [(text[span.start : span.end], span.canonical_key) for span in spans]


class VersaoTest(unittest.TestCase):
    def test_versoes(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.16")  # v0.4.16 sobe as duas (tests/test_v0416.py)
        self.assertEqual(DETECTION_VERSION, "2.22.0")


# ------------------------------------------------------------------------------------------- 1. pai e filho


class SufixoDeGeracaoTests(unittest.TestCase):
    def test_regra_unica(self) -> None:
        self.assertEqual(generation_of("Geraldo Motta Pires Filho"), {"filho"})
        self.assertEqual(generation_of("Geraldo Motta Pires Jr."), {"junior"})
        self.assertEqual(generation_of("Neto Fagundes Lima"), frozenset())  # prenome Neto nao e sufixo
        self.assertEqual(strip_generation("Antero Nunes Vaz Neto"), "Antero Nunes Vaz")
        self.assertTrue(conflicting_generation("Geraldo Motta Pires", "Geraldo Motta Pires Filho"))
        self.assertTrue(conflicting_generation("Geraldo Motta Pires Filho", "Geraldo Motta Pires"))
        self.assertFalse(conflicting_generation("Geraldo Motta Pires Filho", "Geraldo"))  # prenome pode ser dele
        self.assertFalse(conflicting_generation("Geraldo Motta Pires Filho", "Geraldo Motta Pires Filho"))


class ListaPaiEFilhoTests(unittest.TestCase):
    TEXT = (
        "O sócio GERALDO MOTTA PIRES retirou-se. O filho, GERALDO MOTTA PIRES FILHO, administra a holding.\n"
        "Geraldo Motta Pires\nFilho assinou. O neto ANTERO NUNES VAZ NETO e o avô ANTERO NUNES VAZ.\n"
    )

    def test_listado_o_pai_o_filho_tem_chave_propria(self) -> None:
        engine = TargetListEngine(people=["GERALDO MOTTA PIRES", "ANTERO NUNES VAZ"], lookup_cnpj_names=False)
        values = _values(self.TEXT, engine.detect(self.TEXT))
        self.assertIn(("GERALDO MOTTA PIRES", "alvo:pessoa:geraldo motta pires"), values)
        self.assertIn(("GERALDO MOTTA PIRES FILHO", "alvo:pessoa:geraldo motta pires filho"), values)
        self.assertIn(("Geraldo Motta Pires\nFilho", "alvo:pessoa:geraldo motta pires filho"), values)
        self.assertIn(("ANTERO NUNES VAZ NETO", "alvo:pessoa:antero nunes vaz neto"), values)
        self.assertIn(("ANTERO NUNES VAZ", "alvo:pessoa:antero nunes vaz"), values)

    def test_listado_o_filho_o_nome_do_pai_nao_e_dele(self) -> None:
        engine = TargetListEngine(people=["GERALDO MOTTA PIRES FILHO"], lookup_cnpj_names=False)
        values = _values(self.TEXT, engine.detect(self.TEXT))
        self.assertNotIn(("GERALDO MOTTA PIRES", "alvo:pessoa:geraldo motta pires filho"), values)
        self.assertNotIn(("GERALDO MOTTA", "alvo:pessoa:geraldo motta pires filho"), values)
        self.assertIn(("GERALDO MOTTA PIRES FILHO", "alvo:pessoa:geraldo motta pires filho"), values)

    def test_mencao_com_sufixo_nao_resolve_para_o_pai(self) -> None:
        engine = TargetListEngine(people=["GERALDO MOTTA PIRES"], lookup_cnpj_names=False)
        self.assertIsNone(engine.resolve_mention("Geraldo Motta Pires Filho"))
        self.assertIsNone(engine.resolve_mention("Geraldo Filho"))
        self.assertIsNotNone(engine.resolve_mention("Geraldo Motta Pires"))
        son = TargetListEngine(people=["GERALDO MOTTA PIRES FILHO"], lookup_cnpj_names=False)
        self.assertIsNone(son.resolve_mention("Geraldo Motta Pires"))
        text = "Assinou Geraldo Motta Pires Filho."
        start = text.index("Geraldo")
        # o LLM devolveu so o comeco, mas o texto segue com "Filho": nao e o pai listado
        self.assertIsNone(engine.resolve_mention("Geraldo Motta Pires", text=text, start=start, end=start + 19))


class ElencoPaiEFilhoTests(unittest.TestCase):
    def test_nome_canonico_do_pai_nao_leva_o_filho(self) -> None:
        roster = Roster()
        father = roster.add_mention(Mention(kind="pessoa", text="Geraldo Motta Pires", canonical="Geraldo Motta Pires"))
        son = roster.add_mention(
            Mention(kind="pessoa", text="Geraldo Motta Pires Filho", canonical="Geraldo Motta Pires")
        )
        self.assertNotEqual(father.key, son.key)
        self.assertEqual(roster.lookup("pessoa", "Geraldo Motta Pires Filho").key, son.key)

    def test_fusao_de_identidade_nao_une_pai_e_filho(self) -> None:
        roster = Roster()
        roster.add_mention(Mention(kind="pessoa", text="Geraldo Motta Pires", canonical="Geraldo Motta Pires"))
        son = roster.add_mention(Mention(kind="pessoa", text="Geraldo Motta Pires Filho", canonical="x"))
        son.variants.append("Geraldo Motta Pires")  # variante errada vinda de uma revisao antiga
        fuse_identities(roster)
        self.assertEqual(len([entry for entry in roster.entries if entry.kind == "pessoa"]), 2)

    def test_sufixo_solto_depois_do_token_e_outra_pessoa(self) -> None:
        roster = Roster()
        father = roster.add_mention(Mention(kind="pessoa", text="Antero Nunes Vaz", canonical="Antero Nunes Vaz"))
        text = "Assinou Antero Nunes Vaz Neto, sócio."
        start = text.index("Antero")
        span = EntitySpan(start, start + len("Antero Nunes Vaz"), "PERSON", 0.9, "x", canonical_key=father.key)
        joined, count = join_person_names(
            text, [span], generation_key=lambda key, value: _generation_key(roster, key, value)
        )
        self.assertEqual(count, 1)
        self.assertEqual(text[joined[0].start : joined[0].end], "Antero Nunes Vaz Neto")
        self.assertNotEqual(joined[0].canonical_key, father.key)


class PipelinePaiEFilhoTests(unittest.TestCase):
    TEXT = (
        "# AUTOS\n\n<!-- pagina-origem: 1 -->\n\n"
        "A UNIÃO requer o redirecionamento contra GERALDO MOTTA PIRES, sócio-administrador.\n"
        "O filho, GERALDO MOTTA PIRES FILHO, administra a holding. Geraldo Motta Pires Filho assinou.\n"
        "IRENE SOUZA LIMA é a esposa; IRENE SOUZA LIMA BARCELOS é a filha.\n"
        "O avô ANTERO NUNES VAZ fundou a empresa; o neto ANTERO NUNES VAZ NETO é sócio.\n"
    )
    NER = [
        "GERALDO MOTTA PIRES FILHO", "Geraldo Motta Pires Filho", "GERALDO MOTTA PIRES",
        "IRENE SOUZA LIMA BARCELOS", "IRENE SOUZA LIMA", "ANTERO NUNES VAZ NETO", "ANTERO NUNES VAZ",
    ]

    def run_lote(self, people):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(self.TEXT, encoding="utf-8")
        settings = AppSettings(local_ner_enabled=False, cnpj_lookup_enabled=False, target_people=list(people))
        engine = CombinedEngine([build_contextual_engine(settings, use_ner_cache=False), _Ner(self.NER)])
        result = run_local_job(
            [source], export_root=base / "e", private_root=base / "p", fallback_engine=engine,
            settings=settings, use_cache=False,
        )
        records = json.loads(result.job.map_path.read_text(encoding="utf-8"))["records"]
        return {original: record["token"] for record in records for original in record["originals"]}

    def assert_separate(self, tokens) -> None:
        for father, son in (
            ("GERALDO MOTTA PIRES", "GERALDO MOTTA PIRES FILHO"),
            ("IRENE SOUZA LIMA", "IRENE SOUZA LIMA BARCELOS"),
            ("ANTERO NUNES VAZ", "ANTERO NUNES VAZ NETO"),
        ):
            self.assertIn(father, tokens)
            self.assertIn(son, tokens)
            self.assertNotEqual(tokens[father], tokens[son], (father, son))

    def test_sem_lista(self) -> None:
        self.assert_separate(self.run_lote([]))

    def test_com_o_pai_e_o_avo_na_lista(self) -> None:
        self.assert_separate(self.run_lote(["GERALDO MOTTA PIRES", "ANTERO NUNES VAZ"]))


class ElencoDicaTests(unittest.TestCase):
    def test_pai_e_filho_e_grafia_comparada_nao_viram_dica(self) -> None:
        people = [
            ("[PESSOA_1]", "Geraldo Motta Pires"),
            ("[PESSOA_2]", "Geraldo Motta Pires Filho"),
            ("[PESSOA_3]", "Joana Prado"),
            ("[PESSOA_4]", "Joana Prado Siqueira"),
            ("[PESSOA_5]", "Lauro Pires"),
            ("[PESSOA_6]", "Lauro Pires Fagundes"),
        ]
        documents = [("a.md", "Retifica-se a grafia: onde por lapso se fez constar [PESSOA_5], leia-se [PESSOA_6].")]
        rows = partial_name_rows(people, documents)
        tokens = {row[0] for row in rows}
        self.assertNotIn("[PESSOA_1]", tokens)
        self.assertNotIn("[PESSOA_5]", tokens)
        self.assertIn("[PESSOA_3]", tokens)


# ------------------------------------------------------------------------------------------- 2. sobrenome orfao


class SobrenomeOrfaoTests(unittest.TestCase):
    def test_pedaco_volta_quando_o_nome_longo_perde(self) -> None:
        part = EntitySpan(30, 40, "PERSON", 0.95, "ListaDeAlvos", canonical_key="alvo:pessoa:x")
        host = EntitySpan(20, 40, "PERSON", 0.9, "NerAncoradoLista")
        winner = EntitySpan(15, 34, "ORGANIZATION", 0.99, "ListaDeAlvos", canonical_key="alvo:empresa:y")
        resolved = resolve_overlaps([winner])
        restored = restore_orphan_partials(resolved, [(part, host)])
        self.assertEqual(len(restored), 1)  # o pedaco cruza o vencedor: o desempate decide, nada some sem motivo
        free = restore_orphan_partials([EntitySpan(0, 10, "PERSON", 0.9, "x")], [(part, host)])
        self.assertIn((30, 40), [(span.start, span.end) for span in free])

    def test_prenome_listado_no_comeco_do_nome_inteiro_vira_um_token(self) -> None:
        text = "Assinou Quirina Bastos Fagundes Teles no recibo."
        engine = TargetListEngine(
            people=["Quirina Bastos Teles"], aliases=["Quirina Bastos Teles = Quirina"], lookup_cnpj_names=False
        )
        spans = CombinedEngine([engine, _Ner(["Quirina Bastos Fagundes Teles"])]).detect(text)
        values = _values(text, spans)
        self.assertIn(("Quirina Bastos Fagundes Teles", "alvo:pessoa:quirina bastos teles"), values)


# ------------------------------------------------------------------------------------------- 3. mesmo CNPJ


class MesmoCnpjComListaTests(unittest.TestCase):
    def test_nome_antigo_com_o_mesmo_cnpj_entra_na_entrada_da_lista(self) -> None:
        roster = Roster()
        listed = roster.add_listed("empresa", "VALDOBENS ADMINISTRADORA DE BENS LTDA")
        old = roster.add_mention(
            Mention(kind="empresa", text="VALDOBENS TRANSPORTADORA E ADMINISTRADORA LTDA",
                    canonical="VALDOBENS TRANSPORTADORA E ADMINISTRADORA LTDA")
        )
        text = (
            "VALDOBENS ADMINISTRADORA DE BENS LTDA, CNPJ 11.222.333/0001-81, requerida.\n"
            "| 11.222.333/0001-81 | VALDOBENS TRANSPORTADORA E ADMINISTRADORA LTDA |\n"
        )
        removed = fuse_identities(roster, [text])
        self.assertEqual(removed.get(old.key), listed.key)
        self.assertIsNotNone(roster.get(listed.key))

    def test_marca_de_dois_donos_unidos_vai_para_a_entrada_unica(self) -> None:
        roster = Roster()
        entry = roster.add_mention(Mention(kind="empresa", text="Valdobens Administradora Ltda", canonical="x"))
        batch = SimpleNamespace(
            entities=lambda: [
                SimpleNamespace(key="e1", kind="empresa", is_street=False, representative="Valdobens Administradora Ltda"),
                SimpleNamespace(key="e2", kind="empresa", is_street=False, representative="Valdobens Administradora Ltda"),
            ],
            brand_owners={"valdobens": ("e1", "e2")},
        )
        self.assertEqual(_roster_keys(batch, roster)["valdobens"], entry.key)


# ------------------------------------------------------------------------------------------- 4. pos-LLM


def _mention(text: str, value: str, kind: str = "pessoa", canonical: str = "") -> Mention:
    start = text.index(value)
    return Mention(kind=kind, text=value, start=start, end=start + len(value), source_text=value, canonical=canonical)


class ValidacaoPosLlmTests(unittest.TestCase):
    def kept(self, text, *mentions, **kwargs):
        stats = LlmHygieneStats()
        return filter_llm_mentions(list(mentions), text, stats, **kwargs), stats

    def test_data_e_periodo_nao_sao_endereco(self) -> None:
        text = "Os imóveis foram transferidos entre 05/2011 a 05/2012 e em 21/08/2019."
        kept, stats = self.kept(
            text, _mention(text, "05/2011 a 05/2012", "endereco"), _mention(text, "21/08/2019", "endereco")
        )
        self.assertEqual(kept, [])
        self.assertEqual(stats.data, 2)

    def test_codigo_eproc_e_linha_de_folha(self) -> None:
        text = "[fl. 3 · Evento 12 · Joana Prado · p. 2]\nVer CONTES1 e PET1.\n"
        kept, stats = self.kept(text, _mention(text, "Joana Prado"), _mention(text, "CONTES1", "empresa"))
        self.assertEqual(kept, [])
        self.assertEqual((stats.cabecalho_folha, stats.codigo_eproc), (1, 1))

    def test_termos_que_nao_se_mascaram(self) -> None:
        text = "A Holding Patrimonial e a Administradora de Bens; o Titular da Requerida; a Família Prado."
        kept, _stats = self.kept(
            text,
            _mention(text, "Holding Patrimonial", "empresa"),
            _mention(text, "Administradora de Bens", "empresa"),
            _mention(text, "Titular da Requerida"),
            _mention(text, "Família Prado"),
        )
        self.assertEqual(kept, [])

    def test_bordas_aparadas(self) -> None:
        text = "tendo o Eminente Desembargador Federal Otaviano Brum Leal fixado; e Joana Prado e o sócio."
        kept, stats = self.kept(
            text,
            _mention(text, "Eminente Desembargador Federal Otaviano Brum Leal"),
            _mention(text, "e Joana Prado e"),
        )
        self.assertEqual([item.text for item in kept], ["Otaviano Brum Leal", "Joana Prado"])
        self.assertEqual([text[item.start : item.end] for item in kept], ["Otaviano Brum Leal", "Joana Prado"])
        self.assertEqual(stats.borda_aparada, 2)

    def test_empresa_tipada_como_pessoa_vira_empresa(self) -> None:
        text = "A VALDOBENS ADMINISTRADORA LTDA e a Valdobens Administradora responderam."
        kept, stats = self.kept(
            text,
            _mention(text, "VALDOBENS ADMINISTRADORA LTDA"),
            _mention(text, "Valdobens Administradora"),
            known_companies=["VALDOBENS ADMINISTRADORA LTDA"],
        )
        self.assertEqual([item.kind for item in kept], ["empresa", "empresa"])
        self.assertEqual(stats.empresa_como_pessoa, 2)


class AgentePublicoDoLlmTests(_Pipeline):
    def test_relator_e_procurador_da_fazenda_ficam_em_claro(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Relator:\nOTAVIANO BRUM LEAL\n\n"
            "A liminar do relator desembargador federal Otaviano Brum Leal foi cumprida.\n\n"
            "Procurador da Fazenda Nacional SALUSTIANO REIS FARIA requereu a penhora.\n\n"
            "O sócio LAURO PIRES FAGUNDES, CPF 529.982.247-25, foi citado.\n"
        )
        discoverer = FakeDiscoverer(
            [
                ("pessoa", "OTAVIANO BRUM LEAL", "parte"),
                ("pessoa", "Otaviano Brum Leal", "parte"),
                ("pessoa", "SALUSTIANO REIS FARIA", "terceiro"),
                ("pessoa", "LAURO PIRES FAGUNDES", "socio"),
            ]
        )
        _result, body, _audit = self.go(text, discoverer)
        self.assertIn("OTAVIANO BRUM LEAL", body)
        self.assertIn("SALUSTIANO REIS FARIA", body)
        self.assertNotIn("LAURO", body)


# ------------------------------------------------------------------------------------------- 5. prenome da lista


class PrenomeDaListaEmOutroNomeTests(unittest.TestCase):
    def detect(self, text):
        engine = TargetListEngine(people=["AUGUSTO TEIXEIRA ROCHA", "FÁTIMA REGINA LIMA"], lookup_cnpj_names=False)
        return _values(text, engine.detect(text))

    def test_servidor_na_tabela_e_na_assinatura_fica_de_fora(self) -> None:
        for text in (
            "| 12/03/2026 | MARCIO AUGUSTO DO CARMO DE ALMEIDA | Juntada |",
            "Documento assinado por MARCIO AUGUSTO DO CARMO DE ALMEIDA, Servidor",
            "relatora maria de fátima souza lacerda, julgado em 2020",
            "Relator:\nMARIA DE FÁTIMA SOUZA LACERDA\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.detect(text), [])

    def test_prenome_sozinho_com_apoio_continua_do_listado(self) -> None:
        values = self.detect("O sócio AUGUSTO TEIXEIRA ROCHA foi citado. Depois Augusto pagou.")
        self.assertIn(("AUGUSTO TEIXEIRA ROCHA", "alvo:pessoa:augusto teixeira rocha"), values)


# ------------------------------------------------------------------------------------------- 6. trava Gemma


class TravaGemmaTests(unittest.TestCase):
    def test_gemma_no_gemini_pago_e_nivel_gratuito(self) -> None:
        self.assertTrue(is_free_tier_gemma("gemini-pago", "gemma-3-27b-it"))
        self.assertTrue(is_free_tier_gemma("gemini-pago", "models/gemma-4-26b"))
        self.assertFalse(is_free_tier_gemma("gemini-pago", "gemini-2.5-flash"))
        self.assertFalse(is_free_tier_gemma("ollama-local", "gemma4:e4b"))

    def test_so_o_documento_sintetico_passa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "autos.pdf"
            real.write_bytes(b"autos reais ficticios")
            self.assertTrue(gemma_lock_refusals("gemini-pago", "gemma-3-27b-it", [real]))
            self.assertEqual(gemma_lock_refusals("gemini-pago", "gemini-2.5-flash", [real]), [])
            synthetic = next(iter(SYNTHETIC_SHA256))
            with patch("anonimizador_app.llm_gate.file_sha256", lambda _path: synthetic):
                self.assertEqual(gemma_lock_refusals("gemini-pago", "gemma-3-27b-it", [real]), [])

    def test_o_descobridor_nao_cria_o_modelo_gemma_sem_a_trava(self) -> None:
        config = LlmConfig(
            provider="gemini", channel="gemini-pago", model_id="gemma-3-27b-it", api_key=ApiKey("x" * 39, "teste")
        )
        discoverer = LangExtractDiscoverer(config)
        with self.assertRaises(LlmConfigError) as caught:
            discoverer._build_model()
        self.assertIn("Gemma", str(caught.exception))
        discoverer.synthetic_lock = ("0" * 64,)
        with self.assertRaises(LlmConfigError):
            discoverer._build_model()


# ------------------------------------------------------------------------------------------- 7. ELENCO


class ElencoSemCapaTests(unittest.TestCase):
    def test_lote_sem_capa_nao_fala_de_capa_lida(self) -> None:
        cast = build_cast([("a.md", "EXECUTADO: [PESSOA_1]\nO sócio [PESSOA_1] foi citado.\n")])
        self.assertIn("não traz capa", cast)
        self.assertNotIn("capa(s) lida(s)", cast)

    def test_papel_generico_sai_quando_ha_especifico(self) -> None:
        self.assertEqual(_role_summary(["parte", "socio", "terceiro", "desconhecido"]), "socio")
        self.assertEqual(_role_summary(["parte", "terceiro"]), "parte, terceiro")
        self.assertEqual(_role_summary([]), "-")


# ------------------------------------------------------------------------------------------- extras (leitura cega)


class ProcessosNoIndiceTests(unittest.TestCase):
    def test_secao_processos_citados(self) -> None:
        from anonimizador_app.lote_index import build_index

        text = (
            "<!-- pagina-origem: 1 -->\nExecução Fiscal nº [PROCESSO_2] e Agravo de Instrumento [PROCESSO_3].\n"
            "<!-- pagina-origem: 2 -->\nNa execução fiscal [PROCESSO_2] houve penhora. Ver [PROCESSO_4].\n"
        )
        index = build_index([("doc_1.md", text)])
        self.assertIn("## Processos citados", index)
        self.assertIn("| [PROCESSO_2] | 2 | Execução fiscal | doc_1.md, fl. 1 |", index)
        self.assertIn("| [PROCESSO_3] | 1 | Agravo de instrumento | doc_1.md, fl. 1 |", index)
        self.assertIn("| [PROCESSO_4] | 1 |  | doc_1.md, fl. 2 |", index)


class NascimentoConsistenteTests(unittest.TestCase):
    def test_a_mesma_data_ao_lado_leva_o_mesmo_token(self) -> None:
        from anonimizador_app.pipeline_llm import birth_date_spans

        text = "Filho nascido em 14/05/2021, conforme certidão.\nNa certidão: data 14/05/2021. Ajuizada em 14/05/2023."
        start = text.index("14/05/2021")
        span = EntitySpan(start, start + 10, "BR_NASCIMENTO", 0.85, "x", canonical_key="nasc:20210514")
        extra = birth_date_spans(text, [span])
        self.assertEqual([text[item.start : item.end] for item in extra], ["14/05/2021"])
        self.assertEqual(extra[0].canonical_key, "nasc:20210514")
        self.assertGreater(extra[0].start, start)


if __name__ == "__main__":
    unittest.main()
