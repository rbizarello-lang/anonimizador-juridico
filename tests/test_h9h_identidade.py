"""H9-h, itens A e B: fusao de identidade no elenco e regra (c) com o veredito pos-juizo do motor local.

Textos e nomes inventados; sem rede, sem modelo.
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


class FusaoNoElencoTests(unittest.TestCase):
    FULL = "Alessandro Luiz de Domenico"

    def test_quatro_grafias_viram_uma_entrada(self) -> None:
        roster = _roster(
            (self.FULL, "socio"), ("ALESSANDRO LUIZ DE", "socio"), ("Alessandro de Domenica", "socio"), ("Alessandro", "socio")
        )
        self.assertEqual(len(roster), 4)
        removed = fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(len(removed), 3)
        self.assertEqual(roster.identity_merges, 3)
        entry = roster.entries[0]
        self.assertEqual(entry.name, self.FULL)
        for value in ("ALESSANDRO LUIZ DE", "Alessandro de Domenica", "Alessandro"):
            self.assertIn(value, entry.variants)
            self.assertIs(roster.lookup("pessoa", value), entry)

    def test_iniciais_sufixo_e_prefixo(self) -> None:
        roster = _roster((self.FULL, "parte"), ("A. L. de Domenico", "parte"), ("Luiz de Domenico", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)

    def test_homonimos_de_sobrenome_nao_se_fundem(self) -> None:
        roster = _roster(
            ("Alessandro Luiz Domenico", "socio"), ("Beatriz Helena Domenico", "socio"), ("Domenico", "socio"),
            ("Alessandro", "socio"),
        )
        fuse_identities(roster)
        # "Domenico" cabe nas duas; "Alessandro" cabe so numa (e funde).
        self.assertEqual(len(roster), 3)
        self.assertIsNot(roster.lookup("pessoa", "Alessandro Luiz Domenico"), roster.lookup("pessoa", "Beatriz Helena Domenico"))
        self.assertIs(roster.lookup("pessoa", "Alessandro"), roster.lookup("pessoa", "Alessandro Luiz Domenico"))
        self.assertIsNot(roster.lookup("pessoa", "Domenico"), roster.lookup("pessoa", "Alessandro Luiz Domenico"))

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

    def test_servidor_manter_e_variantes_herdam_manter(self) -> None:
        roster = _roster(
            ("Rogerio Matos Tavares", "servidor"), ("ROGERIO MATOS", "terceiro"), ("Rogerio Mattos Tavares", "terceiro")
        )
        self.assertEqual(roster.lookup("pessoa", "Rogerio Matos Tavares").action, KEEP)
        self.assertEqual(roster.lookup("pessoa", "ROGERIO MATOS").action, TOKENIZE)
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, KEEP)
        # Uma mencao nova com papel privado nao desfaz o "manter" confirmado.
        roster.add_mention(Mention(kind="pessoa", text="Rogerio Matos", canonical="Rogerio Matos Tavares", role="terceiro"))
        self.assertEqual(roster.entries[0].action, KEEP)

    def test_sem_cargo_publico_confirmado_tokeniza(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "autor_citado"), ("ROGERIO MATOS", "parte"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, TOKENIZE)

    def test_empresa_prefixo_ate_o_sufixo_societario(self) -> None:
        roster = Roster()
        for name in ("Agroflorestal Vale Setentrional Industria Ltda", "AGROFLORESTAL VALE SETENTRIONAL", "Agroflorestal Vale Setentrionel Industria"):
            roster.add_mention(Mention(kind="empresa", text=name, canonical=name, role="parte"), source="llm")
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)

    def test_empresas_diferentes_com_a_mesma_marca_nao_fundem(self) -> None:
        roster = Roster()
        for name in ("Alfa Comercio de Alimentos Ltda", "Alfa Textil Ltda", "Alfa Participacoes SA"):
            roster.add_mention(Mention(kind="empresa", text=name, canonical=name, role="parte"), source="llm")
        fuse_identities(roster)
        self.assertEqual(len(roster), 3)

    def test_entrada_da_lista_de_alvos_e_a_fixada_pelo_usuario_ficam_como_estao(self) -> None:
        roster = _roster((self.FULL, "socio"))
        roster.add_listed("pessoa", "ALESSANDRO LUIZ DE")
        fuse_identities(roster)
        self.assertEqual(len(roster), 2)


class FusaoNoLoteTests(_Pipeline):
    FULL = "Alessandro Luiz de Domenico"

    def test_um_nome_em_quatro_grafias_vira_um_token_e_a_auditoria_conta(self) -> None:
        text = _doc(
            "Consta ALESSANDRO LUIZ DE na lista de presença.",
            "Falou Alessandro de Domenica no ato. O Sr. Alessandro concordou.",
            f"O sócio {self.FULL} foi citado e {self.FULL} apresentou defesa.",
        )
        entities = [
            ("pessoa", self.FULL, "socio"),
            ("pessoa", "ALESSANDRO LUIZ DE", "socio"),
            ("pessoa", "Alessandro de Domenica", "socio"),
            ("pessoa", "Alessandro", "socio"),
        ]
        result, body, audit = self.go(text, FakeDiscoverer(entities))
        tokens = set(re.findall(r"\[PESSOA_\d+\]", body))
        self.assertEqual(len(tokens), 1, body)
        self.assertNotRegex(body, r"(?i)alessandro|domenic")
        self.assertIn("Entradas fundidas por identidade: 3", audit)
        self.assertEqual(len(result.roster), 1)

    def test_dois_homonimos_de_sobrenome_seguem_com_tokens_diferentes(self) -> None:
        text = _doc(
            "O sócio Alessandro Luiz Domenico foi citado e Alessandro Luiz Domenico recorreu.",
            "A sócia Beatriz Helena Domenico foi citada e Beatriz Helena Domenico recorreu.",
        )
        entities = [("pessoa", "Alessandro Luiz Domenico", "socio"), ("pessoa", "Beatriz Helena Domenico", "socio")]
        _result, body, audit = self.go(text, FakeDiscoverer(entities))
        self.assertEqual(len(set(re.findall(r"\[PESSOA_\d+\]", body))), 2)
        self.assertIn("Entradas fundidas por identidade: 0", audit)

    def test_servidor_manter_com_variantes_terceiro_fica_inteiro_em_claro(self) -> None:
        text = _doc(
            "Certificado por Rogerio Matos Tavares, servidor da Vara.",
            "Depois ROGERIO MATOS juntou o termo e Rogerio Mattos Tavares assinou.",
        )
        entities = [
            ("pessoa", "Rogerio Matos Tavares", "servidor"),
            ("pessoa", "ROGERIO MATOS", "terceiro"),
            ("pessoa", "Rogerio Mattos Tavares", "terceiro"),
        ]
        result, body, _audit = self.go(text, FakeDiscoverer(entities))
        self.assertIn("Rogerio Matos Tavares", body)
        self.assertIn("ROGERIO MATOS", body)
        self.assertIn("Rogerio Mattos Tavares", body)
        self.assertEqual(len(result.roster), 1)
        self.assertEqual(result.roster.entries[0].action, KEEP)

class AcaoDaEntradaFundidaTests(unittest.TestCase):
    def test_socio_com_grafia_rotulada_procurador_tokeniza_a_entrada_toda(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "socio"), ("ROGERIO MATOS", "procurador_publico"))
        self.assertEqual(roster.lookup("pessoa", "ROGERIO MATOS").action, KEEP)
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, TOKENIZE)

    def test_servidor_com_variante_terceiro_ou_desconhecido_fica_manter(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "servidor"), ("ROGERIO MATOS", "terceiro"), ("Rogerio Matos", "inventado"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, KEEP)

    def test_juiz_travado_pelo_juizo_do_lote_com_variante_terceiro_fica_manter(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "magistrado"), ("ROGERIO MATOS", "terceiro"))
        self.assertTrue(roster.lock_keep("pessoa:rogerio matos tavares"))
        fuse_identities(roster)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.entries[0].action, KEEP)

    def test_travado_vence_ate_com_papel_privado_em_outro_membro(self) -> None:
        roster = _roster(("Rogerio Matos Tavares", "servidor"), ("ROGERIO MATOS", "socio"))
        self.assertTrue(roster.lock_keep("pessoa:rogerio matos tavares"))
        fuse_identities(roster)
        self.assertEqual(roster.entries[0].action, KEEP)


class FusaoPorPrefixoDeDuasPalavrasTests(unittest.TestCase):
    def test_mae_e_filha_nao_fundem_e_a_recusa_e_contada(self) -> None:
        roster = _roster(("Maria Silva Souza", "parte"), ("Maria Silva", "parte"))
        text = "Maria Silva, mãe de Maria Silva Souza, compareceu. Depois, Maria Silva assinou sozinha."
        fuse_identities(roster, [text])
        self.assertEqual(len(roster), 2)
        self.assertEqual(len(roster.kinship_refused), 1)
        self.assertEqual(len(roster.prefix2_merges), 0)

    def test_parentesco_perto_de_qualquer_ocorrencia_veta_mesmo_em_outra_frase(self) -> None:
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Fagundes", "parte"))
        fuse_identities(roster, ["Joel Fagundes assinou.\n\nJoel Martinez Fagundes, filho do requerente, recorreu."])
        self.assertEqual(len(roster), 2)
        self.assertEqual(len(roster.kinship_refused), 1)

    def test_sem_parentesco_e_com_a_curta_sozinha_funde(self) -> None:
        roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Fagundes", "parte"))
        text = "Joel Martinez Fagundes assinou o contrato. Na outra folha, Joel Fagundes pagou o preço."
        fuse_identities(roster, [text])
        self.assertEqual(len(roster), 1)
        self.assertEqual(len(roster.prefix2_merges), 1)
        self.assertEqual((len(roster.kinship_refused), len(roster.proof_refused)), (0, 0))

    def test_curta_que_so_aparece_dentro_da_longa_ou_na_mesma_frase_nao_funde(self) -> None:
        for text in (
            "Joel Martinez Fagundes assinou o contrato.",
            "Joel Fagundes e Joel Martinez Fagundes assinaram o contrato.",
            "Joel Fagundes assinou. Joel Martinez Fagundes pagou. Joel Fagundes e Joel Martinez Fagundes saíram.",
        ):
            with self.subTest(text=text):
                roster = _roster(("Joel Martinez Fagundes", "parte"), ("Joel Fagundes", "parte"))
                fuse_identities(roster, [text])
                self.assertEqual(len(roster), 2)
                self.assertEqual(len(roster.proof_refused), 1)

    def test_nome_igual_do_llm_funde_sem_exigir_prova(self) -> None:
        # A entrada curta carrega o nome da longa como variante (o nome canonico do LLM): igualdade, nao prefixo.
        longa = RosterEntry(key="pessoa:maria silva souza", kind="pessoa", name="Maria Silva Souza", roles=["parte"])
        curta = RosterEntry(
            key="pessoa:maria silva", kind="pessoa", name="Maria Silva", roles=["parte"], variants=["Maria Silva Souza"]
        )
        roster = Roster([longa, curta])
        fuse_identities(roster, ["Maria Silva, mãe de Maria Silva Souza, compareceu."])
        self.assertEqual(len(roster), 1)
        self.assertEqual((len(roster.kinship_refused), len(roster.proof_refused)), (0, 0))

    def test_cadeia_com_parentesco_nao_colapsa(self) -> None:
        roster = _roster(("Maria Silva Souza", "parte"), ("Maria Silva", "parte"), ("Maria Souza", "parte"))
        text = "Maria Silva, mãe de Maria Silva Souza, e Maria Souza compareceram. Depois Maria Souza saiu."
        fuse_identities(roster, [text])
        self.assertEqual(len(roster), 3)
        self.assertEqual(len(roster.kinship_refused), 2)

    def test_cadeia_sem_parentesco_com_prova_funde_as_duas(self) -> None:
        roster = _roster(("Maria Silva Souza", "parte"), ("Maria Silva", "parte"), ("Maria Souza", "parte"))
        text = "Maria Silva Souza assinou. Em outra folha, Maria Silva pagou. Noutra, Maria Souza recebeu."
        fuse_identities(roster, [text])
        self.assertEqual(len(roster), 1)
        self.assertEqual(len(roster.prefix2_merges), 2)


class SalvaguardaNoLoteTests(_Pipeline):
    def test_mae_e_filha_seguem_com_tokens_diferentes_e_a_auditoria_conta(self) -> None:
        text = _doc(
            "Maria Silva, mãe de Maria Silva Souza, compareceu.",
            "Depois Maria Silva assinou sozinha e Maria Silva Souza saiu.",
        )
        entities = [("pessoa", "Maria Silva Souza", "parte"), ("pessoa", "Maria Silva", "parte")]
        result, body, audit = self.go(text, FakeDiscoverer(entities))
        self.assertEqual(len(result.roster), 2)
        self.assertIn("Fusoes so por prefixo de 2 palavras: 0; recusadas por parentesco: 1; recusadas por falta de prova no texto: 0", audit)


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
