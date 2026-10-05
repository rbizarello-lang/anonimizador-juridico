"""Aceite do pipeline unico (H5): o mesmo lote pelo motor local atual e pelo pipeline unico sem LLM.

Uso:
    python tools/comparar_pipelines.py <arquivo ou pasta> [...] [--privados DIR]

Roda os dois caminhos com o MESMO motor local (mesmas regras, mesmo cache de NER) e compara os
textos gerados, arquivo por arquivo:

- texto: linhas diferentes depois de trocar cada token pelo seu tipo ([PESSOA_3] -> [PESSOA]);
- agrupamento: token que, na mesma posicao, corresponde a mais de um token do outro caminho
  (duas entidades fundidas num caminho e separadas no outro).

Tudo fica na pasta privada: os textos dos dois caminhos e o ``DIFERENCAS.txt`` em
``saidas/privados/comparacao_.../`` (o ``DIFERENCAS.txt`` mostra as linhas que mudaram e pode ter nome em
claro) e os mapas em ``saidas/privados/comparacao_..._atual`` e ``_unico``. Os dois caminhos usam o cache de
extracao e de NER da pasta privada: o OCR e o GLiNER nao rodam duas vezes. Na tela, so nomes de arquivo e
contagens.
Saida 0 = os dois caminhos geraram o mesmo texto; 1 = ha diferenca (veja o DIFERENCAS.txt).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TOKEN = re.compile(r"\[([A-Z_]+?)_\d+(?:·G\d+)?\]")
SUFFIXES = (".pdf", ".docx", ".md", ".txt")


@dataclass
class FileComparison:
    name: str
    lines_a: int = 0
    lines_b: int = 0
    changed: int = 0
    regrouped: int = 0
    diff: list[str] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return self.changed == 0 and self.regrouped == 0


def _typed(line: str) -> str:
    return TOKEN.sub(r"[\1]", line)


def _regrouped(lines_a: list[str], lines_b: list[str]) -> int:
    """Tokens que, nas linhas de mesmo texto (por tipo), correspondem a mais de um token do outro lado."""

    partners_a: dict[str, set[str]] = defaultdict(set)
    partners_b: dict[str, set[str]] = defaultdict(set)
    matcher = difflib.SequenceMatcher(None, [_typed(line) for line in lines_a], [_typed(line) for line in lines_b])
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for line_a, line_b in zip(lines_a[a0:a1], lines_b[b0:b1]):
            for token_a, token_b in zip(TOKEN.finditer(line_a), TOKEN.finditer(line_b)):
                partners_a[token_a.group(0)].add(token_b.group(0))
                partners_b[token_b.group(0)].add(token_a.group(0))
    return sum(len(found) > 1 for found in partners_a.values()) + sum(
        len(found) > 1 for found in partners_b.values()
    )


def compare_texts(name: str, text_a: str, text_b: str) -> FileComparison:
    lines_a, lines_b = text_a.splitlines(), text_b.splitlines()
    result = FileComparison(name=name, lines_a=len(lines_a), lines_b=len(lines_b))
    typed_a, typed_b = [_typed(line) for line in lines_a], [_typed(line) for line in lines_b]
    result.diff = list(difflib.unified_diff(typed_a, typed_b, "atual", "unico", lineterm="", n=0))
    result.changed = sum(
        1 for line in result.diff if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )
    result.regrouped = _regrouped(lines_a, lines_b)
    return result


def compare_outcomes(pairs: list[tuple[Path, Path]]) -> list[FileComparison]:
    return [
        compare_texts(path_a.name, path_a.read_text(encoding="utf-8"), path_b.read_text(encoding="utf-8"))
        for path_a, path_b in pairs
    ]


def _sources(items: list[str]) -> list[Path]:
    found: list[Path] = []
    for item in items:
        path = Path(item).expanduser()
        if path.is_dir():
            found.extend(sorted(child for child in path.iterdir() if child.suffix.lower() in SUFFIXES))
        else:
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arquivos", nargs="+", help="PDFs (ou pasta com eles) do mesmo lote")
    parser.add_argument("--privados", help="pasta privada (padrao: saidas/privados)")
    args = parser.parse_args(argv)

    from anonimizador_app.contextual import build_contextual_engine
    from anonimizador_app.ner_cache import default_ner_cache_dir
    from anonimizador_app.pipeline import default_private_dir, run_job
    from anonimizador_app.pipeline_llm import run_local_job
    from anonimizador_app.settings import load_settings

    sources = _sources(args.arquivos)
    if not sources:
        print("Nenhum arquivo encontrado.")
        return 2
    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    settings = load_settings(private_root)
    engine = build_contextual_engine(
        settings, use_ner_cache=True, ner_cache_dir=default_ner_cache_dir(private_root)
    )
    stamp = f"comparacao_{datetime.now():%Y%m%d_%H%M%S}"
    work = private_root / stamp
    print(f"{len(sources)} arquivo(s). Textos e diferencas em: {work}")

    print("Motor local atual...")
    # Mesma pasta privada nos dois caminhos: o cache de extracao e o de NER (o OCR e o GLiNER) servem aos
    # dois. Os mapas ficam em privados/<stamp>_atual e _unico; os textos, dentro da pasta da comparacao.
    current = run_job(
        sources,
        export_root=work,
        private_root=private_root,
        job_id=f"{stamp}_atual",
        contextual=engine,
        settings=settings,
    )
    print("Pipeline unico sem LLM...")
    unified = run_local_job(
        sources,
        export_root=work,
        private_root=private_root,
        job_id=f"{stamp}_unico",
        fallback_engine=engine,
        settings=settings,
    ).job

    pairs: list[tuple[Path, Path]] = []
    missing: list[str] = []
    by_source = {outcome.source: outcome for outcome in unified.outcomes}
    for outcome in current.outcomes:
        other = by_source.get(outcome.source)
        if outcome.ok and other is not None and other.ok:
            pairs.append((outcome.markdown_path, other.markdown_path))
        else:
            missing.append(outcome.source.name)
    results = compare_outcomes(pairs)

    report = [
        "DIFERENCAS ENTRE O MOTOR LOCAL ATUAL E O PIPELINE UNICO (H5)",
        "Contem trechos dos textos: pode ter nome em claro. NAO ENVIAR.",
        "Tokens comparados pelo tipo ([PESSOA_3] -> [PESSOA]).",
        "",
    ]
    print(f"{'arquivo':48s} {'linhas':>7s} {'diferentes':>11s} {'reagrupados':>12s}")
    for item in results:
        print(f"{item.name[:48]:48s} {item.lines_a:7d} {item.changed:11d} {item.regrouped:12d}")
        report += [f"## {item.name}: {item.changed} linha(s), {item.regrouped} token(s) reagrupado(s)", *item.diff, ""]
    for name in missing:
        print(f"{name[:48]:48s}  sem comparacao: falhou num dos caminhos")
        report.append(f"## {name}: falhou num dos caminhos (ver AUDITORIA de cada lote)")
    for label, job in (("atual", current), ("unico", unified)):
        if not job.export_allowed:
            print(f"Exportacao bloqueada no caminho {label}: {job.export_block_reason}")
    work.mkdir(parents=True, exist_ok=True)
    (work / "DIFERENCAS.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    same = not missing and all(item.same for item in results)
    print("RESULTADO: " + ("iguais" if same else "HA DIFERENCA (veja DIFERENCAS.txt)"))
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
