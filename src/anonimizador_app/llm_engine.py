"""Portao do motor LLM (3.0): quando, e so quando, o texto pode sair desta maquina.

O app abre sempre no motor **local**. O motor LLM nao e uma preferencia que
se grava: e uma escolha da sessao, e so fica DISPONIVEL se as condicoes de
configuracao valerem:

1. um dos dois canais cadastraveis foi escolhido (``gemini-pago``, ``vertex-sp``).
   ``gemini-gratuito``, canal vazio e canal desconhecido sao BLOQUEADOS;
2. ha chave (``credentials``) ou, no Vertex AI, projeto e credencial local;
3. os pacotes (``langextract``, ``google-genai``) importam;
4. o ``config.json`` nao guarda chave.

O app AVISA (sigilo fiscal, CTN art. 198; residencia dos dados; faturamento), mas nao bloqueia por
politica: decidir enviar, e quais arquivos, e do Operador. Por isso a escolha do motor nao autoriza
envio nenhum: cada RODADA exige uma confirmacao (``llm_gate.RunPermit``) com canal, regiao, lista de
arquivos e total de caracteres, que expira quando a rodada termina.

``check_engine`` so le o ambiente e o disco: nao abre conexao nenhuma.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .credentials import ApiKey, Vault, VertexSetup, load_key, load_vertex_setup, validate_service_account_file
from .llm_discovery import (
    DEFAULT_LOCATION,
    DEFAULT_MODEL,
    LangExtractDiscoverer,
    LlmConfig,
    LlmConfigError,
)
from .llm_gate import RunPermit, SendPlan, env_refusals, env_warnings
from .settings import config_path
from .version import ENGINE_LOCAL

# Muda quando o texto do aviso muda: a AUDITORIA diz que versao do aviso foi confirmada.
WARNING_VERSION = "2026-10-01.1"

CHANNEL_GEMINI_PAGO = "gemini-pago"
CHANNEL_VERTEX_SP = "vertex-sp"
CHANNEL_GEMINI_GRATUITO = "gemini-gratuito"


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    label: str
    provider: str  # gemini | vertex
    needs_key: bool = False
    needs_project: bool = False


CHANNELS: dict[str, Channel] = {
    CHANNEL_GEMINI_PAGO: Channel(
        CHANNEL_GEMINI_PAGO,
        "Gemini API paga (chave de projeto com faturamento)",
        "gemini",
        needs_key=True,
    ),
    CHANNEL_VERTEX_SP: Channel(
        CHANNEL_VERTEX_SP,
        "Vertex AI, São Paulo (projeto institucional do Google Cloud)",
        "vertex",
        needs_project=True,
    ),
}

BLOCKED_CHANNELS: dict[str, str] = {
    CHANNEL_GEMINI_GRATUITO: (
        "O canal gratuito do Gemini é vedado: o conteúdo pode ser usado para "
        "melhorar produtos, com revisão humana."
    ),
}

_KEY_FIELD = re.compile(
    r"(?i)^(?:api[_\- ]?key|apikey|chave(?:[_\- ]?api)?|secret\w*|senha|password|gemini\w*|google\w*)$"
)
_KEY_VALUE = re.compile(r"AIza[0-9A-Za-z_\-]{25,}")


def warning_paragraphs(channel: str) -> tuple[str, ...]:
    """O aviso que o usuario confirma antes de o texto sair da máquina."""

    common = (
        "O texto das peças será enviado ao provedor do modelo. CPF, CNPJ, OAB, conta, placa "
        "e demais identificadores são trocados por token ANTES do envio, nesta máquina; "
        "mas nomes, endereços, fatos e valores seguem em claro. Sigilo fiscal (CTN, art. 198) "
        "e LGPD valem para esse envio.",
        "O mapa dos tokens e o elenco com os nomes ficam só nesta máquina e nunca são enviados.",
        "O que decide o tratamento do texto é o canal da chave. Uma chave de projeto SEM "
        "faturamento cai nos termos do nível gratuito (o conteúdo pode ser usado para melhorar "
        "produtos, com revisão humana). Só chave de projeto com faturamento ativo é paga "
        "(não treina o modelo; adendo de tratamento de dados).",
    )
    if channel == CHANNEL_VERTEX_SP:
        specific = (
            "Vertex AI, região southamerica-east1: termos do contrato de nuvem e residência de "
            "dados no endpoint regional. Confira se o modelo escolhido está publicado nesse "
            "endpoint: o endpoint global não garante residência.",
        )
    else:
        specific = (
            "A Gemini API (Developer API) não oferece garantia de residência de dados: o texto "
            "pode ser processado fora do Brasil. Decidir se esse canal serve para este caso, e quais "
            "arquivos enviar, é do Operador.",
        )
    return (*common, *specific)


def warning_text(channel: str) -> str:
    return "\n\n".join(warning_paragraphs(channel))


def billing_note(channel: str) -> str:
    """Informacao (nao bloqueio) sobre o faturamento do canal."""

    if channel == CHANNEL_VERTEX_SP:
        return (
            "Faturamento: o projeto deve ser o institucional do Google Cloud, com faturamento ativo "
            "e contrato de nuvem."
        )
    return (
        "Faturamento: a chave deve ser de um projeto do Google Cloud COM faturamento ativo; sem "
        "faturamento o conteúdo cai nos termos do nível gratuito."
    )


@dataclass(frozen=True, slots=True)
class EngineChoice:
    """Escolha da sessao. NAO e gravada: o app sempre abre no motor local."""

    channel: str = ""
    project: str = ""
    model_id: str = DEFAULT_MODEL
    location: str = DEFAULT_LOCATION
    # Vertex AI: arquivo de conta de servico desta rodada (vazio = o cadastrado, ou o gcloud).
    credential_path: str = ""


@dataclass(frozen=True, slots=True)
class EngineDecision:
    allowed: bool
    channel: str = ""
    reasons: tuple[str, ...] = ()
    key: ApiKey | None = None
    config: LlmConfig | None = None
    key_source: str = ""

    @property
    def engine_id(self) -> str:
        """Id para a linha de versao: o canal, ou ``local`` quando o LLM nao roda."""

        return self.channel if self.allowed else ENGINE_LOCAL

    def blocked_message(self) -> str:
        return "; ".join(self.reasons)


def missing_packages(channel: str = CHANNEL_GEMINI_PAGO) -> list[str]:
    spec = CHANNELS.get(channel)
    needed = ["langextract"]
    if spec is None or spec.provider in {"gemini", "vertex"}:
        needed.append("google.genai")
    missing: list[str] = []
    for name in needed:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name.replace("google.genai", "google-genai"))
    return missing


def adc_available(env: Mapping[str, str] | None = None) -> bool:
    """Credencial local do Google Cloud (gcloud / conta de servico), so pela existencia do arquivo.

    Nao usa ``google.auth.default()``: ele consulta o servidor de metadados pela rede.
    """

    environment = os.environ if env is None else env
    explicit = (environment.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if explicit:
        return Path(explicit).is_file()
    appdata = (environment.get("APPDATA") or "").strip()
    home = Path(environment.get("USERPROFILE") or environment.get("HOME") or Path.home())
    candidates = [home / ".config" / "gcloud" / "application_default_credentials.json"]
    if appdata:
        candidates.insert(0, Path(appdata) / "gcloud" / "application_default_credentials.json")
    return any(path.is_file() for path in candidates)


def config_key_findings(private_root: Path | str | None, key: ApiKey | None = None) -> list[str]:
    """Onde o ``config.json`` guarda algo que parece chave. Nunca devolve o valor."""

    if private_root is None:
        return []
    candidate = Path(private_root)
    path = candidate if candidate.is_file() else config_path(candidate)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    findings: list[str] = []
    if key is not None and len(key.reveal()) >= 8 and key.reveal() in raw:
        findings.append("o valor da chave guardada aparece no config.json")
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    for where, value in _walk(payload):
        field = where.rsplit(".", 1)[-1]
        if isinstance(value, str) and value.strip() and _KEY_FIELD.match(field):
            findings.append(f"campo de chave no config.json: {where}")
        elif isinstance(value, str) and _KEY_VALUE.search(value):
            findings.append(f"valor com formato de chave no config.json: {where}")
    if payload is None and _KEY_VALUE.search(raw):
        findings.append("texto com formato de chave no config.json")
    return list(dict.fromkeys(findings))


def _walk(node: object, prefix: str = ""):
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _walk(value, f"{prefix}.{name}" if prefix else str(name))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{prefix}[{index}]")
    else:
        yield prefix, node


def check_engine(
    choice: EngineChoice,
    *,
    private_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    vault: Vault | None = None,
    missing_fn: Callable[[str], list[str]] | None = None,
    adc_fn: Callable[[], bool] | None = None,
) -> EngineDecision:
    """Aplica as seis condicoes. ``allowed`` so com todas; ``reasons`` lista o que falta."""

    channel = (choice.channel or "").strip().lower()
    if not channel:
        return EngineDecision(False, "", ("Nenhum canal escolhido: o app segue no motor local.",))
    if channel in BLOCKED_CHANNELS:
        return EngineDecision(False, channel, (BLOCKED_CHANNELS[channel],))
    spec = CHANNELS.get(channel)
    if spec is None:
        return EngineDecision(False, channel, (f"Canal desconhecido: {channel!r}.",))

    reasons: list[str] = []
    key: ApiKey | None = None
    if spec.needs_key:
        key = load_key(env=env, vault=vault)
        if key is None:
            reasons.append(
                "Sem chave guardada. Guarde-a no Gerenciador de Credenciais "
                "(ou na variável ANONIMIZADOR_GEMINI_PAGO_CHAVE); GEMINI_API_KEY e "
                "GOOGLE_API_KEY não são lidas de propósito."
            )
    vertex: VertexSetup | None = None
    if spec.needs_project:
        # Projeto e credencial: o que a tela/CLI informou nesta sessao, senao o cadastrado
        # (variavel de ambiente ou Gerenciador de Credenciais).
        vertex = load_vertex_setup(env=env, vault=vault)
        project = choice.project.strip() or vertex.project
        credential_path = choice.credential_path.strip() or vertex.credential_path
        if not project:
            reasons.append(
                "Vertex AI exige o projeto do Google Cloud (informe-o ou cadastre-o: Gerenciador de "
                "Credenciais, ou a variavel ANONIMIZADOR_VERTEX_PROJETO)."
            )
        if credential_path:
            problem = validate_service_account_file(credential_path)
            if problem:
                reasons.append(problem)
        elif not (adc_fn or (lambda: adc_available(env)))():
            reasons.append(
                "Sem credencial do Google Cloud: aponte o arquivo de conta de servico do projeto "
                "institucional ou rode gcloud auth application-default login."
            )
    # I4: variavel do SDK que desvia o trafego para outro endereco recusa ja na configuracao (e de novo no
    # ponto da rede, em ``llm_gate.check_network_point``).
    reasons.extend(env_refusals(env))
    missing = (missing_fn or missing_packages)(channel)
    if missing:
        reasons.append(
            "Pacotes ausentes: " + ", ".join(missing) + ". Rode: pip install -e .[llm]"
        )
    for finding in config_key_findings(private_root, key):
        reasons.append(f"{finding}. Remova: a chave não pode ficar em arquivo do projeto.")

    if reasons:
        return EngineDecision(False, channel, tuple(reasons), key=None, key_source=key.source if key else "")
    config = LlmConfig(
        provider=spec.provider,
        channel=channel,
        api_key=key,
        model_id=choice.model_id or DEFAULT_MODEL,
        project=(choice.project.strip() or (vertex.project if vertex else "")),
        location=choice.location or DEFAULT_LOCATION,
        credentials_path=(choice.credential_path.strip() or (vertex.credential_path if vertex else "")),
    )
    return EngineDecision(
        True, channel, (), key=key, config=config, key_source=key.source if key else ""
    )


def build_discoverer(
    decision: EngineDecision,
    *,
    cache_dir: Path | None,
    max_workers: int | None = None,
    chunk_chars: int | None = None,
    permit: RunPermit | None = None,
) -> LangExtractDiscoverer:
    """Descobridor com rede, so com o portao liberado. Sem ``permit`` ativo (a confirmacao da rodada),
    ele le o cache mas nao chama o provedor."""

    if not decision.allowed or decision.config is None:
        raise LlmConfigError("Motor LLM bloqueado: " + (decision.blocked_message() or "sem decisao."))
    config = decision.config
    overrides = {}
    if max_workers is not None:
        overrides["max_workers"] = max(1, max_workers)
    if chunk_chars is not None:
        overrides["chunk_chars"] = max(500, chunk_chars)
    if overrides:
        config = replace(config, **overrides)
    return LangExtractDiscoverer(config, cache_dir=cache_dir, permit=permit)


def build_offline_discoverer(
    channel: str,
    *,
    cache_dir: Path | None,
    model_id: str = DEFAULT_MODEL,
    chunk_chars: int | None = None,
) -> LangExtractDiscoverer:
    """Descobridor SEM rede: so le o cache de uma rodada anterior desse canal.

    Nao precisa de chave nem de aviso (nada sai da maquina) e nao cria modelo.
    """

    spec = CHANNELS.get(channel)
    if spec is None:
        raise LlmConfigError(f"Canal desconhecido: {channel!r}.")
    config = LlmConfig(provider=spec.provider, channel=channel, model_id=model_id)
    if chunk_chars is not None:
        config = replace(config, chunk_chars=max(500, chunk_chars))
    return LangExtractDiscoverer(config, cache_dir=cache_dir, offline=True)



def confirmation_lines(
    decision: EngineDecision, plan: SendPlan, *, files_in_full: bool = True
) -> list[str]:
    """O que a confirmacao da rodada mostra (tela e CLI): canal, regiao, arquivos, total de caracteres.

    So informa; o Operador decide. Os nomes de arquivo aparecem aqui (e so aqui): a AUDITORIA
    exportavel nao os leva."""

    spec = CHANNELS.get(decision.channel)
    lines = [f"Canal: {decision.channel} ({spec.label if spec else '?'})"]
    config = decision.config
    if config is not None and config.provider == "vertex":
        lines.append(f"Região do Vertex AI: {config.location} (projeto {config.project or '?'})")
    if config is not None:
        lines.append(f"Modelo: {config.model_id}")
    lines.append(f"Arquivos que serão enviados ({len(plan.files)}):")
    for item in plan.files:
        lines.append(
            f"  - {item.name}: {item.chars:,} caracteres, {item.chunks} trecho(s)"
            f" ({item.cached_chunks} já no cache local)".replace(",", ".")
        )
    for name, message in plan.errors:
        lines.append(f"  - {name}: NÃO será enviado ({message})")
    lines.append(
        f"Total: {plan.total_chars:,} caracteres na descoberta, dos quais {plan.pending_chars:,} "
        "ainda não estão no cache local (os do cache não são reenviados).".replace(",", ".")
    )
    if plan.verification_rounds:
        lines.append(
            f"A conferência reenvia o texto já tokenizado, até {plan.verification_rounds} rodada(s) "
            "do mesmo tamanho."
        )
    lines.append("Os IDs (CPF, CNPJ, OAB, conta...) vão mascarados; nomes, fatos e valores vão em claro.")
    lines.extend(f"Atenção: {warning}" for warning in env_warnings())
    return lines


def audit_lines(
    decision: EngineDecision,
    choice: EngineChoice | None = None,
    permit: RunPermit | None = None,
) -> list[str]:
    """Linhas do bloco "motor" da AUDITORIA: canal, regiao, fonte da chave e a confirmacao da rodada.
    Nunca a chave."""

    spec = CHANNELS.get(decision.channel)
    lines = [f"Canal: {decision.channel or ENGINE_LOCAL}"]
    if decision.config is not None:
        lines.append(f"Modelo: {decision.config.model_id}")
        if decision.config.provider == "vertex":
            lines.append(f"Projeto Vertex AI e regiao: {decision.config.location}")
            lines.append(
                "Credencial do Vertex AI: "
                + (
                    "arquivo de conta de servico (o caminho nao e gravado)"
                    if decision.config.credentials_path
                    else "credencial padrao do gcloud"
                )
            )
    if spec is not None:
        lines.append(
            "O texto das pecas, com os IDs mascarados, foi enviado ao provedor; "
            "nomes, fatos e valores seguiram em claro."
        )
        if decision.key_source:
            lines.append(f"Chave lida de: {decision.key_source} (o valor nao e gravado em lugar nenhum)")
        lines.append(
            f"Aviso (versao {WARNING_VERSION}: sigilo fiscal, residencia, faturamento) apresentado "
            "na confirmacao da rodada; a decisao de enviar foi do Operador."
        )
        if permit is not None:
            lines.extend(permit.audit_lines())
    return lines
