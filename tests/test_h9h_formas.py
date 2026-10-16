"""H9-h, item D: passada final de formas conhecidas (ID que o mapa ja tem, em toda ocorrencia do texto).

Textos e numeros inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import unittest

from anonimizador_juridico import EntitySpan

from anonimizador_app.known_forms import KnownForms

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer

CNJ = "5001234-77.2024.8.24.0023"
FILLER = "\n\n".join("Texto de enchimento da peça, sem nomes nem números, só para passar da capa." for _ in range(70))


def _doc(*lines: str) -> str:
    return "<!-- pagina-origem: 1 -->\n\n" + "\n\n".join(lines) + "\n"


class FormasConhecidasNoLoteTests(_Pipeline):
    def test_processo_principal_em_rodape_com_cue_de_precedente_e_trocado(self) -> None:
        text = _doc(
            f"Processo nº {CNJ}. Execução fiscal.",
            FILLER,
            f"Ementa. TRF4, AG 5009999-12.2023.4.04.0000. Rodapé do ato: TRF4 AG {CNJ} - evento 5.",
            f"Documento assinado. Agravo AG {CNJ}.",
        )
        result, body, audit = self.go(text, FakeDiscoverer([]))
        self.assertNotIn(CNJ, body)
        self.assertEqual(len(re.findall(r"\[PROCESSO_1\]", body)), 3)
        self.assertRegex(audit, r"Formas conhecidas do mapa substituidas na passada final: [1-9]")
        # O precedente de outro processo continua em claro: a regra do precedente vale para numero novo.
        self.assertIn("5009999-12.2023.4.04.0000", body)

    def test_nire_depois_de_outro_rotulo_e_trocado_com_o_mesmo_token(self) -> None:
        text = _doc(
            "Certidão simplificada. NIRE 42200123456, sede em Joinville.",
            "Arquivamento: 42200123456 em 5 de março.",
            "Ato 42.2.0012345-6 foi lavrado.",
        )
        _result, body, audit = self.go(text, FakeDiscoverer([]))
        self.assertNotIn("42200123456", body)
        tokens = re.findall(r"\[NIRE_\d+\]", body)
        self.assertEqual(len(tokens), 3)
        self.assertEqual(len(set(tokens)), 1)
        self.assertIn("Arquivamento: [NIRE_1]", body)
        self.assertRegex(audit, r"BR_NIRE 2\)")

    def test_numero_maior_que_contem_o_mesmo_valor_nao_e_trocado(self) -> None:
        text = _doc("NIRE 42200123456.", "Protocolo 9942200123456 do balcão.")
        _result, body, _audit = self.go(text, FakeDiscoverer([]))
        self.assertIn("9942200123456", body)


class KnownFormsUnitTests(unittest.TestCase):
    def _forms(self, **types: str) -> KnownForms:
        forms = KnownForms()
        for entity_type, value in types.items():
            forms.add(entity_type, "", value)
        return forms

    def test_cpf_e_cnpj_com_ou_sem_pontuacao(self) -> None:
        forms = self._forms(BR_CPF="529.982.247-25", BR_CNPJ="11.222.333/0001-81")
        text = "A 52998224725 e 529.982.247-25; B 11222333000181 e 11.222.333/0001-81."
        found = forms.spans(text, [])
        self.assertEqual([text[item.start : item.end] for item in found],
                         ["52998224725", "529.982.247-25", "11222333000181", "11.222.333/0001-81"])
        self.assertEqual({item.canonical_key for item in found}, {"52998224725", "11222333000181"})
        self.assertEqual(forms.counts["BR_CPF"], 2)

    def test_nao_toca_no_que_ja_esta_marcado_nem_em_numero_vizinho(self) -> None:
        forms = self._forms(BR_CPF="529.982.247-25")
        text = "CPF 529.982.247-25 e 529.982.247-25 99."
        taken = [EntitySpan(start=4, end=18, entity_type="BR_CPF", score=0.9, recognizer="X")]
        found = forms.spans(text, taken)
        self.assertEqual([item.start for item in found], [text.rindex("529.982.247-25")])
        self.assertEqual(forms.spans("12345678952998224725", []), [])

    def test_email_e_placa(self) -> None:
        forms = self._forms(EMAIL_ADDRESS="fulano.silva@exemplo.test", BR_PLACA="ABC1D23")
        text = "Escreva a Fulano.Silva@Exemplo.test ou placa abc-1d23 e ABC1D23."
        found = forms.spans(text, [])
        self.assertEqual(len(found), 3)

    def test_nome_e_endereco_nao_entram(self) -> None:
        forms = KnownForms()
        forms.add("PERSON", "fulano de tal", "Fulano de Tal")
        forms.add("LOCATION", "rua x 10", "Rua X, 10")
        self.assertEqual(len(forms), 0)
        self.assertEqual(forms.spans("Fulano de Tal mora na Rua X, 10.", []), [])

    def test_mapa_alimenta_as_formas(self) -> None:
        from anonimizador_juridico import PseudonymMap

        mapping = PseudonymMap()
        mapping.resolve("BR_NIRE", "42200123456")
        forms = KnownForms()
        forms.add_mapping(mapping)
        text = "Arquivamento 42200123456."
        found = forms.spans(text, [])
        self.assertEqual([text[item.start : item.end] for item in found], ["42200123456"])


if __name__ == "__main__":
    unittest.main()
