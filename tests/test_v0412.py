"""v0.4.12 (deteccao 2.18.0): OCR das imagens embutidas, limpeza do OCR com marcador visivel, nome e numero deturpados pelo
OCR, cartorarios em claro, falsos positivos do caso 2 (motor sozinho), chaves de liga/desliga e AVISOS.md.

Todos os nomes, CPFs, CNPJs, NIREs e enderecos sao inventados.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from anonimizador_app import features, ocr_pages, uncertainty
from anonimizador_app.audit import coverage_note
from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.coverage import parse_coverage
from anonimizador_app.fast_extract import EMBEDDED_CLOSE, EMBEDDED_OPEN, extract_fast
from anonimizador_app.local_ner import CombinedEngine
from anonimizador_app.ocr_fuzzy import Target, find, first_name_ok, normalize
from anonimizador_app.ocr_numbers import corrected
from anonimizador_app.pipeline import run_job
from anonimizador_app.public_agents import public_agent_context
from anonimizador_app.sense_guard import is_generic_expression, misread, public_organ_contact
from anonimizador_app.settings import AppSettings, load_settings, save_settings
from anonimizador_app.target_list import TargetListEngine
from anonimizador_app.version import APP_VERSION, DETECTION_VERSION

from test_v0411 import _Lote, _Ner, _span

HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None

# CPF e CNPJ ficticios com DV valido (gerados para teste; nao sao de ninguem).
CPF_OK = "529.982.247-25"
CPF_OCR = "529.982.247-2S"  # S no lugar de 5
CPF_OCR_DV_ERRADO = "529.982.247-3S"
CNPJ_OK = "11.222.333/0001-81"
CNPJ_OCR = "11.222.333/OOO1-81"


class VersaoTest(unittest.TestCase):
    def test_versoes(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.15")  # v0.4.15 sobe as duas (tests/test_v0415.py)
        self.assertEqual(DETECTION_VERSION, "2.21.0")


# ----------------------------------------------------------------------------------------------- chaves (config.json)


class ChavesTests(unittest.TestCase):
    def test_seis_chaves_ligadas_por_padrao_e_gravadas(self) -> None:
        self.assertEqual(features.FEATURE_KEYS[:6], (
            "ocr_embedded_images", "ocr_cleanup", "ocr_fuzzy_names", "ocr_fuzzy_numbers", "notary_public_agents",
            "case2_false_positives",
        ))  # v0.4.13 acrescentou tres chaves depois destas (test_v0413)
        settings = AppSettings()
        for name in features.FEATURE_KEYS:
            self.assertTrue(getattr(settings, name))
        with tempfile.TemporaryDirectory() as tmp:
            settings.ocr_cleanup = False
            save_settings(Path(tmp), settings)
            payload = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual({name: payload[name] for name in features.FEATURE_KEYS}["ocr_cleanup"], False)
            loaded = load_settings(Path(tmp))
            self.assertFalse(loaded.ocr_cleanup)
            self.assertTrue(loaded.case2_false_positives)

    def test_config_antigo_sem_as_chaves_liga_tudo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text("{}", encoding="utf-8")
            loaded = load_settings(Path(tmp))
        self.assertTrue(all(getattr(loaded, name) for name in features.FEATURE_KEYS))

    def test_ativacao_vale_so_na_rodada_e_separa_o_cache(self) -> None:
        self.assertTrue(features.enabled("ocr_embedded_images"))
        with features.activate(SimpleNamespace(ocr_embedded_images=False, ocr_cleanup=False)):
            self.assertFalse(features.enabled("ocr_embedded_images"))
            self.assertEqual(features.extractor_suffix(), "|sem-recortes|sem-limpeza")
            self.assertEqual(features.current().off(), ("ocr_embedded_images", "ocr_cleanup"))
        self.assertTrue(features.enabled("ocr_embedded_images"))
        self.assertEqual(features.extractor_suffix(), "")


# ---------------------------------------------------------------------------------------- 1. imagens embutidas


def _lines(page, lines, *, top: float, size: float = 9) -> None:
    for index, line in enumerate(lines):
        page.insert_text((40, top + index * 13), line, fontsize=size)


def _image(page, rect) -> None:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    pix.clear_with(200)
    page.insert_image(rect, pixmap=pix)


_ANTES = [f"Linha {n} da peticao ficticia da Uniao, antes do recorte colado como imagem na folha." for n in range(30)]
_DEPOIS = [f"Paragrafo {n} posterior ao recorte, com o texto corrido da peca ficticia em exame." for n in range(12)]


@unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF nao instalado")
class ImagemEmbutidaTests(unittest.TestCase):
    def _pdf(self, directory: Path, *, logo_only: bool = False) -> Path:
        import pymupdf

        path = directory / "peticao_ficticia.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            _lines(page, _ANTES[:12], top=40)
            if logo_only:
                _image(page, pymupdf.Rect(480, 10, 540, 40))  # logotipo: < 5% da folha
            else:
                _image(page, pymupdf.Rect(40, 210, 560, 400))  # recorte: ~20% da folha
            _lines(page, _DEPOIS, top=420)
            document.save(path)
        return path

    def test_recorte_lido_e_posto_no_lugar_da_imagem(self) -> None:
        calls: list[tuple] = []

        def clip(page, box) -> str:
            calls.append(box)
            # a primeira linha repete a camada nativa (legenda): nao se duplica
            return f"{_DEPOIS[0]}\nR.5/99.999 DOACAO ficticia: o donatario Fulano Imaginario recebeu o imovel."

        with tempfile.TemporaryDirectory() as tmp:
            text = extract_fast(self._pdf(Path(tmp)), read_page=lambda _p: "", read_clip=clip).markdown
        self.assertEqual(len(calls), 1)
        self.assertIn(f"{EMBEDDED_OPEN}\nR.5/99.999 DOACAO ficticia", text)
        self.assertIn(EMBEDDED_CLOSE, text)
        self.assertLess(text.index(_ANTES[11]), text.index(EMBEDDED_OPEN))
        self.assertLess(text.index(EMBEDDED_CLOSE), text.index(_DEPOIS[0]))
        self.assertEqual(text.count(_DEPOIS[0]), 1)
        coverage = parse_coverage(text)
        self.assertEqual((coverage.native, coverage.embedded, coverage.embedded_pages), (1, 1, 1))
        self.assertIn("1 imagem(ns) embutida(s) lida(s) por OCR em 1 folha(s) de texto", coverage_note(coverage))

    def test_imagem_sem_texto_e_falha_ficam_marcadas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self._pdf(Path(tmp))
            photo = extract_fast(pdf, read_page=lambda _p: "", read_clip=lambda _p, _b: "").markdown

            def broken(_page, _box) -> str:
                raise RuntimeError("motor indisponivel")

            failed = extract_fast(pdf, read_page=lambda _p: "", read_clip=broken).markdown
        self.assertIn("«[imagem sem texto legível, fl. 1]»", photo)
        self.assertEqual(parse_coverage(photo).embedded_empty, 1)
        self.assertIn("«[imagem não lida, fl. 1]»", failed)
        coverage = parse_coverage(failed)
        self.assertEqual(coverage.embedded_failed, 1)
        self.assertIn("ALERTA", coverage_note(coverage))

    def test_logotipo_pequeno_e_chave_desligada_nao_leem(self) -> None:
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as tmp:
            extract_fast(self._pdf(Path(tmp), logo_only=True), read_page=lambda _p: "", read_clip=lambda *_a: calls.append(1) or "x")
            with features.activate(SimpleNamespace(ocr_embedded_images=False)):
                text = extract_fast(self._pdf(Path(tmp)), read_page=lambda _p: "", read_clip=lambda *_a: calls.append(1) or "x").markdown
        self.assertEqual(calls, [])
        self.assertNotIn(EMBEDDED_OPEN, text)


# ------------------------------------------------------------------------------------- 2. limpeza da leitura do OCR


class _FakeResult:
    def __init__(self, rows) -> None:
        self.txts = [row[0] for row in rows]
        self.scores = [row[1] for row in rows]
        self.boxes = [[(0, 0), (10, 0), (10, 5), (0, 5)] for _row in rows]


class LimpezaOcrTests(unittest.TestCase):
    def _clean(self, rows, rereads):
        """``rereads``: texto -> (leitura virada, score virado, releitura sem sublinhado, score)."""

        import numpy as np

        current = {"text": ""}

        def crop(img, _box):
            return np.array([[3, 3]]) if isinstance(img, str) and img == "limpa" else np.array([[1, 2]])

        def read(_engine, value):
            flipped, flipped_score, again, again_score = rereads.get(current["text"], ("", 0.0, current["text"], 0.0))
            if value.tolist() == [[2, 1]]:
                return flipped, flipped_score
            return again, again_score

        stats: dict = {}
        lines: list[str] = []
        with mock.patch.object(ocr_pages, "_decode", lambda _image: "img"), mock.patch.object(
            ocr_pages, "remove_underlines", lambda _img: "limpa"
        ), mock.patch.object(ocr_pages, "_read_crop", read), mock.patch.object(ocr_pages, "_crop", crop):
            for row in rows:
                current["text"] = row[0]
                lines += ocr_pages.clean_ocr_lines(None, b"", _FakeResult([row]), stats, page_number=7)
        return lines, stats

    def test_verso_espelhado_vira_um_marcador_por_folha(self) -> None:
        rows = [("Texto da frente com confianca alta", 0.99), ("AHMAM", 0.86), ("OMIVRE", 0.80)]
        rereads = {"AHMAM": ("MAMHA", 0.99, "AHMAM", 0.5), "OMIVRE": ("ERVIMO", 0.98, "OMIVRE", 0.5)}
        lines, stats = self._clean(rows, rereads)
        self.assertEqual(lines[0], "Texto da frente com confianca alta")
        self.assertIn(ocr_pages.MIRROR_MARK, lines)
        self.assertNotIn("AHMAM", "\n".join(lines))
        self.assertEqual(stats["espelhada"], 2)

    def test_baixa_confianca_vira_marcador_visivel_com_o_texto(self) -> None:
        rows = [("Mlainekcaury rubrica", 0.55), ("-4", 0.30)]
        lines, stats = self._clean(rows, {})
        self.assertEqual(lines, ["«[OCR incerto, fl. 7: Mlainekcaury rubrica]»"])  # o fragmento de 1-2 caracteres sai
        self.assertEqual((stats["incerta"], stats["fragmento"]), (1, 1))

    def test_sublinhado_relido_fica_com_a_melhor_leitura(self) -> None:
        rows = [("Os abaixo assinados, FULANO DE TAI", 0.80)]
        rereads = {"Os abaixo assinados, FULANO DE TAI": ("", 0.1, "Os abaixo assinados, FULANO DE TAL", 0.97)}
        lines, stats = self._clean(rows, rereads)
        self.assertEqual(lines, ["Os abaixo assinados, FULANO DE TAL"])
        self.assertEqual(stats["relida_sem_sublinhado"], 1)

    def test_marcador_nao_parece_token(self) -> None:
        self.assertEqual(ocr_pages.uncertain_mark("[x] y", 3), "«[OCR incerto, fl. 3: (x) y]»")


# ------------------------------------------------------------------------------------- 3. nome deturpado pelo OCR


def _ocr_text(*lines: str, native: str = "Texto nativo da folha.") -> str:
    return "<!-- pagina-origem: 1 -->\n" + native + "\n<!-- ocr: escaneada -->\n" + "\n".join(lines) + "\n"


class NomeDeturpadoRegraTests(unittest.TestCase):
    PEOPLE = [
        Target("p:g", "PERSON", "Gertrudes Valente Bragança"),
        Target("p:r", "PERSON", "Rosalinda Valente Bragança"),
        Target("p:e", "PERSON", "Lindalva Prates Fontoura Lemes"),
        Target("p:m", "PERSON", "Eunice Prates Fontoura Lemes"),
        Target("p:o", "PERSON", "Mario Augusto Pereira Lins"),
    ]

    def test_casa_letra_trocada_e_nome_colado(self) -> None:
        text = _ocr_text("Os abaixo, GERTRUDESVALENTE BRAGANGA, casada, e Lindalva Prates FONTOURA LEMES.")
        hits, refusals = find(text, self.PEOPLE)
        self.assertEqual(
            {(text[h.start : h.end], h.target.key) for h in hits},
            {("GERTRUDESVALENTE BRAGANGA", "p:g"), ("Lindalva Prates FONTOURA LEMES", "p:e")},
        )
        self.assertEqual(refusals, [])

    def test_irmas_com_prenome_diferente_nao_casam(self) -> None:
        text = _ocr_text("Eunice Prates FONTOURA LEMES assinou; ROSALINDA VALEMTE BRAGANÇA tambem.")
        hits, _refusals = find(text, self.PEOPLE)
        self.assertEqual({h.target.key for h in hits}, {"p:m", "p:r"})  # cada irma no seu nome, nunca na outra

    def test_prenome_nunca_tolerado_por_inteiro(self) -> None:
        self.assertFalse(first_name_ok(normalize("mario"), normalize("maria")))  # vogal trocada: outra pessoa
        self.assertTrue(first_name_ok(normalize("karia"), normalize("maria")))  # M/K: letra que o OCR confunde
        text = _ocr_text("MARIA AUGUSTO PEREIRA LINS compareceu.")  # nao e o Mario
        hits, _refusals = find(text, self.PEOPLE)
        self.assertEqual(hits, [])

    def test_grafia_perto_de_dois_nomes_nao_casa_nenhum(self) -> None:
        targets = [Target("p:1", "PERSON", "Joana Silveira Prado"), Target("p:2", "PERSON", "Joana Silveira Pardo")]
        text = _ocr_text("A socia JOANA SILVEIRA PRRDO retirou-se.")
        hits, refusals = find(text, targets)
        self.assertEqual(hits, [])
        self.assertEqual(len(refusals), 1)
        self.assertEqual({t.key for t in refusals[0].targets}, {"p:1", "p:2"})

    def test_so_nas_folhas_de_ocr_e_so_nome_longo(self) -> None:
        native = "<!-- pagina-origem: 1 -->\nGERTRUDESVALENTE BRAGANGA assinou na folha nativa.\n"
        self.assertEqual(find(native, self.PEOPLE), ([], []))
        short = [Target("p:s", "PERSON", "Ana Lima")]  # < 10 letras: nunca tolerante
        self.assertEqual(find(_ocr_text("ANA LIMB assinou."), short), ([], []))


class NomeDeturpadoMotorTests(unittest.TestCase):
    def _detect(self, text, people=(), companies=()):
        uncertainty.reset()
        engine = TargetListEngine(people=people, companies=companies, lookup_cnpj_names=False)
        return {text[s.start : s.end]: s for s in engine.detect(text)}

    def test_mesmo_token_do_nome_certo_e_aviso(self) -> None:
        text = _ocr_text("socios GERTRUDES VALENTE BRAGANÇA e GERTRUDESVALENTE BRAGANGA.")
        found = self._detect(text, people=["Gertrudes Valente Bragança"])
        self.assertIn("GERTRUDESVALENTE BRAGANGA", found)
        self.assertEqual(
            found["GERTRUDESVALENTE BRAGANGA"].canonical_key, found["GERTRUDES VALENTE BRAGANÇA"].canonical_key
        )
        pending = uncertainty._pending[uncertainty._key(text)]
        self.assertEqual([item.reason for item in pending], [uncertainty.FUZZY_NAME])

    def test_homonimo_de_empresario_individual_nao_funde(self) -> None:
        text = _ocr_text("O contrato de JOAQUIM FICTICIO LARANJEIRA ME, firma de JOAQUlM FICTICIO LARANJEIRA ME.")
        found = self._detect(
            text, people=["Joaquim Ficticio Laranjeira"], companies=["Joaquim Ficticio Laranjeira ME"]
        )
        keys = {span.canonical_key for value, span in found.items() if "LARANJEIRA" in value}
        self.assertEqual(len(keys), 1)  # a firma inteira, nunca a pessoa colada nela

    def test_chave_desligada(self) -> None:
        text = _ocr_text("GERTRUDESVALENTE BRAGANGA assinou.")
        with features.activate(SimpleNamespace(ocr_fuzzy_names=False)):
            found = self._detect(text, people=["Gertrudes Valente Bragança"])
        # v0.4.14: os pedacos exatos colados ("GERTRUDESVALENTE" + "BRAGANGA", particula com 1 letra de OCR) viram um
        # span so; o que a chave desliga e o casamento do nome inteiro deturpado (e o aviso dele).
        self.assertFalse(uncertainty._pending.get(uncertainty._key(text)))

    def test_recusa_por_ambiguidade_vai_para_o_aviso(self) -> None:
        text = _ocr_text("A socia JOANA SILVEIRA PRRDO retirou-se.")
        found = self._detect(text, people=["Joana Silveira Prado", "Joana Silveira Pardo"])
        self.assertNotIn("JOANA SILVEIRA PRRDO", found)
        pending = uncertainty._pending[uncertainty._key(text)]
        self.assertEqual(pending[0].reason, uncertainty.AMBIGUOUS)
        self.assertEqual(len(pending[0].candidates), 2)


# ------------------------------------------------------------------------------------ 4. numero deturpado pelo OCR


class NumeroDeturpadoTests(_Lote):
    def test_correcao_so_em_sequencia_de_digitos(self) -> None:
        self.assertEqual(corrected("529.982.247-2S"), "529.982.247-25")
        self.assertIsNone(corrected("529.982.247-25"))  # sem letra: regra de sempre
        self.assertIsNone(corrected("SOLO"))

    def test_cpf_e_cnpj_com_dv_levam_o_token_do_numero_limpo(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            f"Socio inscrito no CPF {CPF_OK}. Na folha copiada, o mesmo socio: {CPF_OCR}.\n"
            f"Outro numero sem DV: {CPF_OCR_DV_ERRADO}.\n"
            f"Empresa inscrita no CNPJ {CNPJ_OK} e, no carimbo, {CNPJ_OCR}.\n"
            "NIRE 4220O860156 registrado na Junta. RG nº 4.l23.456 SSP/SC. Matrícula nº 2O.443 do registro.\n"
        )
        body, _cast = self.local(text, [])
        self.assertNotIn(CPF_OCR, body)
        self.assertNotIn(CNPJ_OCR, body)
        cpf_tokens = set(__import__("re").findall(r"\[CPF_\d+\]", body))
        self.assertEqual(len(cpf_tokens), 1)  # o deturpado no token do limpo
        self.assertIn(CPF_OCR_DV_ERRADO, body)  # DV nao confere: nao e CPF
        self.assertNotIn("4220O860156", body)
        self.assertNotIn("4.l23.456", body)
        self.assertNotIn("2O.443", body)

    def test_chave_desligada(self) -> None:
        text = "<!-- pagina-origem: 1 -->\n\nNIRE 4220O860156 registrado na Junta.\n"
        base, source = self._paths(text)
        settings = AppSettings(local_ner_enabled=False, cnpj_lookup_enabled=False, ocr_fuzzy_numbers=False)
        from anonimizador_app.pipeline_llm import run_local_job

        result = run_local_job(
            [source], export_root=base / "e", private_root=base / "p", settings=settings, use_cache=False,
            fallback_engine=CombinedEngine([build_contextual_engine(settings, use_ner_cache=False), _Ner([])]),
        )
        body = result.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("4220O860156", body)


# ---------------------------------------------------------------------------------------------- 5. cartorios


class CartorioTests(unittest.TestCase):
    def test_titulos_de_cartorio_colados_ao_nome(self) -> None:
        for value, needle in (
            ("Lavrada pelo Tabelião Substituto Fulano Beltrano Siqueira, conforme o livro.", "Fulano Beltrano Siqueira"),
            ("Oficial Registradora: Maria Ficticia Soares\n", "Maria Ficticia Soares"),
            ("Eu, Carlos Imaginario Lopes, Escrevente Notarial, lavrei a escritura.", "Carlos Imaginario Lopes"),
            ("Substituta do Oficial Helena Ficticia Prates conferiu.", "Helena Ficticia Prates"),
            ("Registrador de Imóveis Otavio Inventado Ramos certifica.", "Otavio Inventado Ramos"),
        ):
            with self.subTest(value=value):
                self.assertTrue(public_agent_context(value, *_span(value, needle)))

    def test_particular_na_frase_segue_trocado(self) -> None:
        value = "O socio Pedro Ficticio Ramos, CPF 529.982.247-25, compareceu perante o tabelião."
        self.assertFalse(public_agent_context(value, *_span(value, "Pedro Ficticio Ramos")))

    def test_chave_desligada(self) -> None:
        value = "Lavrou a escritura o notário Otavio Inventado Ramos, conforme o livro."
        self.assertTrue(public_agent_context(value, *_span(value, "Otavio Inventado Ramos")))
        with features.activate(SimpleNamespace(notary_public_agents=False)):
            self.assertFalse(public_agent_context(value, *_span(value, "Otavio Inventado Ramos")))


# ------------------------------------------------------------------------------ 6. falsos positivos do caso 2


CASO2 = (
    "<!-- pagina-origem: 1 -->\n"
    "Procuradoria-Geral da Fazenda Nacional\n"
    "Procuradoria Regional da Fazenda Nacional 9ª Região\n"
    "Núcleo de Investigação Ficticio – PRFN/9R\n"
    "Rua das Acácias Imaginárias, nº 100, Térreo, Centro, Exemplópolis -RS – CEP 99000-900\n"
    "Fone: (99) 3290-4000\n"
    "Nesse sentido, sob a relatoria do Min. Teodoro Fictício Lago, a Turma decidiu.\n"
    "Ressalte-se que a empresa executada não pagou; a empresa executada é devedora.\n"
    "Lavrada pelo Tabelião Substituto Fulano Beltrano Siqueira no 1º Tabelionato de Notas de Exemplópolis.\n"
    "O sócio Rodolfo Inventado Pires, CPF 529.982.247-25, reside na Rua dos Ipês Fictícios, nº 50.\n"
    "<!-- pagina-origem: 2 -->\n"
    "Procuradoria Regional da Fazenda Nacional 9ª Região\n"
    "Rua das Acácias Imaginárias, nº 100, Térreo, Centro, Exemplópolis -RS – CEP 99000-900\n"
    "Fone: (99) 3290-4000\n"
    "A executada TEXTIL FICTICIA LTDA foi citada.\n"
)
CASO2_NOMES = [
    ("Min. Teodoro Fictício Lago", "PERSON"),
    ("empresa executada", "ORGANIZATION"),
    ("Fulano Beltrano Siqueira", "PERSON"),
    ("1º Tabelionato de Notas de Exemplópolis", "ORGANIZATION"),
    ("Rodolfo Inventado Pires", "PERSON"),
    ("TEXTIL FICTICIA LTDA", "ORGANIZATION"),
    ("Rua das Acácias Imaginárias, nº 100", "LOCATION"),
    ("Rua dos Ipês Fictícios, nº 50", "LOCATION"),
]
CASO2_EM_CLARO = [
    "Min. Teodoro Fictício Lago",
    "a empresa executada não pagou",
    "Tabelião Substituto Fulano Beltrano Siqueira",
    "1º Tabelionato de Notas de Exemplópolis",
    "Rua das Acácias Imaginárias, nº 100, Térreo",
    "CEP 99000-900",
    "Fone: (99) 3290-4000",
]


class FalsosPositivosRegraTests(unittest.TestCase):
    def test_expressao_generica(self) -> None:
        for value in ("empresa executada", "a executada", "sociedade empresária", "sócio remanescente",
                      "EMPRESA EXECUTADA", "pessoa jurídica executada nos autos"):
            with self.subTest(value=value):
                self.assertTrue(is_generic_expression(value))
        for value in ("empresa Alfa Ficticia", "TEXTIL FICTICIA LTDA", "Fulano de Tal"):
            with self.subTest(value=value):
                self.assertFalse(is_generic_expression(value))

    def test_contato_do_orgao_e_do_particular(self) -> None:
        text = CASO2
        self.assertTrue(public_organ_contact(text, *_span(text, "Rua das Acácias Imaginárias, nº 100"), "LOCATION"))
        self.assertTrue(public_organ_contact(text, *_span(text, "(99) 3290-4000"), "BR_PHONE"))
        self.assertTrue(public_organ_contact(text, *_span(text, "99000-900", 1), "BR_CEP"))
        self.assertFalse(public_organ_contact(text, *_span(text, "Rua dos Ipês Fictícios, nº 50"), "LOCATION"))
        frase = "Manifestou-se o Procurador da Fazenda Nacional pelo prosseguimento.\nPenhora no imóvel da Rua X, nº 5.\n"
        self.assertFalse(public_organ_contact(frase, *_span(frase, "Rua X, nº 5"), "LOCATION"))

    def test_ministro_com_titulo_no_trecho(self) -> None:
        text = CASO2
        self.assertEqual(misread(text, *_span(text, "Min. Teodoro Fictício Lago"), "PERSON"), "julgador_titulo_no_trecho")


class FalsosPositivosLoteTests(_Lote):
    def _run_job(self, text: str, names, **settings_kw) -> str:
        base, source = self._paths(text)
        settings = AppSettings(local_ner_enabled=False, cnpj_lookup_enabled=False, **settings_kw)
        engine = CombinedEngine([build_contextual_engine(settings, use_ner_cache=False), _Ner(names)])
        job = run_job([source], export_root=base / "e", private_root=base / "p", contextual=engine, settings=settings,
                      use_cache=False)
        return job.outcomes[0].markdown_path.read_text(encoding="utf-8")

    def check(self, body: str) -> None:
        for snippet in CASO2_EM_CLARO:
            self.assertIn(snippet, body)
        self.assertNotIn("Rodolfo", body)
        self.assertNotIn("Ipês", body)
        self.assertNotIn("TEXTIL FICTICIA", body)
        self.assertEqual(body.count("Rua das Acácias Imaginárias"), 2)  # o cabecalho das duas folhas

    def test_motor_local_da_tela(self) -> None:
        # pipeline.py (prepare_file/finish_file): o da tela no modo local e o de tools\\rodar_deteccao.py
        self.check(self._run_job(CASO2, CASO2_NOMES))

    def test_pipeline_unico(self) -> None:
        body, _cast = self.local(CASO2, CASO2_NOMES)
        self.check(body)

    def test_chave_desligada_volta_ao_comportamento_antigo(self) -> None:
        body = self._run_job(CASO2, CASO2_NOMES, case2_false_positives=False, notary_public_agents=False)
        self.assertNotIn("Min. Teodoro Fictício Lago", body)
        self.assertIn("Fone: [TELEFONE_", body)


# ------------------------------------------------------------------------------------------------ AVISOS.md


class AvisosTests(_Lote):
    def test_avisos_e_instrucoes_sem_dado_real(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\nO sócio GERTRUDES VALENTE BRAGANÇA assinou.\n"
            "<!-- pagina-origem: 2 -->\nCarimbo\n<!-- ocr: escaneada -->\n"
            "Os abaixo, GERTRUDESVALENTE BRAGANGA, casada.\n«[OCR incerto, fl. 2: Mlainekcaury]»\n"
            "A socia JOANA SILVEIRA PRRDO retirou-se.\n"
        )
        base, source = self._paths(text)
        settings = AppSettings(
            local_ner_enabled=False, cnpj_lookup_enabled=False,
            target_people=["Gertrudes Valente Bragança", "Joana Silveira Prado", "Joana Silveira Pardo"],
        )
        engine = CombinedEngine([build_contextual_engine(settings, use_ner_cache=False), _Ner([])])
        job = run_job([source], export_root=base / "e", private_root=base / "p", contextual=engine, settings=settings,
                      use_cache=False)
        body = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertNotIn("BRAGANGA", body)
        self.assertIn("JOANA SILVEIRA PRRDO", body)  # ambigua: em claro, nem o pedaco comum (a revisao corrige)
        avisos = (job.export_dir / uncertainty.NOTICES_NAME).read_text(encoding="utf-8")
        self.assertRegex(avisos, r"\| \S+\.md \| 2 \| \[PESSOA_\d+\] \| " + uncertainty.FUZZY_NAME)
        self.assertIn(uncertainty.AMBIGUOUS, avisos)
        self.assertIn("leitura incerta do OCR", avisos)
        for real in ("GERTRUDES", "BRAGAN", "JOANA", "PRRDO", "Mlainekcaury"):
            self.assertNotIn(real, avisos)
        instructions = (job.export_dir / "INSTRUCOES_IA.md").read_text(encoding="utf-8")
        self.assertIn("Trechos incertos", instructions)
        self.assertIn("AVISOS.md", instructions)
        self.assertIn("conferir no PDF original", instructions)


if __name__ == "__main__":
    unittest.main()
