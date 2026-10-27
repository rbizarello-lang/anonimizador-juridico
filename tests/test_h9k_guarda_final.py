"""H9-k: guarda final antes da troca (lote de 19/10/2026). Nomes e valores INVENTADOS."""

from __future__ import annotations

import unittest
from collections import Counter
from types import SimpleNamespace

from anonimizador_juridico import EntitySpan
from anonimizador_app.final_guard import FinalGuard, audit_lines
from anonimizador_app.fidelity import is_institutional_contact, is_precedent_judge


def _entry(key, name, *, kind="pessoa", source="llm", variants=(), action="tokenizar"):
    return SimpleNamespace(key=key, kind=kind, name=name, variants=list(variants), action=action, source=source)


def _span(text, needle, key, entity_type="PERSON", occurrence=0):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(needle, start + 1)
    return EntitySpan(
        start=start, end=start + len(needle), entity_type=entity_type, score=0.9, recognizer="Elenco",
        canonical_key=key, alvo=needle,
    )


SOCIO_A = _entry("pessoa:a", "Bartolomeu Quirino Valadares", source="lista")
SOCIO_B = _entry("pessoa:b", "Teodoro de Albuquerque Valadares dos Reis", source="lista")
TERCEIRO = _entry("pessoa:c", "Osvaldo Valadares dos Reis")
EMPRESA = _entry("empresa:x", "Grupo Econômico", kind="empresa", source="motor_local")


class GuardaFinalTest(unittest.TestCase):
    def _apply(self, text, spans, entries=(SOCIO_A, SOCIO_B, TERCEIRO, EMPRESA)):
        counts = Counter()
        kept = FinalGuard(list(entries)).apply(text, spans, counts)
        return kept, counts

    def test_termo_juridico_sai(self):
        text = "Ha fortes indicios de GRUPO ECONÔMICO de fato entre as empresas."
        kept, counts = self._apply(text, [_span(text, "GRUPO ECONÔMICO", "empresa:x", "ORGANIZATION")])
        self.assertEqual(kept, [])
        self.assertEqual(counts["termo_juridico"], 1)

    def test_papel_sai_e_nome_fica(self):
        text = "o sócio gerente Bartolomeu Quirino Valadares assinou"
        spans = [_span(text, "sócio gerente", "pessoa:z"), _span(text, "Bartolomeu Quirino Valadares", "pessoa:a")]
        kept, _ = self._apply(text, spans)
        self.assertEqual([text[s.start : s.end] for s in kept], ["Bartolomeu Quirino Valadares"])

    def test_minuscula_sai_tambem_em_empresa(self):
        text = "para formar o convencimento e firmar o termo"
        spans = [_span(text, "formar", "empresa:y", "ORGANIZATION"), _span(text, "firmar", "empresa:y", "ORGANIZATION")]
        kept, counts = self._apply(text, spans)
        self.assertEqual(kept, [])
        self.assertEqual(counts["minuscula"], 2)

    def test_palavra_de_cidade_sai_inclusive_colada_pelo_ocr(self):
        text = "com sede em São Paulo-SP e filial emSão Paulo/SP"
        spans = [_span(text, "Paulo", "pessoa:p"), _span(text, "Paulo", "pessoa:p", occurrence=1)]
        kept, counts = self._apply(text, spans)
        self.assertEqual(kept, [])
        self.assertEqual(counts["lugar"], 2)

    def test_sobrenome_que_e_cidade_fica(self):
        text = "o Sr. Toledo compareceu"
        kept, _ = self._apply(text, [_span(text, "Toledo", "pessoa:a")])
        self.assertEqual(len(kept), 1)

    def test_prenome_e_sobrenome_sozinhos_da_lista_seguem_trocados(self):
        # Decisao do Operador (19/10/2026): o prenome da lista nunca fica em claro.
        text = "Bartolomeu disse; Valadares respondeu"
        spans = [_span(text, "Bartolomeu", "pessoa:a"), _span(text, "Valadares", "pessoa:a")]
        kept, counts = self._apply(text, spans)
        self.assertEqual([text[s.start : s.end] for s in kept], ["Bartolomeu", "Valadares"])
        self.assertEqual(sum(counts.values()), 0)

    def test_agente_publico_pelo_cargo(self):
        text = (
            "Usuário: ABC99 - Clotilde Ferraz Mendonça - ESTAGIÁRIO • Processo\n"
            "Usuário: XYZ12 - Heitor Saldanha Brito - ADVOGADO • Processo\n"
            "Rosalina Prates Caldas\nProcuradora da Fazenda Nacional\n"
            "Ivone Matos Lacerda - Escrevente\n"
        )
        spans = [
            _span(text, "Clotilde Ferraz Mendonça", "pessoa:e1"),
            _span(text, "Heitor Saldanha Brito", "pessoa:e2"),
            _span(text, "Rosalina Prates Caldas", "pessoa:e3"),
            _span(text, "Ivone Matos Lacerda", "pessoa:e4"),
        ]
        kept, counts = self._apply(text, spans)
        self.assertEqual([text[s.start : s.end] for s in kept], ["Heitor Saldanha Brito"])
        self.assertEqual(counts["agente_publico"], 3)

    def test_secretario_geral_da_junta_mesmo_tipado_empresa(self):
        text = "Protocolo: 00/000000-0\nLauro Peixoto Damasceno\nP/ SECRETARIA GERAL \"AD HOC\""
        kept, _ = self._apply(text, [_span(text, "Lauro Peixoto Damasceno", "empresa:j", "ORGANIZATION")])
        self.assertEqual(kept, [])

    def test_banco_e_sistema_protegido_ficam_em_claro_empresa_privada_nao(self):
        text = "BCO EXEMPLO S.A | Pagadora Fictícia Ltda | OURIBANK S.A | Assinador SERPRO | União Comercial de Grãos Ltda"
        spans = [
            _span(text, "BCO EXEMPLO S.A", "empresa:1", "ORGANIZATION"),
            _span(text, "Pagadora Fictícia Ltda", "empresa:2", "ORGANIZATION"),
            _span(text, "OURIBANK S.A", "empresa:3", "ORGANIZATION"),
            _span(text, "Assinador SERPRO", "empresa:4", "ORGANIZATION"),
            _span(text, "União Comercial de Grãos Ltda", "empresa:5", "ORGANIZATION"),
        ]
        kept, _ = self._apply(text, spans)
        self.assertEqual(
            [text[s.start : s.end] for s in kept], ["Pagadora Fictícia Ltda", "União Comercial de Grãos Ltda"]
        )

    def test_endereco_que_nao_e_endereco(self):
        text = "conforme r. decisão proferida | R.4 | Camboriú/SC | Quadra H 15 | Rua das Acácias, 10"
        spans = [
            _span(text, "r. decisão proferida", "end:1", "LOCATION"),
            _span(text, "R.4", "end:2", "LOCATION"),
            _span(text, "Camboriú/SC", "end:3", "LOCATION"),
            _span(text, "Quadra H 15", "end:4", "LOCATION"),
            _span(text, "Rua das Acácias, 10", "end:5", "LOCATION"),
        ]
        kept, _ = self._apply(text, spans)
        self.assertEqual([text[s.start : s.end] for s in kept], ["Rua das Acácias, 10"])

    def test_cauda_do_nome_de_outra_pessoa_volta_para_ela(self):
        # O OCR perdeu o comeco do nome do socio B; o pedaco veio com a chave do socio A.
        text = "oss o o s\nVALADARES DOS REIS, ja qualificado anteriormente"
        kept, counts = self._apply(text, [_span(text, "VALADARES DOS REIS", "pessoa:a")])
        # Duas candidatas (B, da lista, e C): vence a da lista.
        self.assertEqual(kept[0].canonical_key, "pessoa:b")
        self.assertEqual(counts["cauda_de_outra_pessoa"], 1)

    def test_cauda_ambigua_sem_lista_fica_em_token_proprio(self):
        entries = (
            _entry("pessoa:a", "Bartolomeu Quirino Valadares"),
            _entry("pessoa:b", "Teodoro Valadares dos Reis"),
            _entry("pessoa:c", "Osvaldo Valadares dos Reis"),
        )
        text = "VALADARES DOS REIS assinou"
        kept, _ = self._apply(text, [_span(text, "VALADARES DOS REIS", "pessoa:a")], entries)
        self.assertEqual(kept[0].canonical_key, "pessoa:valadares reis")

    def test_nome_da_propria_entrada_nao_muda(self):
        text = "BARTOLOMEU QUIRINO VALADARES e ANTONlO QUIRINO VALADARES"
        kept, counts = self._apply(text, [_span(text, "BARTOLOMEU QUIRINO VALADARES", "pessoa:a")])
        self.assertEqual(kept[0].canonical_key, "pessoa:a")
        self.assertEqual(sum(counts.values()), 0)

    def test_grafia_colada_ou_com_lixo_de_ocr_vai_para_a_lista(self):
        entries = (*[SOCIO_A, SOCIO_B], _entry("pessoa:o1", "Teodorodealbuquerque Valadares dos Reis"),
                   _entry("pessoa:o2", "Brteodoro de Albuquerque Valadares dos Reis"))
        text = "TEODORODEALBUQUERQUE VALADARES DOS REIS e BRTEODORO DE ALBUQUERQUE VALADARES DOS REIS"
        spans = [
            _span(text, "TEODORODEALBUQUERQUE VALADARES DOS REIS", "pessoa:o1"),
            _span(text, "BRTEODORO DE ALBUQUERQUE VALADARES DOS REIS", "pessoa:o2"),
        ]
        kept, counts = self._apply(text, spans, entries)
        self.assertEqual([s.canonical_key for s in kept], ["pessoa:b", "pessoa:b"])
        self.assertEqual(counts["grafia_colada_da_lista"], 2)

    def test_parente_com_prenome_diferente_nunca_vai_para_a_lista(self):
        entries = (SOCIO_B, _entry("pessoa:irmao", "Teodora de Albuquerque Valadares dos Reis"))
        text = "TEODORA DE ALBUQUERQUE VALADARES DOS REIS"
        kept, _ = self._apply(text, [_span(text, text, "pessoa:irmao")], entries)
        self.assertEqual(kept[0].canonical_key, "pessoa:irmao")

    def test_linha_da_auditoria(self):
        line = audit_lines(Counter({"minuscula": 2, "lugar": 1}))[0]
        self.assertIn("3 span(s)", line)
        self.assertIn("(nao e pessoa nem empresa) 2", line)


class ContatoDeServentiaTest(unittest.TestCase):
    def test_cabecalho_de_cartorio_em_duas_linhas(self):
        text = "REGISTRO DE IMÓVEIS DA 9ª CIRCUNSCRIÇÃO\nRua Fictícia, 100 - Fone (41) 3000-0000\n"
        start = text.index("Rua")
        self.assertTrue(is_institutional_contact(text, start, start + 16, "LOCATION"))

    def test_email_de_serventia(self):
        text = "e-mail: atendimento@cartoriodeexemplo.com.br"
        start = text.index("atendimento")
        self.assertTrue(is_institutional_contact(text, start, len(text), "EMAIL_ADDRESS"))

    def test_endereco_de_particular_perto_de_cartorio_segue_trocado(self):
        text = "matrícula do Cartório de Registro de Imóveis, residente na Rua Fictícia, 100"
        start = text.index("Rua")
        self.assertFalse(is_institutional_contact(text, start, start + 16, "LOCATION"))

    def test_email_comum_segue_trocado(self):
        text = "e-mail: fulano@exemplo.com.br"
        start = text.index("fulano")
        self.assertFalse(is_institutional_contact(text, start, len(text), "EMAIL_ADDRESS"))


class JulgadorDeEmentaTest(unittest.TestCase):
    def test_titulo_com_espaco_e_nome_do_meio(self):
        text = "TRF-3, ApCiv 0000000-00.2016.4.03.0000, j. 14.07.2023, Rel. Des. Fed . Lucio Amaral Fictício, Sexta Turma"
        start = text.index("Amaral")
        self.assertTrue(is_precedent_judge(text, start, start + len("Amaral Fictício")))

    def test_nome_sem_titulo_nao_e_julgador(self):
        text = "TRF-3, Sexta Turma, ApCiv, o executado Lucio Amaral Fictício"
        start = text.index("Amaral")
        self.assertFalse(is_precedent_judge(text, start, start + len("Amaral Fictício")))


if __name__ == "__main__":
    unittest.main()
