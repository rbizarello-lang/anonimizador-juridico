from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico import EntitySpan, PseudonymMap, pseudonymize
from anonimizador_app.batch_propagation import RECOGNIZER, BatchRoster
from anonimizador_app.pipeline import run_job


def _span(text: str, value: str, entity_type: str, *, occurrence: int = 0, canonical: str = "") -> EntitySpan:
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(value, start + 1)
    return EntitySpan(
        start=start,
        end=start + len(value),
        entity_type=entity_type,
        score=0.9,
        recognizer="NerLocal",
        canonical_key=canonical,
    )


class RosterGroupingTests(unittest.TestCase):
    def _roster(self, *pairs: tuple[str, str]) -> BatchRoster:
        roster = BatchRoster()
        for value, entity_type in pairs:
            roster.add(value, [EntitySpan(0, len(value), entity_type, 0.9, "NerLocal")])
        return roster

    def test_person_variants_share_one_entity(self) -> None:
        # A grafia de OCR ("TAVARFS") e mais longa que nada, mas e rara: nao vira a cabeca.
        roster = self._roster(
            ("FULANO BELTRANO DE TAVARES", "PERSON"),
            ("Fulano Beltrano de Tavares", "PERSON"),
            ("FULANO BELTRANO DE TAVARESS", "PERSON"),
            ("Fulano de Tavares", "PERSON"),
            ("Fulano Tavarfs", "PERSON"),
        )
        names = ("Fulano de Tavares", "Fulano Tavarfs", "FULANO BELTRANO DE TAVARESS")
        self.assertEqual({roster.key_for("PERSON", name) for name in names}, {"fulano beltrano de tavares"})
        self.assertEqual(len(roster.entities()), 1)

    def test_company_short_suffix_and_typo_forms_share_one_entity(self) -> None:
        roster = self._roster(
            ("EMPRESA FICTICIA SERVICOS LTDA", "ORGANIZATION"),
            ("Empresa Ficticia Servicos", "ORGANIZATION"),
            ("EMPRESA FICTICIA SERVICSO LTDA.", "ORGANIZATION"),
            ("EMPRESA FICTICIA", "ORGANIZATION"),
        )
        keys = {
            roster.key_for("ORGANIZATION", name)
            for name in ("Empresa Ficticia Servicos", "EMPRESA FICTICIA SERVICSO LTDA.", "EMPRESA FICTICIA")
        }
        self.assertEqual(len(keys), 1)
        self.assertEqual(len(roster.entities()), 1)

    def test_ambiguous_person_prefix_stays_apart(self) -> None:
        roster = self._roster(
            ("JOSE CARLOS SOUZA FICTICIO", "PERSON"),
            ("JOSE CARLOS PEREIRA FICTICIO", "PERSON"),
            ("Jose Carlos", "PERSON"),
        )
        self.assertEqual(len(roster.entities()), 3)
        self.assertEqual(roster.key_for("PERSON", "Jose Carlos"), "jose carlos")


class RosterApplyTests(unittest.TestCase):
    def test_line_split_mention_gets_the_same_token(self) -> None:
        first = "O socio FULANO BELTRANO DE TAL assinou o contrato."
        second = "Assinatura do socio:\nFULANO BELTRANO\nDE TAL, em nome proprio."
        roster = BatchRoster()
        detected = [_span(first, "FULANO BELTRANO DE TAL", "PERSON")]
        roster.add(first, detected)
        roster.review([first, second])
        mapping = PseudonymMap()
        out_first = pseudonymize(first, roster.apply(first, detected), mapping=mapping).text
        spans = roster.apply(second, [])
        self.assertEqual([span.recognizer for span in spans], [RECOGNIZER])
        out_second = pseudonymize(second, spans, mapping=mapping).text
        self.assertIn("[PESSOA_1]", out_first)
        self.assertIn("socio:\n[PESSOA_1], em nome", out_second)

    def test_variants_are_unified_under_one_token(self) -> None:
        text = "FULANO BELTRANO DE TAL e depois Fulano de Tal, a mesma pessoa."
        detected = [_span(text, "FULANO BELTRANO DE TAL", "PERSON"), _span(text, "Fulano de Tal", "PERSON")]
        roster = BatchRoster()
        roster.add(text, detected)
        out = pseudonymize(text, roster.apply(text, detected), mapping=PseudonymMap()).text
        self.assertEqual(out, "[PESSOA_1] e depois [PESSOA_1], a mesma pessoa.")

    def test_public_agent_is_not_propagated(self) -> None:
        detected_in = "Juiz Inclusao: CICRANO FICTICIO DA SILVA"
        elsewhere = "Documento eletronico assinado por CICRANO FICTICIO DA SILVA, Juiz Federal."
        other = "Conforme decidiu CICRANO FICTICIO DA SILVA no despacho."
        roster = BatchRoster()
        roster.add(detected_in, [_span(detected_in, "CICRANO FICTICIO DA SILVA", "PERSON")])
        roster.review([detected_in, elsewhere, other])
        self.assertEqual(roster.apply(other, []), [])

    def test_party_near_the_word_juiz_still_propagates(self) -> None:
        party = "O socio FULANO BELTRANO DE TAL foi incluido."
        petition = "Excelentissimo Senhor Juiz Federal, a Uniao requer em face de FULANO BELTRANO DE TAL a inclusao."
        roster = BatchRoster()
        roster.add(party, [_span(party, "FULANO BELTRANO DE TAL", "PERSON")])
        roster.review([party, petition])
        spans = roster.apply(petition, [])
        self.assertEqual(len(spans), 1)
        self.assertEqual(petition[spans[0].start : spans[0].end], "FULANO BELTRANO DE TAL")

    def test_label_taken_for_a_name_and_single_words_do_not_spread(self) -> None:
        first = "Tipo de Devedor: PRINCIPAL. Socio Fulano."
        second = "Tipo de Devedor: SOLIDARIO. Fulano chegou."
        roster = BatchRoster()
        roster.add(first, [_span(first, "Tipo de Devedor", "PERSON"), _span(first, "Fulano", "PERSON")])
        roster.review([first, second])
        self.assertEqual(roster.apply(second, []), [])

    def test_legal_structure_words_never_spread(self) -> None:
        # No lote de 25/09 o NER tomou "pessoa juridica" por empresa: 15 trocas viraram 80.
        first = "EMPRESA FICTICIA LTDA, pessoa juridica de direito privado."
        second = "Outra pessoa juridica de direito privado e a pessoa fisica."
        roster = BatchRoster()
        roster.add(first, [_span(first, "pessoa juridica de direito privado", "ORGANIZATION")])
        roster.review([first, second])
        self.assertEqual(roster.apply(second, []), [])

    def test_legal_structure_is_a_protected_term(self) -> None:
        from anonimizador_app.gemini_detector import is_kept_term

        for value in ("pessoa jurídica", "PESSOA JURIDICA", "Pessoa Física"):
            with self.subTest(value=value):
                self.assertTrue(is_kept_term(value))

    def test_detected_spans_are_kept_as_they_are(self) -> None:
        text = "A empresa EMPRESA FICTICIA SERVICOS LTDA e a EMPRESA FICTICIA SERVICOS LTDA."
        detected = [_span(text, "EMPRESA FICTICIA SERVICOS LTDA", "ORGANIZATION")]
        roster = BatchRoster()
        roster.add(text, detected)
        spans = roster.apply(text, detected)
        self.assertEqual((spans[0].start, spans[0].end, spans[0].recognizer), (detected[0].start, detected[0].end, "NerLocal"))
        self.assertEqual(spans[1].recognizer, RECOGNIZER)


class RosterIdTests(unittest.TestCase):
    def test_known_plate_is_propagated_with_its_token(self) -> None:
        first = "| Placa |\n|---|\n| ABC1D23 |"
        second = "Veiculo PR/ABC1D23 em circulacao."
        roster = BatchRoster()
        detected = [_span(first, "ABC1D23", "BR_PLACA", canonical="ABC1D23")]
        roster.add(first, detected)
        mapping = PseudonymMap()
        pseudonymize(first, roster.apply(first, detected), mapping=mapping)
        out = pseudonymize(second, roster.apply(second, []), mapping=mapping).text
        self.assertEqual(out, "Veiculo [PLACA_1] em circulacao.")

    def test_cnpj_root_of_private_company_follows_the_cnpj_token(self) -> None:
        text = "CNPJ 12.345.678/0001-90 | Raiz 12345678 | outra 123456789 | Uniao 00394460"
        detected = [
            _span(text, "12.345.678/0001-90", "BR_CNPJ", canonical="12345678000190"),
        ]
        roster = BatchRoster()
        roster.add(text, detected)
        roster.add("Uniao 00.394.460/0216-53", [EntitySpan(6, 24, "BR_CNPJ", 0.9, "Presidio", canonical_key="00394460021653")])
        out = pseudonymize(text, roster.apply(text, detected), mapping=PseudonymMap()).text
        self.assertEqual(out, "CNPJ [CNPJ_1] | Raiz [CNPJ_1] | outra 123456789 | Uniao 00394460")


class _OnlySeesMarkedFile:
    """NER falso: so reconhece o nome no arquivo que tem o marcador (como o GLiNER no limiar)."""

    def detect(self, text: str) -> list[EntitySpan]:
        if "MARCADOR" not in text:
            return []
        spans = []
        for name in ("FULANO BELTRANO DE TAL", "Fulano Tal"):
            start = text.index(name)
            spans.append(EntitySpan(start, start + len(name), "PERSON", 0.9, "NerLocal"))
        return spans

    def scan_residual(self, text: str) -> list[str]:
        del text
        return []


class BatchJobTests(unittest.TestCase):
    def test_name_found_in_a_later_file_is_replaced_in_the_earlier_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            earlier = root / "a_peticao.md"
            later = root / "b_contrato.md"
            earlier.write_text("Em face de FULANO BELTRANO\nDE TAL, dito Fulano Tal.\n", encoding="utf-8")
            later.write_text("MARCADOR socio FULANO BELTRANO DE TAL (Fulano Tal).\n", encoding="utf-8")
            job = run_job(
                [earlier, later],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_teste",
                contextual=_OnlySeesMarkedFile(),
                use_cache=False,
            )
            first = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
            second = job.outcomes[1].markdown_path.read_text(encoding="utf-8")
            audit = (job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        self.assertNotIn("FULANO", first)
        self.assertNotIn("Fulano", first)
        self.assertEqual(first.count("[PESSOA_1]"), 2)
        self.assertIn("socio [PESSOA_1] ([PESSOA_1]).", second)
        self.assertEqual(job.outcomes[0].propagated, 2)
        self.assertIn("Propagacao do lote: 2 ocorrencia(s)", audit)


if __name__ == "__main__":
    unittest.main()


class EntityTypeTests(unittest.TestCase):
    """Tipo errado julgado por entidade: rua com nome de pessoa e banco do SISBAJUD."""

    def _run(self, text: str, spans: list[EntitySpan]) -> str:
        roster = BatchRoster()
        roster.add(text, spans)
        roster.review([text])
        return pseudonymize(text, roster.apply(text, spans)).text

    def test_street_named_after_person_becomes_address(self) -> None:
        name = "Marechal Fictício de Souza Lima"
        text = (
            f"Endereço: {name}, NR. 656, Cidade Industrial.\n"
            f"Sede na Rua {name}, nº 656.\n"
            f"Filial também na {name}, NR. 700."
        )
        out = self._run(text, [_span(text, name, "PERSON")])
        self.assertNotIn(name, out)
        self.assertNotIn("[PESSOA_", out)
        self.assertEqual(out.count("[ENDERECO_1]"), 3)

    def test_person_seldom_in_address_position_stays_person(self) -> None:
        name = "Fulano Fictício Tavares"
        text = (
            f"Endereço: {name}, NR. 10.\n"
            f"O sócio {name} assinou. Depois {name} saiu. Enfim {name} voltou."
        )
        out = self._run(text, [_span(text, name, "PERSON", occurrence=1)])
        self.assertIn("[PESSOA_1]", out)
        self.assertNotIn("[ENDERECO_", out)

    def test_bank_in_sisbajud_stays_in_clear_everywhere(self) -> None:
        bank = "BANCO FICTICIO S.A."
        company = "EMPRESA FICTICIA SERVICOS LTDA"
        text = (
            f"Instituições\n05422 - {bank}\n  /\n{company}\n"
            f"Respostas\n{bank}\n  Data/hora   Tipo de ordem\n"
            f"Depósito no {bank} pela {company}."
        )
        spans = [
            _span(text, bank, "ORGANIZATION"),
            _span(text, bank, "ORGANIZATION", occurrence=1),
            _span(text, company, "ORGANIZATION"),
        ]
        out = self._run(text, spans)
        self.assertEqual(out.count(bank), 3)
        self.assertNotIn(company, out)
