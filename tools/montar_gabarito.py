"""Monta o gabarito de um processo a partir da UNIAO dos mapas de varios lotes, pre-classificado para o
Operador revisar. Nada sai da pasta privada; na tela, so contagens.

Uso:
    python tools/montar_gabarito.py <pasta privada do lote 1> [<lote 2> ...] --pdfs <pasta dos PDFs>
        [--privados DIR] [--nome gabarito_xxx]

Por que a uniao: um gabarito feito so com o que UM motor achou nunca mostra o que esse motor deixou
passar. Juntando os mapas de varios motores (local, LLM...), cada entidade recebe os motores que a
acharam; a que so um achou e suspeita (ganho dele, ou falso positivo) e vai para revisao. Uma varredura
por padroes simples ainda procura, no texto extraido, nomes que NENHUM motor pegou.

Para cada entidade, o texto extraido (cache privado) da o papel aparente pelo contexto, com as mesmas
regras do motor local: cargo colado ao nome (juiz, procurador, servidor) -> agente publico, fica em claro;
empresa em posicao de instituicao do SISBAJUD -> fica em claro; rua com nome de pessoa -> endereco; termo
protegido (Uniao, PGFN...) -> orgao, fica em claro. O resto e parte, socio ou terceiro: troca.

Saida (em ``saidas/privados/gabaritos``, NAO ENVIAR): ``<nome>.json`` no formato que ``medir_vazamento.py``
le (``entidades`` com categoria, rotulo e padroes; ``ids`` com valor), com campos extras ``tipo``, ``papel``,
``acao``, ``certeza``, ``ocorrencias``, ``motores`` e ``revisar``; e ``<nome>.REVISAO.md``, a lista para ler,
com os itens a revisar primeiro. O Operador corrige ``acao`` (tokenizar/manter) e apaga o que nao for
entidade; depois o arquivo e o padrao para medir qualquer motor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.batch_propagation import _financial_position, _public_role, _street_position  # noqa: E402
from anonimizador_app.gemini_detector import is_kept_term  # noqa: E402
from anonimizador_app.propagation import fold_with_index  # noqa: E402
from anonimizador_app.roster import KEEP, TOKENIZE, RosterEntry, entity_core, normalize_role  # noqa: E402
from anonimizador_juridico.pseudonymizer import fold_lexical  # noqa: E402

NAME_TYPES = {"PERSON": "pessoa", "ORGANIZATION": "empresa", "LOCATION": "endereco"}
# Papel inferido -> categoria do gabarito (a de ``medir_vazamento``: AGENTE-PUBLICO e esperado em claro).
KEEP_ROLES = {
    "agente_publico": "AGENTE-PUBLICO",
    "orgao_publico": "ORGAO-PUBLICO",
    "instituicao_financeira": "INSTITUICAO-FINANCEIRA",
    "autor_citado": "AUTOR-CITADO",
}
_PUBLIC_ORG = re.compile(
    r"(?i)\b(uniao|fazenda nacional|pgfn|procuradoria|ministerio|secretaria|receita federal|tribunal|"
    r"vara federal|justica federal|juizo|inss|banco central|conselho|prefeitura|estado d[eo]|municipio d[eo]|"
    r"advocacia[- ]geral|defensoria|ordem dos advogados)\b"
)
# Pistas de papel perto da ocorrencia (ate 60 caracteres antes), para o voto de papel.
_CUES = {
    "socio": re.compile(r"(?i)s[oó]ci[oa]s?|administrador[a]?|representad[oa] por|quotista|diretor[a]? d[ae]"),
    "parte": re.compile(r"(?i)executad[oa]s?|devedor[a]?|r[eé]u|r[eé]\b|embargante|requerid[oa]|contribuinte|autor[a]?\b"),
    "advogado": re.compile(r"(?i)advogad[oa]|oab\b|patron[oa]"),
    "terceiro": re.compile(r"(?i)testemunha|adquirente|comprador[a]?|vendedor[a]?|arrematante|terceir[oa]"),
}
_ID_WITH_DIGITS = re.compile(r"\d")
# Varredura: nome proprio de 2 a 5 palavras (Title Case ou CAIXA ALTA), precedido de pista de pessoa.
_CANDIDATE_CUE = re.compile(
    r"(?i)(?:s[oó]ci[oa]s?|administrador[a]?|representad[oa] por|representante|testemunha|residente|"
    r"procurador[a]? d[oa] (?:executad|empresa|devedor)|sr\.|sra\.|dr\.|dra\.|cpf[^\n]{0,20}?)\s*[:\-–]?\s*$"
)
_PROPER = re.compile(
    r"\b(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})"
    r"(?:\s+(?:d[aeo]s?|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})){1,4}\b"
)
_STOP_CANDIDATE = re.compile(
    r"(?i)^(?:execu[cç][aã]o fiscal|certid[aã]o|d[ií]vida ativa|poder judici[aá]rio|justi[cç]a federal|"
    r"se[cç][aã]o judici[aá]ria|documento eletr[oô]nico|evento \d+|cnpj|cpf|rg|oab|cep|data|valor)"
)


@dataclass
class Entity:
    kind: str
    core: str
    originals: list[str] = field(default_factory=list)
    engines: set[str] = field(default_factory=set)
    llm_roles: set[str] = field(default_factory=set)
    occurrences: int = 0
    public: int = 0
    financial: int = 0
    street: int = 0
    street_label: bool = False
    cues: Counter = field(default_factory=Counter)
    spans: list[tuple[int, int, int]] = field(default_factory=list)  # (texto, inicio, fim)

    @property
    def name(self) -> str:
        return max(self.originals, key=lambda item: (len(item.split()), len(item))) if self.originals else self.core


# Prefixo da chave canonica: o pipeline com elenco grava "pessoa:<nucleo>" e a lista de alvos
# "alvo:pessoa:<nucleo>"; mapas antigos gravam so o nome dobrado. Sem tirar o prefixo, a mesma entidade
# vira uma linha por motor e cada uma sai "so um motor achou".
_CANONICAL_PREFIX = re.compile(r"^(?:alvo:)?(?:pessoa|empresa|endereco|particula):")


def _entity_core(kind: str, canonical: str) -> str:
    return entity_core(kind, _CANONICAL_PREFIX.sub("", canonical))


def _engine_label(lote_dir: Path) -> str:
    """Rotulo do motor do lote, lido do LEIA-ME privado ("motor local" / "motor LLM: canal"), sem nomes."""

    guide = lote_dir / "LEIA-ME.txt"
    if guide.is_file():
        for line in guide.read_text(encoding="utf-8", errors="ignore").splitlines():
            # So a linha de versao ("Gerado por: v0.4.1 · ... · motor LLM: gemini-pago · ..."): o resto do
            # LEIA-ME tambem fala em "motor local" e enganaria o rotulo.
            if not line.startswith("Gerado por"):
                continue
            match = re.search(r"(?i)motor (?:LLM: ?([\w.\-]+)|local)", line)
            if match:
                return match.group(1) or "local"
    return lote_dir.name


def load_lote(lote_dir: Path, entities: dict[tuple[str, str], Entity], ids: dict[tuple[str, str], dict]) -> str:
    payload = json.loads((lote_dir / "mapa.json").read_text(encoding="utf-8"))
    engine = _engine_label(lote_dir)
    for record in payload.get("records", []):
        entity_type = str(record.get("entity_type") or "")
        canonical = str(record.get("canonical") or "")
        originals = [str(item) for item in record.get("originals") or [] if str(item).strip()]
        kind = NAME_TYPES.get(entity_type)
        if kind is None:
            if not canonical:
                continue
            item = ids.setdefault((entity_type, canonical), {"tipo": entity_type, "valor": canonical, "motores": set(), "exemplos": []})
            item["motores"].add(engine)
            for original in originals:
                if original not in item["exemplos"]:
                    item["exemplos"].append(original)
            continue
        core = _entity_core(kind, canonical or (originals[0] if originals else ""))
        if not core:
            continue
        entity = entities.setdefault((kind, core), Entity(kind=kind, core=core))
        entity.engines.add(engine)
        for original in originals or [canonical]:
            if original not in entity.originals:
                entity.originals.append(original)
    roster = lote_dir / "elenco.json"
    if roster.is_file():
        for item in json.loads(roster.read_text(encoding="utf-8")).get("entradas", []):
            try:
                entry = RosterEntry.from_dict(item)
            except ValueError:
                continue
            key = (entry.kind, _entity_core(entry.kind, entry.name))
            if key in entities:
                entities[key].llm_roles.update(role for role in entry.roles if role != "desconhecido")
    return engine


def merge_shared_spellings(entities: dict[tuple[str, str], Entity]) -> None:
    """Junta, no mesmo tipo, entidades que dividem uma grafia de duas palavras ou mais.

    Cada motor escolhe seu proprio valor canonico (um usa a forma longa, outro a curta; um guarda o
    numero do endereco, outro nao), entao a mesma entidade pode chegar com chaves diferentes. A grafia
    tirada do texto e o que os motores realmente marcaram: se coincide, e a mesma entidade. Grafia de uma
    palavra so (um sobrenome) nao junta, porque duas pessoas da mesma familia a dividem.
    """

    keys = list(entities)
    parent = {key: key for key in keys}

    def find(key: tuple[str, str]) -> tuple[str, str]:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owner: dict[tuple[str, str], tuple[str, str]] = {}
    for key in keys:
        entity = entities[key]
        for original in entity.originals:
            folded = fold_lexical(original)
            if len(folded.split()) < 2:
                continue
            seen = owner.setdefault((entity.kind, folded), key)
            if seen != key:
                parent[find(key)] = find(seen)
    for key in keys:
        root = find(key)
        if root == key:
            continue
        target, source = entities[root], entities.pop(key)
        target.engines |= source.engines
        target.llm_roles |= source.llm_roles
        for original in source.originals:
            if original not in target.originals:
                target.originals.append(original)


def load_texts(private_root: Path, pdf_dir: Path | None, text_dir: Path | None) -> list[tuple[str, str]]:
    """Texto extraido de cada PDF do processo: o mais novo do cache privado por nome de arquivo."""

    texts: list[tuple[str, str]] = []
    if text_dir is not None:
        for path in sorted(text_dir.glob("*.md")) + sorted(text_dir.glob("*.txt")):
            texts.append((path.name, path.read_text(encoding="utf-8", errors="ignore")))
        return texts
    if pdf_dir is None:
        return texts
    wanted = {child.name.casefold() for child in pdf_dir.iterdir() if child.is_file()}
    newest: dict[str, tuple[str, Path]] = {}
    cache = private_root / "cache_extracao"
    for meta_path in cache.glob("*.json") if cache.is_dir() else ():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        original = Path(str(meta.get("caminho_original") or ""))
        if original.name.casefold() not in wanted:
            continue
        stamp = str(meta.get("data") or "")
        md_path = meta_path.with_suffix(".md")
        if md_path.is_file() and (original.name not in newest or stamp > newest[original.name][0]):
            newest[original.name] = (stamp, md_path)
    for name in sorted(newest):
        texts.append((name, newest[name][1].read_text(encoding="utf-8", errors="ignore")))
    return texts


def _find_all(folded: str, needle: str) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    start = folded.find(needle)
    while start >= 0:
        end = start + len(needle)
        before = folded[start - 1] if start > 0 else " "
        after = folded[end] if end < len(folded) else " "
        if not before.isalnum() and not after.isalnum():
            hits.append((start, end))
        start = folded.find(needle, start + 1)
    return hits


def annotate(entities: dict[tuple[str, str], Entity], texts: list[tuple[str, str]]) -> None:
    """Conta ocorrencias e sinais de contexto de cada entidade em todos os textos."""

    folded_texts = [(text, fold_with_index(text)) for _name, text in texts]
    for entity in entities.values():
        needles = {fold_lexical(item) for item in entity.originals if len(fold_lexical(item)) >= 3}
        for index, (text, folded) in enumerate(folded_texts):
            seen: set[tuple[int, int]] = set()
            for needle in needles:
                for fstart, fend in _find_all(folded.text, needle):
                    start, end = folded.to_original(fstart, fend)
                    if (start, end) in seen:
                        continue
                    seen.add((start, end))
                    entity.occurrences += 1
                    entity.spans.append((index, start, end))
                    if entity.kind == "pessoa":
                        if _public_role(text, start, end):
                            entity.public += 1
                        labelled, street = _street_position(text, start, end)
                        entity.street_label = entity.street_label or labelled
                        entity.street += street
                    elif entity.kind == "empresa":
                        if _public_role(text, start, end):
                            entity.public += 1
                        if _financial_position(text, start, end):
                            entity.financial += 1
                    # Pista antes ("o sócio FULANO") ou logo depois ("FULANO, sócia, reside...").
                    before = text[max(0, start - 60) : start]
                    after = text[end : end + 40]
                    for role, pattern in _CUES.items():
                        if pattern.search(before) or pattern.search(after):
                            entity.cues[role] += 1


def classify(entity: Entity, total_engines: int) -> dict:
    """Papel, acao, certeza e motivo de revisao de uma entidade."""

    reasons: list[str] = []
    role, action = "", TOKENIZE
    if entity.kind == "endereco":
        role = "endereco"
    elif entity.kind == "empresa" and (is_kept_term(entity.name) or _PUBLIC_ORG.search(entity.name)):
        role, action = "orgao_publico", KEEP
    elif entity.kind == "empresa" and entity.financial:
        role, action = "instituicao_financeira", KEEP
    elif entity.kind == "pessoa" and entity.occurrences and entity.street >= 0.6 * entity.occurrences and entity.street_label:
        role = "logradouro"
        reasons.append("rua com nome de pessoa: confira se e endereco")
    elif entity.public and entity.public >= 0.5 * entity.occurrences:
        role, action = "agente_publico", KEEP
    elif entity.public:
        role = "agente_publico?"
        reasons.append(f"cargo publico em {entity.public} de {entity.occurrences} ocorrencia(s): confira")
    if not role:
        votes = Counter({r: n for r, n in entity.cues.items()})
        for llm_role in entity.llm_roles:
            normalized = normalize_role(llm_role)
            if normalized in {"magistrado", "servidor_publico", "procurador_publico", "orgao_publico", "autor_citado"}:
                reasons.append(f"LLM classificou como {normalized}, o contexto nao: confira")
            else:
                votes[normalized if normalized != "desconhecido" else "terceiro"] += 2
        role = votes.most_common(1)[0][0] if votes else ("parte_ou_terceiro" if entity.kind == "pessoa" else "empresa_privada")
    if entity.occurrences == 0:
        reasons.append("nenhuma ocorrencia no texto extraido (grafia so no mapa?)")
    if len(entity.engines) == 1 and total_engines > 1:
        reasons.append("so um motor achou: ganho dele ou falso positivo")
    if len(entity.name.split()) == 1 and entity.kind == "pessoa":
        reasons.append("nome de uma palavra")
    certainty = "alta" if not reasons and entity.occurrences >= 3 and len(entity.engines) == total_engines else ("media" if not reasons else "baixa")
    categoria = KEEP_ROLES.get(role) or f"{entity.kind.upper()}-{role}"
    return {
        "categoria": categoria,
        "rotulo": "",
        "padroes": list(dict.fromkeys(entity.originals)),
        "tipo": entity.kind,
        "papel": role,
        "acao": action,
        "certeza": certainty,
        "ocorrencias": entity.occurrences,
        "motores": sorted(entity.engines),
        "sinais": {"cargo_publico": entity.public, "sisbajud": entity.financial, "logradouro": entity.street, **dict(entity.cues)},
        "revisar": bool(reasons),
        "motivo_revisao": "; ".join(reasons),
    }


def sweep_candidates(texts: list[tuple[str, str]], entities: dict[tuple[str, str], Entity]) -> list[dict]:
    """Nomes que nenhum motor pegou: nome proprio depois de pista de pessoa, fora do que o mapa cobre."""

    known = {core for _kind, core in entities}
    covered: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for entity in entities.values():
        for index, start, end in entity.spans:
            covered[index].append((start, end))
    found: dict[str, dict] = {}
    for index, (_name, text) in enumerate(texts):
        spans = sorted(covered[index])
        for match in _PROPER.finditer(text):
            value = match.group(0).strip()
            core = fold_lexical(value)
            if len(value.split()) < 2 or _STOP_CANDIDATE.match(value) or is_kept_term(value) or _PUBLIC_ORG.search(value):
                continue
            if core in known or any(core in other or other in core for other in known):
                continue
            if any(start < match.end() and match.start() < end for start, end in spans):
                continue
            before = text[max(0, match.start() - 45) : match.start()]
            if not _CANDIDATE_CUE.search(before):
                continue
            item = found.setdefault(core, {"categoria": "CANDIDATO-sem-motor", "rotulo": "", "padroes": [], "tipo": "pessoa",
                                           "papel": "revisar", "acao": TOKENIZE, "certeza": "baixa", "ocorrencias": 0,
                                           "motores": [], "sinais": {}, "revisar": True,
                                           "motivo_revisao": "nenhum motor achou; veio da varredura por pista"})
            if value not in item["padroes"]:
                item["padroes"].append(value)
            item["ocorrencias"] += 1
    return sorted(found.values(), key=lambda item: -item["ocorrencias"])


def build(lotes: list[Path], private_root: Path, pdf_dir: Path | None, text_dir: Path | None) -> dict:
    entities: dict[tuple[str, str], Entity] = {}
    ids: dict[tuple[str, str], dict] = {}
    engines = [load_lote(lote, entities, ids) for lote in lotes]
    merge_shared_spellings(entities)
    texts = load_texts(private_root, pdf_dir, text_dir)
    annotate(entities, texts)
    rows = [classify(entity, len(set(engines))) for entity in entities.values()]
    rows.sort(key=lambda row: (not row["revisar"], row["acao"] != KEEP, row["tipo"], -row["ocorrencias"]))
    counters: Counter[str] = Counter()
    for row in rows:
        counters[row["tipo"]] += 1
        row["rotulo"] = f"{row['tipo']} {counters[row['tipo']]}"
    candidates = sweep_candidates(texts, entities)
    for number, item in enumerate(candidates, start=1):
        item["rotulo"] = f"candidato {number}"
    id_rows, extra_rows = [], []
    id_counter: Counter[str] = Counter()
    for (entity_type, _canonical), item in sorted(ids.items()):
        label = entity_type.replace("BR_", "")
        id_counter[label] += 1
        base = {"categoria": f"ID-{label}", "rotulo": f"{label} {id_counter[label]}", "acao": TOKENIZE, "motores": sorted(item["motores"])}
        if _ID_WITH_DIGITS.search(item["valor"]) and not re.search(r"[@]", item["valor"]):
            id_rows.append({**base, "valor": item["valor"]})
        else:
            extra_rows.append({**base, "padroes": item["exemplos"] or [item["valor"]], "tipo": "id", "papel": label.lower(),
                               "certeza": "alta", "ocorrencias": 0, "sinais": {}, "revisar": False, "motivo_revisao": ""})
    return {
        "aviso": "CONTEUDO SENSIVEL: NAO ENVIAR A SERVICOS EXTERNOS. Gabarito pre-classificado; revise 'acao'.",
        "como_revisar": (
            "Leia o .REVISAO.md ao lado. Para cada linha marcada 'revisar', decida 'acao' (tokenizar/manter) aqui no "
            "JSON e, se nao for entidade, apague o item. Categoria AGENTE-PUBLICO e ORGAO-PUBLICO devem ficar em claro."
        ),
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "pdfs": pdf_dir.name if pdf_dir else (text_dir.name if text_dir else ""),
        "lotes": [lote.name for lote in lotes],
        "motores": sorted(set(engines)),
        "textos_lidos": [name for name, _text in texts],
        "entidades": [*rows, *extra_rows, *candidates],
        "ids": id_rows,
    }


def review_markdown(payload: dict) -> str:
    lines = [
        f"# Revisao do gabarito ({payload['gerado_em']})",
        "",
        "CONTEM NOMES REAIS. NAO ENVIAR. Corrija 'acao' no JSON ao lado; apague o que nao for entidade.",
        "",
        f"Lotes: {', '.join(payload['lotes'])} · motores: {', '.join(payload['motores'])} · "
        f"textos lidos: {len(payload['textos_lidos'])}",
        "",
        "| # | tipo | nome (grafias) | papel | acao | certeza | ocorr. | motores | revisar |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["entidades"]:
        names = " / ".join(row["padroes"][:3]) + (" …" if len(row["padroes"]) > 3 else "")
        flag = row["motivo_revisao"] if row["revisar"] else ""
        lines.append(
            f"| {row['rotulo']} | {row['tipo']} | {names} | {row['papel']} | **{row['acao']}** | {row['certeza']} | "
            f"{row['ocorrencias']} | {', '.join(row['motores']) or '-'} | {flag} |"
        )
    lines += ["", f"IDs (sempre tokenizar): {len(payload['ids'])}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lotes", nargs="+", help="pastas privadas dos lotes (com mapa.json), um por motor")
    parser.add_argument("--pdfs", help="pasta dos PDFs do processo (acha o texto extraido no cache privado)")
    parser.add_argument("--textos", help="em vez de --pdfs: pasta com os textos extraidos (.md)")
    parser.add_argument("--privados", help="raiz privada (padrao: saidas/privados)")
    parser.add_argument("--nome", help="nome do gabarito (padrao: gabarito_<pasta dos PDFs>)")
    args = parser.parse_args(argv)

    from anonimizador_app.pipeline import default_private_dir

    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    lotes = [Path(item).expanduser() for item in args.lotes]
    missing = [str(lote) for lote in lotes if not (lote / "mapa.json").is_file()]
    if missing:
        print("Sem mapa.json em: " + ", ".join(missing), file=sys.stderr)
        return 2
    pdf_dir = Path(args.pdfs).expanduser() if args.pdfs else None
    text_dir = Path(args.textos).expanduser() if args.textos else None
    if pdf_dir is None and text_dir is None:
        print("Informe --pdfs (pasta dos PDFs) ou --textos (pasta dos textos extraidos).", file=sys.stderr)
        return 2
    payload = build(lotes, private_root, pdf_dir, text_dir)
    if not payload["textos_lidos"]:
        print("AVISO: nenhum texto extraido encontrado; papeis e ocorrencias ficaram sem contexto.", file=sys.stderr)
    name = args.nome or ("gabarito_" + re.sub(r"\W+", "_", payload["pdfs"] or "processo").strip("_"))
    out_dir = private_root / "gabaritos"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / f"{name}.REVISAO.md").write_text(review_markdown(payload), encoding="utf-8")

    rows = payload["entidades"]
    by_action = Counter(row["acao"] for row in rows)
    print(f"Gabarito: {json_path}")
    print(f"Lotes: {len(lotes)} · motores: {', '.join(payload['motores'])} · textos lidos: {len(payload['textos_lidos'])}")
    print(f"Entidades: {len(rows)} (tokenizar {by_action[TOKENIZE]}, manter {by_action[KEEP]}) · IDs: {len(payload['ids'])}")
    print(f"A revisar: {sum(1 for row in rows if row['revisar'])} · candidatos sem motor: "
          f"{sum(1 for row in rows if row['categoria'] == 'CANDIDATO-sem-motor')}")
    print(f"Por certeza: {dict(Counter(row['certeza'] for row in rows))}")
    print("Leia o .REVISAO.md na pasta privada. Nao envie nenhum dos dois arquivos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
