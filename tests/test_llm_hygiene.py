"""Higiene das mencoes do LLM (H9-c): o que se descarta, o que passa, papel publico, juizo do lote e AUDITORIA.

Textos e nomes inventados; sem rede, sem modelo.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from anonimizador_app.llm_discovery import Discovery, DiscoveryReport
from anonimizador_app.llm_hygiene import LlmHygieneStats, filter_llm_mentions, role_counts
from anonimizador_app.pipeline_llm import run_llm_job
from anonimizador_app.roster import KEEP, TOKENIZE, Mention, Roster, policy_action

from test_pipeline_llm import FakeDiscoverer, FakeLocalEngine


def mention(kind: str, value: str, haystack: str, *, role: str = "", canonical: str = "", at: int | None = None) -> Mention:
    index = haystack.find(value) if at is None else at
    end = index + len(value) if index >= 0 else -1
    return Mention(
        kind=kind, text=value, role=role, canonical=canonical or value, start=index, end=end,
        source_text=value if index >= 0 else "",
    )


def run(mentions: list[Mention], text: str, **kwargs) -> tuple[list[Mention], LlmHygieneStats]:
    stats = LlmHygieneStats()
    return filter_llm_mentions(mentions, text, stats, **kwargs), stats


class DiscardReasonTests(unittest.TestCase):
    """Um caso por motivo de descarte."""

    def test_class_outside_the_list(self) -> None:
        text = "Contato: fulano@exemplo.test e evento 4."
        out, stats = run([mention("email", "fulano@exemplo.test", text), mention(": ", "Contato", text),
                          mention("evento", "evento 4", text)], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.classe, 3)

    def test_mention_without_anchor_is_dropped(self) -> None:
        text = "O socio Olavo Ricardo Teixeira foi citado."
        out, stats = run([mention("pessoa", "Olavo Ricardo Teixeira", text, at=-1)], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.ancora, 1)

    def test_all_lowercase_text_is_not_a_proper_name(self) -> None:
        text = "A desconsideração da personalidade e a pessoa jurídica pagaram."
        out, stats = run([
            mention("empresa", "desconsideração da personalidade", text),
            mention("pessoa", "pessoa jurídica", text),
        ], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.minusculas, 2)

    def test_protected_term_and_name_that_contains_it(self) -> None:
        text = "A Receita Federal de Joinville e o STJ; a Delegacia da Receita Federal respondeu. União Federal."
        out, stats = run([
            mention("empresa", "Delegacia da Receita Federal", text),
            mention("empresa", "Receita Federal", text),
            mention("empresa", "STJ", text),
            mention("empresa", "União Federal", text),
            mention("empresa", "Banco do Brasil S.A.", "Banco do Brasil S.A."),
        ], text + " Banco do Brasil S.A.")
        self.assertEqual(out, [])
        self.assertEqual(stats.protegido, 5)

    def test_company_that_starts_with_a_protected_common_word_is_kept(self) -> None:
        text = "Contra UNIÃO COMERCIAL DE GRÃOS LTDA."
        out, stats = run([mention("empresa", "UNIÃO COMERCIAL DE GRÃOS LTDA", text)], text)
        self.assertEqual(len(out), 1)
        self.assertEqual(stats.descartadas, 0)

    def test_label_followed_by_number_or_only_number_or_acronym(self) -> None:
        text = "Página 12. Evento 45. Despacho: Valor Total 1.234,00. 12.345-6. TRF. 98765 4321."
        values = ["Página 12", "Evento 45", "Despacho:", "Valor Total", "12.345-6", "TRF", "98765 4321"]
        out, stats = run([mention("pessoa", value, text) for value in values], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.rotulo_numero, len(values))

    def test_legal_term_including_the_prefix_form(self) -> None:
        text = "Decisão Interlocutória; Embargos de Declaração; Recurso Especial; Petição Inicial."
        values = ["Decisão Interlocutória", "Embargos de Declaração", "Recurso Especial", "Petição Inicial"]
        out, stats = run([mention("pessoa", value, text) for value in values], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.juridico, len(values))

    def test_code_of_civil_procedure_of_2015_does_not_become_a_name(self) -> None:
        text = "Nos termos do Código de Processo Civil de 2015, a parte foi citada."
        out, stats = run([mention("empresa", "Código de Processo Civil de 2015", text)], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.descartadas, 1)

    def test_city_state_and_place_after_a_label(self) -> None:
        text = "Foro da Comarca de Joinville, Santa Catarina. Bairro: Centro. Cidade: Pomerode."
        out, stats = run([
            mention("empresa", "Joinville", text),
            mention("pessoa", "Santa Catarina", text),
            mention("pessoa", "Centro", text),
            mention("pessoa", "Pomerode", text),
            mention("endereco", "Joinville", text),
        ], text)
        self.assertEqual(out, [])
        self.assertEqual(stats.geografia, 5)

    def test_one_word_person_needs_a_full_name(self) -> None:
        text = "O sócio SOUZA assinou. A sócia Carla Souza Lima também."
        alone, stats = run([mention("pessoa", "SOUZA", text)], text)
        self.assertEqual(alone, [])
        self.assertEqual(stats.uma_palavra, 1)
        with_canonical, _ = run([mention("pessoa", "SOUZA", text, canonical="Carla Souza Lima")], text)
        self.assertEqual(len(with_canonical), 1)
        with_roster, _ = run([mention("pessoa", "SOUZA", text)], text, known_people=["Carla Souza Lima"])
        self.assertEqual(len(with_roster), 1)
        with_sibling, _ = run(
            [mention("pessoa", "SOUZA", text), mention("pessoa", "Carla Souza Lima", text)], text
        )
        self.assertEqual(len(with_sibling), 2)

    def test_legitimate_names_pass(self) -> None:
        text = (
            "O sócio Olavo Ricardo Teixeira e a Beta Industrial Ltda moram na Rua das Acácias, 120. "
            "Site: www.betaindustrial.com.br"
        )
        out, stats = run([
            mention("pessoa", "Olavo Ricardo Teixeira", text, role="socio"),
            mention("empresa", "Beta Industrial Ltda", text, role="parte"),
            mention("endereco", "Rua das Acácias, 120", text),
            mention("empresa", "www.betaindustrial.com.br", text),
        ], text, extra_keep=["Gama Ficticia"])
        self.assertEqual(len(out), 4)
        self.assertEqual(stats.descartadas, 0)


class GluedMentionTests(unittest.TestCase):
    def test_span_glued_after_a_digit_is_cut_at_the_word_boundary(self) -> None:
        text = "Contrato 2024PEDRO ALVES SANTOS assinado."
        start = text.index("PEDRO")
        out, stats = run([mention("pessoa", "PEDRO ALVES SANTOS", text, at=start)], text)
        self.assertEqual(len(out), 1)
        self.assertEqual(text[out[0].start : out[0].end], "ALVES SANTOS")
        self.assertEqual((stats.colado_recortado, stats.colado_descartado), (1, 0))

    def test_span_glued_before_a_digit_is_cut_and_a_single_word_is_dropped(self) -> None:
        text = "Empresa ACME INDUSTRIAL LTDA123 e 7FULANO."
        out, stats = run([
            mention("empresa", "ACME INDUSTRIAL LTDA", text),
            mention("pessoa", "FULANO", text, canonical="Fulano Silva Souza"),
        ], text)
        self.assertEqual([text[item.start : item.end] for item in out], ["ACME INDUSTRIAL"])
        self.assertEqual((stats.colado_recortado, stats.colado_descartado), (1, 1))


class AuditLineTests(unittest.TestCase):
    def test_lines_carry_only_numbers(self) -> None:
        stats = LlmHygieneStats(classe=1, ancora=2, minusculas=3, protegido=4, rotulo_numero=5, juridico=6,
                                geografia=7, uma_palavra=8, colado_recortado=9, colado_descartado=10)
        lines = stats.audit_lines(kept_public=11, mixed=12)
        self.assertEqual(
            lines[0],
            "Higiene das mencoes do LLM: descartadas 36 (classe fora da lista 1, sem ancora 2, minusculas 3, "
            "termo protegido 4, rotulo/numero 5, termo juridico 6, geografia 7, uma palavra 8, "
            "rotulo de formulario 0, clausula ordinal 0, URL ou dominio 0, duas pessoas 0, "
            "variante sem relacao 0, banco como endereco 0, marca ou modelo de veiculo 0); "
            "entradas mantidas pelo papel publico do LLM: 11; papel misto: 12",
        )
        for line in lines:
            self.assertNotRegex(line, r"[A-Z][a-z]+ [A-Z][a-z]+ ")


class PublicRoleTests(unittest.TestCase):
    def test_unknown_role_does_not_undo_the_public_role(self) -> None:
        self.assertEqual(policy_action(["magistrado", "desconhecido"]), KEEP)
        self.assertEqual(policy_action(["desconhecido"]), TOKENIZE)
        self.assertEqual(policy_action(["magistrado", "parte"]), TOKENIZE)
        roster = Roster()
        roster.add_mention(Mention(kind="pessoa", text="Luciana Montefusco de Almeida", role="magistrada"))
        entry = roster.add_mention(Mention(kind="pessoa", text="Luciana Montefusco de Almeida", role="data"))
        self.assertEqual(entry.action, KEEP)
        roster.add_mention(Mention(kind="pessoa", text="Luciana Montefusco de Almeida"))
        self.assertEqual(entry.action, KEEP)

    def test_mixed_roles_stay_tokenized_and_are_counted(self) -> None:
        roster = Roster()
        roster.add_mention(Mention(kind="pessoa", text="Luciana Montefusco de Almeida", role="magistrado"))
        roster.add_mention(Mention(kind="pessoa", text="Olavo Ricardo Teixeira", role="juiz"))
        roster.add_mention(Mention(kind="pessoa", text="Olavo Ricardo Teixeira", role="socio"))
        roster.add_mention(Mention(kind="pessoa", text="Marlene Oliveira Sampaio", role="socio"))
        self.assertEqual(roster.lookup("pessoa", "Olavo Ricardo Teixeira").action, TOKENIZE)
        self.assertEqual(role_counts(roster), (1, 1))

    def test_locked_keep_survives_a_new_private_role(self) -> None:
        roster = Roster()
        entry = roster.add_mention(Mention(kind="pessoa", text="Pedro Alves Santos", role="parte"))
        self.assertTrue(roster.lock_keep(entry.key))
        roster.add_mention(Mention(kind="pessoa", text="Pedro Alves Santos", role="terceiro"))
        self.assertEqual(entry.action, KEEP)
        listed = roster.add_listed("pessoa", "Bruno Costa Lima")
        self.assertFalse(roster.lock_keep(listed.key))


DOC_JUDGE = """\
<!-- pagina-origem: 1 -->

Documento eletrônico assinado por LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.

A sócia MARLENE OLIVEIRA SAMPAIO foi citada. A magistrada LUCIANA MONTEFUSCO DE ALMEIDA determinou a penhora.

Instituições da ordem:
00123 - BANCO ALFA FICTICIO SA
"""


class _Pipeline(unittest.TestCase):
    def go(self, text: str, discoverer, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / "autos.md"
        source.write_text(text, encoding="utf-8")
        kwargs.setdefault("local_floor", False)
        kwargs.setdefault("verification_rounds", 0)
        result = run_llm_job(
            [source], export_root=base / "exportaveis", private_root=base / "privados",
            discoverer=discoverer, use_cache=False, **kwargs,
        )
        body = result.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        audit = (result.job.export_dir / "AUDITORIA.txt").read_text(encoding="utf-8")
        return result, body, audit


class BatchJudgmentOnLlmMentionsTests(_Pipeline):
    ENTITIES = [
        ("pessoa", "LUCIANA MONTEFUSCO DE ALMEIDA", "parte"),  # o LLM errou o papel
        ("pessoa", "MARLENE OLIVEIRA SAMPAIO", "socio"),
        ("empresa", "BANCO ALFA FICTICIO SA", "terceiro"),
    ]

    def test_judge_signed_by_stays_clear_and_the_partner_is_still_replaced(self) -> None:
        result, body, audit = self.go(DOC_JUDGE, FakeDiscoverer(self.ENTITIES))
        self.assertEqual(body.count("LUCIANA MONTEFUSCO DE ALMEIDA"), 2)
        self.assertNotIn("MARLENE", body)  # a prova de que a higiene e o juizo nao tiram protecao legitima
        self.assertIn("00123 - BANCO ALFA FICTICIO SA", body)
        self.assertRegex(audit, r"mantidas por agente publico 1, por instituicao financeira 1, retipadas como rua com nome de pessoa 0")
        self.assertEqual(result.roster.lookup("pessoa", "MARLENE OLIVEIRA SAMPAIO").action, TOKENIZE)

    def test_a_partner_named_by_the_llm_is_replaced_even_with_junk_around(self) -> None:
        text = "O sócio JOSE FICTICIO DA SILVA foi citado em 5 de março. Página 12.\n"
        junk = [("pessoa", "Página 12", "parte"), ("pessoa", "foi citado", "parte")]
        _result, body, audit = self.go(text, FakeDiscoverer([("pessoa", "JOSE FICTICIO DA SILVA", "socio"), *junk]))
        self.assertNotIn("JOSE FICTICIO", body)
        self.assertIn("Página 12", body)
        self.assertIn("foi citado", body)
        self.assertRegex(audit, r"descartadas 2 \(")


class JunkDiscoverer(FakeDiscoverer):
    """O LLM do diagnostico: acha o socio, mas tambem classe, rotulo, termo juridico, cidade e mencao solta."""

    def __init__(self, entities, junk) -> None:
        super().__init__(entities)
        self.junk = junk

    def discover(self, text: str) -> Discovery:
        found = super().discover(text)
        for kind, value, role in self.junk:
            index = text.find(value)
            end = index + len(value) if index >= 0 else -1
            found.mentions.append(
                Mention(kind=kind, text=value, role=role, canonical=value, start=index, end=end, source_text=value)
            )
        return found


class PipelineHygieneTests(_Pipeline):
    TEXT = (
        "<!-- pagina-origem: 1 -->\n\n"
        "A decisão interlocutória, na Página 12 do Evento 45, aplicou o Código de Processo Civil de 2015.\n"
        "A Delegacia da Receita Federal de Joinville informou. Comarca de Pomerode. Contato: Maria.\n"
        "O sócio OLAVO RICARDO TEIXEIRA foi citado.\n"
    )
    JUNK = [
        ("email", "Contato", "terceiro"),
        ("pessoa", "decisão interlocutória", "parte"),
        ("pessoa", "Página 12", "parte"),
        ("empresa", "Código de Processo Civil de 2015", "parte"),
        ("empresa", "Delegacia da Receita Federal de Joinville", "parte"),
        ("pessoa", "Pomerode", "parte"),
        ("pessoa", "NAO ESTA NO TEXTO", "parte"),
    ]

    def test_junk_stays_in_the_text_and_only_counts_reach_the_audit(self) -> None:
        discoverer = JunkDiscoverer([("pessoa", "OLAVO RICARDO TEIXEIRA", "socio")], self.JUNK)
        result, body, audit = self.go(self.TEXT, discoverer)
        for kept in ("decisão interlocutória", "Página 12", "Código de Processo Civil de 2015",
                     "Delegacia da Receita Federal de Joinville", "Comarca de Pomerode", "Contato: Maria"):
            self.assertIn(kept, body)
        self.assertNotIn("OLAVO", body)
        self.assertEqual(len(result.roster), 1)
        line = next(item for item in audit.splitlines() if "Higiene das mencoes do LLM" in item)
        numbers = [int(value) for value in re.findall(r"(\d+)", line)]
        self.assertEqual(numbers[0], 7 - 0)  # descartadas
        # classe, ancora, minusculas, protegido (CPC de 2015 e Delegacia), rotulo/numero, juridico, geografia, uma palavra
        self.assertEqual(numbers[1:9], [1, 1, 1, 2, 1, 0, 1, 0])
        # so contagens: nenhum nome nem trecho do texto vai para a AUDITORIA
        for forbidden in ("OLAVO", "Pomerode", "Joinville", "NAO ESTA"):
            self.assertNotIn(forbidden, audit)

    def test_public_role_entry_survives_a_verification_mention_with_an_invented_role(self) -> None:
        text = "<!-- pagina-origem: 1 -->\n\nA magistrada LUCIANA MONTEFUSCO DE ALMEIDA determinou. O sócio OLAVO RICARDO TEIXEIRA.\n"

        class Verifier(FakeDiscoverer):
            def discover(self, body: str) -> Discovery:
                found = super().discover(body)
                if len(self.calls) > 1:  # a 2a leitura: o modelo pequeno inventa o papel
                    found.mentions = [
                        Mention(kind=m.kind, text=m.text, role="data", canonical=m.canonical, start=m.start,
                                end=m.end, source_text=m.source_text)
                        for m in found.mentions
                    ]
                return found

        entities = [("pessoa", "LUCIANA MONTEFUSCO DE ALMEIDA", "magistrada"),
                    ("pessoa", "OLAVO RICARDO TEIXEIRA", "socio")]
        result, body, audit = self.go(text, Verifier(entities), verification_rounds=1)
        self.assertIn("LUCIANA MONTEFUSCO DE ALMEIDA", body)
        self.assertNotIn("OLAVO", body)
        self.assertRegex(audit, r"entradas mantidas pelo papel publico do LLM: 1; papel misto: 0")

    def test_mixed_roles_are_counted(self) -> None:
        text = "<!-- pagina-origem: 1 -->\n\nPEDRO ALVES SANTOS aparece duas vezes: PEDRO ALVES SANTOS.\n"

        class Mixed(FakeDiscoverer):
            def discover(self, body: str) -> Discovery:
                first = body.index("PEDRO")
                second = body.index("PEDRO", first + 1)
                name = "PEDRO ALVES SANTOS"
                return Discovery(
                    mentions=[
                        Mention(kind="pessoa", text=name, role="magistrado", canonical=name, start=first,
                                end=first + len(name), source_text=name),
                        Mention(kind="pessoa", text=name, role="terceiro", canonical=name, start=second,
                                end=second + len(name), source_text=name),
                    ],
                    report=DiscoveryReport(chunks=1, sent=1),
                )

        _result, body, audit = self.go(text, Mixed([]))
        self.assertNotIn("PEDRO", body)  # a regra atual protege na duvida
        self.assertRegex(audit, r"entradas mantidas pelo papel publico do LLM: 0; papel misto: 1")

    def test_local_engine_name_does_not_undo_the_keep_of_the_llm(self) -> None:
        text = "<!-- pagina-origem: 1 -->\n\nA magistrada LUCIANA MONTEFUSCO DE ALMEIDA determinou. A Dra. Luciana Montefusco também.\n"
        engine = FakeLocalEngine([("PERSON", "LUCIANA MONTEFUSCO DE ALMEIDA"), ("PERSON", "Luciana Montefusco")])
        _result, body, _audit = self.go(
            text, FakeDiscoverer([("pessoa", "LUCIANA MONTEFUSCO DE ALMEIDA", "magistrado")]),
            local_floor=True, fallback_engine=engine,
        )
        self.assertIn("LUCIANA MONTEFUSCO DE ALMEIDA", body)


if __name__ == "__main__":
    unittest.main()
