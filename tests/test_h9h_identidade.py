"""H9-h, item A: fusao de identidade no elenco.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_app.roster import KEEP, TOKENIZE, Mention, Roster
from anonimizador_app.roster_identity import fuse_identities

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer


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


if __name__ == "__main__":
    unittest.main()
