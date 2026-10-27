"""Anonimiza com o motor LLM (LangExtract) e grava um lote. O padrao do app segue sendo o motor local.

O texto das pecas, com os IDs ja mascarados, vai ao provedor. Quem decide enviar, e quais arquivos, e o
Operador, A CADA RODADA: o comando mostra o canal (e a regiao, no Vertex AI), a lista de arquivos e o total
de caracteres, mais os avisos (sigilo fiscal, CTN art. 198; residencia dos dados; faturamento), e SO
ENVIA se a rodada trouxer --confirmo-envio. O aviso e informacao: o app nao bloqueia por politica. Sem a
opcao, nada e enviado (codigo 2) e a lista serve de conferencia. A confirmacao vale para esta rodada e expira
no fim dela.

Exemplos:

  # 1. Guardar a chave no Gerenciador de Credenciais do Windows (pede sem mostrar na tela):
  python tools/anonimizar_llm.py --guardar-chave

  # 2. Conferir a configuracao (nao envia nada):
  python tools/anonimizar_llm.py --verificar --canal gemini-pago

  # 3. Ver o que seria enviado (lista de arquivos e caracteres; nada e enviado):
  python tools/anonimizar_llm.py autos.pdf --canal gemini-pago

  # 4. Enviar (Gemini API paga; chave de projeto COM faturamento), confirmando esta rodada:
  python tools/anonimizar_llm.py autos.pdf --canal gemini-pago --confirmo-envio

  # Vertex AI (credencial do gcloud ou arquivo de conta de servico; contrato do Google Cloud):
  python tools/anonimizar_llm.py autos.pdf --canal vertex-sp --projeto MEU-PROJETO --confirmo-envio

  # Depois de revisar o elenco (trocar "acao" de uma entrada), SEM rede (so o cache; nada e enviado):
  python tools/anonimizar_llm.py autos.pdf --canal gemini-pago --sem-rede --elenco saidas/privados/lote_.../elenco.json

  # Apagar a chave guardada:
  python tools/anonimizar_llm.py --apagar-chave

  # Cadastrar o Vertex AI (projeto + arquivo de conta de servico, ou so o projeto e o gcloud):
  python tools/anonimizar_llm.py --guardar-vertex
  python tools/anonimizar_llm.py --apagar-vertex

Codigos de saida: 0 = ok; 1 = lote nao liberado (erro, folha retida); 2 = configuracao, portao,
argumento ou rodada sem --confirmo-envio; 3 = alguma folha foi tratada pelo motor local por falha do LLM (use --aceitar-fallback para
aceitar). O texto da folha que o LLM nao leu nunca sai em claro: o motor local o trata ou o arquivo fica
retido na pasta privada.

LLM local (H9-b): --canal ollama-local usa o Ollama desta maquina (127.0.0.1:11434). Nada sai da maquina:
nao pede --confirmo-envio, nao imprime o aviso de sigilo e roda com a rede bloqueada fora do loopback
(a AUDITORIA conta as tentativas; tem de dar 0). Modelo padrao qwen3:4b (--modelo; nunca ':cloud').

  python tools/anonimizar_llm.py autos.pdf --canal ollama-local
  python tools/anonimizar_llm.py autos.pdf --canal ollama-local --modelo qwen3:8b

Canal de TESTE (H9-p): --canal openrouter-teste usa o OpenRouter (API compativel com a da OpenAI) e SO roda
com o documento sintetico: o SHA-256 de cada arquivo tem de estar na lista do codigo (llm_gate.SYNTHETIC_SHA256),
senao nada e lido nem enviado. Pede --confirmo-envio. --modelo e o id do OpenRouter (ex.: qwen/qwen3-30b-a3b).
Chave: Gerenciador de Credenciais, servico AnonimizadorJuridico/openrouter (ou ANONIMIZADOR_OPENROUTER_CHAVE).

  python tools/anonimizar_llm.py autos_sinteticos.pdf --canal openrouter-teste --modelo qwen/qwen3-30b-a3b --confirmo-envio

A chave nunca vai para argumento de linha de comando, arquivo do projeto, log ou AUDITORIA.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from anonimizador_app.credentials import (  # noqa: E402
    ENV_VAR,
    OPENROUTER_ENV_VAR,
    OPENROUTER_VAULT_SERVICE,
    ENV_VERTEX_CREDENTIAL,
    ENV_VERTEX_PROJECT,
    VAULT_SERVICE,
    VERTEX_VAULT_SERVICE,
    CredentialsError,
    delete_key,
    delete_vertex_setup,
    describe_vertex,
    load_vertex_setup,
    store_key,
    store_openrouter_key,
    store_vertex_setup,
)
from anonimizador_app.llm_discovery import (  # noqa: E402
    DEFAULT_LOCATION,
    DEFAULT_MODEL,
    DEFAULT_THINKING,
    THINKING_LEVELS,
    LlmConfigError,
)
from anonimizador_app.llm_engine import (  # noqa: E402
    CHANNELS,
    TEST_CHANNELS,
    WARNING_VERSION,
    EngineChoice,
    audit_lines,
    billing_note,
    build_discoverer,
    build_offline_discoverer,
    channel_spec,
    check_engine,
    confirmation_lines,
    is_local_channel,
    warning_text,
)
from anonimizador_app.llm_gate import (  # noqa: E402
    RunPermit,
    file_sha256,
    gemma_lock_refusals,
    is_free_tier_gemma,
    synthetic_lock_refusals,
)
from anonimizador_app.local_ner import ner_fallback_warning  # noqa: E402
from anonimizador_app.netblock import block_network  # noqa: E402
from anonimizador_app.pipeline import default_export_dir, default_private_dir  # noqa: E402
from anonimizador_app.pipeline_llm import LLM_CACHE_SUBDIR, fallback_split, plan_send, run_llm_job  # noqa: E402
from anonimizador_app.settings import load_settings  # noqa: E402


def _with_operation(settings, args):
    """H9-l: ``--operacao`` vence a escolha da aba Caso; "todas" usa a lista inteira."""

    chosen = getattr(args, "operacao", None)
    if chosen is not None:
        settings.case_operation = "" if chosen.strip().casefold() == "todas" else chosen.strip()
    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Anonimiza PDF/DOCX/TXT/MD: IDs locais, elenco por LLM, troca local."
    )
    parser.add_argument("arquivos", nargs="*", help="Arquivos do mesmo lote (mesmos tokens).")
    parser.add_argument(
        "--canal",
        choices=(*CHANNELS, *TEST_CHANNELS),
        help="gemini-pago = Gemini API paga; vertex-sp = Vertex AI Sao Paulo; ollama-local = LLM local "
        "(Ollama em 127.0.0.1, nada sai da maquina); openrouter-teste = SO TESTE, so o documento sintetico. "
        "Sem canal, nada roda.",
    )
    parser.add_argument(
        "--confirmo-envio",
        action="store_true",
        help="Confirmo, para ESTA rodada, o envio ao provedor dos arquivos e do total de caracteres "
        "listados (impressos antes). Sem esta opcao nada e enviado. Nao vale para a proxima rodada. "
        "Nao e exigida no canal ollama-local (nada sai da maquina).",
    )
    parser.add_argument(
        "--guardar-chave",
        action="store_true",
        help="Pede a chave (sem mostrar) e guarda no Gerenciador de Credenciais do Windows.",
    )
    parser.add_argument("--apagar-chave", action="store_true", help="Apaga a chave guardada.")
    parser.add_argument(
        "--guardar-chave-openrouter",
        action="store_true",
        help="Pede a chave do OpenRouter (sem mostrar) e guarda no Gerenciador de Credenciais (canal de teste).",
    )
    parser.add_argument(
        "--guardar-vertex",
        action="store_true",
        help="Pede o projeto do Google Cloud e o caminho do arquivo de conta de servico (vazio = gcloud) e "
        "guarda no Gerenciador de Credenciais do Windows (so o caminho, nunca o conteudo do arquivo).",
    )
    parser.add_argument("--apagar-vertex", action="store_true", help="Apaga o cadastro do Vertex AI.")
    parser.add_argument(
        "--credencial",
        default="",
        help="Arquivo de conta de servico do Vertex AI so para esta rodada (senao: o cadastrado, ou o gcloud).",
    )
    parser.add_argument(
        "--verificar", action="store_true", help="Mostra o que falta para liberar o motor; nao envia nada."
    )
    parser.add_argument(
        "--sem-rede",
        action="store_true",
        help="Reaplica o elenco usando so o cache (nenhum socket e aberto; sem chave nem aviso).",
    )
    parser.add_argument("--modelo", default=DEFAULT_MODEL, help=f"Padrao: {DEFAULT_MODEL}.")
    parser.add_argument("--projeto", default="", help="Projeto do Google Cloud (Vertex AI).")
    parser.add_argument(
        "--regiao", default=DEFAULT_LOCATION, help=f"Regiao do Vertex AI (padrao {DEFAULT_LOCATION})."
    )
    parser.add_argument("--paralelo", type=int, default=4, help="Chamadas simultaneas ao LLM (padrao 4).")
    parser.add_argument(
        "--operacao",
        default=None,
        help="H9-l: id da operacao do Nexus (so a lista dela); vazio = a escolhida na aba Caso; \"todas\" = lista inteira.",
    )
    parser.add_argument(
        "--trecho",
        type=int,
        default=None,
        help="Caracteres por chamada (padrao 4000; 2000 no canal ollama-local).",
    )
    parser.add_argument(
        "--raciocinio",
        choices=sorted(THINKING_LEVELS),
        default=DEFAULT_THINKING,
        help=(
            f"Quanto o modelo raciocina (cobrado como saida). Padrao: {DEFAULT_THINKING}. 'padrao' deixa o nivel "
            "do modelo; num modelo que pensa muito, a resposta pode voltar cortada."
        ),
    )
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
    parser.add_argument(
        "--piso-local",
        dest="piso_local",
        action="store_true",
        default=None,
        help="Piso local (padrao: ligado, ou o que o config.json diz): o motor local roda em TODAS as folhas e o "
        "resultado e a uniao com o LLM, que so acrescenta protecao.",
    )
    parser.add_argument(
        "--sem-piso-local",
        dest="piso_local",
        action="store_false",
        help="Desliga o piso local: o motor local so trata as folhas que o LLM nao leu ou respondeu vazio.",
    )
    parser.add_argument(
        "--aceitar-fallback",
        action="store_true",
        help="Aceita o resultado mesmo que alguma folha tenha sido tratada pelo motor local por falha "
        "do LLM. Sem esta opcao, o comando sai com codigo 3 nesse caso (o texto e gravado, mas conferir "
        "essas folhas e do Operador).",
    )
    parser.add_argument("--sem-cache", action="store_true", help="Relê o PDF e reenvia ao LLM.")
    parser.add_argument("--sem-legenda", action="store_true", help="Nao grava a secao de papeis do ELENCO.md.")
    return parser


def manage_key(args: argparse.Namespace) -> int | None:
    """``--guardar-chave`` / ``--apagar-chave``. ``None`` se nenhum dos dois foi pedido."""

    try:
        if args.apagar_chave:
            removed = delete_key()
            print("Chave apagada do Gerenciador de Credenciais." if removed else "Nao havia chave guardada.")
        if args.guardar_chave:
            value = getpass.getpass(
                "Chave da API (projeto com faturamento; nao aparece na tela): "
            )
            key = store_key(value)
            print(f"Chave guardada no Gerenciador de Credenciais (servico {VAULT_SERVICE}); {key.source}.")
        if args.guardar_chave_openrouter:
            value = getpass.getpass("Chave do OpenRouter (nao aparece na tela): ")
            key = store_openrouter_key(value)
            print(f"Chave guardada no Gerenciador de Credenciais (servico {OPENROUTER_VAULT_SERVICE}); {key.source}.")
        if args.apagar_vertex:
            removed = delete_vertex_setup()
            print("Cadastro do Vertex AI apagado." if removed else "Nao havia cadastro do Vertex AI.")
        if args.guardar_vertex:
            project = input("Projeto do Google Cloud (Vertex AI): ")
            credential = input("Arquivo de conta de servico .json (vazio = credencial do gcloud): ")
            setup = store_vertex_setup(project, credential)
            print(f"Guardado no Gerenciador de Credenciais (servico {VERTEX_VAULT_SERVICE}): {describe_vertex(setup)}.")
    except CredentialsError as exc:
        print(f"Credencial: {exc}", file=sys.stderr)
        return 2
    if (
        args.apagar_chave
        or args.guardar_chave
        or args.guardar_chave_openrouter
        or args.apagar_vertex
        or args.guardar_vertex
    ):
        return 0 if not args.arquivos else None
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    handled = manage_key(args)
    if handled is not None:
        return handled
    if not args.arquivos and not args.verificar:
        print("Informe os arquivos (ou use --guardar-chave / --verificar).", file=sys.stderr)
        return 2

    private_root = Path(args.privados).expanduser() if args.privados else default_private_dir(ROOT)
    export_root = (
        Path(args.exportaveis).expanduser() if args.exportaveis else default_export_dir(ROOT)
    )
    config_source = Path(args.config).expanduser() if args.config else private_root
    cache_dir = None if args.sem_cache else private_root / LLM_CACHE_SUBDIR
    channel = args.canal or ""
    local = is_local_channel(channel)

    if args.sem_rede:
        return run_offline(args, channel, config_source, export_root, private_root, cache_dir)

    choice = EngineChoice(
        channel=channel,
        project=args.projeto,
        model_id=args.modelo,
        location=args.regiao,
        credential_path=args.credencial,
    )
    decision = check_engine(choice, private_root=config_source)
    if channel_spec(channel) is not None and not local:
        print(f"AVISO (versao {WARNING_VERSION}, canal {channel}), so informacao:", file=sys.stderr)
        print(warning_text(channel), file=sys.stderr)
        print(billing_note(channel), file=sys.stderr)
    if args.verificar:
        return report_gate(decision)
    if not decision.allowed:
        print("Motor LLM bloqueado:", file=sys.stderr)
        for reason in decision.reasons:
            print(f"  - {reason}", file=sys.stderr)
        print("O app segue disponivel no motor local (sem LLM).", file=sys.stderr)
        return 2

    missing = missing_files(args)
    if missing:
        return 2
    lock: tuple[str, ...] = ()
    if decision.channel in TEST_CHANNELS:
        # H9-p: a trava ANTES de ler o arquivo: so o documento sintetico sai pelo canal de teste.
        refusals = synthetic_lock_refusals(Path(item).expanduser() for item in args.arquivos)
        if refusals:
            print("Canal de teste recusado (so o documento sintetico pode ser enviado):", file=sys.stderr)
            for reason in refusals:
                print(f"  - {reason}", file=sys.stderr)
            return 2
        lock = tuple(file_sha256(Path(item).expanduser()) for item in args.arquivos)
        print("Trava do canal de teste: todos os arquivos sao o documento sintetico (SHA-256 conferido).")
    elif decision.config is not None and is_free_tier_gemma(decision.channel, decision.config.model_id):
        # v0.4.10: Gemma na Gemini API e sempre nivel gratuito: a mesma trava do documento sintetico.
        refusals = gemma_lock_refusals(
            decision.channel, decision.config.model_id, (Path(item).expanduser() for item in args.arquivos)
        )
        if refusals:
            print("Modelo Gemma recusado no gemini-pago (so o documento sintetico pode ser enviado):", file=sys.stderr)
            for reason in refusals:
                print(f"  - {reason}", file=sys.stderr)
            return 2
        lock = tuple(file_sha256(Path(item).expanduser()) for item in args.arquivos)
        print("Trava do Gemma (nivel gratuito): todos os arquivos sao o documento sintetico (SHA-256 conferido).")
    discoverer = build_discoverer(
        decision,
        cache_dir=cache_dir,
        max_workers=max(1, args.paralelo),
        chunk_chars=args.trecho,
        thinking=args.raciocinio,
    )
    discoverer.synthetic_lock = lock
    local = is_local_channel(decision.channel)
    settings = _with_operation(load_settings(config_source), args)
    sources = [Path(item).expanduser() for item in args.arquivos]
    try:
        plan = plan_send(
            sources,
            private_root=private_root,
            discoverer=discoverer,
            settings=settings,
            verification_rounds=max(0, args.verificacao),
            use_cache=not args.sem_cache,
            on_progress=lambda message, _fraction: print(message, flush=True),
        )
    except LlmConfigError as exc:
        print(f"Configuracao: {exc}", file=sys.stderr)
        return 2
    print("LLM LOCAL (Ollama, loopback), NADA ENVIADO; arquivos desta rodada:" if local else "O QUE ESTA RODADA VAI ENVIAR AO PROVEDOR:")
    for line in confirmation_lines(decision, plan):
        print("  " + line)
    if not plan.files:
        print("Nenhum arquivo pode ser enviado.", file=sys.stderr)
        return 2
    if local:
        # H8: nada sai da maquina; sem confirmacao de envio e sem RunPermit.
        return run_and_report(
            args,
            discoverer,
            export_root,
            private_root,
            config_source,
            engine=decision.channel,
            engine_notes=audit_lines(decision, choice, None),
        )
    if not args.confirmo_envio:
        print(
            "Nada foi enviado. Confira a lista acima e, se decidir enviar, repita o comando com "
            "--confirmo-envio (a confirmacao vale so para esta rodada).",
            file=sys.stderr,
        )
        return 2
    permit = RunPermit.for_plan(
        decision.channel,
        plan,
        region=decision.config.location if decision.config and decision.config.provider == "vertex" else "",
        project=decision.config.project if decision.config else "",
    )
    discoverer.permit = permit
    return run_and_report(
        args,
        discoverer,
        export_root,
        private_root,
        config_source,
        engine=decision.channel,
        engine_notes=audit_lines(decision, choice, permit),
    )


def report_gate(decision) -> int:
    print(f"Canal: {decision.channel or '(nenhum)'}")
    if is_local_channel(decision.channel):
        print("LLM local: Ollama em 127.0.0.1:11434; sem chave, sem projeto, nada sai da maquina.")
    elif decision.channel in TEST_CHANNELS:
        print(
            f"Chave do OpenRouter: {decision.key_source or 'nenhuma chave guardada'} "
            f"(variavel {OPENROUTER_ENV_VAR} ou Gerenciador de Credenciais, servico {OPENROUTER_VAULT_SERVICE})"
        )
        print("Canal de TESTE: so roda com o documento sintetico (SHA-256 conferido antes de ler o arquivo).")
    elif decision.channel == "vertex-sp":
        setup = load_vertex_setup()
        print(
            f"Vertex AI: {describe_vertex(setup)} "
            f"(variaveis {ENV_VERTEX_PROJECT} e {ENV_VERTEX_CREDENTIAL}, ou Gerenciador de Credenciais)"
        )
    else:
        print(
            f"Chave: {decision.key_source or 'nenhuma chave guardada'} "
            f"(variavel {ENV_VAR} ou Gerenciador de Credenciais)"
        )
    if decision.allowed:
        print("Motor LLM LIBERADO. Nada foi enviado.")
        return 0
    print("Motor LLM BLOQUEADO:")
    for reason in decision.reasons:
        print(f"  - {reason}")
    return 1


def missing_files(args: argparse.Namespace) -> list[str]:
    missing = [item for item in args.arquivos if not Path(item).expanduser().is_file()]
    if missing:
        print("Arquivo nao encontrado: " + ", ".join(missing), file=sys.stderr)
    return missing


def run_offline(
    args: argparse.Namespace,
    channel: str,
    config_source: Path,
    export_root: Path,
    private_root: Path,
    cache_dir: Path | None,
) -> int:
    if channel_spec(channel) is None:
        print("--sem-rede precisa do --canal da rodada anterior (o cache e por canal).", file=sys.stderr)
        return 2
    if not args.arquivos or missing_files(args):
        return 2
    try:
        discoverer = build_offline_discoverer(
            channel, cache_dir=cache_dir, model_id=args.modelo, chunk_chars=args.trecho
        )
    except LlmConfigError as exc:
        print(f"Configuracao: {exc}", file=sys.stderr)
        return 2
    notes = ["Canal: " + channel, "Modo sem rede: so o cache; nenhum socket foi aberto."]
    # Defesa em profundidade: qualquer tentativa de conexao aborta a rodada.
    with block_network() as attempts:
        code = run_and_report(
            args, discoverer, export_root, private_root, config_source, engine=channel, engine_notes=notes
        )
    if attempts:
        print(f"ALERTA: {len(attempts)} tentativa(s) de rede bloqueada(s).", file=sys.stderr)
        return 1
    return code


def run_and_report(
    args: argparse.Namespace,
    discoverer,
    export_root: Path,
    private_root: Path,
    config_source: Path,
    *,
    engine: str,
    engine_notes: list[str],
) -> int:
    sources = [Path(item).expanduser() for item in args.arquivos]
    settings = _with_operation(load_settings(config_source), args)
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
            engine=engine,
            engine_notes=engine_notes,
            on_progress=lambda message, _fraction: print(message, flush=True),
            local_floor=args.piso_local,
        )
    except LlmConfigError as exc:
        print(f"Configuracao: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started

    job = result.job
    print(f"Motor: LLM {engine}")
    print("Piso local: " + ("LIGADO (motor local em todas as folhas; uniao com o LLM)" if result.local_floor else "DESLIGADO"))
    print(f"Textos: {job.export_dir}")
    print(f"Mapa e elenco (privados): {job.map_path.parent}")
    print(f"Tempo: {elapsed:.1f}s")
    for line in getattr(discoverer, "usage_lines", lambda: [])():
        print(line)
    blocked = getattr(discoverer, "blocked_attempts", None) if is_local_channel(engine) else None
    if blocked is not None:
        print(f"Tentativas de rede fora de loopback bloqueadas: {blocked}")
    for name, report in result.reports.items():
        found = report.discovery
        print(
            f"  {name}: {found.chunks} trechos ({found.cached} do cache), "
            f"{len(found.failed)} falha(s) na descoberta, "
            f"+{report.new_in_verification} na verificacao"
        )
    for outcome in job.failed:
        print(f"ERRO {outcome.source.name}: {outcome.error}", file=sys.stderr)
    ner_warning = ner_fallback_warning()
    if ner_warning:
        print(f"AVISO NER: {ner_warning}", file=sys.stderr)
    # v0.4.13: falha real do LLM e omissao do LLM coberta pelo motor local em linhas separadas.
    for name, (failed, omitted) in fallback_split(result).items():
        if failed:
            print(
                f"FALLBACK {name}: folhas tratadas pelo motor local por falha do LLM: "
                + ", ".join(str(page) for page in failed),
                file=sys.stderr,
            )
        if omitted:
            print(
                f"FALLBACK {name}: folhas em que o LLM respondeu sem nomes e o motor local achou: "
                + ", ".join(str(page) for page in omitted),
                file=sys.stderr,
            )
    if not job.export_allowed:
        print(f"NAO ENVIE AINDA: {job.export_block_reason}", file=sys.stderr)
        return 1
    if blocked:
        print(
            f"ALERTA: {blocked} tentativa(s) de rede fora de loopback foram bloqueadas durante a rodada "
            "local (veja a AUDITORIA); confira antes de usar o resultado.",
            file=sys.stderr,
        )
        return 1
    if result.fallback_pages and not args.aceitar_fallback:
        print(
            "Houve folha tratada pelo motor local por falha do LLM (acima). Confira essas folhas; "
            "para aceitar sem este codigo de saida, rode de novo com --aceitar-fallback.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
