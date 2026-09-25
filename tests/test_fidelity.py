from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_juridico import EntitySpan
from anonimizador_app.fidelity import (
    drop_protected_spans,
    is_blocked_numeric_context,
    is_magistrate_or_citation,
    is_pfn_officer,
    is_precedent_party,
    is_precedent_process,
    is_uniao_cnpj,
    is_unlabeled_compact_phone,
)
from anonimizador_app.header_heuristic import HeaderHeuristicEngine
from anonimizador_app.local_ner import CombinedEngine, LocalNerEngine
from anonimizador_app.pipeline import run_job
from anonimizador_app.settings import AppSettings

COVER = """\
Tipo documento: Evento: CAPA PROCESSO abertura

Juiz(a):

GUSTAVO FICTICIO BARCELOS

## Partes e Representantes

| EXEQUENTE | EXECUTADO |
|-----------|-----------|
| UNIÃO - FAZENDA NACIONAL | Norte Inventado S.A. |

## Listagem dos Eventos do Processo

Segundo estudo de Fabio Konder Comparato, o grupo e de fato.
(TRF4, AG 0015031, Relatora Maria de Fatima Labarrere, D.E. 15/02/2012)
Rel. Ministro Napoleao Nunes Maia Filho, PRIMEIRA TURMA.
No dizer da Ministra Andrighi, do STJ:
A executada Carla Exemplo Souza juntou defesa. R$ 10.000,00. SISBAJUD.
"""


def _person(text: str, value: str, recognizer: str = "GLiNER") -> EntitySpan:
    start = text.index(value)
    return EntitySpan(
        start=start,
        end=start + len(value),
        entity_type="PERSON",
        score=0.9,
        recognizer=recognizer,
    )


class FidelityFilterTests(unittest.TestCase):
    def test_cover_judge_is_protected(self) -> None:
        text = "Juiz(a):\n\nGUSTAVO FICTICIO BARCELOS\n\nPartes"
        self.assertTrue(
            is_magistrate_or_citation(
                text,
                text.index("GUSTAVO"),
                text.index("GUSTAVO") + len("GUSTAVO FICTICIO BARCELOS"),
            )
        )
        kept = drop_protected_spans(text, [_person(text, "GUSTAVO FICTICIO BARCELOS")])
        self.assertEqual(kept, [])

    def test_relator_and_ministro_are_protected(self) -> None:
        text = (
            "Relatora Maria de Fatima Labarrere, D.E. 15/02/2012. "
            "Rel. Ministro Napoleao Nunes Maia Filho, TURMA."
        )
        self.assertTrue(
            is_magistrate_or_citation(
                text,
                text.index("Maria"),
                text.index("Maria") + len("Maria de Fatima Labarrere"),
            )
        )
        self.assertTrue(
            is_magistrate_or_citation(
                text,
                text.index("Napoleao"),
                text.index("Napoleao") + len("Napoleao Nunes Maia Filho"),
            )
        )

    def test_doctrine_and_ministra_title_are_protected(self) -> None:
        study = "Segundo estudo de Fabio Konder Comparato, o grupo e de fato."
        self.assertTrue(
            is_magistrate_or_citation(
                study,
                study.index("Fabio Konder"),
                study.index("Fabio Konder") + len("Fabio Konder"),
            )
        )
        saying = "No dizer da Ministra Andrighi, do STJ:"
        self.assertTrue(
            is_magistrate_or_citation(
                saying,
                saying.index("Ministra Andrighi"),
                saying.index("Ministra Andrighi") + len("Ministra Andrighi"),
            )
        )

    def test_rel_min_abbreviation_is_protected(self) -> None:
        text = "Rel. min. Silva negou provimento ao agravo."
        start = text.index("Silva")
        self.assertTrue(is_magistrate_or_citation(text, start, start + len("Silva")))

    def test_ministro_without_rel_is_protected(self) -> None:
        text = "O ministro Silva indeferiu o pedido da Uniao."
        start = text.index("Silva")
        self.assertTrue(is_magistrate_or_citation(text, start, start + len("Silva")))

    def test_min_dot_ementa_is_protected(self) -> None:
        text = "Min. Teori Zavascki, Tribunal Pleno."
        start = text.index("Teori")
        self.assertTrue(
            is_magistrate_or_citation(
                text, start, start + len("Teori Zavascki")
            )
        )

    def test_gliner_all_common_name_is_dropped(self) -> None:
        text = "Helena Lopes da Silva juntou defesa."
        kept = drop_protected_spans(text, [_person(text, "Helena Lopes da Silva")])
        self.assertEqual(kept, [])

    def test_list_keeps_all_common_name(self) -> None:
        text = "Helena Lopes da Silva juntou defesa."
        kept = drop_protected_spans(
            text,
            [_person(text, "Helena Lopes da Silva", recognizer="ListaDeAlvos")],
        )
        self.assertEqual(len(kept), 1)

    def test_header_keeps_all_common_name(self) -> None:
        text = "EXECUTADO: Helena Lopes da Silva"
        kept = drop_protected_spans(
            text,
            [_person(text, "Helena Lopes da Silva", recognizer="HeaderHeuristic")],
        )
        self.assertEqual(len(kept), 1)

    def test_gliner_distinctive_name_is_kept(self) -> None:
        text = "Carla Castanheira Souza juntou defesa."
        kept = drop_protected_spans(text, [_person(text, "Carla Castanheira Souza")])
        self.assertEqual(len(kept), 1)

    def test_party_name_is_not_protected(self) -> None:
        text = "A executada Carla Exemplo Souza juntou defesa."
        start = text.index("Carla")
        end = start + len("Carla Exemplo Souza")
        self.assertFalse(is_magistrate_or_citation(text, start, end))
        kept = drop_protected_spans(text, [_person(text, "Carla Exemplo Souza")])
        self.assertEqual(len(kept), 1)

    def test_target_list_still_tokenizes_even_after_judge_cue(self) -> None:
        text = "Juiz(a):\n\nCarla Exemplo Souza\n"
        kept = drop_protected_spans(
            text,
            [_person(text, "Carla Exemplo Souza", recognizer="ListaDeAlvos")],
        )
        self.assertEqual(len(kept), 1)

    def test_juiz_federal_without_colon_is_protected(self) -> None:
        text = "Juiz Federal GUSTAVO FICTICIO BARCELOS indeferiu."
        start = text.index("GUSTAVO")
        end = start + len("GUSTAVO FICTICIO BARCELOS")
        self.assertTrue(is_magistrate_or_citation(text, start, end))

    def test_pipeline_drops_gliner_judge_and_citation(self) -> None:
        def predict(chunk: str):
            hits = []
            mapping = (
                ("GUSTAVO FICTICIO BARCELOS", "pessoa"),
                ("Fabio Konder", "pessoa"),
                ("Maria de Fatima Labarrere", "pessoa"),
                ("Napoleao Nunes Maia Filho", "pessoa"),
                ("Ministra Andrighi", "pessoa"),
                ("Carla Exemplo Souza", "pessoa"),
                ("Norte Inventado S.A.", "empresa privada"),
            )
            for word, label in mapping:
                if word in chunk:
                    hits.append({"entity_group": label, "word": word, "score": 0.91})
            return hits

        engine = CombinedEngine(
            [
                HeaderHeuristicEngine(),
                LocalNerEngine(predict=predict),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capa.md"
            source.write_text(COVER, encoding="utf-8")
            job = run_job(
                [source],
                export_root=root / "exportaveis",
                private_root=root / "privados",
                job_id="lote_juiz",
                contextual=engine,
                settings=AppSettings(local_ner_enabled=True),
            )
            markdown = job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        self.assertIn("GUSTAVO FICTICIO BARCELOS", markdown)
        self.assertIn("Fabio Konder", markdown)
        self.assertIn("Maria de Fatima Labarrere", markdown)
        self.assertIn("Napoleao Nunes Maia Filho", markdown)
        self.assertIn("Ministra Andrighi", markdown)
        self.assertIn("[PESSOA_", markdown)
        self.assertNotIn("Carla Exemplo Souza", markdown)
        self.assertIn("UNIÃO - FAZENDA NACIONAL", markdown)
        self.assertIn("SISBAJUD", markdown)
        self.assertIn("R$ 10.000,00", markdown)

    def test_pfn_officer_from_ner_is_dropped(self) -> None:
        text = (
            "Juliano Ficticio Reis, Procurador da Fazenda Nacional, "
            "requereu SISBAJUD. A Uniao permanece."
        )
        start = text.index("Juliano")
        end = start + len("Juliano Ficticio Reis")
        self.assertTrue(is_pfn_officer(text, start, end))
        kept = drop_protected_spans(text, [_person(text, "Juliano Ficticio Reis")])
        self.assertEqual(kept, [])

    def test_pfn_event_code_on_same_line_is_dropped(self) -> None:
        text = (
            "26/08/2025 15:45:55 PFN2041305 - Juliano Ficticio Reis - "
            "PROCURADOR 5017674-26.2025.4.04.7001/PR 1"
        )
        start = text.index("Juliano")
        end = start + len("Juliano Ficticio Reis")
        self.assertTrue(is_pfn_officer(text, start, end))
        kept = drop_protected_spans(text, [_person(text, "Juliano Ficticio Reis")])
        self.assertEqual(kept, [])

    def test_pfn_does_not_protect_target_list(self) -> None:
        text = "Roger Ficticio Bizarello, Procurador da Fazenda Nacional."
        kept = drop_protected_spans(
            text,
            [_person(text, "Roger Ficticio Bizarello", recognizer="ListaDeAlvos")],
        )
        self.assertEqual(len(kept), 1)

    def test_party_near_fazenda_without_procurador_stays(self) -> None:
        text = "A executada Carla Exemplo Souza contestou a Fazenda Nacional."
        start = text.index("Carla")
        end = start + len("Carla Exemplo Souza")
        self.assertFalse(is_pfn_officer(text, start, end))
        kept = drop_protected_spans(text, [_person(text, "Carla Exemplo Souza")])
        self.assertEqual(len(kept), 1)

    def test_state_attorney_from_ner_is_dropped(self) -> None:
        text = (
            "Ana Ficticia Lima, Procuradora do Estado, requereu penhora. "
            "A executada permanece."
        )
        start = text.index("Ana")
        end = start + len("Ana Ficticia Lima")
        self.assertTrue(is_pfn_officer(text, start, end))
        kept = drop_protected_spans(text, [_person(text, "Ana Ficticia Lima")])
        self.assertEqual(kept, [])

    def test_party_near_pge_without_procurador_stays(self) -> None:
        text = "A executada Carla Exemplo Souza contestou a PGE."
        start = text.index("Carla")
        end = start + len("Carla Exemplo Souza")
        self.assertFalse(is_pfn_officer(text, start, end))
        kept = drop_protected_spans(text, [_person(text, "Carla Exemplo Souza")])
        self.assertEqual(len(kept), 1)


class Ciclo2FidelityTests(unittest.TestCase):
    def test_nire_and_verification_code_are_not_ids(self) -> None:
        nire = "NIRE: 4120352196 na junta."
        start = nire.index("4120352196")
        self.assertTrue(is_blocked_numeric_context(nire, start, start + 10))
        verificacao = (
            "CÓDIGO DE VERIFICAÇÃO: 11900654132. NIRE: 41203521963."
        )
        phone_at = verificacao.index("11900654132")
        self.assertTrue(
            is_blocked_numeric_context(verificacao, phone_at, phone_at + 11)
        )
        registro = "CERTIFICO O REGISTRO EM 22/02/2022 SOB Nº 20221059318."
        cpf_at = registro.index("20221059318")
        self.assertTrue(is_blocked_numeric_context(registro, cpf_at, cpf_at + 11))

    def test_uniao_cnpj_is_dropped(self) -> None:
        text = "UNIÃO - FAZENDA NACIONAL (00.394.460/0216-53) no polo ativo."
        start = text.index("00.394.460/0216-53")
        end = start + len("00.394.460/0216-53")
        self.assertTrue(is_uniao_cnpj(text, start, end, "00.394.460/0216-53"))
        span = EntitySpan(
            start=start,
            end=end,
            entity_type="BR_CNPJ",
            score=0.9,
            recognizer="BrCnpjRecognizer",
        )
        kept = drop_protected_spans(text, [span])
        self.assertEqual(kept, [])

    def test_private_cnpj_on_same_row_is_kept(self) -> None:
        text = (
            "UNIÃO - FAZENDA NACIONAL (00.394.460/0216-53) | "
            "NORTE INVENTADO LTDA (04.252.011/0001-10)"
        )
        start = text.index("04.252.011/0001-10")
        end = start + len("04.252.011/0001-10")
        self.assertFalse(is_uniao_cnpj(text, start, end, "04.252.011/0001-10"))

    def test_precedent_ag_is_dropped_cover_process_stays(self) -> None:
        ementa = "(TRF4, AG 5013638-94.2022.4.04.0000, SEGUNDA TURMA)"
        start = ementa.index("5013638-94.2022.4.04.0000")
        self.assertTrue(is_precedent_process(ementa, start))
        capa = "Processo 5017674-26.2025.4.04.7001/PR, Evento 1"
        cover_start = capa.index("5017674-26.2025.4.04.7001")
        self.assertFalse(is_precedent_process(capa, cover_start))

    def test_natureza_juridica_is_too_generic(self) -> None:
        from anonimizador_app.gemini_detector import is_too_generic

        self.assertTrue(
            is_too_generic("ORGANIZATION", "SOCIEDADE EMPRESÁRIA LIMITADA")
        )
        text = "Natureza Jurídica: SOCIEDADE EMPRESÁRIA LIMITADA no cartao."
        start = text.index("SOCIEDADE EMPRESÁRIA LIMITADA")
        end = start + len("SOCIEDADE EMPRESÁRIA LIMITADA")
        span = EntitySpan(
            start=start,
            end=end,
            entity_type="ORGANIZATION",
            score=0.9,
            recognizer="NerLocal",
        )
        self.assertEqual(drop_protected_spans(text, [span]), [])

    def test_cpf_formatted_after_sob_numero_stays(self) -> None:
        text = "inscrito no CPF/MF sob o nº 023.650.449-56 na ficha."
        start = text.index("023.650.449-56")
        self.assertFalse(is_blocked_numeric_context(text, start, start + 14))

    def test_unlabeled_compact_phone_is_dropped(self) -> None:
        text = "Coluna da tabela 1223520231 sem rotulo de telefone."
        start = text.index("1223520231")
        self.assertTrue(is_unlabeled_compact_phone(text, start, start + 10))
        span = EntitySpan(
            start=start,
            end=start + 10,
            entity_type="BR_PHONE",
            score=0.8,
            recognizer="BrPhoneRecognizer",
        )
        self.assertEqual(drop_protected_spans(text, [span]), [])

    def test_labeled_compact_phone_stays(self) -> None:
        text = "Tel. 11987654321 no rodape da peticao."
        start = text.index("11987654321")
        self.assertFalse(is_unlabeled_compact_phone(text, start, start + 11))
        span = EntitySpan(
            start=start,
            end=start + 11,
            entity_type="BR_PHONE",
            score=0.8,
            recognizer="BrPhoneRecognizer",
        )
        self.assertEqual(len(drop_protected_spans(text, [span])), 1)

    def test_precedent_organization_is_dropped(self) -> None:
        text = (
            "EMENTA: REsp 1.234.567, Rel. Ministro Ficticio. "
            "Jornal do Brasil S.A. vs. Uniao, julgado em 2012, DJe 20/01/2012."
        )
        start = text.index("Jornal do Brasil S.A.")
        end = start + len("Jornal do Brasil S.A.")
        self.assertTrue(is_precedent_party(text, start, end))
        span = EntitySpan(
            start=start,
            end=end,
            entity_type="ORGANIZATION",
            score=0.9,
            recognizer="NerLocal",
        )
        self.assertEqual(drop_protected_spans(text, [span]), [])

    def test_own_case_ag_stays_when_filename_matches(self) -> None:
        number = "5017674-26.2025.4.04.7001"
        text = f"Interposto AG {number} contra a sentenca."
        start = text.index(number)
        end = start + len(number)
        self.assertTrue(is_precedent_process(text, start, end))
        self.assertFalse(
            is_precedent_process(
                text,
                start,
                end,
                source_name="PR-50176742620254047001-2026-8-21_PARTE_1.pdf",
            )
        )

    def test_truncated_comprar_is_not_acao_rescisoria(self) -> None:
        text = (
            "Z" * 5000
            + "comprar\n\n<!-- pagina-origem: 47 -->\n"
            + "Processo 5017674-26.2025.4.04.7001/PR,"
        )
        start = text.index("5017674-26.2025.4.04.7001")
        self.assertFalse(is_precedent_process(text, start, start + 25))


if __name__ == "__main__":
    unittest.main()
