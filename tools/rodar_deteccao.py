"""Roda a mesma pipeline do app e grava um lote (md, AUDITORIA, LEIA-ME, mapa)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from anonimizador_app.contextual import build_contextual_engine
from anonimizador_app.ner_cache import default_ner_cache_dir
from anonimizador_app.pipeline import default_export_dir, default_private_dir, run_job
from anonimizador_app.settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Anonimiza um arquivo com a pipeline do app (motor local + config)."
    )
    parser.add_argument("arquivo", help="PDF, DOCX, TXT ou MD de entrada.")
    parser.add_argument(
        "--config",
        help="config.json do app (padrao: saidas/privados/config.json).",
    )
    parser.add_argument(
        "--exportaveis",
        help="Pasta-raiz dos lotes exportaveis (padrao: saidas/exportaveis).",
    )
    parser.add_argument(
        "--privados",
        help="Pasta-raiz dos mapas e do cache (padrao: saidas/privados).",
    )
    parser.add_argument(
        "--sem-cache",
        action="store_true",
        help="Ignora o cache de extracao e reprocessa o arquivo.",
    )
    parser.add_argument(
        "--sem-cache-ner",
        action="store_true",
        help="Ignora o cache do NER local (GLiNER) e reprocessa o modelo.",
    )
    args = parser.parse_args(argv)

    source = Path(args.arquivo).expanduser()
    if not source.is_file():
        print(f"Arquivo nao encontrado: {source}", file=sys.stderr)
        return 2

    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    export_root = (
        Path(args.exportaveis).expanduser() if args.exportaveis else default_export_dir(ROOT)
    )
    config_src = Path(args.config).expanduser() if args.config else private_root
    settings = load_settings(config_src)
    ner_cache_dir = None if args.sem_cache_ner else default_ner_cache_dir(private_root)
    contextual = build_contextual_engine(
        settings,
        use_ner_cache=not args.sem_cache_ner,
        ner_cache_dir=ner_cache_dir,
    )

    started = time.perf_counter()
    job = run_job(
        [source],
        export_root=export_root,
        private_root=private_root,
        contextual=contextual,
        settings=settings,
        use_cache=not args.sem_cache,
    )
    elapsed = time.perf_counter() - started
    print(str(job.export_dir))
    print(f"{elapsed:.1f}s")
    if job.failed:
        for outcome in job.failed:
            print(f"ERRO {outcome.source.name}: {outcome.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
