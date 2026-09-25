"""Smoke: carrega o motor local real (GLiNER ou spaCy PT) no corpus sintetico."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.local_ner import LocalNerEngine, _ACTIVE_BACKEND, load_local_predict
from anonimizador_app.pipeline import run_job
from anonimizador_app.settings import AppSettings


def main() -> int:
    load_local_predict()
    print("backend", _ACTIVE_BACKEND)
    engine = LocalNerEngine()
    corpus = ROOT / "tests" / "corpus" / "caso_001_processo_sintetico.md"
    job = run_job(
        [corpus],
        export_root=ROOT / "saidas" / "exportaveis",
        private_root=ROOT / "saidas" / "privados",
        job_id="lote_smoke_local",
        contextual=engine,
        settings=AppSettings(local_ner_enabled=True),
    )
    outcome = job.outcomes[0]
    if not outcome.ok or outcome.markdown_path is None:
        print("FALHA", outcome.error)
        return 1
    markdown = outcome.markdown_path.read_text(encoding="utf-8")
    print("export_allowed", job.export_allowed)
    print("counts", outcome.entity_counts)
    print("helena_gone", "Helena Ficticia da Silva" not in markdown)
    print("horizonte_gone", "Horizonte Ficticio Ltda." not in markdown)
    print("uniao", "Uniao" in markdown)
    print("sisbajud", "SISBAJUD" in markdown)
    print("---")
    print(markdown[:1600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
