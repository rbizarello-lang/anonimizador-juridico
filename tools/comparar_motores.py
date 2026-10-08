"""Compara detectores de nomes (NER) em CPU contra o gabarito revisado do processo. Sem rede; so contagens na tela.

Uso:
    python tools/comparar_motores.py <gabarito.json> --pdfs <pasta dos PDFs> [--motor NOME ...] [--sombra]
        [--limiar 0.75] [--privados DIR] [--textos DIR]

Motores (``--motor``, repetivel; padrao: gliner-atual):
    gliner-atual   o GLiNER do app, na revisao fixada (urchade/gliner_multi-v2.1)
    gliner-large   urchade/gliner_large-v2.1 (baixar antes: tools/instalar_modelo_ner.py --extra gliner-large)
    lener          BERT treinado no LeNER-Br (pierreguillou/ner-bert-base-cased-pt-lenerbr; --extra lener)
    app-local      o motor local inteiro do app (regras + lista de alvos + GLiNER), como linha de base
    app-local+lener  o mesmo, com o LeNER somado ao GLiNER (uniao, mesmos filtros): o fator em avaliacao

``--sombra``: cada motor roda tambem na copia em Title Case dos trechos em CAIXA ALTA (passada-sombra de
caixa do GOVERNANCA): mesmo modelo, mesmo limiar, um fator so.

Para cada motor, contra as entidades do gabarito com ``acao = tokenizar``: quantas ocorrencias no texto o
detector cobriu (recall por ocorrencia), quantas entidades ele achou ao menos uma vez e quantas cobriu por
inteiro. Contra as de ``acao = manter`` (juiz, procurador, orgao, banco do SISBAJUD): quantas ocorrencias o
detector cru marcou (o juizo do lote do app ainda filtra parte disso). E o que o detector marcou fora do
gabarito: possivel falso positivo ou lacuna do gabarito, com os termos protegidos (Uniao, PGFN...) a parte.

Saida: tabela na tela (so numeros) e ``saidas/privados/gabaritos/comparacao_motores_<data>.json`` com o
detalhe por entidade (quais ficaram sem cobertura em cada motor). O JSON tem nomes: NAO ENVIAR.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.gemini_detector import is_kept_term  # noqa: E402
from anonimizador_app.local_ner import GLINER_LABELS, GLINER_MODEL, GLINER_REVISION, hub_offline, shadow_case_copy  # noqa: E402
from anonimizador_app.propagation import fold_with_index  # noqa: E402
from anonimizador_juridico.pseudonymizer import fold_lexical  # noqa: E402

Span = tuple[int, int, str]  # inicio, fim, tipo (PERSON | ORGANIZATION | LOCATION)
Detector = Callable[[str], list[Span]]

GLINER_LARGE_MODEL = "urchade/gliner_large-v2.1"
LENER_MODEL = "pierreguillou/ner-bert-base-cased-pt-lenerbr"
_LABELS = {
    "PESSOA": "PERSON", "PERSON": "PERSON", "PER": "PERSON",
    "EMPRESA PRIVADA": "ORGANIZATION", "ORGANIZACAO": "ORGANIZATION", "ORGANIZAÇÃO": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION", "ORG": "ORGANIZATION",
    "LOGRADOURO": "LOCATION", "LOCAL": "LOCATION", "LOCATION": "LOCATION", "LOC": "LOCATION",
}
_CHUNK = 1200


def _chunks(text: str, size: int = _CHUNK) -> list[tuple[int, str]]:
    """Trechos de ate ``size`` caracteres, cortados em quebra de linha; (deslocamento, texto)."""

    pieces: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut > start:
                end = cut + 1
        pieces.append((start, text[start:end]))
        start = end
    return pieces


def _label(raw: str) -> str | None:
    return _LABELS.get(str(raw or "").split("-")[-1].strip().upper())


def gliner_detector(model_id: str, revision: str | None, threshold: float) -> Detector:
    from gliner import GLiNER

    offline = hub_offline()
    kwargs = {"local_files_only": offline}
    if revision:
        kwargs["revision"] = revision
    model = GLiNER.from_pretrained(model_id, **kwargs)

    def detect(text: str) -> list[Span]:
        spans: list[Span] = []
        for offset, piece in _chunks(text):
            for item in model.predict_entities(piece, list(GLINER_LABELS), threshold=threshold):
                kind = _label(item.get("label", ""))
                if kind:
                    spans.append((offset + int(item["start"]), offset + int(item["end"]), kind))
        return spans

    return detect


def lener_detector(model_id: str, threshold: float) -> Detector:
    from transformers import AutoTokenizer, pipeline

    # Offline vem de HF_HUB_OFFLINE (padrao do app): com ela ligada o pipeline ja repassa local_files_only ao
    # AutoConfig, e passar de novo em model_kwargs da TypeError ("multiple values").
    # O tokenizador do LeNER nao declara model_max_length: sem o teto de 512 do BERT, um trecho longo passa
    # inteiro e o modelo quebra (RuntimeError de tamanho de tensor). Com o teto, ``stride`` divide o trecho
    # em janelas sobrepostas e o pipeline junta as entidades.
    tokenizer = AutoTokenizer.from_pretrained(model_id, model_max_length=512)
    tagger = pipeline(
        "token-classification",
        model=model_id,
        tokenizer=tokenizer,
        aggregation_strategy="simple",
        stride=64,
    )

    def detect(text: str) -> list[Span]:
        spans: list[Span] = []
        for offset, piece in _chunks(text):
            for item in tagger(piece):
                kind = _label(item.get("entity_group", ""))
                if kind and float(item.get("score", 0.0)) >= threshold:
                    spans.append((offset + int(item["start"]), offset + int(item["end"]), kind))
        return spans

    return detect


def app_local_detector(private_root: Path, extra_ner: str = "") -> Detector:
    from dataclasses import replace

    from anonimizador_app.contextual import build_contextual_engine
    from anonimizador_app.ner_cache import default_ner_cache_dir
    from anonimizador_app.settings import load_settings

    settings = replace(load_settings(private_root), extra_ner=extra_ner)
    engine = build_contextual_engine(settings, use_ner_cache=True, ner_cache_dir=default_ner_cache_dir(private_root))
    if engine is None:
        raise RuntimeError("motor local indisponivel")

    def detect(text: str) -> list[Span]:
        return [(span.start, span.end, span.entity_type) for span in engine.detect(text) if span.entity_type in {"PERSON", "ORGANIZATION", "LOCATION"}]

    return detect


ENGINES: dict[str, Callable[[float, Path], Detector]] = {
    "gliner-atual": lambda threshold, _root: gliner_detector(GLINER_MODEL, GLINER_REVISION, threshold),
    "gliner-large": lambda threshold, _root: gliner_detector(GLINER_LARGE_MODEL, None, threshold),
    "lener": lambda threshold, _root: lener_detector(LENER_MODEL, threshold),
    "app-local": lambda _threshold, root: app_local_detector(root),
    # O motor local inteiro com o LeNER somado ao GLiNER (H8): o fator a medir.
    "app-local+lener": lambda _threshold, root: app_local_detector(root, extra_ner="lener"),
}


def with_shadow(detect: Detector) -> Detector:
    """Roda no original e na copia em Title Case dos trechos em CAIXA ALTA; as posicoes sao as mesmas."""

    def both(text: str) -> list[Span]:
        seen = set(detect(text))
        seen.update(detect(shadow_case_copy(text)))
        return sorted(seen)

    return both


@dataclass
class Occurrence:
    text_index: int
    start: int
    end: int


@dataclass
class Target:
    rotulo: str
    tipo: str
    acao: str
    categoria: str
    patterns: list[str]
    occurrences: list[Occurrence] = field(default_factory=list)


def load_gabarito(path: Path) -> list[Target]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for item in payload.get("entidades", []):
        if str(item.get("tipo") or "") not in {"pessoa", "empresa", "endereco"}:
            continue
        targets.append(Target(
            rotulo=str(item.get("rotulo") or ""),
            tipo=str(item.get("tipo") or ""),
            acao=str(item.get("acao") or "tokenizar"),
            categoria=str(item.get("categoria") or ""),
            patterns=[str(p) for p in item.get("padroes") or [] if str(p).strip()],
        ))
    return targets


def _find_all(folded: str, needle: str) -> list[tuple[int, int]]:
    hits = []
    start = folded.find(needle)
    while start >= 0:
        end = start + len(needle)
        before = folded[start - 1] if start > 0 else " "
        after = folded[end] if end < len(folded) else " "
        if not before.isalnum() and not after.isalnum():
            hits.append((start, end))
        start = folded.find(needle, start + 1)
    return hits


def locate(targets: Sequence[Target], texts: Sequence[str]) -> None:
    folded = [fold_with_index(text) for text in texts]
    for target in targets:
        needles = {fold_lexical(p) for p in target.patterns if len(fold_lexical(p)) >= 3}
        for index, fold in enumerate(folded):
            seen = set()
            for needle in needles:
                for fstart, fend in _find_all(fold.text, needle):
                    start, end = fold.to_original(fstart, fend)
                    if (start, end) not in seen:
                        seen.add((start, end))
                        target.occurrences.append(Occurrence(index, start, end))


def _covered(occurrence: Occurrence, spans: Sequence[Span]) -> bool:
    length = occurrence.end - occurrence.start
    for start, end, _kind in spans:
        overlap = min(end, occurrence.end) - max(start, occurrence.start)
        if overlap > 0 and overlap >= 0.5 * length:
            return True
    return False


def evaluate(name: str, detect: Detector, targets: Sequence[Target], texts: Sequence[str]) -> dict:
    started = time.perf_counter()
    spans_by_text = [detect(text) for text in texts]
    elapsed = time.perf_counter() - started

    tokenize = [t for t in targets if t.acao == "tokenizar" and t.occurrences]
    keep = [t for t in targets if t.acao != "tokenizar" and t.occurrences]
    occ_total = occ_covered = 0
    ent_any = ent_full = 0
    misses: list[dict] = []
    for target in tokenize:
        covered = [_covered(o, spans_by_text[o.text_index]) for o in target.occurrences]
        occ_total += len(covered)
        occ_covered += sum(covered)
        ent_any += any(covered)
        ent_full += all(covered)
        if not all(covered):
            misses.append({"rotulo": target.rotulo, "tipo": target.tipo, "nome": target.patterns[0],
                           "ocorrencias": len(covered), "cobertas": sum(covered)})
    keep_hits = sum(_covered(o, spans_by_text[o.text_index]) for t in keep for o in t.occurrences)
    keep_total = sum(len(t.occurrences) for t in keep)

    # Spans fora do gabarito: nao encostam em ocorrencia nenhuma (de qualquer acao).
    by_text: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for target in targets:
        for o in target.occurrences:
            by_text[o.text_index].append((o.start, o.end))
    outside: Counter[str] = Counter()
    protected = 0
    for index, spans in enumerate(spans_by_text):
        taken = by_text[index]
        for start, end, kind in spans:
            if any(s < end and start < e for s, e in taken):
                continue
            outside[kind] += 1
            if is_kept_term(texts[index][start:end]):
                protected += 1
    return {
        "motor": name,
        "segundos": round(elapsed, 1),
        "spans": sum(len(s) for s in spans_by_text),
        "tokenizar": {
            "entidades": len(tokenize), "achou_alguma_vez": ent_any, "cobriu_todas": ent_full,
            "ocorrencias": occ_total, "cobertas": occ_covered,
            "recall_ocorrencias": round(occ_covered / occ_total, 3) if occ_total else None,
        },
        "manter": {"entidades": len(keep), "ocorrencias": keep_total, "marcadas_pelo_detector": keep_hits},
        "fora_do_gabarito": dict(outside),
        "termos_protegidos_atingidos": protected,
        "sem_cobertura": misses,
    }


def table(results: Sequence[dict]) -> str:
    head = f"{'motor':22s} {'recall oc.':>10s} {'ent. achou':>10s} {'ent. toda':>9s} {'manter marc.':>12s} {'fora gab.':>9s} {'protegidos':>10s} {'seg':>6s}"
    lines = [head]
    for r in results:
        t, k = r["tokenizar"], r["manter"]
        recall = f"{t['recall_ocorrencias']:.1%}" if t["recall_ocorrencias"] is not None else "-"
        lines.append(
            f"{r['motor'][:22]:22s} {recall:>10s} {t['achou_alguma_vez']:>4d}/{t['entidades']:<5d} "
            f"{t['cobriu_todas']:>4d}/{t['entidades']:<4d} {k['marcadas_pelo_detector']:>5d}/{k['ocorrencias']:<6d} "
            f"{sum(r['fora_do_gabarito'].values()):>9d} {r['termos_protegidos_atingidos']:>10d} {r['segundos']:>6.1f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None, engines: dict[str, Callable[[float, Path], Detector]] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gabarito")
    parser.add_argument("--pdfs", help="pasta dos PDFs (textos do cache privado)")
    parser.add_argument("--textos", help="pasta com os textos extraidos (.md), em vez de --pdfs")
    parser.add_argument("--privados")
    parser.add_argument("--motor", action="append", help="motores a comparar (repetivel)")
    parser.add_argument("--sombra", action="store_true", help="tambem a passada-sombra de caixa")
    parser.add_argument("--limiar", type=float, default=0.75)
    args = parser.parse_args(argv)
    registry = engines or ENGINES

    from anonimizador_app.pipeline import default_private_dir

    sys.path.insert(0, str(ROOT / "tools"))
    import montar_gabarito  # noqa: E402  (mesma leitura de textos do cache)

    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    pdf_dir = Path(args.pdfs).expanduser() if args.pdfs else None
    text_dir = Path(args.textos).expanduser() if args.textos else None
    texts = [text for _name, text in montar_gabarito.load_texts(private_root, pdf_dir, text_dir)]
    if not texts:
        print("Nenhum texto extraido encontrado (--pdfs ou --textos).", file=sys.stderr)
        return 2
    targets = load_gabarito(Path(args.gabarito).expanduser())
    locate(targets, texts)
    names = args.motor or ["gliner-atual"]
    unknown = [n for n in names if n not in registry]
    if unknown:
        print("Motor desconhecido: " + ", ".join(unknown) + ". Opcoes: " + ", ".join(registry), file=sys.stderr)
        return 2

    results = []
    for name in names:
        try:
            detect = registry[name](args.limiar, private_root)
        except Exception as exc:  # noqa: BLE001 - modelo ausente nao derruba os outros
            print(f"{name}: nao carregou ({type(exc).__name__}); baixe com tools/instalar_modelo_ner.py --extra", file=sys.stderr)
            continue
        runs = [(name, detect)] + ([(f"{name}+sombra", with_shadow(detect))] if args.sombra else [])
        for label, runner in runs:
            try:
                results.append(evaluate(label, runner, targets, texts))
            except Exception as exc:  # noqa: BLE001 - um motor que quebra no meio nao apaga os outros
                print(f"{label}: falhou ao rodar ({type(exc).__name__})", file=sys.stderr)
    if not results:
        return 1
    print(f"Gabarito: {len([t for t in targets if t.acao == 'tokenizar'])} entidade(s) a tokenizar, "
          f"{len([t for t in targets if t.acao != 'tokenizar'])} a manter; textos: {len(texts)}; limiar {args.limiar}")
    print(table(results))
    out = private_root / "gabaritos" / f"comparacao_motores_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"aviso": "CONTEM NOMES: NAO ENVIAR", "resultados": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Detalhe por entidade (privado, nao enviar): {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
