"""Anonimiza com o pipeline LLM (LangExtract + Gemini/Vertex/Ollama) e grava um lote.

Exemplos:

  # Gemini API paga (chave em GEMINI_API_KEY; conta gratuita treina o modelo):
  python tools/anonimizar_llm.py autos.pdf

  # Vertex AI em Sao Paulo (credencial do gcloud; contrato do Google Cloud):
  python tools/anonimizar_llm.py autos.pdf --provedor vertex --projeto meu-projeto

  # 100% local (lento em CPU; bom para peca curta):
  python tools/anonimizar_llm.py peca.pdf --provedor ollama --modelo gemma3:12b

  # Depois de revisar o elenco (trocar "acao" de uma entrada):
  python tools/anonimizar_llm.py autos.pdf --elenco saidas/privados/lote_.../elenco.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from anonimizador_app.llm_discovery import (  # noqa: E402
    DEFAULT_LOCATION,
    DEFAULT_MODEL,
    LangExtractDiscoverer,
    LlmConfig,
    LlmConfigError,
)
from anonimizador_app.pipeline import default_export_dir, default_private_dir  # noqa: E402
from anonimizador_app.pipeline_llm import LLM_CACHE_SUBDIR, run_llm_job  # noqa: E402
from anonimizador_app.settings import load_settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Anonimiza PDF/DOCX/TXT/MD: IDs locais, elenco por LLM, troca local."
    )
    parser.add_argument("arquivos", nargs="+", help="Arquivos do mesmo lote (mesmos tokens).")
    parser.add_argument(
        "--provedor",
        choices=("gemini", "vertex", "ollama"),
        default="gemini",
        help="gemini = Gemini API paga; vertex = Vertex AI; ollama = modelo local.",
    )
    parser.add_argument("--modelo", default=DEFAULT_MODEL, help=f"Padrao: {DEFAULT_MODEL}.")
    parser.add_argument("--projeto", default="", help="Projeto do Google Cloud (Vertex AI).")
    parser.add_argument(
        "--regiao", default=DEFAULT_LOCATION, help=f"Regiao do Vertex AI (padrao {DEFAULT_LOCATION})."
    )
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--paralelo", type=int, default=16, help="Chamadas simultaneas ao LLM.")
    parser.add_argument("--trecho", type=int, default=4000, help="Caracteres por chamada.")
    parser.add_argument(
        "--verificacao",
        type=int,
        default=1,
        help="Rodadas em que o LLM le o texto ja tokenizado (0 desliga).",
    )
    parser.add_argument("--elenco", help="elenco.json revisado de um lote anterior.")
    parser.add_argument("--config", help="config.json do app (lista de alvos, exclusoes).")
    parser.add_argument("--exportaveis", help="Raiz dos lotes exportaveis.")
    parser.add_argument("--privados", help="Raiz dos mapas, elencos e caches.")
    parser.add_argument("--sem-cache", action="store_true", help="Relê o PDF e reenvia ao LLM.")
    parser.add_argument("--sem-legenda", action="store_true", help="Nao grava ELENCO.md.")
    args = parser.parse_args(argv)

    sources = [Path(item).expanduser() for item in args.arquivos]
    missing = [str(item) for item in sources if not item.is_file()]
    if missing:
        print("Arquivo nao encontrado: " + ", ".join(missing), file=sys.stderr)
        return 2

    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    export_root = (
        Path(args.exportaveis).expanduser() if args.exportaveis else default_export_dir(ROOT)
    )
    settings = load_settings(Path(args.config).expanduser() if args.config else private_root)
    config = LlmConfig(
        provider=args.provedor,
        model_id=args.modelo,
        project=args.projeto,
        location=args.regiao,
        model_url=args.ollama_url,
        max_workers=max(1, args.paralelo),
        chunk_chars=max(500, args.trecho),
    )
    discoverer = LangExtractDiscoverer(
        config,
        cache_dir=None if args.sem_cache else private_root / LLM_CACHE_SUBDIR,
    )

    started = time.perf_counter()
    try:
        result = run_llm_job(
            sources,
            export_root=export_root,
            private_root=private_root,
            discoverer=discoverer,
            settings=settings,
            roster_path=Path(args.elenco).expanduser() if args.elenco else None,
            verification_rounds=max(0, args.verificacao),
            use_cache=not args.sem_cache,
            write_legend=not args.sem_legenda,
            on_progress=lambda message, _fraction: print(message, flush=True),
        )
    except LlmConfigError as exc:
        print(f"Configuracao: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started

    job = result.job
    print(f"Textos: {job.export_dir}")
    print(f"Mapa e elenco (privados): {job.map_path.parent}")
    print(f"Tempo: {elapsed:.1f}s")
    for name, report in result.reports.items():
        found = report.discovery
        print(
            f"  {name}: {found.chunks} trechos ({found.cached} do cache), "
            f"{len(found.failed)} falha(s) na descoberta, "
            f"+{report.new_in_verification} na verificacao"
        )
    for outcome in job.failed:
        print(f"ERRO {outcome.source.name}: {outcome.error}", file=sys.stderr)
    if not job.export_allowed:
        print(f"NAO ENVIE AINDA: {job.export_block_reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
