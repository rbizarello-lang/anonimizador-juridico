"""v0.4.13 (deteccao 2.19.0): denominacao usada por duas empresas, forma parcial do LLM ligada ao elenco, assinante do
carimbo da Junta em claro, prazo das chamadas ao LLM local, tokens por chamada e respostas fora do formato na AUDITORIA,
falha do LLM separada da omissao do LLM.

Todos os nomes, CNPJs, CPFs e enderecos sao inventados (os CNPJs tem digito verificador calculado aqui).
"""

from __future__ import annotations

import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from anonimizador_juridico import EntitySpan

from anonimizador_app import features
from anonimizador_app.llm_discovery import (
    BREAKER_REASON,
    CallDeadlineExceeded,
    Discovery,
    DiscoveryReport,
    LlmConfig,
    LocalCallGuard,
    UsageMeter,
    call_with_deadline,
    is_timeout_error,
)
from anonimizador_app.pipeline_llm import (
    EMPTY_PAGES_PHRASE,
    FAILURE_PAGES_PHRASE,
    LlmFileReport,
    _fallback_lines,
    _split_fallback,
    run_llm_job,
)
from anonimizador_app.public_agents import lot_public_agent, public_agent_context, stamp_signer
from anonimizador_app.roster import Mention, Roster
from anonimizador_app.roster_identity import fuse_identities
from anonimizador_app.shared_names import AMBIGUOUS_SUFFIX, split_shared_names
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

from test_pipeline_llm import FakeDiscoverer, FakeLocalEngine

HAS_LANGEXTRACT = importlib.util.find_spec("langextract") is not None


def _cnpj(base: str, branch: str = "0001") -> str:
    """CNPJ inventado com digito verificador valido, formatado."""

    digits = [int(char) for char in base + branch]
    for weights in ((5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)):
        rest = sum(d * w for d, w in zip(digits, weights)) % 11
        digits.append(0 if rest < 2 else 11 - rest)
    raw = "".join(map(str, digits))
    return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}/{raw[8:12]}-{raw[12:]}"


CNPJ_A = _cnpj("31415926")  # empresa A: hoje "ORVALHO TEXTIL LTDA", sigla OTL
CNPJ_B = _cnpj("27182818")  # empresa B: hoje "BRISA CONFECCOES LTDA"
SHARED = "VENTANIA MALHAS LTDA"  # usada por B (antes) e por A (depois)


class VersaoTest(unittest.TestCase):
    def test_versao(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.17")  # v0.4.17 sobe a do app (tests/test_v0417.py)
        self.assertEqual(DETECTION_VERSION, "2.22.0")

    def test_tres_chaves_novas_ligadas(self) -> None:
        for name in ("shared_company_names", "llm_subform_link", "stamp_public_agents"):
            self.assertIn(name, features.FEATURE_KEYS)
            self.assertTrue(features.enabled(name))


# ----------------------------------------------------------------------------------- 1. denominacao compartilhada


def _shared_doc() -> str:
    return (
        "<!-- pagina-origem: 1 -->\n"
        f"A BRISA CONFECCOES LTDA (CNPJ nº {CNPJ_B}), na época denominada {SHARED}., teve sede na rua de cima.\n"
        f"Em 2005 a empresa {SHARED}. (CNPJ nº {CNPJ_A}, denominação atual: ORVALHO TEXTIL LTDA) entrou na sociedade.\n"
        "\n<!-- pagina-origem: 2 -->\n"
        f"Em 2018 a requerida outorgou poderes para a OTL (na época denominada {SHARED}.) vender o imóvel.\n"
        f"A testemunha foi empregada da OTL (CNPJ nº {CNPJ_A}) por dez anos.\n"
        f"A ORVALHO TEXTIL LTDA (CNPJ nº {CNPJ_A}) segue ativa; a BRISA CONFECCOES LTDA (CNPJ nº {CNPJ_B}) não.\n"
        "\n<!-- pagina-origem: 3 -->\n"
        f"As empresas Alfa Ltda., Beta Ltda. e {SHARED}. formavam um grupo.\n"
    )


def _spans(text: str) -> list[EntitySpan]:
    found = []
    for value, key in (
        (SHARED, "empresa:ventania malhas"),
        ("BRISA CONFECCOES LTDA", "empresa:brisa confeccoes"),
        ("ORVALHO TEXTIL LTDA", "empresa:orvalho textil"),
        ("Alfa Ltda", "empresa:alfa"),
        ("Beta Ltda", "empresa:beta"),
    ):
        start = text.find(value)
        while start >= 0:
            found.append(EntitySpan(start, start + len(value), "ORGANIZATION", 1.0, "teste", canonical_key=key))
            start = text.find(value, start + 1)
    return sorted(found, key=lambda span: span.start)


class DenominacaoCompartilhadaTests(unittest.TestCase):
    def _split(self, text: str):
        spans = _spans(text)
        (out,), found = split_shared_names([(text, spans)])
        keys = [span.canonical_key for span in out if text[span.start : span.end] == SHARED]
        return keys, found, out

    def test_separa_por_cnpj_e_por_outro_nome_ligado_por_denominacao(self) -> None:
        keys, found, _out = self._split(_shared_doc())
        self.assertEqual(len(found), 1)
        by_b, by_a, by_sigla, enumeration = keys
        # A (CNPJ colado) e a sigla de A ligada por "na epoca denominada" ficam juntas (a questao D04 do sintetico 3).
        self.assertEqual(by_a, by_sigla)
        self.assertNotEqual(by_a, by_b)
        # Enumeracao sem expressao de denominacao, numa folha sem outra ocorrencia decidida: token proprio, nunca a fusao.
        self.assertTrue(enumeration.endswith(AMBIGUOUS_SUFFIX))
        self.assertEqual(found[0].counts["cnpj"], 2)
        self.assertEqual(found[0].counts["contexto"], 1)

    def test_span_com_o_ponto_do_ltda(self) -> None:
        # O motor local devolve "Alfa Ltda." com o ponto: o CNPJ colado depois ainda e prova (verificacao no sintetico 3).
        text = _shared_doc()
        spans = [
            span if text[span.start : span.end] != SHARED or text[span.end : span.end + 1] != "."
            else EntitySpan(span.start, span.end + 1, "ORGANIZATION", 1.0, "teste", canonical_key=span.canonical_key)
            for span in _spans(text)
        ]
        (out,), found = split_shared_names([(text, spans)])
        keys = [span.canonical_key for span in out if text[span.start : span.end].rstrip(".") == SHARED]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].counts["cnpj"], 2)
        self.assertEqual(keys[1], keys[2])
        self.assertNotEqual(keys[0], keys[1])

    def test_a_mesma_folha_decide_quando_o_trecho_nao_diz(self) -> None:
        text = _shared_doc() + f"Depois disso, a {SHARED}. mudou de endereço.\n".replace("\n", "") + "\n"
        text = text.replace(
            "\n<!-- pagina-origem: 3 -->", f"Ainda em 2018 a {SHARED}. pagou o imóvel.\n\n<!-- pagina-origem: 3 -->"
        )
        keys, _found, _out = self._split(text)
        # a 3a ocorrencia (folha 2, sem contexto proprio) segue a ocorrencia decidida da mesma folha
        self.assertEqual(keys[2], keys[3])

    def test_forma_de_uma_empresa_so_nao_muda(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n"
            f"A {SHARED}. (CNPJ nº {CNPJ_A}) contratou. A {SHARED}. (CNPJ nº {CNPJ_A}) pagou.\n"
            f"A BRISA CONFECCOES LTDA (CNPJ nº {CNPJ_B}) e a BRISA CONFECCOES LTDA (CNPJ nº {CNPJ_B}).\n"
        )
        spans = _spans(text)
        (out,), found = split_shared_names([(text, spans)])
        self.assertEqual(found, [])
        self.assertEqual([span.canonical_key for span in out], [span.canonical_key for span in spans])

    def test_cnpj_visto_uma_vez_so_nao_basta(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n"
            f"A {SHARED}. (CNPJ nº {CNPJ_A}) contratou; a {SHARED}. (CNPJ nº {CNPJ_A}) pagou.\n"
            f"Numa tabela: {SHARED} | {CNPJ_B}\n"
        )
        (_out,), found = split_shared_names([(text, _spans(text))])
        self.assertEqual(found, [])

    def test_qualificacao_inscrita_no_cnpj(self) -> None:
        text = _shared_doc() + (
            "\n<!-- pagina-origem: 4 -->\n"
            f"{SHARED}., pessoa jurídica de direito privado, com sede na rua de baixo, inscrita no CNPJ sob o nº "
            f"{CNPJ_B}, por seus sócios.\n"
        )
        keys, _found, _out = self._split(text)
        self.assertEqual(keys[-1], keys[0])  # a empresa B, como a da primeira folha

    def test_nome_colado_a_dois_cnpj_nao_une_por_cnpj(self) -> None:
        roster = Roster()
        for name in (SHARED, "ORVALHO TEXTIL LTDA", "BRISA CONFECCOES LTDA"):
            roster.add_mention(Mention(kind="empresa", text=name, canonical=name, role="parte"), source="llm")
        text = (
            f"A ORVALHO TEXTIL LTDA (CNPJ nº {CNPJ_A}) e a BRISA CONFECCOES LTDA (CNPJ nº {CNPJ_B}).\n"
            f"Em 2001: {SHARED} (CNPJ nº {CNPJ_B}). Em 2010: {SHARED} (CNPJ nº {CNPJ_A}).\n"
        )
        removed = fuse_identities(roster, [text])
        self.assertNotIn("empresa:ventania malhas", removed)
        self.assertEqual(len(roster), 3)
        with features.activate(SimpleNamespace(shared_company_names=False)):
            roster2 = Roster()
            for name in (SHARED, "ORVALHO TEXTIL LTDA", "BRISA CONFECCOES LTDA"):
                roster2.add_mention(Mention(kind="empresa", text=name, canonical=name, role="parte"), source="llm")
            self.assertEqual(len(fuse_identities(roster2, [text])), 1)  # a fusao antiga (com uma das duas), desligada

    def test_no_lote_os_tokens_se_separam_e_o_elenco_explica(self) -> None:
        entities = [
            ("empresa", SHARED, "parte"),
            ("empresa", "BRISA CONFECCOES LTDA", "parte"),
            ("empresa", "ORVALHO TEXTIL LTDA", "parte"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text(_shared_doc(), encoding="utf-8")
            result = run_llm_job(
                [source], export_root=base / "exp", private_root=base / "priv", discoverer=FakeDiscoverer(entities),
                use_cache=False, local_floor=False, verification_rounds=0,
            )
            text = (result.job.export_dir / "doc_1.md").read_text(encoding="utf-8")
            cast = (result.job.export_dir / "ELENCO.md").read_text(encoding="utf-8")
            audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertNotIn("VENTANIA", text)
        self.assertIn("Mesma denominação usada por empresas diferentes", cast)
        self.assertIn("Denominacao usada por 2+ empresas (CNPJ diferentes)", audit)
        import re

        lines = text.splitlines()
        bridge_b = next(line for line in lines if "na época denominada" in line and "teve sede" in line)
        otl = next(line for line in lines if "outorgou poderes para a OTL" in line)
        token_b = re.findall(r"\[EMPRESA_\d+[^\]]*\]", bridge_b)[-1]
        token_otl = re.findall(r"\[EMPRESA_\d+[^\]]*\]", otl)[-1]
        self.assertNotEqual(token_b.split("·")[0], token_otl.split("·")[0])


class MotorLocalTests(unittest.TestCase):
    """O caminho so do motor (``pipeline.run_job``, o da tela e do ``tools/rodar_deteccao.py``)."""

    def _run(self, doc: str, names):
        from anonimizador_app.pipeline import run_job

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(doc, encoding="utf-8")
        job = run_job(
            [source], export_root=base / "exp", private_root=base / "priv", contextual=FakeLocalEngine(names),
            use_cache=False,
        )
        read = lambda name: (job.export_dir / name).read_text(encoding="utf-8")  # noqa: E731
        return read("doc_1.md"), read("ELENCO.md"), read("AUDITORIA.txt")

    def test_denominacao_compartilhada_no_motor_local(self) -> None:
        import re

        names = [("ORGANIZATION", value) for value in (SHARED, "BRISA CONFECCOES LTDA", "ORVALHO TEXTIL LTDA")]
        text, cast, audit = self._run(_shared_doc(), names)
        self.assertNotIn("VENTANIA", text)
        lines = text.splitlines()
        bridge_b = next(line for line in lines if "na época denominada" in line and "teve sede" in line)
        otl = next(line for line in lines if "outorgou poderes para a OTL" in line)
        joined = next(line for line in lines if "entrou na sociedade" in line)
        token = lambda line: re.findall(r"\[EMPRESA_\d+[^\]]*\]", line)[0].split("·")[0]  # noqa: E731
        self.assertEqual(token(joined), token(otl))  # empresa A: CNPJ colado e sigla ligada por denominacao
        self.assertNotEqual(re.findall(r"\[EMPRESA_\d+[^\]]*\]", bridge_b)[-1].split("·")[0], token(otl))
        self.assertIn("Mesma denominação usada por empresas diferentes", cast)
        self.assertIn("Denominacao usada por 2+ empresas (CNPJ diferentes), ocorrencias separadas por empresa: 1", audit)

    def test_sem_denominacao_compartilhada_a_auditoria_diz_nenhuma(self) -> None:
        doc = f"<!-- pagina-origem: 1 -->\nA ORVALHO TEXTIL LTDA (CNPJ nº {CNPJ_A}) contratou.\n"
        _text, cast, audit = self._run(doc, [("ORGANIZATION", "ORVALHO TEXTIL LTDA")])
        self.assertIn("Denominacao usada por 2+ empresas (CNPJ diferentes): nenhuma.", audit)
        self.assertNotIn("Mesma denominação usada", cast)

    def test_assinante_do_carimbo_no_motor_local(self) -> None:
        text, _cast, _audit = self._run(
            "<!-- pagina-origem: 1 -->\n" + STAMP + "\nO sócio TIBURCIO LEMOS VARELA assinou.\n",
            [("PERSON", CarimboDaJuntaTests.NAME), ("PERSON", "TIBURCIO LEMOS VARELA")],
        )
        self.assertIn(CarimboDaJuntaTests.NAME, text)
        self.assertNotIn("TIBURCIO", text)


# --------------------------------------------------------------------------- 2. forma parcial do LLM ligada ao elenco


def _people(*items: tuple[str, str]) -> Roster:
    roster = Roster()
    for name, source in items:
        roster.add_mention(Mention(kind="pessoa", text=name, canonical=name, role="socio"), source=source)
    return roster


def _companies(*items: tuple[str, str]) -> Roster:
    roster = Roster()
    for name, source in items:
        roster.add_mention(Mention(kind="empresa", text=name, canonical=name, role="parte"), source=source)
    return roster


class FormaParcialDoLlmTests(unittest.TestCase):
    def test_sobrenome_sozinho_de_uma_pessoa_so_entra_nela(self) -> None:
        roster = _people(("Teodoro Valadares Quintela", "lista"), ("Quintela", "llm"))
        removed = fuse_identities(roster)
        self.assertEqual(removed, {"pessoa:quintela": "pessoa:teodoro valadares quintela"})

    def test_sobrenome_de_familia_com_dois_donos_fica_separado(self) -> None:
        roster = _people(
            ("Teodoro Valadares Quintela", "lista"), ("Iolanda Valadares Quintela", "llm"), ("Quintela", "llm")
        )
        self.assertNotIn("pessoa:quintela", fuse_identities(roster))
        self.assertEqual(len(roster), 3)

    def test_prenome_sozinho_e_prenome_com_segundo_nome(self) -> None:
        roster = _people(("Godofredo Arantes Pimpão", "motor_local"), ("Godofredo", "llm"), ("Godofredo Arantes", "llm"))
        removed = fuse_identities(roster)
        self.assertEqual(set(removed), {"pessoa:godofredo", "pessoa:godofredo arantes"})

    def test_pai_e_filho_nunca(self) -> None:
        roster = _people(("Abelardo Siqueira Neto", "lista"), ("Abelardo Siqueira", "llm"))
        self.assertEqual(fuse_identities(roster), {})
        self.assertEqual(len(roster), 2)

    def test_irmas_e_mae_e_filha_seguem_separadas(self) -> None:
        roster = _people(
            ("Clarice Moura Bettega", "lista"), ("Celina Moura Bettega", "lista"), ("Moura Bettega", "llm"),
            ("Bettega", "llm"),
        )
        removed = fuse_identities(roster)
        self.assertNotIn("pessoa:bettega", removed)
        roster = _people(("Rute Lins Pasquali", "lista"), ("Rute Lins Pasquali Dornelles", "llm"))
        self.assertEqual(fuse_identities(roster), {})  # 3+ palavras com outro ultimo sobrenome: nada novo (v0.4.10)

    def test_so_entrada_do_llm(self) -> None:
        roster = _people(("Teodoro Valadares Quintela", "lista"), ("Quintela", "motor_local"))
        self.assertEqual(fuse_identities(roster), {})

    def test_razao_social_sem_sufixo_e_abreviada(self) -> None:
        roster = _companies(
            ("AURORA SUL VESTUARIO INFANTIL LTDA", "lista"), ("Aurora Sul Vestuario", "llm"),
            ("Aurora Sul Vestuario Inf. Ltda", "llm"),
        )
        removed = fuse_identities(roster)
        self.assertEqual(
            set(removed), {"empresa:aurora sul vestuario", "empresa:aurora sul vestuario inf"}
        )

    def test_razao_social_com_sufixo_e_outra_empresa(self) -> None:
        # "AURORA SUL LTDA" e uma razao social inteira (pode ser o nome antigo de outra empresa do grupo): nao entra.
        roster = _companies(("AURORA SUL VESTUARIO INFANTIL LTDA", "lista"), ("Aurora Sul Ltda", "llm"))
        self.assertEqual(fuse_identities(roster), {})

    def test_radical_comum_de_duas_empresas_fica_separado(self) -> None:
        roster = _companies(
            ("AURORA SUL VESTUARIO INFANTIL LTDA", "lista"), ("AURORA SUL MALHARIA LTDA", "lista"),
            ("Aurora Sul", "llm"),
        )
        self.assertEqual(fuse_identities(roster), {})

    def test_chave_desligada(self) -> None:
        roster = _people(("Teodoro Valadares Quintela", "lista"), ("Quintela", "llm"))
        with features.activate(SimpleNamespace(llm_subform_link=False)):
            self.assertEqual(fuse_identities(roster), {})


# ------------------------------------------------------------------- 3. assinante do carimbo da Junta Comercial


STAMP = (
    "JUNTA COMERCIAL DO ESTADO DE XAVANTINA\nCERTIFICO O REGISTRO EM: 03/04/2005\nSOB O NUMERO: 20051234567\n"
    "HILDEBRANDO NOGUEIRA FALCAO\nProtocolo: 05/123456-7\nSECRETARIO GERAL\n"
)


class CarimboDaJuntaTests(unittest.TestCase):
    NAME = "HILDEBRANDO NOGUEIRA FALCAO"

    def _check(self, text: str) -> bool:
        start = text.index(self.NAME)
        return public_agent_context(text, start, start + len(self.NAME))

    def test_cargo_abaixo_do_protocolo(self) -> None:
        self.assertTrue(self._check(STAMP))

    def test_cargo_deturpado_pelo_ocr(self) -> None:
        for garbled in ("BECRETARO GERAL", "SECRETÁIIO GERAL", "Secretária-Geral"):
            text = f"Assinado:\n{self.NAME}\n{garbled}\n"
            self.assertTrue(self._check(text), garbled)

    def test_sem_carimbo_tres_linhas_nao_bastam(self) -> None:
        text = f"{self.NAME}\nRua das Flores, 10\nSECRETARIO GERAL\n"
        self.assertFalse(self._check(text))

    def test_particular_na_frase_e_nome_que_continua_na_linha(self) -> None:
        self.assertFalse(self._check(f"{self.NAME}, brasileiro, casado, CPF 000\nSECRETARIO GERAL\n"))
        self.assertFalse(self._check(f"{self.NAME}\nSECRETARIA DA VARA\n"))

    def test_lote_e_chave(self) -> None:
        start = STAMP.index(self.NAME)
        self.assertTrue(lot_public_agent([(STAMP, start, start + len(self.NAME))]))
        with features.activate(SimpleNamespace(stamp_public_agents=False)):
            self.assertFalse(stamp_signer(STAMP, start, start + len(self.NAME)) and features.enabled("stamp_public_agents"))
            self.assertFalse(public_agent_context(STAMP, start, start + len(self.NAME)))

    def test_nome_do_llm_no_carimbo_fica_em_claro_no_lote(self) -> None:
        doc = "<!-- pagina-origem: 1 -->\n" + STAMP
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text(doc, encoding="utf-8")
            result = run_llm_job(
                [source], export_root=base / "exp", private_root=base / "priv",
                discoverer=FakeDiscoverer([("pessoa", self.NAME, "parte")]),
                use_cache=False, local_floor=False, verification_rounds=0,
            )
            text = (result.job.export_dir / "doc_1.md").read_text(encoding="utf-8")
        self.assertIn(self.NAME, text)


# ------------------------------------------------------------------------- 4. prazo das chamadas ao LLM local


class PrazoTests(unittest.TestCase):
    def test_call_with_deadline(self) -> None:
        self.assertEqual(call_with_deadline(lambda: 7, 1.0), 7)
        self.assertEqual(call_with_deadline(lambda: 8, None), 8)
        abandoned: list[bool] = []
        release = threading.Event()
        started = time.perf_counter()
        with self.assertRaises(CallDeadlineExceeded) as caught:
            call_with_deadline(lambda: release.wait(5), 0.2, lambda: abandoned.append(True))
        release.set()
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(abandoned, [True])
        self.assertTrue(is_timeout_error(caught.exception))
        with self.assertRaises(ValueError):
            call_with_deadline(lambda: (_ for _ in ()).throw(ValueError("x")), 1.0)

    def test_guard(self) -> None:
        guard = LocalCallGuard(limit=2)
        guard.timed_out()
        self.assertFalse(guard.tripped())
        guard.answered()
        guard.timed_out()
        guard.timed_out()
        self.assertTrue(guard.tripped())


@unittest.skipUnless(HAS_LANGEXTRACT, "langextract nao instalado")
class PrazoNoModeloTests(unittest.TestCase):
    def _model(self, behaviour, **kwargs):
        from langextract.core import base_model
        from langextract.core import types as lx_types

        from anonimizador_app.llm_discovery import wrap_model

        calls: list[str] = []
        failures: list[tuple[str, str]] = []

        class Inner(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **_kwargs):
                for prompt in batch_prompts:
                    calls.append(prompt)
                    yield [lx_types.ScoredOutput(score=1.0, output=behaviour(prompt))]

        model = wrap_model(Inner(), max_workers=1, retries=1, on_failure=lambda p, r: failures.append((p, r)), **kwargs)
        return model, calls, failures

    def test_chamada_pendurada_tem_uma_nova_tentativa_e_vai_ao_motor_local(self) -> None:
        hang = threading.Event()
        meter = UsageMeter()
        model, calls, failures = self._model(
            lambda prompt: hang.wait(5) and "", local=True, deadline=0.15, guard=LocalCallGuard(), meter=meter
        )
        started = time.perf_counter()
        outputs = list(model.infer(["trecho 1"]))
        hang.set()
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertEqual(len(calls), 2)  # a chamada e UMA nova tentativa
        self.assertIn("tempo esgotado", failures[0][1])
        self.assertEqual(outputs[0][0].output, '{"extractions": []}')
        self.assertEqual(meter.timeouts, 2)

    def test_disjuntor_para_de_enviar_depois_de_chamadas_seguidas_fora_do_prazo(self) -> None:
        hang = threading.Event()
        guard = LocalCallGuard(limit=2)
        model, calls, failures = self._model(lambda prompt: hang.wait(5) and "", local=True, deadline=0.1, guard=guard)
        list(model.infer(["trecho 1"]))
        list(model.infer(["trecho 2"]))
        hang.set()
        self.assertEqual(len(calls), 2)  # o 2o trecho nem foi enviado
        self.assertEqual(failures[-1][1], BREAKER_REASON)
        self.assertEqual(guard.skipped, 1)

    def test_resposta_fora_do_formato_e_contada(self) -> None:
        meter = UsageMeter()
        model, _calls, failures = self._model(lambda prompt: "{quebrado", local=True, meter=meter)
        list(model.infer(["trecho"]))
        self.assertEqual(failures[0][1], "resposta fora do formato JSON")
        self.assertEqual(meter.unparsable, 1)


# ---------------------------------------------------------- 5. tokens por chamada e respostas fora do formato


class TokensPorChamadaTests(unittest.TestCase):
    def test_ollama_gemini_e_openrouter(self) -> None:
        meter = UsageMeter()
        meter.record_ollama(SimpleNamespace(json=lambda: {"eval_count": 120, "prompt_eval_count": 900}))
        meter.record_ollama(SimpleNamespace(json=lambda: {"eval_count": 480}))
        meter.record_ollama(SimpleNamespace(json=lambda: (_ for _ in ()).throw(ValueError("sem json"))))
        usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=200, thoughts_token_count=100,
                                cached_content_token_count=0)
        meter.record(SimpleNamespace(usage_metadata=usage, candidates=[]))
        meter.record_openrouter({"usage": {"completion_tokens": 300, "completion_tokens_details": {"reasoning_tokens": 0}}})
        self.assertEqual(meter.generated, [120, 480, 300, 300])
        meter.record_unparsable()
        line = meter.output_lines()[0]
        self.assertIn("media 300, maximo 480, total 1200, em 4 chamada(s)", line)
        self.assertIn("respostas fora do formato (JSON invalido ou ilegivel): 1", line)

    def test_sem_contagem_e_alerta_de_prazo(self) -> None:
        meter = UsageMeter()
        self.assertIn("o provedor nao informou", meter.output_lines()[0])
        meter.record_timeout()
        lines = meter.output_lines(deadline=660, skipped=3)
        self.assertIn("ALERTA: 1 chamada(s) ao LLM fora do prazo (prazo total por chamada: 660 s)", lines[1])
        self.assertIn("nao enviados porque o LLM parou de responder: 3", lines[1])

    def test_descobridor_local_informa_o_prazo(self) -> None:
        from anonimizador_app.llm_discovery import LangExtractDiscoverer

        discoverer = LangExtractDiscoverer(LlmConfig(provider="ollama", model_id="qwen3:8b", timeout=600))
        discoverer.usage.record_timeout()
        self.assertIn("660 s", discoverer.output_stats_lines()[1])

    def test_auditoria_exportavel_e_privada(self) -> None:
        class Counting(FakeDiscoverer):
            def output_stats_lines(self):
                return ["Tokens gerados por chamada (saida + raciocinio, como o provedor informa): media 250, maximo "
                        "400, total 500, em 2 chamada(s) com contagem; respostas fora do formato (JSON invalido ou "
                        "ilegivel): 1."]

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text("<!-- pagina-origem: 1 -->\nTexto sem nomes.\n", encoding="utf-8")
            result = run_llm_job(
                [source], export_root=base / "exp", private_root=base / "priv", discoverer=Counting([]),
                use_cache=False, local_floor=False, verification_rounds=0,
            )
            audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
            private = (result.job.map_path.parent / "AUDITORIA_PRIVADA.txt").read_text(encoding="utf-8")
        for body in (audit, private):
            self.assertIn("media 250, maximo 400, total 500", body)
            self.assertIn("respostas fora do formato (JSON invalido ou ilegivel): 1", body)
        self.assertIn("## Tokens gerados pelo LLM por chamada e respostas fora do formato", private)


# --------------------------------------------------------------- 6. falha do LLM x omissao do LLM coberta pelo motor


DOC3 = (
    "<!-- pagina-origem: 1 -->\nA sócia GENOVEVA PIRES TABORDA assinou.\n\n"
    "<!-- pagina-origem: 2 -->\nO sócio FLORISVALDO MATTOS BRANDT assinou.\n\n"
    "<!-- pagina-origem: 3 -->\nA testemunha EUSTAQUIA RAMOS VIDIGAL assinou.\n"
)


class FalhaOuOmissao(FakeDiscoverer):
    """Folha 2: falha do LLM; folha 3: resposta vazia (sem nomes); folha 1: lida."""

    def discover(self, text: str) -> Discovery:
        found = super().discover(text)
        two, three = text.index("<!-- pagina-origem: 2 -->"), text.index("<!-- pagina-origem: 3 -->")
        found.report.failed = [(two, three)]
        found.report.empty = [(three, len(text))]
        found.report.failure_reasons = ["simulada"]
        found.mentions = [mention for mention in found.mentions if mention.start < two]
        return found


class FalhaOuOmissaoTests(unittest.TestCase):
    def test_linhas_separadas(self) -> None:
        report = LlmFileReport(discovery=DiscoveryReport(failed=[(0, 1)]), fallback_pages=[2, 3], failure_pages=[2],
                               omission_pages=[3])
        failed, omitted = _split_fallback(report)
        lines = _fallback_lines({"doc_1.md": failed}, [], omitted={"doc_1.md": omitted})
        self.assertEqual(lines, [f"{FAILURE_PAGES_PHRASE}: doc_1.md: paginas 2.", f"{EMPTY_PAGES_PHRASE}: doc_1.md: paginas 3."])

    def test_relatorio_antigo_sem_separacao(self) -> None:
        old = LlmFileReport(discovery=DiscoveryReport(), fallback_pages=[4])
        self.assertEqual(_split_fallback(old), ([], [4]))
        failed = LlmFileReport(discovery=DiscoveryReport(failed=[(0, 1)]), fallback_pages=[4])
        self.assertEqual(_split_fallback(failed), ([4], []))

    def test_no_lote(self) -> None:
        names = [("pessoa", "GENOVEVA PIRES TABORDA", "socio")]
        engine = FakeLocalEngine([("PERSON", "FLORISVALDO MATTOS BRANDT"), ("PERSON", "EUSTAQUIA RAMOS VIDIGAL")])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "autos.md"
            source.write_text(DOC3, encoding="utf-8")
            result = run_llm_job(
                [source], export_root=base / "exp", private_root=base / "priv", discoverer=FalhaOuOmissao(names),
                use_cache=False, local_floor=False, verification_rounds=0, fallback_engine=engine,
            )
            audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        report = result.reports["autos.md"]
        self.assertEqual((report.failure_pages, report.omission_pages), ([2], [3]))
        self.assertEqual(result.fallback_pages, {"autos.md": [2, 3]})
        self.assertIn(f"{FAILURE_PAGES_PHRASE}: doc_1.md: paginas 2.", audit)
        self.assertIn(f"{EMPTY_PAGES_PHRASE}: doc_1.md: paginas 3.", audit)
        self.assertNotIn(f"{FAILURE_PAGES_PHRASE}: doc_1.md: paginas 2 e 3", audit)


if __name__ == "__main__":
    unittest.main()
