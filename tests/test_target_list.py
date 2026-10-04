from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.settings import AppSettings, load_settings, save_settings
from anonimizador_app.target_list import TargetListEngine, attach_list_canonicals
from anonimizador_juridico import pseudonymize

BODY = """\
## Evento 12

A executada NORTE INVENTADO COMERCIO informou penhora. A Uniao requereu
SISBAJUD no valor de R$ 12.345,67. Assinado por Carla Exemplo Souza.
Norte Inventado Comercio tambem consta no evento 13.
"""


class TargetListEngineTests(unittest.TestCase):
    def test_tokenizes_company_in_any_case(self) -> None:
        engine = TargetListEngine(companies=["Norte Inventado Comercio"])
        spans = engine.detect(BODY)
        values = {BODY[span.start : span.end] for span in spans}
        self.assertIn("NORTE INVENTADO COMERCIO", values)
        self.assertIn("Norte Inventado Comercio", values)
        for span in spans:
            self.assertEqual(span.entity_type, "ORGANIZATION")
            self.assertEqual(span.recognizer, "ListaDeAlvos")

    def test_single_word_company_is_accepted(self) -> None:
        engine = TargetListEngine(companies=["Ecotires"])
        spans = engine.detect("A empresa ECOTIRES nao pagou. Ecotires segue devendo.")
        self.assertEqual(len(spans), 2)

    def test_person_target(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        spans = engine.detect(BODY)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].entity_type, "PERSON")

    def test_keep_terms_and_exclusions_win(self) -> None:
        engine = TargetListEngine(
            companies=["Uniao", "Fazenda Nacional", "Empresa Protegida"],
            extra_keep=["Empresa Protegida"],
        )
        spans = engine.detect("A Uniao e a Fazenda Nacional citaram a Empresa Protegida.")
        self.assertEqual(spans, [])

    def test_word_boundary_prevents_partial_hit(self) -> None:
        engine = TargetListEngine(companies=["Norte"])
        spans = engine.detect("O Nortear do caso nao contem o alvo isolado.")
        self.assertEqual(spans, [])

    def test_strips_legal_suffix_when_searching(self) -> None:
        engine = TargetListEngine(companies=["Norte Inventado Comercio Ltda"])
        text = "Compareceu NORTE INVENTADO COMERCIO no evento 4."
        spans = engine.detect(text)
        values = {text[span.start : span.end] for span in spans}
        self.assertIn("NORTE INVENTADO COMERCIO", values)

    def test_accent_and_linebreak_do_not_block_company(self) -> None:
        engine = TargetListEngine(companies=["Norte Inventado Comércio Ltda"])
        text = "Depositante: NORTE INVENTADO COMERCIO\nLTDA juntou comprovante."
        spans = engine.detect(text)
        blob = " ".join(text[span.start : span.end] for span in spans)
        self.assertIn("NORTE INVENTADO COMERCIO", blob)
        self.assertIn("LTDA", blob)

    def test_person_particles_from_full_target(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = "CARLA EXEMPLO SOUZA juntou. CARLA EXEMPLO tambem. A Uniao permanece."
        spans = engine.detect(text)
        blob = " ".join(text[span.start : span.end] for span in spans)
        self.assertIn("CARLA EXEMPLO SOUZA", blob)
        self.assertIn("CARLA EXEMPLO", blob)
        self.assertEqual({span.entity_type for span in spans}, {"PERSON"})

    def test_common_surname_alone_is_not_tokenized(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = "SOUZA assinou a peticao da Uniao no evento 12."
        spans = engine.detect(text)
        self.assertEqual(spans, [])

    def test_common_surname_with_role_cue_is_tokenized(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = "O socio Souza juntou defesa. A Uniao permanece."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertIn("Souza", values)

    def test_distinctive_particle_still_matches_alone(self) -> None:
        engine = TargetListEngine(people=["Carla Cantagalo Souza"])
        text = "CANTAGALO juntou peticao. A Uniao permanece."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertIn("CANTAGALO", values)

    def test_common_particles_together_support_each_other(self) -> None:
        engine = TargetListEngine(people=["Helena Lopes da Silva"])
        text = "Compareceu LOPES DA SILVA no evento. Uniao."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertTrue(any("LOPES" in item and "SILVA" in item for item in values))

    def test_common_particles_across_sentence_are_not_support(self) -> None:
        engine = TargetListEngine(people=["Helena Lopes da Silva"])
        text = "Silva indeferiu o agravo. Lopes juntou peticao. Uniao."
        self.assertEqual(engine.detect(text), [])

    def test_silva_e_lopes_are_two_people_not_one_name(self) -> None:
        engine = TargetListEngine(people=["Helena Lopes da Silva"])
        text = "Compareceram Silva e Lopes na audiencia. Uniao."
        self.assertEqual(engine.detect(text), [])

    def test_reu_cue_tokenizes_common_surname(self) -> None:
        engine = TargetListEngine(people=["Helena Lopes da Silva"])
        text = "O reu Silva juntou defesa. A Uniao permanece."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertIn("Silva", values)

    def test_rel_min_does_not_tokenize_listed_surname(self) -> None:
        engine = TargetListEngine(people=["Helena Lopes da Silva"])
        text = "Rel. min. Silva negou provimento. A Uniao permanece."
        self.assertEqual(engine.detect(text), [])

    def test_ministro_does_not_tokenize_listed_surname(self) -> None:
        engine = TargetListEngine(people=["Helena Lopes da Silva"])
        text = "O ministro Silva indeferiu. A Uniao permanece."
        self.assertEqual(engine.detect(text), [])

    def test_particle_does_not_eat_company_reason(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = (
            "SOUZA COMERCIO DE PECAS LTDA juntou defesa. "
            "Depois SOUZA assinou a peticao."
        )
        spans = engine.detect(text)
        blob = " ".join(text[span.start : span.end] for span in spans)
        self.assertNotIn("SOUZA COMERCIO", blob)
        self.assertEqual(spans, [])

    def test_one_char_ocr_typo_on_particle_is_tokenized(self) -> None:
        engine = TargetListEngine(people=["Marcelo Odorico Bernardelli"])
        text = "UDORICO assinou. ODORCO tambem. UXURICO nao."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertIn("UDORICO", values)
        self.assertIn("ODORCO", values)
        self.assertNotIn("UXURICO", values)

    def test_short_particle_is_not_fuzzed(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = "O socio SOUZA assinou. SOUSA nao e o alvo."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertIn("SOUZA", values)
        self.assertNotIn("SOUSA", values)

    def test_company_ocr_insertion_is_tokenized(self) -> None:
        engine = TargetListEngine(companies=["Crisalida Confeccoes Ltda"])
        text = "Compareceu CRISALIDA CONFECCCOES LTDA no evento 4."
        blob = " ".join(text[span.start : span.end] for span in engine.detect(text))
        self.assertIn("CONFECCCOES", blob)

    def test_particle_keeps_word_boundary(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = "A palavra Nortear nao e o alvo Carla Exemplo Souza."
        spans = engine.detect(text)
        values = {text[span.start : span.end] for span in spans}
        self.assertTrue(any("Carla Exemplo Souza" in item for item in values))
        self.assertTrue(all("Nortear" not in item for item in values))

    def test_short_and_duplicate_targets_are_dropped(self) -> None:
        engine = TargetListEngine(companies=["ab", "  Norte  Inventado ", "norte inventado"])
        self.assertEqual(engine.companies, ("Norte Inventado",))

    def test_settings_roundtrip_and_engine_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings(
                local_ner_enabled=False,
                target_companies=["Norte Inventado Comercio"],
                target_people=["Carla Exemplo Souza"],
                target_ids=["529.982.247-25", "12.345.678/0001-95"],
                cnpj_lookup_enabled=False,
            )
            save_settings(root, settings)
            loaded = load_settings(root)
            self.assertEqual(loaded.target_companies, ["Norte Inventado Comercio"])
            self.assertEqual(loaded.target_people, ["Carla Exemplo Souza"])
            self.assertEqual(
                loaded.target_ids, ["529.982.247-25", "12.345.678/0001-95"]
            )
            self.assertFalse(loaded.case_shadow_enabled)
            engine = build_contextual_engine(loaded)
            self.assertIsNotNone(engine)
            spans = engine.detect(BODY)
            recognizers = {span.recognizer for span in spans}
            self.assertIn("ListaDeAlvos", recognizers)


def _apply(engine: TargetListEngine, text: str):
    return pseudonymize(text, engine.detect(text))


class TargetListAliasTests(unittest.TestCase):
    def test_unique_particle_shares_entry_token(self) -> None:
        engine = TargetListEngine(people=["Helena Ficticia da Silva"])
        text = "Helena Ficticia da Silva juntou. Depois HELENA assinou."
        result = _apply(engine, text)
        self.assertNotIn("Helena Ficticia da Silva", result.text)
        self.assertIn("HELENA", result.text)
        self.assertEqual(result.text.count("[PESSOA_1]"), 1)
        self.assertEqual(len(result.mapping.records), 1)
        self.assertEqual(result.mapping.records[0].alvo, "Helena Ficticia da Silva")
        self.assertNotIn("HELENA", result.mapping.records[0].originals)

    def test_ambiguous_particle_with_company_context(self) -> None:
        engine = TargetListEngine(
            companies=["Valverde Comercio de Pecas Ltda"],
            people=["Ana Valverde Souza"],
        )
        text = "A empresa Valverde Comercio executou. A Uniao permanece."
        result = _apply(engine, text)
        self.assertIn("[EMPRESA_1]", result.text)
        self.assertNotIn("[PESSOA_", result.text)
        self.assertEqual(result.mapping.records[0].entity_type, "ORGANIZATION")

    def test_company_one_word_particle_is_not_extracted(self) -> None:
        from anonimizador_app.target_list import _entry_particles

        self.assertEqual(
            _entry_particles("Valverde Comercio de Pecas Ltda", company=True),
            (),
        )
        engine = TargetListEngine(companies=["Valverde Comercio de Pecas Ltda"])
        self.assertEqual(engine.detect("A empresa Valverde executou. A Uniao permanece."), [])

    def test_listed_company_does_not_eat_cnae_or_ctn(self) -> None:
        engine = TargetListEngine(companies=["Valverde Comercio de Pecas Ltda"])
        text = (
            "CNAE 47.89-0-99 comercio varejista de artigos. "
            "CTN art. 133 nome individual. A Uniao permanece."
        )
        self.assertEqual(engine.detect(text), [])

    def test_nearest_complete_mention_without_marker(self) -> None:
        engine = TargetListEngine(
            companies=["Valverde Comercio de Pecas Ltda"],
            people=["Ana Valverde Souza", "Bruno Valverde Souza"],
        )
        text = (
            "Compareceu Ana Valverde Souza no evento 2. "
            "Depois Valverde juntou peticao. Uniao permanece."
        )
        result = _apply(engine, text)
        self.assertEqual(result.text.count("[PESSOA_1]"), 2)
        self.assertNotIn("[PESSOA_2]", result.text)
        self.assertNotIn("[EMPRESA_", result.text)

    def test_legal_suffix_ltda_eireli_me_fuse(self) -> None:
        engine = TargetListEngine(
            companies=[
                "Norte Inventado Comercio Ltda",
                "Norte Inventado Comercio Ltda ME",
                "Norte Inventado Comercio Eireli",
            ]
        )
        text = (
            "NORTE INVENTADO COMERCIO LTDA e NORTE INVENTADO COMERCIO EIRELI "
            "e NORTE INVENTADO COMERCIO LTDA ME."
        )
        result = _apply(engine, text)
        self.assertEqual(result.text.count("[EMPRESA_1]"), 3)
        self.assertEqual(len(result.mapping.records), 1)
        self.assertEqual(len(result.mapping.records[0].originals), 3)

    def test_fuzzy_particle_shares_token(self) -> None:
        engine = TargetListEngine(people=["Marcelo Odorico Bernardelli"])
        text = "MARCELO ODORICO BERNARDELLI assinou. UDORICO tambem."
        result = _apply(engine, text)
        self.assertEqual(result.text.count("[PESSOA_1]"), 2)
        self.assertEqual(len(result.mapping.records), 1)
        self.assertIn("UDORICO", result.mapping.records[0].originals)

    def test_map_is_reversible_via_originals(self) -> None:
        engine = TargetListEngine(companies=["Horizonte Ficticio Ltda"])
        text = "Horizonte Ficticio Ltda e HORIZONTE FICTICIO."
        result = _apply(engine, text)
        originals = result.mapping.records[0].originals
        self.assertIn("Horizonte Ficticio Ltda", originals)
        self.assertIn("HORIZONTE FICTICIO", originals)


class TargetListCiclo3Tests(unittest.TestCase):
    def test_ocr_legal_tail_etda_extends_reason(self) -> None:
        engine = TargetListEngine(
            companies=["Norte Inventado Comercio de Vidros e Acessorios Ltda"]
        )
        text = "Compareceu NORTE INVENTADO COMERCIO DE VIDROS E TIZIFAROTA PIDI –ME."
        result = _apply(engine, text)
        self.assertNotIn("ACESŠÓRIOS", result.text)
        self.assertNotIn("ETDA", result.text)
        self.assertIn("[EMPRESA_1]", result.text)

    def test_three_word_prefix_covers_truncated_reason(self) -> None:
        engine = TargetListEngine(
            companies=["Norte Inventado Comercio de Vidros e Acessorios Ltda"]
        )
        text = "administradora de NORTE INVENTADO COMERCIO DE VIDROS."
        result = _apply(engine, text)
        self.assertNotIn("NORTE INVENTADO COMERCIO DE VIDROS", result.text)
        self.assertIn("[EMPRESA_1]", result.text)

    def test_glued_person_pair_is_tokenized(self) -> None:
        engine = TargetListEngine(people=["Carla Exemplo Souza"])
        text = "CARLAEXEMPLO assinou a peticao."
        result = _apply(engine, text)
        self.assertNotIn("CARLAEXEMPLO", result.text)
        self.assertIn("[PESSOA_1]", result.text)

    def test_glued_connective_julianade_shape(self) -> None:
        engine = TargetListEngine(people=["Helena de Silva"])
        text = "HELENADE SILVA juntou."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertTrue(any("HELENADE" in item for item in values))

    def test_ampersand_tail_after_brand_not_generic_vidros(self) -> None:
        engine = TargetListEngine(companies=["Norte Vidros & Esquadrias"])
        hit = engine.detect("NORTE\nLOJA\nVIDROS & ESQUADRIAS\nDesign")
        blob = " ".join("NORTE\nLOJA\nVIDROS & ESQUADRIAS\nDesign"[s.start : s.end] for s in hit)
        self.assertIn("VIDROS & ESQUADRIAS", blob)
        miss = engine.detect(
            "Pedido de substituicao de vidros em teses da Uniao. Evento 12."
        )
        values = {
            "Pedido de substituicao de vidros em teses da Uniao. Evento 12."[
                span.start : span.end
            ]
            for span in miss
        }
        self.assertFalse(any(item.strip().casefold() == "vidros" for item in values))

    def test_particle_does_not_eat_street_or_judge(self) -> None:
        engine = TargetListEngine(
            people=["Solange Aparecida de Oliveira", "Valmor Fiorentino de Souza"]
        )
        street = "sede na Rua Ari Jose de Oliveira Meireles, nº 579."
        self.assertEqual(
            {street[span.start : span.end] for span in engine.detect(street)},
            set(),
        )
        judge = "Juiz(a):\n\nOSVALDO LUIZ FICTICIO\n\nPartes"
        values = {judge[span.start : span.end] for span in engine.detect(judge)}
        self.assertNotIn("OSVALDO", values)
        pfn = "Rafael Teste das Neves, Procurador da Fazenda Nacional, juntou."
        pfn_values = {pfn[span.start : span.end] for span in engine.detect(pfn)}
        self.assertFalse(any("Rafael" in item for item in pfn_values))

    def test_listed_full_name_is_tokenized_near_pgfn(self) -> None:
        engine = TargetListEngine(
            people=["Paulo Ficticio Souza", "Rafael Exemplo Bertoldi"]
        )
        text = (
            "ADVOGADO: RAFAEL EXEMPLO BERTOLDI, OAB/PR 12.345\n\n"
            "<!-- pagina-origem: 2 -->\n"
            "A PGFN requer a citação do sócio PAULO FICTICIO SOUZA, administrador.\n"
        )
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertIn("PAULO FICTICIO SOUZA", values)
        self.assertIn("RAFAEL EXEMPLO BERTOLDI", values)
        partial = TargetListEngine(people=["Paulo Ficticio Souza"])
        officer = "PAULO FICTICIO, Procurador da Fazenda Nacional, juntou."
        officer_values = {
            officer[span.start : span.end] for span in partial.detect(officer)
        }
        self.assertFalse(any("PAULO" in item for item in officer_values))

    def test_leading_person_pair_is_tokenized(self) -> None:
        engine = TargetListEngine(people=["Vera Lucia Bernardelli", "Sutilogu Cico Bernardelli"])
        text = "Compareceram VERA LUCIA e SUTILOGU CICO como testemunhas."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertTrue(any("VERA LUCIA" in item for item in values))
        self.assertTrue(any("SUTILOGU CICO" in item for item in values))

    def test_company_glued_does_not_eat_generic_comercial(self) -> None:
        engine = TargetListEngine(companies=["Norte Comercial e Transportes Ltda"])
        text = "Registro na Junta Comercial do Parana. Uniao permanece."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertFalse(any("Comercial" in item for item in values))

    def test_ner_person_shares_list_canonical(self) -> None:
        from anonimizador_juridico import EntitySpan, PseudonymMap, pseudonymize

        engine = TargetListEngine(people=["Helena Ficticia da Silva"])
        text = "Helena Ficticia da Silva juntou defesa."
        start = text.index("Helena Ficticia da Silva")
        ner = EntitySpan(
            start=start,
            end=start + len("Helena Ficticia da Silva"),
            entity_type="PERSON",
            score=0.81,
            recognizer="NerLocal",
        )
        bound = attach_list_canonicals(text, [ner], engine)
        self.assertEqual(bound[0].canonical_key, engine._entries[0].canonical_key)
        self.assertEqual(bound[0].alvo, "Helena Ficticia da Silva")
        listed = engine.detect(text)
        result = pseudonymize(text, listed, mapping=PseudonymMap())
        second = pseudonymize(text, bound, mapping=result.mapping)
        self.assertEqual(second.text.count("[PESSOA_1]"), 1)
        self.assertEqual(len(second.mapping.records), 1)

    def test_domain_stem_shares_company_token(self) -> None:
        engine = TargetListEngine(
            companies=[
                "Tramontana Comercio de Vidros Ltda",
                "Bernardelli Comercio de Vidros e Acessorios Ltda",
                "Bernardelli Vidros & Esquadrias",
            ]
        )
        text = (
            "TRAMONTANA COMERCIO DE VIDROS citou o site bernardellividros.com.br/blog "
            "e crisalidaconfeccoes.com"
        )
        result = _apply(engine, text)
        self.assertNotIn("bernardellividros", result.text.casefold())
        self.assertIn("[EMPRESA_1]", result.text)
        self.assertIn("[EMPRESA_2]", result.text)

    def test_ocr_glued_domain_without_word_boundary(self) -> None:
        engine = TargetListEngine(companies=["Bernardelli Comercio de Vidros Ltda"])
        text = "visite vendasuabernardellidros.co.or e vendasabernardellivdros.combr"
        result = _apply(engine, text)
        self.assertNotIn("bernardellidros", result.text.casefold())
        self.assertNotIn("bernardellivdros", result.text.casefold())
        self.assertIn("[EMPRESA_1]", result.text)

    def test_two_word_brand_prefix(self) -> None:
        engine = TargetListEngine(companies=["Bernardelli Vidros & Esquadrias"])
        text = "A loja Bernardelli Vidros abriu filial."
        result = _apply(engine, text)
        self.assertNotIn("Bernardelli Vidros", result.text)
        self.assertIn("[EMPRESA_1]", result.text)

    def test_ampersand_ocr_garbled_tail(self) -> None:
        engine = TargetListEngine(companies=["Bernardelli Vidros & Esquadrias"])
        text = "VIDR0S & ESQUADRLAS\nDesign"
        result = _apply(engine, text)
        self.assertNotIn("VIDRDS", result.text)
        self.assertNotIn("ESQUADDRIAS", result.text)
        self.assertIn("[EMPRESA_1]", result.text)

    def test_crisalida_domain_stem(self) -> None:
        engine = TargetListEngine(companies=["Crisalida Confeccoes Ltda"])
        text = "site crisalidaconfeccoes.com.br da executada."
        result = _apply(engine, text)
        self.assertNotIn("crisalidaconfeccoes", result.text.casefold())
        self.assertIn("[EMPRESA_1]", result.text)


class TargetListCiclo5ParticleDenylistTests(unittest.TestCase):
    def test_uf_in_other_reason_does_not_tokenize_state(self) -> None:
        engine = TargetListEngine(companies=["Aurora Vidros do Parana Ltda"])
        text = "Registro na Junta Comercial do Paraná. A Uniao permanece."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertFalse(any("paran" in item.casefold() for item in values))

    def test_full_reason_with_uf_still_tokenizes(self) -> None:
        engine = TargetListEngine(companies=["Aurora Vidros do Parana Ltda"])
        text = "Compareceu AURORA VIDROS DO PARANA LTDA no evento 4."
        result = _apply(engine, text)
        self.assertNotIn("AURORA VIDROS DO PARANA", result.text)
        self.assertIn("[EMPRESA_1]", result.text)

    def test_campos_particle_does_not_eat_campo_mourao(self) -> None:
        engine = TargetListEngine(people=["Claudia Maria Campos Hollerbach"])
        text = "Subseção de Campo Mourão. CAMPOS juntou peticao."
        result = _apply(engine, text)
        self.assertIn("Campo Mourão", result.text)
        self.assertIn("CAMPOS", result.text)
        cued = _apply(engine, "Subseção de Campo Mourão. O socio CAMPOS juntou.")
        self.assertIn("Campo Mourão", cued.text)
        self.assertNotIn("CAMPOS", cued.text)
        self.assertIn("[PESSOA_1]", cued.text)

    def test_casado_as_marital_status_is_not_tokenized(self) -> None:
        engine = TargetListEngine(people=["Osvaldo Eustaquio Casado"])
        text = (
            "OSVALDO EUSTAQUIO CASADO, brasileiro, casado, comerciante. "
            "A Uniao permanece."
        )
        result = _apply(engine, text)
        self.assertNotIn("OSVALDO EUSTAQUIO CASADO", result.text)
        self.assertIn("casado, comerciante", result.text)
        self.assertIn("[PESSOA_1]", result.text)

    def test_marcos_does_not_eat_month(self) -> None:
        engine = TargetListEngine(people=["Marcos Mitsuo Kanamori"])
        text = "MARCOS MITSUO KANAMORI assinou em 26 de março de 2010."
        result = _apply(engine, text)
        self.assertNotIn("MARCOS MITSUO KANAMORI", result.text)
        self.assertIn("26 de março de 2010", result.text)

    def test_sistemas_particle_does_not_eat_thesis(self) -> None:
        engine = TargetListEngine(
            companies=["Conplan - Sistemas de Informatica Ltda"]
        )
        text = (
            "CONPLAN - SISTEMAS DE INFORMATICA LTDA. "
            "Os sistemas de constrição permanecem. Uniao."
        )
        result = _apply(engine, text)
        self.assertIn("[EMPRESA_1]", result.text)
        self.assertIn("sistemas de constrição", result.text)

    def test_recuperacao_judicial_is_not_a_particle(self) -> None:
        engine = TargetListEngine(
            companies=["Lindenau Moveis Ltda em Recuperacao Judicial"]
        )
        text = (
            "LINDENAU MOVEIS LTDA EM RECUPERACAO JUDICIAL. "
            "Pedido de recuperacao judicial. Uniao."
        )
        result = _apply(engine, text)
        self.assertIn("[EMPRESA_1]", result.text)
        self.assertIn("Pedido de recuperacao judicial", result.text)

    def test_parenthetical_labels_are_not_particles(self) -> None:
        engine = TargetListEngine(
            companies=[
                "Joaquim Peixoto Gontijo (Empresa Individual / antiga Tramatex Ltda)"
            ]
        )
        text = (
            "Pessoa juridica. Empresa individual. A antiga regra permanece. "
            "TRAMATEX juntou. Uniao."
        )
        result = _apply(engine, text)
        self.assertIn("Empresa individual", result.text)
        self.assertIn("antiga regra", result.text)
        self.assertIn("TRAMATEX", result.text)

    def test_cordeiro_does_not_eat_correio(self) -> None:
        engine = TargetListEngine(people=["Carla Cordeiro Magalhaes Ferreira"])
        text = "CARLA CORDEIRO MAGALHAES FERREIRA. Citacao pelo correio. Uniao."
        result = _apply(engine, text)
        self.assertNotIn("CARLA CORDEIRO", result.text)
        self.assertIn("correio", result.text)

    def test_espolio_prefix_is_not_a_particle(self) -> None:
        engine = TargetListEngine(people=["Espólio de Vitorino Augusto de Oliveira"])
        text = (
            "Espólio de Vitorino Augusto de Oliveira juntou. "
            "O espólio do executado. Uniao."
        )
        result = _apply(engine, text)
        self.assertIn("[PESSOA_1]", result.text)
        self.assertIn("espólio do executado", result.text)

    def test_brasil_particle_does_not_eat_oab(self) -> None:
        engine = TargetListEngine(companies=["Kanamori do Brasil Sociedade Anonima"])
        text = (
            "KANAMORI DO BRASIL SOCIEDADE ANONIMA. "
            "Ordem dos Advogados do Brasil. Uniao."
        )
        result = _apply(engine, text)
        self.assertIn("[EMPRESA_1]", result.text)
        self.assertIn("Advogados do Brasil", result.text)

    def test_blocked_particle_helper_ignores_phrases(self) -> None:
        from anonimizador_app.target_list import is_blocked_particle

        self.assertTrue(is_blocked_particle("Paraná"))
        self.assertTrue(is_blocked_particle("casado"))
        self.assertTrue(is_blocked_particle("locação"))
        self.assertFalse(is_blocked_particle("Aurora Vidros do Parana"))
        self.assertFalse(is_blocked_particle("Helena"))

    def test_apostrophe_variants_share_company(self) -> None:
        engine = TargetListEngine(companies=["CANJICA'S DISTRIBUIDORA LTDA"])
        text = "Compareceu CANJICA´S DISTRIBUIDORA LTDA no polo. Uniao."
        values = {text[span.start : span.end] for span in engine.detect(text)}
        self.assertTrue(any("DISTRIBUIDORA" in item for item in values))


class TargetListIdTests(unittest.TestCase):
    def test_parses_only_valid_private_ids(self) -> None:
        from anonimizador_app.target_ids import parse_target_ids

        parsed = parse_target_ids(
            [
                "CPF 52998224725",
                "12.345.678/0001-95",
                "00.394.460/0216-53",
                "111.111.111-11",
                "texto qualquer",
            ]
        )
        self.assertEqual(parsed, ("529.982.247-25", "12.345.678/0001-95"))

    def test_finds_listed_ids_and_leaves_other_cpf(self) -> None:
        engine = TargetListEngine(
            ids=["529.982.247-25", "12.345.678/0001-95"],
            lookup_cnpj_names=False,
        )
        text = (
            "O CPF 529.982.247-2S e o compacto 52998224725. "
            "CNPJ 12.345.678/0001-\n95. Outro 111.444.777-35 fica. Uniao."
        )
        spans = engine.detect(text)
        types = {span.entity_type for span in spans}
        self.assertEqual(types, {"BR_CPF", "BR_CNPJ"})
        result = _apply(engine, text)
        self.assertNotIn("529.982.247-2S", result.text)
        self.assertNotIn("52998224725", result.text)
        self.assertNotIn("12.345.678/0001-", result.text)
        self.assertIn("111.444.777-35", result.text)
        self.assertIn("Uniao", result.text)

    def test_listed_cnpj_looks_up_razao_without_number_in_text(self) -> None:
        from anonimizador_app.cnpj_index import CompanyRecord, write_records
        from anonimizador_juridico.validators import _cnpj_check_digit

        body = "112223330001"
        first = _cnpj_check_digit(body, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
        second = _cnpj_check_digit(
            body + str(first), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
        )
        cnpj = body + str(first) + str(second)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cnpj_sul.sqlite"
            write_records(
                path,
                [
                    CompanyRecord(
                        cnpj=cnpj,
                        cnpj_basico=cnpj[:8],
                        razao_social="Norte Inventado Comercio Ltda",
                        nome_fantasia="NorteCom",
                        natureza="2062",
                        uf="RS",
                        publica=False,
                    )
                ],
            )
            engine = TargetListEngine(ids=[cnpj], cnpj_index_path=path)
            text = (
                "A NORTE INVENTADO COMERCIO LTDA requereu SISBAJUD. Uniao permanece."
            )
            result = _apply(engine, text)
        self.assertIn("[EMPRESA_1]", result.text)
        self.assertIn("Uniao", result.text)
        self.assertIn("SISBAJUD", result.text)
        self.assertNotIn("NORTE INVENTADO", result.text)


if __name__ == "__main__":
    unittest.main()
