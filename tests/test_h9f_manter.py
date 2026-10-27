"""H9-f, item A: "manter" indevido nas entradas do LLM.

O juizo do lote estendido as entradas do LLM (H9-c) mantinha em claro as duas partes centrais do processo
porque o socio assina contrato digitalmente. Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import unittest

from anonimizador_juridico import EntitySpan

from anonimizador_app.batch_propagation import BatchRoster, _public_role
from anonimizador_app.pipeline_llm import _local_engine_tokenizes
from anonimizador_app.roster import KEEP, TOKENIZE, RosterEntry

from test_llm_hygiene import _Pipeline
from test_pipeline_llm import FakeDiscoverer


class PistaDeAssinaturaTests(unittest.TestCase):
    def test_assinado_por_so_conta_com_cargo_publico_depois_do_nome(self) -> None:
        sem_cargo = "Documento assinado digitalmente por FULANO DE TAL, sócio-administrador."
        start = sem_cargo.index("FULANO")
        end = start + len("FULANO DE TAL")
        self.assertFalse(_public_role(sem_cargo, start, end, strict=True))
        self.assertTrue(_public_role(sem_cargo, start, end, strict=False))  # o NER local segue como antes

    def test_cargo_publico_colado_ao_nome_mantem(self) -> None:
        for cargo in (
            "Juíza Federal", "Desembargador Federal", "Procurador da Fazenda Nacional", "Procurador Federal",
            "Oficial de Justiça", "Diretor de Secretaria", "Analista Judiciário", "Técnico Judiciário", "Servidor",
        ):
            with self.subTest(cargo=cargo):
                text = f"Documento eletrônico assinado por BELTRANA SICRANA, {cargo}, na forma da lei."
                start = text.index("BELTRANA")
                self.assertTrue(_public_role(text, start, start + len("BELTRANA SICRANA"), strict=True))

    def test_procurador_sozinho_e_procuracao_nao_sao_cargo_publico(self) -> None:
        for text in (
            "FULANO DE TAL, procurador da executada, assinou.",
            "Outorgada procuração a FULANO DE TAL pela empresa.",
            "O procurador da empresa FULANO DE TAL requereu.",
            "Procurador FULANO DE TAL requereu.",
        ):
            with self.subTest(text=text):
                start = text.index("FULANO")
                self.assertFalse(_public_role(text, start, start + len("FULANO DE TAL"), strict=True))

    def test_cargo_antes_do_nome_continua_valendo(self) -> None:
        text = "Procurador da Fazenda Nacional SICRANO DE TAL requereu."
        start = text.index("SICRANO")
        self.assertTrue(_public_role(text, start, start + len("SICRANO DE TAL"), strict=True))


class JuizoDasEntradasDoLlmTests(_Pipeline):
    def test_socio_que_assina_contrato_digitalmente_continua_trocado(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Documento assinado digitalmente por FULANO DE TAL FICTICIO, sócio-administrador, brasileiro.\n"
        )
        result, body, audit = self.go(text, FakeDiscoverer([("pessoa", "FULANO DE TAL FICTICIO", "parte")]))
        self.assertNotIn("FULANO", body)
        self.assertEqual(result.roster.lookup("pessoa", "FULANO DE TAL FICTICIO").action, TOKENIZE)

    def test_juiza_que_assina_com_cargo_continua_em_claro(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Documento eletrônico assinado por BELTRANA SICRANA FICTICIA, Juíza Federal.\n"
        )
        result, body, _audit = self.go(text, FakeDiscoverer([("pessoa", "BELTRANA SICRANA FICTICIA", "parte")]))
        self.assertIn("BELTRANA SICRANA FICTICIA", body)
        self.assertEqual(result.roster.lookup("pessoa", "BELTRANA SICRANA FICTICIA").action, KEEP)

    def test_procurador_da_executada_e_trocado_e_o_da_fazenda_fica(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Pela executada, FULANO DE TAL FICTICIO, procurador da executada, requereu vista.\n\n"
            "Procurador da Fazenda Nacional SICRANO DE QUALQUER FICTICIO requereu a penhora.\n"
        )
        entities = [
            ("pessoa", "FULANO DE TAL FICTICIO", "terceiro"),
            ("pessoa", "SICRANO DE QUALQUER FICTICIO", "terceiro"),
        ]
        _result, body, _audit = self.go(text, FakeDiscoverer(entities))
        self.assertNotIn("FULANO DE TAL FICTICIO", body)
        self.assertIn("SICRANO DE QUALQUER FICTICIO", body)

    def test_parte_com_cpf_nunca_vira_manter_mesmo_com_trecho_isolado_de_procurador(self) -> None:
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "Procurador da Fazenda Nacional ROBERTO MARTINEZ FICTICIO requereu vista.\n\n"
            "ROBERTO MARTINEZ FICTICIO, brasileiro, casado, sócio, CPF 529.982.247-25, residente em Itajaí.\n"
        )
        result, body, audit = self.go(text, FakeDiscoverer([("pessoa", "ROBERTO MARTINEZ FICTICIO", "socio")]))
        # v0.4.11 (decisao do Operador: homonimo agente publico x parte decide-se por ocorrencia): a parte qualificada
        # segue trocada; a ocorrencia com o cargo colado ("Procurador da Fazenda Nacional X") fica em claro.
        self.assertIn("[PESSOA_1], brasileiro, casado", body)
        self.assertIn("Procurador da Fazenda Nacional ROBERTO MARTINEZ FICTICIO requereu", body)
        self.assertEqual(result.roster.lookup("pessoa", "ROBERTO MARTINEZ FICTICIO").action, TOKENIZE)
        # v0.4.11: o juizo do lote ja nao marca o nome como agente publico (prova de particular em outra ocorrencia).
        self.assertRegex(audit, r"por papel so privado com CPF ou qualificacao 0")

    def test_socia_da_frase_vizinha_nao_qualifica_a_juiza(self) -> None:
        # A janela de evidencia fica na frase: a "socia" da frase anterior nao protege a magistrada.
        text = (
            "<!-- pagina-origem: 1 -->\n\n"
            "A sócia MARLENE OLIVEIRA SAMPAIO foi citada. Documento eletrônico assinado por "
            "LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.\n"
        )
        entities = [
            ("pessoa", "MARLENE OLIVEIRA SAMPAIO", "socio"),
            ("pessoa", "LUCIANA MONTEFUSCO DE ALMEIDA", "parte"),
        ]
        _result, body, _audit = self.go(text, FakeDiscoverer(entities))
        self.assertIn("LUCIANA MONTEFUSCO DE ALMEIDA", body)
        self.assertNotIn("MARLENE", body)


class MotorLocalNaoPerdeProtecaoTests(unittest.TestCase):
    def test_entrada_que_o_motor_local_tambem_achou_e_vai_trocar_nao_recebe_manter(self) -> None:
        text = "O executado ALBERTO NUNES FICTICIO, brasileiro, foi citado. ALBERTO NUNES FICTICIO pagou."
        judge = BatchRoster()
        span = EntitySpan(
            start=text.index("ALBERTO"), end=text.index("ALBERTO") + len("ALBERTO NUNES FICTICIO"),
            entity_type="PERSON", score=0.9, recognizer="NerLocal",
        )
        judge.add(text, [span])
        judge.review([text])
        entities = {entity.key: entity for entity in judge.entities()}
        entry = RosterEntry(key="pessoa:alberto nunes ficticio", kind="pessoa", name="ALBERTO NUNES FICTICIO")
        self.assertTrue(_local_engine_tokenizes(entry, judge, entities))
        outro = RosterEntry(key="pessoa:fulano", kind="pessoa", name="FULANO SEM LOCAL")
        self.assertFalse(_local_engine_tokenizes(outro, judge, entities))
        self.assertFalse(_local_engine_tokenizes(entry, None, {}))
