"""Portao do motor LLM (3.0): quando, e so quando, o texto pode sair desta maquina.

O app abre sempre no motor **local**. O motor LLM nao e uma preferencia que
se grava: e uma escolha da sessao, e roda somente se TODAS as condicoes
valerem:

1. um dos dois canais cadastraveis foi escolhido (``gemini-pago``, ``vertex-sp``).
   ``gemini-gratuito``, canal vazio e canal desconhecido sao BLOQUEADOS;
2. ha chave (``credentials``) ou, no Vertex AI, projeto e credencial local;
3. o aviso do canal foi lido e confirmado;
4. o faturamento do projeto foi confirmado de forma explicita;
5. os pacotes (``langextract``, ``google-genai``) importam;
6. o ``config.json`` nao guarda chave.

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

from .credentials import ApiKey, Vault, load_key
from .llm_discovery import (
    DEFAULT_LOCATION,
    DEFAULT_MODEL,
    LangExtractDiscoverer,
    LlmConfig,
    LlmConfigError,
)
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
            "pode ser processado fora do Brasil. Só prossiga se a instituição aceita esse canal.",
        )
    return (*common, *specific)


def warning_text(channel: str) -> str:
    return "\n\n".join(warning_paragraphs(channel))


def billing_statement(channel: str) -> str:
    if channel == CHANNEL_VERTEX_SP:
        return (
            "Confirmo que o projeto é o institucional do Google Cloud, com faturamento ativo "
            "e contrato de nuvem."
        )
    return (
        "Confirmo que a chave é de um projeto do Google Cloud COM faturamento ativo "
        "(não é chave gratuita)."
    )


@dataclass(frozen=True, slots=True)
class EngineChoice:
    """Escolha da sessao. NAO e gravada: o app sempre abre no motor local."""

    channel: str = ""
    acknowledged: bool = False
    billing_confirmed: bool = False
    project: str = ""
    model_id: str = DEFAULT_MODEL
    location: str = DEFAULT_LOCATION


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
    if spec.needs_project:
        if not choice.project.strip():
            reasons.append("Vertex AI exige o projeto do Google Cloud.")
        if not (adc_fn or (lambda: adc_available(env)))():
            reasons.append(
                "Sem credencial local do Google Cloud (gcloud auth application-default login)."
            )
    if not choice.acknowledged:
        reasons.append("Falta confirmar que o aviso foi lido.")
    if not choice.billing_confirmed:
        reasons.append("Falta confirmar o faturamento do projeto.")
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
        project=choice.project.strip(),
        location=choice.location or DEFAULT_LOCATION,
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
) -> LangExtractDiscoverer:
    """Descobridor com rede, so com o portao liberado."""

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
    return LangExtractDiscoverer(config, cache_dir=cache_dir)


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



def audit_lines(decision: EngineDecision, choice: EngineChoice | None = None) -> list[str]:
    """Linhas do bloco "motor" da AUDITORIA: canal, fonte da chave e confirmacoes. Nunca a chave."""

    spec = CHANNELS.get(decision.channel)
    lines = [f"Canal: {decision.channel or ENGINE_LOCAL}"]
    if decision.config is not None:
        lines.append(f"Modelo: {decision.config.model_id}")
        if decision.config.provider == "vertex":
            lines.append(f"Projeto Vertex AI e regiao: {decision.config.location}")
    if spec is not None:
        lines.append(
            "O texto das pecas, com os IDs mascarados, foi enviado ao provedor; "
            "nomes, fatos e valores seguiram em claro."
        )
        if decision.key_source:
            lines.append(f"Chave lida de: {decision.key_source} (o valor nao e gravado em lugar nenhum)")
        if choice is not None:
            lines.append(
                f"Aviso (versao {WARNING_VERSION}) lido e faturamento do projeto confirmado "
                "pelo usuario: "
                + ("sim" if choice.acknowledged and choice.billing_confirmed else "NAO")
            )
    return lines
