"""Reaplica o pipeline (higiene, papel publico, juizo do lote, troca, AUDITORIA) sobre as respostas que um
modelo local ja deu, SEM chamar o modelo de novo: le o cache de uma medicao do ``comparar_motores`` e gera o
lote exportavel pelo canal ``ollama-local`` em modo sem rede.

Por que existe: as medicoes do ``comparar_motores`` anteriores ao H9-c mandavam ao modelo o texto CRU e
gravavam o cache em ``<privados>/cache_llm_local``; o pipeline consulta o cache com o texto ja MASCARADO (IDs
trocados por token) e em ``<privados>/cache_llm``. Como a chave do cache leva o texto do trecho, uma coisa nao
enxerga a outra. Esta ferramenta descobre sobre o texto cru (onde o cache esta) e leva cada mencao para a
posicao correspondente no texto mascarado; todo o resto e o pipeline do app, intocado.

Nada e enviado: nenhum modelo e criado, e a rodada roda dentro do bloqueio de rede do modo sem rede (a
AUDITORIA registra as tentativas barradas; tem de dar 0). Trecho sem resposta no cache cai no piso local,
como numa rodada normal, e a saida da CLI diz quantos foram.

Uso (no PC, a partir da raiz do app; so contagens saem na tela):

    python tools\\reaplicar_cache.py <PDFs do lote> --modelo gemma4:e4b
    python tools\\reaplicar_cache.py <PDFs do lote> --modelo qwen3:4b --cache saidas\\privados\\cache_llm_local

Depois, as reguas de sempre: ``tools\\medir_vazamento.py <gabarito> <lote A> <lote B>`` e
``tools\\medir_estrutura.py <PDFs> <lote>``. O lote sai em ``saidas\\exportaveis\\lote_<data>``.

Aproximacao assumida: o modelo leu o texto cru, nao o mascarado; mencao que encosta num ID mascarado e
descartada (fica sem ancora). Para o cache gravado ja pelo ``comparar_motores`` do H9-c (texto mascarado, pasta
``cache_llm``) esta ferramenta nao e necessaria: basta ``anonimizar_llm.py --canal ollama-local --sem-rede``.
"""

from __future__ import annotations

import argparse
import sys
from bisect import bisect_right
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import anonimizar_llm as cli  # noqa: E402
from anonimizador_app import pipeline_llm  # noqa: E402
from anonimizador_app.llm_discovery import LangExtractDiscoverer  # noqa: E402
from anonimizador_app.pipeline import default_private_dir  # noqa: E402
from anonimizador_app.pipeline_llm import OffsetMap  # noqa: E402

RAW_CACHE_SUBDIR = "cache_llm_local"


def original_to_masked(offsets: OffsetMap, start: int, end: int) -> tuple[int, int] | None:
    """Posicao no texto original -> posicao no texto mascarado. ``None`` se o trecho encosta num ID que virou
    token (o inverso de ``OffsetMap.to_original``)."""

    ends = offsets.orig_ends
    first = max(bisect_right(ends, start) - 1, 0)
    last = min(len(ends), bisect_right(ends, end) + 1)
    for index in range(first, last):
        previous_shift = (offsets.orig_ends[index - 1] - offsets.new_ends[index - 1]) if index > 0 else 0
        orig_start = offsets.new_starts[index] + previous_shift
        if orig_start < end and start < offsets.orig_ends[index]:
            return None
    before = bisect_right(ends, start) - 1
    shift = (offsets.orig_ends[before] - offsets.new_ends[before]) if before >= 0 else 0
    return start - shift, end - shift


def clamp_to_masked(offsets: OffsetMap, start: int, end: int) -> tuple[int, int]:
    """Faixa do texto original -> faixa do mascarado, sem recusar (para as faixas de falha do relatorio)."""

    def one(position: int) -> int:
        index = bisect_right(offsets.orig_ends, position) - 1
        shift = (offsets.orig_ends[index] - offsets.new_ends[index]) if index >= 0 else 0
        return position - shift

    first = one(start)
    return first, max(one(end), first + 1)


class RawCacheDiscoverer(LangExtractDiscoverer):
    """Descobridor sem rede que consulta o cache com o texto CRU e devolve mencoes no texto MASCARADO."""

    records: dict[str, tuple[str, OffsetMap]] = {}

    def discover(self, text: str):
        raw, offsets = self.records[text]
        discovery = super().discover(raw)
        moved = []
        for mention in discovery.mentions:
            if mention.start >= 0:
                target = original_to_masked(offsets, mention.start, mention.end)
                mention = (
                    replace(mention, start=target[0], end=target[1])
                    if target
                    else replace(mention, start=-1, end=-1)
                )
            moved.append(mention)
        discovery.mentions = moved
        discovery.report.failed = [clamp_to_masked(offsets, a, b) for a, b in discovery.report.failed]
        discovery.report.empty = [clamp_to_masked(offsets, a, b) for a, b in discovery.report.empty]
        return discovery


def install(raw_cache_dir: Path) -> None:
    """Liga a ferramenta ao app: guarda o par (texto cru, offsets) de cada arquivo e troca o descobridor
    sem rede pelo que le o cache cru. Vale so neste processo."""

    original_extract = pipeline_llm._extract_and_mask

    def extract_and_record(*args, **kwargs):
        result = original_extract(*args, **kwargs)
        _cached, text, _stats, _ids, masked, offsets = result
        RawCacheDiscoverer.records[masked.text] = (text, offsets)
        return result

    pipeline_llm._extract_and_mask = extract_and_record  # type: ignore[assignment]

    original_build = cli.build_offline_discoverer

    def build(channel, *, cache_dir, model_id, chunk_chars=None):
        base = original_build(channel, cache_dir=cache_dir, model_id=model_id, chunk_chars=chunk_chars)
        return RawCacheDiscoverer(base.config, cache_dir=raw_cache_dir, offline=True)

    cli.build_offline_discoverer = build  # type: ignore[assignment]


def cli_arguments(args: argparse.Namespace) -> list[str]:
    """Os argumentos que vao para ``anonimizar_llm.main``: canal local, sem rede, sem verificacao."""

    forwarded = [*args.arquivos, "--canal", "ollama-local", "--modelo", args.modelo, "--sem-rede", "--verificacao", "0"]
    for name in ("privados", "exportaveis", "config"):
        value = getattr(args, name)
        if value:
            forwarded.extend([f"--{name}", value])
    if args.trecho:
        forwarded.extend(["--trecho", str(args.trecho)])
    if args.sem_piso_local:
        forwarded.append("--sem-piso-local")
    return forwarded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arquivos", nargs="+", help="PDFs do lote, os mesmos da medicao.")
    parser.add_argument("--modelo", required=True, help="Modelo da medicao (ex.: qwen3:4b, gemma4:e4b); o cache e por modelo.")
    parser.add_argument("--cache", help=f"Pasta do cache cru (padrao: <privados>/{RAW_CACHE_SUBDIR}).")
    parser.add_argument("--privados", help="Raiz privada (padrao: a do app).")
    parser.add_argument("--exportaveis", help="Raiz dos exportaveis (padrao: a do app).")
    parser.add_argument("--config", help="Pasta ou arquivo do config.json (padrao: a raiz privada).")
    parser.add_argument("--trecho", type=int, help="Caracteres por trecho da medicao (padrao: o do canal, 2000).")
    parser.add_argument("--sem-piso-local", action="store_true", help="Sem o motor local em todas as folhas.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    raw_cache = Path(args.cache).expanduser() if args.cache else private_root / RAW_CACHE_SUBDIR
    if not raw_cache.is_dir():
        print(f"Cache cru nao encontrado: {raw_cache}", file=sys.stderr)
        return 2
    install(raw_cache)
    print(f"Reaplicando o cache de {args.modelo} ({raw_cache.name}); nenhum modelo sera chamado.", flush=True)
    return cli.main(cli_arguments(args))


if __name__ == "__main__":
    raise SystemExit(main())
