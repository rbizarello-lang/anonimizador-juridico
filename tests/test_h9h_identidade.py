"""H9-h (fusao restrita), itens A e B: fusao de identidade no elenco e regra (c) com o veredito pos-juizo do motor local.

Politica "na duvida, separado": pessoa e empresa so fundem por forma igual, grafia de OCR com o mesmo numero de palavras
ou nome canonico do LLM. Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico import EntitySpan

from anonimizador_app.pipeline_llm import run_llm_job
from anonimizador_app.roster import KEEP, TOKENIZE, Mention, Roster, RosterEntry
from anonimizador_app.roster_identity import fuse_identities

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer, FakeLocalEngine


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


def _roster(*items: tuple[str, str]) -> Roster:
    roster = Roster()
    for name, role in items:
        roster.add_mention(Mention(kind="pessoa", text=name, canonical=name, role=role), source="llm")
    return roster


def _empresa(*names: str, kind: str = "empresa") -> Roster:
    roster = Roster()
    for name in names:
        roster.add_mention(Mention(kind=kind, text=name, canonical=name, role="parte"), source="llm")
    return roster


def _with_variant(roster: Roster, name: str, variant: str, kind: str = "pessoa") -> None:
    entry = roster.lookup(kind, name)
    assert entry is not None
    entry.variants.append(variant)


class FusaoNoElencoTests(unittest.TestCase):
    FULL = "Alessandro Luiz de Domenico"

    def test_prefixo_sufixo_iniciais_e_nome_curto_nao_fundem(self) -> None:
        roster = _roster(
            (self.FULL, "socio"), ("ALESSANDRO LUIZ DE", "socio"), ("A. L. de Domenico", "socio"),
            ("Luiz de Domenico", "socio"), ("Alessandro de Domenico", "socio"), ("Alessandro", "socio"),
        )
        self.assertEqual(fuse_identities(roster), {})
        self.assertEqual(len(roster), 6)

    def test_nome_do_meio_da_magistrada_nao_funde_com_a_ponta_da_variante_de_uma_parte(self) -> None:
        roster = _roster(("Ana Paula Ferreira Lima", "magistrado"), ("Paula Lima", "parte"), ("Ana Lima", "parte"))
        _with_variant(roster, "Paula Lima", "Paula Ferreira Lima")
        fuse_identities(roster)
        self.assertEqual(len(roster), 3)
        self.assertEqual(roster.lookup("pessoa", "Ana Paula Ferreira Lima").action, KEEP)

    def test_mesmo_prenome_ou_so_o_sobrenome_em_comum_nao_funde(self) -> None:
        roster = _roster(
            ("Alessandro Luiz Domenico", "socio"), ("Alessandro Pedro Domenico", "socio"), ("Alessandro Rocha", "parte")
        )
        fuse_identities(roster)
        self.assertEqual(len(roster), 3)

    def test_adriano_e_adriana_sao_pessoas_diferentes(self) -> None:
        roster = _roster(("Adriano Souza Lima", "socio"), ("Adriana Souza Lima", "socio"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)

    def test_grafia_de_ocr_no_mesmo_nome_de_tres_palavras_funde(self) -> None:
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Martinez Fagundcs", "parte"))
        removed = fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(len(removed), 1)
        self.assertEqual(roster.identity_reasons, {"igual": 0, "ocr": 1, "canonico": 0})
        self.assertIs(roster.lookup("pessoa", "Joel Martinez Fagundcs"), roster.entries[0])

    def test_ocr_com_numero_de_palavras_diferente_nao_funde(self) -> None:
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Fagundcs", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)

    def test_forma_igual_depois_de_normalizar_funde_inclusive_hifen_de_quebra_de_linha(self) -> None:
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Martinez Fagun-\ndes", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.identity_reasons, {"igual": 1, "ocr": 0, "canonico": 0})

    def test_mesmo_nome_canonico_do_llm_funde(self) -> None:
        roster = _roster(("Maria Silva Souza", "parte"), ("Maria Silva", "parte"))
        _with_variant(roster, "Maria Silva", "Maria Silva Souza")
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.identity_reasons, {"igual": 0, "ocr": 0, "canonico": 1})

    def test_servidor_manter_e_variantes_herdam_manter(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "servidor"), ("Rogerio Mattos Tavares", "terceiro"))
        self.assertEqual(roster.lookup("pessoa", "Rogerio Mattos Tavares").action, TOKENIZE)
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, KEEP)
        # Uma mencao nova com papel privado nao desfaz o "manter" confirmado.
        roster.add_mention(Mention(kind="pessoa", text="Rogerio Matos", canonical="Rogerio Matos Tavares", role="terceiro"))
        self.assertEqual(roster.entries[0].action, KEEP)

    def test_sem_cargo_publico_confirmado_tokeniza(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "autor_citado"), ("Rogerio Mattos Tavares", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, TOKENIZE)

    def test_empresas_diferentes_com_a_mesma_marca_nao_fundem(self) -> None:
        roster = _empresa("Alfa Comercio de Alimentos Ltda", "Alfa Textil Ltda", "Alfa Participacoes SA", "Alfa")
        fuse_identities(roster)
        self.assertEqual(len(roster), 4)

    def test_entrada_da_lista_de_alvos_e_a_fixada_pelo_usuario_ficam_como_estao(self) -> None:
        roster = _roster((self.FULL, "socio"), ("Alessandro Luiz de Domenica", "socio"))
        roster.add_listed("pessoa", "Alessandro Luiz de Domenicp")
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)


class FusaoNoLoteTests(_Pipeline):
    FULL = "Alessandro Luiz de Domenico"

    def test_grafia_de_ocr_vira_um_token_e_a_auditoria_conta_o_motivo(self) -> None:
        text = _doc(
            "Consta Alessandro Luiz de Domenica na lista de presença.",
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa.",
        )
        entities = [("pessoa", self.FULL, "socio"), ("pessoa", "Alessandro Luiz de Domenica", "socio")]
        result, body, audit = self.go(text, FakeDiscoverer(entities))
        tokens = set(re.findall(r"\[PESSOA_\d+\]", body))
        self.assertEqual(len(tokens), 1, body)
        self.assertNotRegex(body, r"(?i)alessandro|domenic")
        self.assertIn("Entradas fundidas por identidade: 1 (igual normalizado 0, OCR 1, nome canonico do LLM 0)", audit)
        self.assertEqual(len(result.roster), 1)

    def test_dois_homonimos_de_sobrenome_seguem_com_tokens_diferentes(self) -> None:
        text = _doc(
            "O sócio Alessandro Luiz Domenico foi citado e Alessandro Luiz Domenico recorreu.",
            "A sócia Beatriz Helena Domenico foi citada e Beatriz Helena Domenico recorreu.",
        )
        entities = [("pessoa", "Alessandro Luiz Domenico", "socio"), ("pessoa", "Beatriz Helena Domenico", "socio")]
        _result, body, audit = self.go(text, FakeDiscoverer(entities))
        self.assertEqual(len(set(re.findall(r"\[PESSOA_\d+\]", body))), 2)
        self.assertIn("Entradas fundidas por identidade: 0 (igual normalizado 0, OCR 0, nome canonico do LLM 0)", audit)

    def test_servidor_manter_com_grafia_de_ocr_terceiro_fica_inteiro_em_claro(self) -> None:
        text = _doc(
            "Certificado por Rogerio Matos Tavares, servidor da Vara.",
            "Depois Rogerio Mattos Tavares assinou.",
        )
        entities = [("pessoa", "Rogerio Matos Tavares", "servidor"), ("pessoa", "Rogerio Mattos Tavares", "terceiro")]
        result, body, _audit = self.go(text, FakeDiscoverer(entities))
        self.assertIn("Rogerio Matos Tavares", body)
        self.assertIn("Rogerio Mattos Tavares", body)
        self.assertEqual(len(result.roster), 1)
        self.assertEqual(result.roster.entries[0].action, KEEP)


class AcaoDaEntradaFundidaTests(unittest.TestCase):
    def test_socio_com_grafia_rotulada_procurador_tokeniza_a_entrada_toda(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "socio"), ("Rogerio Mattos Tavares", "procurador_publico"))
        self.assertEqual(roster.lookup("pessoa", "Rogerio Mattos Tavares").action, KEEP)
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, TOKENIZE)

    def test_servidor_com_variante_terceiro_ou_desconhecido_fica_manter(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "servidor"), ("Rogerio Mattos Tavares", "terceiro"), ("Rogerio Matos Tavarez", "inventado"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, KEEP)

    def test_juiz_travado_pelo_juizo_do_lote_com_variante_terceiro_fica_manter(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "magistrado"), ("Rogerio Mattos Tavares", "terceiro"))
        self.assertTrue(roster.lock_keep("pessoa:rogerio matos tavares"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, KEEP)

    def test_travado_vence_ate_com_papel_privado_em_outro_membro(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "servidor"), ("Rogerio Mattos Tavares", "socio"))
        self.assertTrue(roster.lock_keep("pessoa:rogerio matos tavares"))
        fuse_identities(roster)
        self.assertEqual(roster.entries[0].action, KEEP)


class NomesJustapostosTests(unittest.TestCase):
    """Nome colado ao outro numa celula ou num bloco de assinaturas nunca liga duas pessoas."""

    def test_ex_companheira_e_outro_socio_colados_na_celula_nao_fundem(self) -> None:
        cell = "MARIA CLARA SOUZA LIMA PEDRO HENRIQUE COSTA"
        for both in (False, True):
            with self.subTest(variante_nas_duas=both):
                roster = _roster(("Maria Clara Souza Lima", "parte"), ("Pedro Henrique Costa", "socio"))
                _with_variant(roster, "Maria Clara Souza Lima", cell)
                if both:
                    _with_variant(roster, "Pedro Henrique Costa", cell)
                fuse_identities(roster)
                self.assertEqual(len(roster), 2)

    def test_a_mesma_variante_repetida_em_duas_entradas_nao_liga_as_duas(self) -> None:
        roster = _roster(("Helena Mendes", "parte"), ("Clara Prado Mendes", "parte"))
        for name in ("Helena Mendes", "Clara Prado Mendes"):
            _with_variant(roster, name, "Mendes Pereira")
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)

    def test_mae_e_filha_com_o_prenome_da_mae_colado_ao_nome_da_filha_nao_fundem(self) -> None:
        block = "Helena Clara Prado Mendes"
        for both in (False, True):
            with self.subTest(variante_nas_duas=both):
                roster = _roster(("Helena Mendes", "parte"), ("Clara Prado Mendes", "parte"))
                _with_variant(roster, "Clara Prado Mendes", block)
                if both:
                    _with_variant(roster, "Helena Mendes", block)
                fuse_identities(roster)
                self.assertEqual(len(roster), 2)

    def test_forma_que_contem_dois_nomes_do_elenco_nunca_serve_de_ponte(self) -> None:
        roster = _roster(("Helena Prado", "parte"), ("Clara Prado Mendes", "parte"))
        glued = "Helena Prado Clara Prado Mendes"
        _with_variant(roster, "Helena Prado", glued)
        _with_variant(roster, "Clara Prado Mendes", glued)
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)

    def test_variante_com_duas_pessoas_nao_liga_as_duas_entradas(self) -> None:
        for variant in ("Fernanda Lima e Paulo Rocha", "Fernanda Lima/Paulo Rocha", "Fernanda Lima, Paulo Rocha", "FERNANDA LIMA & PAULO ROCHA"):
            with self.subTest(variant=variant):
                roster = _roster(("Fernanda Lima Rocha", "parte"), ("Paulo Rocha", "socio"))
                _with_variant(roster, "Fernanda Lima Rocha", variant)
                _with_variant(roster, "Paulo Rocha", variant)
                fuse_identities(roster)
                self.assertEqual(len(roster), 2)

    def test_palavra_minuscula_nunca_e_variante_de_pessoa(self) -> None:
        roster = _roster(("Joana Souza Fatica", "parte"), ("fatica", "terceiro"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)


class TipoPorSufixoSocietarioTests(unittest.TestCase):
    def test_pessoa_com_ltda_vira_empresa_e_nao_funde_com_pessoa(self) -> None:
        roster = _roster(("Adriana Tavernard", "parte"), ("Adriana Tavernard Agroflorestal Ltda", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)
        kinds = {entry.name: entry.kind for entry in roster.entries}
        self.assertEqual(kinds["Adriana Tavernard"], "pessoa")
        self.assertEqual(kinds["Adriana Tavernard Agroflorestal Ltda"], "empresa")
        self.assertEqual(roster.lookup("empresa", "Adriana Tavernard Agroflorestal Ltda").entity_type, "ORGANIZATION")

    def test_palavra_de_razao_social_sem_sufixo_tambem_e_empresa(self) -> None:
        roster = _roster(("Santos Agroflorestal", "parte"), ("Rui Santos", "parte"))
        fuse_identities(roster)
        self.assertEqual({entry.name: entry.kind for entry in roster.entries}["Santos Agroflorestal"], "empresa")

    def test_na_fusao_o_tipo_da_entrada_com_sufixo_prevalece(self) -> None:
        roster = _roster(("Santos Comercio de Graos", "parte"))
        roster.add_mention(Mention(kind="empresa", text="Santos Comercio de Graos Ltda", canonical="Santos Comercio de Graos Ltda", role="parte"))
        fuse_identities(roster)
        self.assertEqual([entry.kind for entry in roster.entries], ["empresa"])


class PessoaEEmpresaNuncaFundemTests(unittest.TestCase):
    def test_pessoa_e_firma_individual_me_do_mesmo_nome_nao_fundem(self) -> None:
        roster = _roster(("Joana Prado", "parte"), ("Joana Prado ME", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)
        self.assertEqual({entry.name: entry.kind for entry in roster.entries}, {"Joana Prado": "pessoa", "Joana Prado ME": "empresa"})

    def test_mesma_forma_curta_como_pessoa_e_como_empresa_vira_uma_entrada_empresa(self) -> None:
        roster = _roster(("Vale Verde Gestao", "parte"))
        roster.add_mention(Mention(kind="empresa", text="Vale Verde Gestao", canonical="Vale Verde Gestao", role="parte"))
        self.assertEqual(len(roster), 2)
        removed = fuse_identities(roster)
        self.assertEqual(len(removed), 1)
        self.assertEqual([(entry.kind, entry.name) for entry in roster.entries], [("empresa", "Vale Verde Gestao")])
        self.assertEqual(roster.lookup("empresa", "Vale Verde Gestao").entity_type, "ORGANIZATION")
        self.assertIsNone(roster.lookup("pessoa", "Vale Verde Gestao"))

    def test_forma_curta_da_pessoa_nao_absorve_a_razao_social_inteira(self) -> None:
        roster = _roster(("Vale Verde", "parte"))
        roster.add_mention(Mention(kind="empresa", text="Vale Verde Gestao Ltda", canonical="Vale Verde Gestao Ltda", role="parte"))
        fuse_identities(roster)
        self.assertEqual(sorted((entry.kind, entry.name) for entry in roster.entries), [("empresa", "Vale Verde Gestao Ltda"), ("pessoa", "Vale Verde")])


class EmpresaDividida(unittest.TestCase):
    NAME = "Eurovidros Importação e Exportação Ltda"

    def test_abreviaturas_acento_e_ocr_sao_a_mesma_razao_social(self) -> None:
        roster = _empresa(self.NAME, "EUROVIDROS IMP. E EXP.", "Eurovidros Importacao e Exportacao", "Eurovidros lmportacao e Exportacao")
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)

    def test_caixa_alta_com_acento_e_abreviada_com_ponto_sao_a_mesma(self) -> None:
        roster = _empresa("LIMA IMPORTAÇÃO E EXPORTAÇÃO LTDA", "Lima Imp. e Exp. Ltda")
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.identity_reasons["igual"], 1)

    def test_razoes_sociais_diferentes_com_a_mesma_marca_seguem_separadas(self) -> None:
        roster = _empresa(self.NAME, "Eurovidros Comercio de Vidros Ltda", "Eurovidros")
        fuse_identities(roster)
        self.assertEqual(len(roster), 3)

    def test_siglas_diferentes_com_o_resto_igual_nao_fundem(self) -> None:
        roster = _empresa("Alfa Importadora Ltda", "Beta Importadora Ltda", "Alfa Importadora Ltda.", "BETA IMPORTADORA LTDA")
        fuse_identities(roster)
        self.assertEqual(sorted(entry.name for entry in roster.entries), ["Alfa Importadora Ltda", "Beta Importadora Ltda"])

    def test_forma_com_barra_ou_e_entre_duas_razoes_sociais_nao_funde(self) -> None:
        for variant in ("Alfa Ltda/Beta Ltda", "Alfa Ltda e Beta Ltda", "Alfa Ltda & Beta Ltda"):
            with self.subTest(variant=variant):
                roster = _empresa("Alfa Ltda", "Beta Ltda")
                _with_variant(roster, "Alfa Ltda", variant, "empresa")
                _with_variant(roster, "Beta Ltda", variant, "empresa")
                fuse_identities(roster)
                self.assertEqual(len(roster), 2)

    def test_sufixo_s_barra_a_nao_e_ponte(self) -> None:
        roster = _empresa("Gama Alimentos S/A", "GAMA ALIMENTOS S.A.")
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)

    def test_forma_que_contem_duas_razoes_sociais_do_elenco_nao_funde(self) -> None:
        roster = _empresa("Alfa Importadora Ltda", "Beta Exportadora Ltda")
        glued = "Alfa Importadora Beta Exportadora"
        _with_variant(roster, "Alfa Importadora Ltda", glued, "empresa")
        _with_variant(roster, "Beta Exportadora Ltda", glued, "empresa")
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)


class NaoTransitivaTests(unittest.TestCase):
    def test_grupo_nao_herda_a_forma_do_outro_para_casar_com_um_terceiro(self) -> None:
        # X e Y se fundem (nome canonico). Z e grafia de OCR de X, mas nao de Y: fica de fora.
        roster = _roster(("Ana Luz Fagundes", "parte"), ("Ana Luz Fagundes Melo", "parte"), ("Ana Luz Fagundcs", "parte"))
        _with_variant(roster, "Ana Luz Fagundes", "Ana Luz Fagundes Melo")
        fuse_identities(roster)
        self.assertEqual(sorted(entry.name for entry in roster.entries), ["Ana Luz Fagundcs", "Ana Luz Fagundes Melo"])
        self.assertEqual(roster.identity_merges, 1)

    def test_corrente_de_grafias_de_ocr_fica_toda_separada(self) -> None:
        # A~B e B~C, mas A distinto de C: nenhuma se junta (na duvida, separado).
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Martinez Fagundcs", "parte"), ("Joel Martinez Fagundcz", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 3)

    def test_tres_grafias_de_ocr_que_casam_entre_si_viram_uma(self) -> None:
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Martinez Fagundas", "parte"), ("Joel Martinez Fagundcs", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.identity_reasons["ocr"], 2)


class SelectiveLocalEngine(FakeLocalEngine):
    """Motor local que so acha o nome nos textos que contem a marca; os outros ele nao detecta (o juizo do lote
    dele, porem, le todos os textos em que rodou)."""

    def __init__(self, names, marker: str) -> None:
        super().__init__(names)
        self.marker = marker

    def detect(self, text: str) -> list[EntitySpan]:
        return super().detect(text) if self.marker in text else []


class VeredictoPosJuizoTests(unittest.TestCase):
    NAME = "ZULEIDE ANTUNES FICTICIA"

    def _run(self, texts: list[str], *, engine, discoverer):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        sources = []
        for number, text in enumerate(texts, start=1):
            source = base / f"autos{number}.md"
            source.write_text(text, encoding="utf-8")
            sources.append(source)
        result = run_llm_job(
            sources, export_root=base / "exportaveis", private_root=base / "privados", discoverer=discoverer,
            use_cache=False, local_floor=True, verification_rounds=0, fallback_engine=engine,
        )
        bodies = [outcome.markdown_path.read_text(encoding="utf-8") for outcome in result.job.outcomes]
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        return result, bodies, audit

    def test_nome_que_o_juizo_local_mantem_nao_e_negado_mesmo_com_papel_terceiro(self) -> None:
        # O motor local acha o nome so no arquivo 1 (sem cargo); o cargo esta no arquivo 2, onde ele nao detecta.
        # O juizo do lote dele le os dois textos: o nome e de servidor, e fica em claro.
        first = _doc(f"Consta na lista de presença AUDIENCIA {self.NAME} e outros.")
        second = _doc(f"{self.NAME}, Analista Judiciário, certificou o ato.")
        engine = SelectiveLocalEngine([("PERSON", self.NAME)], "AUDIENCIA")
        discoverer = FakeDiscoverer([("pessoa", self.NAME, "terceiro")])
        result, bodies, audit = self._run([first, second], engine=engine, discoverer=discoverer)
        self.assertEqual(result.roster.lookup("pessoa", self.NAME).action, KEEP)
        self.assertIn(self.NAME, bodies[1])
        self.assertIn("manter negado por o motor local tambem a ter achado e trocar 0", audit)

    def test_nome_que_o_motor_local_troca_continua_com_o_manter_negado(self) -> None:
        first = _doc(f"Consta na lista de presença AUDIENCIA {self.NAME} e outros.")
        second = _doc(f"{self.NAME}, Analista Judiciário, certificou o ato.")
        # Aqui o motor local acha o nome nos dois arquivos: o juizo dele o mantem (cargo), entao nao ha negacao.
        engine = FakeLocalEngine([("PERSON", self.NAME)])
        discoverer = FakeDiscoverer([("pessoa", self.NAME, "terceiro")])
        result, bodies, _audit = self._run([first, second], engine=engine, discoverer=discoverer)
        self.assertEqual(result.roster.lookup("pessoa", self.NAME).action, KEEP)

    def test_nome_particular_que_o_motor_local_troca_nao_vira_manter(self) -> None:
        text = _doc(f"O sócio {self.NAME} foi citado em 5 de março.")
        engine = FakeLocalEngine([("PERSON", self.NAME)])
        discoverer = FakeDiscoverer([("pessoa", self.NAME, "socio")])
        result, bodies, audit = self._run([text], engine=engine, discoverer=discoverer)
        self.assertEqual(result.roster.lookup("pessoa", self.NAME).action, TOKENIZE)
        self.assertNotIn(self.NAME, bodies[0])


if __name__ == "__main__":
    unittest.main()
