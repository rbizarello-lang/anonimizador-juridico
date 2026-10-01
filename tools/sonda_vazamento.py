"""Sonda de vazamento: os dois pipelines com o MESMO detector perfeito.

Se o detector acha a entidade e ela ainda sai em claro, o vazamento e das
regras de pos-processamento, nao do modelo. Roda sem rede e sem GLiNER.
Todos os nomes e numeros sao ficticios.

  python tools/sonda_vazamento.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from anonimizador_app.address_heuristic import AddressHeuristicEngine  # noqa: E402
from anonimizador_app.fidelity import (  # noqa: E402
    is_magistrate_or_citation,
    is_pfn_officer,
    is_precedent_party,
)
from anonimizador_app.gemini_detector import is_kept_term, is_too_generic  # noqa: E402
from anonimizador_app.header_heuristic import HeaderHeuristicEngine  # noqa: E402
from anonimizador_app.llm_discovery import Discovery, DiscoveryReport  # noqa: E402
from anonimizador_app.local_ner import CombinedEngine, LocalNerEngine  # noqa: E402
from anonimizador_app.pipeline import run_job  # noqa: E402
from anonimizador_app.pipeline_llm import run_llm_job  # noqa: E402
from anonimizador_app.roster import Mention  # noqa: E402
from anonimizador_app.target_list import is_common_person_name_span  # noqa: E402

DOC = """\
# EXECUÇÃO FISCAL

| EXEQUENTE | EXECUTADO |
|---|---|
| UNIÃO - FAZENDA NACIONAL | FAZENDA SANTA RITA AGROPECUARIA LTDA (CNPJ 11.222.333/0001-81) |

<!-- pagina-origem: 1 -->

## Evento 1 - Petição inicial

A UNIÃO (FAZENDA NACIONAL), por intermédio da PGFN, ajuíza execução fiscal
contra FAZENDA SANTA RITA AGROPECUARIA LTDA, conforme CDA anexa.

A PGFN requer a citação do sócio OLAVO RICARDO TEIXEIRA, administrador da executada.

Consta que MARLENE OLIVEIRA SAMPAIO, sócia, reside na Rua das Acácias, 120.

No Tema 1.209/STJ discute-se a responsabilidade; aqui, a empresa UNIÃO COMERCIAL DE GRÃOS LTDA
recebeu os bens em sucessão empresarial (art. 133 do CTN).

Em diligência, o oficial encontrou CARLOS PEREIRA LIMA no endereço da sede.

Valor da causa: R$ 1.234.567,89. Data: 12/03/2024. Pedido de SISBAJUD e RENAJUD.

## Evento 20 - Decisão

Defiro. Intime-se JOSÉ ANTÔNIO FERREIRA, representante legal, pela PGFN.
Ana Lu Costa, contadora da empresa, prestou declarações.
"""

# (tipo interno, texto, papel) — o que um detector perfeito devolveria.
TRUTH = [
    ("ORGANIZATION", "FAZENDA SANTA RITA AGROPECUARIA LTDA", "parte"),
    ("PERSON", "OLAVO RICARDO TEIXEIRA", "socio"),
    ("PERSON", "MARLENE OLIVEIRA SAMPAIO", "socio"),
    ("LOCATION", "Rua das Acácias, 120", "socio"),
    ("ORGANIZATION", "UNIÃO COMERCIAL DE GRÃOS LTDA", "terceiro"),
    ("PERSON", "CARLOS PEREIRA LIMA", "terceiro"),
    ("PERSON", "JOSÉ ANTÔNIO FERREIRA", "socio"),
    ("PERSON", "Ana Lu Costa", "terceiro"),
]
MUST_STAY = ["UNIÃO", "PGFN", "R$ 1.234.567,89", "12/03/2024", "SISBAJUD", "Tema 1.209/STJ",
             "art. 133 do CTN"]
_GLINER_LABEL = {"PERSON": "pessoa", "ORGANIZATION": "empresa privada", "LOCATION": "logradouro"}
_KIND = {"PERSON": "pessoa", "ORGANIZATION": "empresa", "LOCATION": "endereco"}


def perfect_gliner(chunk: str):
    return [
        {"entity_group": _GLINER_LABEL[kind], "word": value, "score": 0.99}
        for kind, value, _role in TRUTH
        if value.casefold() in chunk.casefold()
    ]


class PerfectLlm:
    def describe(self) -> str:
        return "LLM perfeito (simulado)"

    def discover(self, text: str) -> Discovery:
        mentions = []
        for kind, value, role in TRUTH:
            index = text.casefold().find(value.casefold())
            if index >= 0:
                surface = text[index : index + len(value)]
                mentions.append(
                    Mention(kind=_KIND[kind], text=surface, role=role, canonical=value,
                            start=index, end=index + len(value), source_text=surface)
                )
        return Discovery(mentions=mentions, report=DiscoveryReport(chunks=1, sent=1))


def why_dropped(kind: str, value: str) -> str:
    start = DOC.rfind(value)
    end = start + len(value)
    reasons = []
    if is_kept_term(value):
        reasons.append("KEEP_TERMS por prefixo")
    if kind == "PERSON" and is_magistrate_or_citation(DOC, start, end):
        reasons.append("magistrado/citacao")
    if kind == "PERSON" and is_pfn_officer(DOC, start, end):
        reasons.append("linha com PGFN/PFN")
    if kind == "PERSON" and is_common_person_name_span(value):
        reasons.append("nome so de particulas comuns")
    if kind == "ORGANIZATION" and is_precedent_party(DOC, start, end):
        reasons.append("janela de precedente (+-200)")
    if is_too_generic(kind, value):
        reasons.append("generico (marcador de orgao / palavra generica)")
    return ", ".join(reasons) or "-"


def report(title: str, text: str) -> int:
    leaked = [(kind, value) for kind, value, _role in TRUTH if value.casefold() in text.casefold()]
    print(f"\n== {title}: {len(leaked)} de {len(TRUTH)} vazaram ==")
    for kind, value in leaked:
        print(f"  VAZOU {kind:13s} {value:38s} <- {why_dropped(kind, value)}")
    missing = [item for item in MUST_STAY if item not in text]
    print("  estrutura: " + ("intacta" if not missing else "PERDEU " + ", ".join(missing)))
    return len(leaked)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "caso.md"
        source.write_text(DOC, encoding="utf-8")
        engine = CombinedEngine(
            [
                HeaderHeuristicEngine(),
                AddressHeuristicEngine(),
                LocalNerEngine(predict=perfect_gliner, use_cache=False),
            ]
        )
        current = run_job(
            [source],
            export_root=base / "atual" / "exportaveis",
            private_root=base / "atual" / "privados",
            contextual=engine,
            use_cache=False,
        )
        report(
            "Pipeline atual (GLiNER perfeito)",
            current.outcomes[0].markdown_path.read_text(encoding="utf-8"),
        )
        proposed = run_llm_job(
            [source],
            export_root=base / "llm" / "exportaveis",
            private_root=base / "llm" / "privados",
            discoverer=PerfectLlm(),
            use_cache=False,
        )
        report(
            "Pipeline LLM (LLM perfeito)",
            proposed.job.outcomes[0].markdown_path.read_text(encoding="utf-8"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
