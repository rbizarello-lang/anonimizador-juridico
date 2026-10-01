from __future__ import annotations

import unittest

from anonimizador_app.audit import (
    audit_map_records,
    audit_markdown,
    extractor_note,
    write_audit_report,
)
from anonimizador_juridico.pseudonymizer import MapRecord


class AuditMarkdownTests(unittest.TestCase):
    def test_date_with_space_is_not_glued_alert(self) -> None:
        text = "Registrado por: [PESSOA_1] 05/08/2025 16:43. R$ 10.000,00. SISBAJUD."
        notes = " ".join(audit_markdown(text))
        self.assertNotIn("token colado em data", notes)

    def test_street_without_endereco_token_alerts(self) -> None:
        text = "A empresa, sediada à Rua das Palmeiras, 100, permanece. Uniao no polo ativo."
        notes = " ".join(audit_markdown(text))
        self.assertIn("nenhum [ENDERECO_n]", notes)

    def test_endereco_token_suppresses_street_alert(self) -> None:
        text = "Sediada à [ENDERECO_1], 100. Uniao no polo ativo."
        notes = " ".join(audit_markdown(text))
        self.assertNotIn("nenhum [ENDERECO_n]", notes)

    def test_ocr_failure_marker_raises_alert(self) -> None:
        text = "<!-- ocr-falhas: 53 -->\n<!-- pagina-origem: 1 -->\nUniao."
        notes = " ".join(audit_markdown(text))
        self.assertIn("OCR falhou em 53 pagina(s)", notes)

    def test_extractor_note_flags_fallback(self) -> None:
        self.assertIn("ALERTA", extractor_note("pymupdf-reserva"))
        self.assertNotIn("ALERTA", extractor_note("docling-nativo"))

    def test_token_before_reais_with_space_is_not_money_alert(self) -> None:
        text = "Depositante [EMPRESA_1] R$2.971,28 em 22/12/2025. Uniao."
        notes = " ".join(audit_markdown(text))
        self.assertNotIn("token encostado em valor em R$", notes)

    def test_capital_reais_with_person_on_next_line_is_not_money_alert(self) -> None:
        text = "CAPITAL R$\n[PESSOA_1], socio da executada. Uniao."
        notes = " ".join(audit_markdown(text))
        self.assertNotIn("token encostado em valor em R$", notes)

    def test_glued_or_interpolated_token_alerts_money(self) -> None:
        glued = " ".join(audit_markdown("Pagamento R$[EMPRESA_1] na Uniao."))
        other_side = " ".join(audit_markdown("Trecho [EMPRESA_1]R$ na Uniao."))
        interpol = " ".join(audit_markdown("Valor R$ [EMPRESA_1],28. SISBAJUD."))
        self.assertIn("token encostado em valor em R$", glued)
        self.assertIn("token encostado em valor em R$", other_side)
        self.assertIn("token encostado em valor em R$", interpol)


class AuditMapTests(unittest.TestCase):
    def test_blocked_particle_in_map_alerts_without_party_name(self) -> None:
        records = [
            MapRecord(
                token="[EMPRESA_3]",
                entity_type="ORGANIZATION",
                canonical="parana",
                originals=["Paraná"],
            ),
            MapRecord(
                token="[PESSOA_1]",
                entity_type="PERSON",
                canonical="nome ficticio da parte alvo",
                originals=["Nome Ficticio da Parte Alvo"],
            ),
        ]
        notes = audit_map_records(records)
        joined = " ".join(notes)
        self.assertIn("ALERTA", joined)
        self.assertIn("parana", joined)
        self.assertNotIn("Nome Ficticio", joined)
        self.assertNotIn("parte alvo", joined)

    def test_html_original_in_map_alerts(self) -> None:
        records = [
            MapRecord(
                token="[PESSOA_2]",
                entity_type="PERSON",
                canonical="pagina",
                originals=["<!-- pagina-origem: 13 -->"],
            )
        ]
        joined = " ".join(audit_map_records(records))
        self.assertIn("ancora HTML", joined)

    def test_leftover_formatted_cnpj_is_counted(self) -> None:
        notes = " ".join(audit_markdown("Orgao 61.738.402/0001-82. Uniao."))
        self.assertIn("CNPJ formatado em claro: 1", notes)

    def test_leftover_formatted_cpf_alerts(self) -> None:
        notes = " ".join(audit_markdown("Documento 529.982.247-25. Uniao."))
        self.assertIn("CPF formatado", notes)
        self.assertIn("ALERTA", notes)

    def test_leftover_formatted_cep_is_counted(self) -> None:
        notes = " ".join(audit_markdown("Endereco 86032-010. Uniao."))
        self.assertIn("CEP formatado em claro: 1", notes)

    def test_audit_report_has_no_party_pii(self) -> None:
        import tempfile
        from pathlib import Path

        records = [
            MapRecord(
                token="[PESSOA_9]",
                entity_type="PERSON",
                canonical="silva lopes da silva",
                originals=["Silva Lopes da Silva"],
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_audit_report(
                Path(directory),
                [("mapa.json", audit_map_records(records))],
            )
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("Silva Lopes", text)
        self.assertIn("mapa.json", text)


if __name__ == "__main__":
    unittest.main()
