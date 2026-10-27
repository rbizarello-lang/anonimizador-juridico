"""Nome pseudonimizado dos arquivos exportados (H9-f).

O nome do arquivo de origem pode trazer razao social ("IDPJ EMPRESA FICTICIA LTDA.txt") e ia inteiro para a
pasta exportavel: o .md, o INDICE.md, o ELENCO.md, a AUDITORIA.txt e o LEIA-ME.txt o repetiam. Agora:

- nome no padrao eproc ("SC-<processo>-<data>_PARTE_<n>"): fica "SC-PROCESSO_<k>_PARTE_<n>", com o numero do
  processo trocado pelo token que ele tem no mapa (o mesmo do texto) e a data da exportacao descartada; o
  sufixo ``_PARTE_<n>`` fica, porque o INDICE liga o fim de uma parte ao comeco da seguinte por ele;
- qualquer outro nome: ``doc_<n>`` (n = ordem do arquivo no lote). Se o nome termina em codigo de documento do
  eproc de familia conhecida ("..._1_CONTRSOCIAL13"), o codigo fica (``doc_<n>_CONTRSOCIAL13``): o INDICE o
  usa como rotulo da folha e ele e tipo de documento, nao nome. Codigo de familia desconhecida cai fora,
  porque poderia ser pedaco de razao social ("..._ACME2").

O mapa ``nomes_arquivos.json`` (exportado -> original) fica na pasta privada, ao lado de ``mapa.json``. A tela
(lista de arquivos, progresso, erros) segue mostrando o nome real: ele esta no PC do usuario.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from anonimizador_juridico.pseudonymizer import PseudonymMap

NAMES_FILENAME = "nomes_arquivos.json"

# SC-50011115420254047001-2026-8-21_PARTE_1 (processo CNJ de 20 digitos, data da exportacao opcional).
_EPROC_NAME = re.compile(
    r"^(?P<uf>[A-Za-z]{2})-(?P<proc>\d{20}|\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})(?:-\d{1,4}(?:-\d{1,2}){0,2})?"
    r"_PARTE_(?P<part>\d+)$"
)
_DOC_CODE = re.compile(r"_(?P<letters>[A-Z]{2,})(?P<number>\d{1,3})(?:_\d+)?$")
# Familias de codigo de documento do eproc (tipo do documento no evento). Fora da lista, o codigo cai.
_CODE_FAMILIES = frozenset(
    "CONTRSOCIAL INIC PROCADM SENT DESP DESPADEC DECI CERT OUT PET ANEXO EXTR COMP MAND OFIC CDA RENAJUD "
    "SISBAJUD INFOJUD CNIB LAUDO CUSTAS EMENDA REPLICA DEFESA CONTEST PROC ACOR EMBARGOS MANIF CITACAO "
    "INTIMACAO PENHORA AUTO LAUDAV".split()
)


def export_stem(source: Path, mapping: PseudonymMap, ordinal: int) -> str:
    """O nome (sem extensao) com que o arquivo de origem vai para a pasta exportavel."""

    stem = Path(source).stem
    match = _EPROC_NAME.match(stem)
    if match is not None:
        digits = re.sub(r"\D", "", match.group("proc"))
        token = mapping.token_for("BR_PROCESSO", digits) or mapping.resolve("BR_PROCESSO", digits)
        label = token.strip("[]")
        return f"{match.group('uf').upper()}-{label}_PARTE_{int(match.group('part'))}"
    code = _DOC_CODE.search(stem)
    suffix = ""
    if code is not None and code.group("letters") in _CODE_FAMILIES:
        suffix = f"_{code.group('letters')}{code.group('number')}"
    return f"doc_{max(1, ordinal)}{suffix}"


def write_name_map(private_dir: Path, pairs: Iterable[tuple[str, str]]) -> Path | None:
    """``nomes_arquivos.json``: (nome exportado, nome de origem) de cada arquivo gravado. Pasta privada."""

    listed = {exported: original for exported, original in pairs}
    if not listed:
        return None
    payload = {
        "aviso": "CONTEUDO SENSIVEL: o nome de origem pode trazer razao social. NAO ENVIAR.",
        "arquivos": listed,
    }
    path = Path(private_dir) / NAMES_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
