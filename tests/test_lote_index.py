from __future__ import annotations

import unittest

from anonimizador_app.lote_index import build_index

_STAMP = "Processo [PROCESSO_1]/PR, Evento {event}, {code}, Página {page}"

PARTE_1 = """<!-- cobertura: paginas=4; nativas=3; escaneadas=1; vetoriais=0; fonte-quebrada=0; em-branco=0; nao-lidas=nenhuma -->
<!-- pagina-origem: 1 -->
## PROCESSO [PROCESSO_1]/PR - Parte 1

Tipo documento: CAPA PROCESSO — Evento: abertura
<!-- pagina-origem: 2 -->
### Evento 1 - Doc. 1 - PETIÇÃO INICIAL

{s1}
Texto da inicial.
<!-- pagina-origem: 3 -->
### Evento 1 - Doc. 2 - CONTRATO SOCIAL

{s2}
<!-- ocr: vetorial -->
Cláusula primeira.
<!-- pagina-origem: 4 -->
{s3}
| a | b |
|---|---|
| 1 | 2 |
""".format(
    s1=_STAMP.format(event=1, code="INIC1", page=1),
    s2=_STAMP.format(event=1, code="CONTRSOCIAL2", page=1),
    s3=_STAMP.format(event=1, code="CONTRSOCIAL2", page=2),
)

PARTE_2 = """<!-- pagina-origem: 1 -->
## PROCESSO [PROCESSO_1]/PR - Parte 2

<!-- pagina-origem: 2 -->
### Evento 1 - Doc. 2 - CONTRATO SOCIAL

{s1}
Cláusula décima.
<!-- pagina-origem: 3 -->
PÁGINA DE SEPARAÇÃO
Evento 28
Evento: EXPEDIDA INTIMACAO ELETRONICA — Data: 09/06/2026 13:11:55 — Usuário: X — Sequência Evento: 28 — Prazo: 5 Dias
""".format(s1=_STAMP.format(event=1, code="CONTRSOCIAL2", page=3))


class LoteIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = build_index(
            [("autos_PARTE_1.md", PARTE_1), ("autos_PARTE_2.md", PARTE_2), ("extrato.md", "<!-- pagina-origem: 1 -->\nsaldo")]
        )

    def test_documents_with_sheet_and_page_ranges(self) -> None:
        self.assertIn("| 1 | Doc. 1 — PETIÇÃO INICIAL (INIC1) | 2 | 1 |  |  |", self.index)
        self.assertIn("| 1 | Doc. 2 — CONTRATO SOCIAL (CONTRSOCIAL2) · continua em autos_PARTE_2.md | 3–4 | 1–2 | 1 | 1 |", self.index)
        self.assertIn("processo [PROCESSO_1]/PR · 4 folhas (3 nativas, 1 por OCR, não lidas: nenhuma)", self.index)

    def test_document_continues_across_parts_despite_cover(self) -> None:
        self.assertIn(
            "| 1 | Doc. 2 — CONTRATO SOCIAL (CONTRSOCIAL2) · continuação de autos_PARTE_1.md | 2 | 3 |",
            self.index,
        )

    def test_event_without_document_is_its_own_row(self) -> None:
        self.assertIn(
            "| 28 | movimentação sem documento: EXPEDIDA INTIMACAO ELETRONICA (09/06/2026) | 3 | — |",
            self.index,
        )

    def test_file_without_stamp_or_bookmark(self) -> None:
        self.assertIn("## extrato.md", self.index)
        self.assertIn("documento avulso, folhas 1–1", self.index)


if __name__ == "__main__":
    unittest.main()
