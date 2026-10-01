"""Ensaio do motor LLM SEM REDE (L4, passo zero): prova que o caminho inteiro funciona antes da 1a chamada real.

Usa um PDF sintetico (dados inventados) e um LLM falso (modelo do LangExtract
que acha as entidades por busca de texto). Todo o ensaio roda com os sockets
BLOQUEADOS: qualquer tentativa de conexao, resolucao de nome ou datagrama
aborta e reprova o ensaio. A chave e falsa e o cofre e falso; o Gerenciador de
Credenciais real nao e tocado.

Passos:

  1. portao do motor: bloqueia sem canal, sem aviso, sem faturamento, canal gratuito;
  2. rodada A: PDF -> lote (ancoras, INDICE, ELENCO, AUDITORIA com o motor);
  3. revisao do elenco: linhas com tipo, papel, mencoes e token; decisoes fixas;
  4. rodada B: elenco revisado reaplicado SEM rede (so cache);
  5. as duas reguas (tools/medir_estrutura.py e tools/medir_vazamento.py) nas rodadas A e B;
  6. varredura: a chave falsa nao aparece em nenhum arquivo gerado nem na saida do ensaio.

Uso:  python tools/ensaio_llm.py [--saida PASTA] [--manter]
Codigo de saida: 0 se todas as conferencias passaram; 1 se alguma falhou.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEYRING_WAS_LOADED = "keyring" in sys.modules

from anonimizador_app import credentials  # noqa: E402
from anonimizador_app.credentials import ENV_VAR, VAULT_SERVICE, VAULT_USER  # noqa: E402
from anonimizador_app.llm_discovery import LangExtractDiscoverer  # noqa: E402
from anonimizador_app.llm_engine import (  # noqa: E402
    EngineChoice,
    audit_lines,
    build_discoverer,
    check_engine,
)
from anonimizador_app.netblock import block_network  # noqa: E402
from anonimizador_app.pipeline_llm import LLM_CACHE_SUBDIR, run_llm_job  # noqa: E402
from anonimizador_app.roster import KEEP, TOKENIZE  # noqa: E402
from anonimizador_app.roster_review import build_rows, reapply_offline, summarize  # noqa: E402

FAKE_KEY = "chave-ficticia-ensaio-7f3a91c2d4e6b8a0"
OTHER_FAKE_KEY = "outra-chave-ficticia-nao-usar-123456"
CHANNEL = "gemini-pago"
PDF_NAME = "autos_sinteticos.pdf"

# (tipo, texto, papel). Tudo inventado.
ENTITIES = [
    ("empresa", "MADEIREIRA PINHAL VERDE LTDA", "parte"),
    ("pessoa", "ANTONIO CARLOS PEDROSO", "socio"),
    ("pessoa", "CLAUDETE SCHNEIDER PEDROSO", "socio"),
    ("empresa", "TRANSPORTADORA RIO DO PEIXE EIRELI", "terceiro"),
    ("endereco", "Rua Tupinambás, 1.520", "parte"),
    ("pessoa", "LUCIANA MARCHETTI DE ALMEIDA", "magistrado"),
]
CPF = "529.982.247-25"
CNPJ = "11.222.333/0001-81"

PAGES = [
    "EXECUÇÃO FISCAL\n"
    "Exequente: UNIÃO - FAZENDA NACIONAL\n"
    f"Executada: MADEIREIRA PINHAL VERDE LTDA, CNPJ {CNPJ},\n"
    "estabelecida na Rua Tupinambás, 1.520, centro.\n"
    "A UNIÃO (FAZENDA NACIONAL), por intermédio da PGFN, ajuíza esta execução\n"
    "fiscal para cobrança dos créditos inscritos em dívida ativa, no valor de\n"
    "R$ 1.234.567,89, apurado em 12/03/2024.",
    f"O sócio-administrador ANTONIO CARLOS PEDROSO, CPF {CPF},\n"
    "e a sócia CLAUDETE SCHNEIDER PEDROSO dirigiam a sociedade ao tempo\n"
    "dos fatos geradores, conforme a ficha cadastral da Junta Comercial.\n"
    "Requer-se a inclusão de ambos no polo passivo, com fundamento no\n"
    "art. 135, III, do CTN e no Tema 962 do STJ.\n"
    "Valor atualizado: R$ 2.010.450,10 em 01/09/2025.",
    "A empresa TRANSPORTADORA RIO DO PEIXE EIRELI adquiriu o imóvel da\n"
    "executada em 14/03/2023, por R$ 850.000,00, conforme o evento 45.\n"
    "Pede-se a penhora via SISBAJUD e RENAJUD, sem prejuízo da\n"
    "indisponibilidade prevista no art. 185-A do CTN, e a citação de\n"
    "ANTONIO CARLOS PEDROSO.",
    "Documento eletrônico assinado por LUCIANA MARCHETTI DE ALMEIDA,\n"
    "Juíza Federal. Defiro a penhora requerida, devendo a Secretaria cumprir\n"
    "as diligências no prazo legal, a contar de 20/10/2025, com ciência à\n"
    "executada MADEIREIRA PINHAL VERDE LTDA e aos sócios, na pessoa de seus\n"
    "advogados.",
]

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"[{'OK' if ok else 'FALHOU'}] {name}" + (f": {detail}" if detail else ""), flush=True)
    return bool(ok)


class MemoryVault:
    """Cofre falso em memoria: o Gerenciador de Credenciais real nao e tocado."""

    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.stored.get((service, username))

    def set_password(self, service, username, password):
        self.stored[(service, username)] = password

    def delete_password(self, service, username):
        self.stored.pop((service, username), None)


def make_pdf(folder: Path) -> Path:
    import pymupdf

    document = pymupdf.open()
    for number, body in enumerate(PAGES, start=1):
        page = document.new_page(width=595, height=842)
        page.insert_text((50, 80), body, fontsize=10.5, fontname="helv")
        page.insert_text(
            (50, 800),
            f"Processo 5009999-11.2025.4.04.7001/PR, Evento {number}, PET{number}, Página 1",
            fontsize=8,
            fontname="helv",
        )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PDF_NAME
    document.save(path)
    document.close()
    return path


def fake_langextract_model():
    """Modelo falso do LangExtract: devolve as entidades que achar no trecho, por busca de texto."""

    from langextract.core import base_model
    from langextract.core import types as lx_types

    class FakeModel(base_model.BaseLanguageModel):
        def infer(self, batch_prompts, **kwargs):
            for prompt in batch_prompts:
                chunk = prompt.rsplit("Q: ", 1)[-1]
                items = []
                for kind, value, role in ENTITIES:
                    if value.casefold() in chunk.casefold():
                        items.append(
                            {kind: value, f"{kind}_attributes": {"papel": role, "nome_canonico": value}}
                        )
                yield [lx_types.ScoredOutput(score=1.0, output=json.dumps({"extractions": items}))]

    return FakeModel()


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"ensaio_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_ruler(module, argv: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main(argv)
    return buffer.getvalue()


def structure_totals(output: str) -> dict:
    match = re.search(r"TOTAL: (\{.*\})", output)
    return eval(match.group(1), {"__builtins__": {}}, {}) if match else {}  # noqa: S307 - saida da propria regua


def leak_counts(output: str) -> dict[str, int]:
    """rotulo -> mencoes em claro (primeiro lote da tabela); so contagens."""

    counts: dict[str, list[int]] = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            continue
        numbers = []
        for token in reversed(parts):
            if token.isdigit():
                numbers.insert(0, int(token))
            else:
                break
        label = " ".join(parts[1 : len(parts) - len(numbers)])  # sem a coluna da categoria
        counts[label] = numbers
    return counts


def write_gabarito(path: Path) -> None:
    gabarito = {
        "entidades": [
            {"categoria": "EMPRESA-parte", "rotulo": "executada", "padroes": ["MADEIREIRA PINHAL VERDE"]},
            {"categoria": "PESSOA-socio", "rotulo": "socio A", "padroes": ["ANTONIO CARLOS PEDROSO"]},
            {"categoria": "PESSOA-socio", "rotulo": "socia B", "padroes": ["CLAUDETE SCHNEIDER PEDROSO"]},
            {"categoria": "EMPRESA-terceiro", "rotulo": "adquirente", "padroes": ["TRANSPORTADORA RIO DO PEIXE"]},
            {"categoria": "AGENTE-PUBLICO", "rotulo": "juiza", "padroes": ["LUCIANA MARCHETTI DE ALMEIDA"]},
        ],
        "ids": [
            {"categoria": "ID", "rotulo": "CPF socio A", "valor": CPF},
            {"categoria": "ID", "rotulo": "CNPJ executada", "valor": CNPJ},
        ],
    }
    path.write_text(json.dumps(gabarito, ensure_ascii=False, indent=2), encoding="utf-8")


def scan_for_key(folders: list[Path], extra_text: str = "") -> list[str]:
    needles = {FAKE_KEY, OTHER_FAKE_KEY}
    hits: list[str] = []
    for folder in folders:
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if any(needle.encode("utf-8") in data for needle in needles):
                hits.append(path.name)
    if any(needle in extra_text for needle in needles):
        hits.append("(saida do ensaio)")
    return hits


def step_gate(private_root: Path, vault: MemoryVault) -> None:
    print("\n== 1. Portao do motor ==")
    env = {ENV_VAR: FAKE_KEY}
    good = EngineChoice(channel=CHANNEL, acknowledged=True, billing_confirmed=True)
    base = dict(private_root=private_root, env=env, vault=vault)
    for name, choice in (
        ("sem canal", EngineChoice()),
        ("canal gratuito", EngineChoice(channel="gemini-gratuito", acknowledged=True, billing_confirmed=True)),
        ("canal desconhecido", EngineChoice(channel="outro", acknowledged=True, billing_confirmed=True)),
        ("sem aviso lido", EngineChoice(channel=CHANNEL, billing_confirmed=True)),
        ("sem faturamento confirmado", EngineChoice(channel=CHANNEL, acknowledged=True)),
    ):
        check(f"bloqueia: {name}", not check_engine(choice, **base).allowed)
    check(
        "bloqueia: sem chave (GEMINI_API_KEY/GOOGLE_API_KEY nao contam)",
        not check_engine(good, private_root=private_root, env={"GEMINI_API_KEY": FAKE_KEY, "GOOGLE_API_KEY": FAKE_KEY}, vault=vault).allowed,
    )
    config_json = private_root / "config.json"
    config_json.write_text(json.dumps({"api_key": "x"}), encoding="utf-8")
    check("bloqueia: config.json com chave", not check_engine(good, **base).allowed)
    config_json.unlink()
    decision = check_engine(good, **base)
    check("libera com tudo em ordem", decision.allowed, "; ".join(decision.reasons))
    check("a chave nao aparece em repr da decisao", FAKE_KEY not in repr(decision) and FAKE_KEY not in repr(decision.config))
    # O cofre: chave guardada la tambem libera; a fonte e o que a interface mostra.
    credentials.store_key(FAKE_KEY, vault=vault)
    from_vault = check_engine(good, private_root=private_root, env={}, vault=vault)
    check("chave do cofre falso libera", from_vault.allowed and from_vault.key_source == credentials.SOURCE_VAULT)
    credentials.delete_key(vault=vault)
    check("apagar a chave bloqueia de novo", not check_engine(good, private_root=private_root, env={}, vault=vault).allowed)


def step_real_model_is_built_with_the_explicit_key(decision, cache_dir: Path) -> None:
    print("\n== 1b. Modelo real do provedor: criado com a chave explicita, sem rede ==")
    discoverer = build_discoverer(decision, cache_dir=cache_dir)
    previous = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = OTHER_FAKE_KEY
    try:
        with block_network() as attempts:
            try:
                model = discoverer._build_model()
                error = ""
            except Exception as exc:  # noqa: BLE001
                model, error = None, type(exc).__name__
    finally:
        if previous is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = previous
    check("construir o modelo nao abre socket", not attempts, f"{len(attempts)} tentativa(s) {error}")
    if model is not None:
        used = getattr(model, "api_key", None)
        check("o modelo usa a chave do cofre, nao a GEMINI_API_KEY do ambiente", used == FAKE_KEY)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--saida", help="Pasta de trabalho (padrao: temporaria, apagada no fim).")
    parser.add_argument("--manter", action="store_true", help="Nao apaga a pasta de trabalho.")
    args = parser.parse_args(argv)

    if importlib.util.find_spec("langextract") is None:
        print("langextract nao instalado: o ensaio precisa dele (pip install -e .[llm]).", file=sys.stderr)
        return 2

    workdir = Path(args.saida).expanduser() if args.saida else Path(tempfile.mkdtemp(prefix="ensaio_llm_"))
    workdir.mkdir(parents=True, exist_ok=True)
    pdf_dir, export_root, private_root = workdir / "pdfs", workdir / "exportaveis", workdir / "privados"
    private_root.mkdir(parents=True, exist_ok=True)
    print(f"Pasta de trabalho: {workdir}")
    print("Rede: BLOQUEADA durante todo o ensaio. Chave e cofre: falsos.")

    vault = MemoryVault()
    pdf = make_pdf(pdf_dir)
    step_gate(private_root, vault)

    choice = EngineChoice(channel=CHANNEL, acknowledged=True, billing_confirmed=True)
    decision = check_engine(choice, private_root=private_root, env={ENV_VAR: FAKE_KEY}, vault=vault)
    if not decision.allowed:
        check("portao liberado para a rodada", False, decision.blocked_message())
        return finish(workdir, args)
    step_real_model_is_built_with_the_explicit_key(decision, private_root / LLM_CACHE_SUBDIR)

    structure = load_tool("medir_estrutura")
    structure.CACHE = private_root / "cache_extracao"
    leak = load_tool("medir_vazamento")
    gabarito = workdir / "gabarito_sintetico.json"
    write_gabarito(gabarito)

    with block_network() as attempts:
        print("\n== 2. Rodada A: PDF sintetico -> lote ==")
        live = LangExtractDiscoverer(
            decision.config,
            cache_dir=private_root / LLM_CACHE_SUBDIR,
            model_factory=fake_langextract_model,
        )
        first = run_llm_job(
            [pdf],
            export_root=export_root,
            private_root=private_root,
            discoverer=live,
            verification_rounds=1,
            engine=decision.channel,
            engine_notes=audit_lines(decision, choice),
        )
        folder_a = first.job.export_dir
        text_a = first.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        check("exportacao liberada", first.job.export_allowed, first.job.export_block_reason)
        check("ancora visivel em todas as folhas", len(re.findall(r"(?m)^\[fl\. \d+ · Evento \d+ · PET\d+ · p\. 1\]$", text_a)) == len(PAGES))
        check("INDICE.md e ELENCO.md gravados", (folder_a / "INDICE.md").is_file() and (folder_a / "ELENCO.md").is_file())
        cast = (folder_a / "ELENCO.md").read_text(encoding="utf-8")
        check("ELENCO.md tem a secao de papeis do LLM", "## Papéis atribuídos pelo LLM" in cast)
        audit = (folder_a / "AUDITORIA.txt").read_text(encoding="utf-8")
        check("AUDITORIA registra o motor e a fonte da chave", f"motor LLM: {CHANNEL}" in audit and "variavel de ambiente" in audit)
        check("LEIA-ME registra o motor", f"motor LLM: {CHANNEL}" in (folder_a / "LEIA-ME.txt").read_text(encoding="utf-8"))
        for _kind, value, role in ENTITIES:
            if role != "magistrado":
                check(f"sem '{value[:14]}...' em claro (A)", value.casefold() not in text_a.casefold())
        check("juiza em claro (item H)", "LUCIANA MARCHETTI DE ALMEIDA" in text_a)
        for fixed in ("UNIÃO - FAZENDA NACIONAL", "R$ 1.234.567,89", "12/03/2024", "SISBAJUD", "Tema 962 do STJ"):
            check(f"permanece: {fixed}", fixed in text_a)
        check("CPF e CNPJ sem sair em claro", CPF not in text_a and CNPJ not in text_a)

        print("\n== 3. Revisao do elenco ==")
        rows = build_rows(first.roster, occurrences=first.occurrences, tokens=first.tokens)
        for row in rows:
            print(f"  {row.kind_label:9s} | {row.role_label:12s} | {row.occurrences:2d} mencoes | {row.token or '(mantido)':14s} | {row.action}")
        print("  " + summarize(rows))
        check("uma linha por entidade", len(rows) == len(ENTITIES), f"{len(rows)} linhas")
        check("toda linha tokenizada tem token e mencoes", all(r.token and r.occurrences for r in rows if r.action == TOKENIZE))
        claudete = next(r for r in rows if r.name.startswith("CLAUDETE"))
        judge = next(r for r in rows if r.name.startswith("LUCIANA"))
        decisions = {claudete.key: KEEP, judge.key: TOKENIZE}

        print("\n== 4. Rodada B: elenco revisado, SEM rede (so cache) ==")
        second = reapply_offline(first, decisions, [pdf], export_root=export_root, private_root=private_root)
        folder_b = second.result.job.export_dir
        text_b = second.result.job.outcomes[0].markdown_path.read_text(encoding="utf-8")
        check("duas decisoes aplicadas", second.changed == 2)
        check("lote novo (a rodada A fica intacta)", folder_b != folder_a and (folder_a / "AUDITORIA.txt").is_file())
        check("a socia mantida volta ao claro", "CLAUDETE SCHNEIDER PEDROSO" in text_b)
        check("a juiza tokenizada some", "LUCIANA MARCHETTI DE ALMEIDA" not in text_b)
        check("os outros seguem tokenizados", "ANTONIO CARLOS PEDROSO" not in text_b and "MADEIREIRA PINHAL VERDE" not in text_b)
        check("exportacao liberada", second.result.job.export_allowed, second.result.job.export_block_reason)
        audit_b = (folder_b / "AUDITORIA.txt").read_text(encoding="utf-8")
        check("AUDITORIA da B registra o modo sem rede", "Modo sem rede" in audit_b and f"motor LLM: {CHANNEL}" in audit_b)
        check("elenco revisado so na pasta privada", (second.reviewed_path.parent == first.roster_path.parent) and not list(export_root.rglob("elenco*.json")))

        print("\n== 5. As duas reguas ==")
        out_a = run_ruler(structure, ["x", str(pdf_dir), str(folder_a)])
        out_b = run_ruler(structure, ["x", str(pdf_dir), str(folder_b)])
        tot_a, tot_b = structure_totals(out_a), structure_totals(out_b)
        print(f"  estrutura A: {tot_a}")
        print(f"  estrutura B: {tot_b}")
        for label, totals in (("A", tot_a), ("B", tot_b)):
            check(f"estrutura {label}: ancoras = folhas", totals.get("ancoras") == totals.get("folhas") == len(PAGES))
            check(f"estrutura {label}: 0 valores, decimais e datas perdidos", all(totals.get(f"{kind}_perdidos_na_troca", 0) == 0 and totals.get(f"{kind}_perdidos_na_extracao", 0) == 0 for kind in ("valores", "decimais", "datas")))
            check(f"estrutura {label}: 0 token colado em numero e 0 nao lida", totals.get("tokens_colados_em_numero", 0) == 0 and totals.get("nao_lidas", 0) == 0)
        check("estrutura igual entre A e B", {k: v for k, v in tot_a.items() if k != "tokens_em_cabecalho_de_tabela"} == {k: v for k, v in tot_b.items() if k != "tokens_em_cabecalho_de_tabela"})
        leak_out = run_ruler(leak, ["x", str(gabarito), str(folder_a), str(folder_b)])
        print(leak_out.rstrip())
        counts = leak_counts(leak_out)
        check("vazamento: 0 em claro nas entidades e IDs privados (A)", all(c[0] == 0 for label, c in counts.items() if label not in {"juiza"}))
        check("vazamento: a regua enxerga a decisao (socia B 0 -> mais; juiza mais -> 0)", counts.get("socia B", [1, 0])[1] > 0 and counts.get("juiza", [0, 1])[1] == 0)

    check("nenhuma tentativa de rede", not attempts, f"{len(attempts)} tentativa(s)")
    check("o Gerenciador de Credenciais real nao foi tocado (keyring nem importado)", KEYRING_WAS_LOADED or "keyring" not in sys.modules)
    hits = scan_for_key([workdir], extra_text="\n".join(detail for _n, _ok, detail in _RESULTS))
    check("a chave falsa nao esta em nenhum arquivo gerado", not hits, ", ".join(hits))
    return finish(workdir, args)


def finish(workdir: Path, args: argparse.Namespace) -> int:
    failed = [name for name, ok, _detail in _RESULTS if not ok]
    print(f"\nEnsaio: {len(_RESULTS) - len(failed)} de {len(_RESULTS)} conferencias passaram.")
    for name in failed:
        print(f"  FALHOU: {name}")
    if args.manter or args.saida:
        print(f"Arquivos mantidos em {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
