"""Portao no ponto da rede e confirmacao por rodada do motor LLM.

Este modulo e o que o ``llm_discovery`` consulta no momento de falar com o provedor (o ``llm_engine``
decide antes, na tela e na CLI; aqui se confere de novo, onde o texto sai da maquina):

- ``RunPermit``: a confirmacao de UMA rodada. A escolha do motor LLM nao fica armada: cada rodada precisa
  de uma confirmacao (canal, regiao, arquivos, total de caracteres), que vale so enquanto a rodada
  roda e expira quando ela termina. Sem permissao ativa o modelo nem e criado.
- (I5) nenhum recurso de reproducao/depuracao do SDK ou do LangExtract grava o texto enviado em disco:
  as variaveis do SDK que ligam isso sao recusadas, o cliente criado e conferido, e o log das bibliotecas
  fica em aviso;
- ``check_network_point`` (I4): o canal e um dos cadastrados, o host do endpoint e o oficial da Google
  para ele, e nenhuma variavel do SDK redireciona o trafego; ``verify_client_endpoint`` confere o
  endereco do cliente ja criado;
- (H8) canal ``ollama-local``: so o loopback ``127.0.0.1`` na porta 11434 do Ollama; o endereco e
  comparado com uma lista fixa (nenhum parser de URL decide), variavel que aponte para outro lugar,
  proxy e modelo ``:cloud`` sao recusados.

Nada aqui abre rede.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse


class GateError(RuntimeError):
    """O portao recusou o envio. A mensagem nunca traz texto de peca nem chave."""


# Os dois canais cadastraveis e o provedor de cada um.
CHANNEL_PROVIDERS: dict[str, str] = {
    "gemini-pago": "gemini",
    "vertex-sp": "vertex",
    # H8: LLM local (Ollama) so em loopback; nada sai da maquina.
    "ollama-local": "ollama",
}


@dataclass(frozen=True, slots=True)
class FilePlan:
    """O que um arquivo manda ao provedor na descoberta (texto ja com os IDs mascarados)."""

    name: str
    pages: int
    chars: int
    chunks: int
    cached_chunks: int
    pending_chars: int


@dataclass(frozen=True, slots=True)
class SendPlan:
    """O que a rodada vai mandar ao provedor. So contagens e nomes de arquivo."""

    files: tuple[FilePlan, ...]
    verification_rounds: int = 1
    # Arquivos que nao puderam ser lidos e, portanto, nao serao enviados: (nome, motivo curto).
    errors: tuple[tuple[str, str], ...] = ()

    @property
    def total_chars(self) -> int:
        return sum(item.chars for item in self.files)

    @property
    def pending_chars(self) -> int:
        """Caracteres que de fato serao enviados na descoberta (o resto ja esta no cache local)."""

        return sum(item.pending_chars for item in self.files)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.files)


@dataclass(slots=True)
class RunPermit:
    """Confirmacao de uma rodada. Nasce ``nova``, vale ``em andamento`` e expira ao fim: uso unico."""

    channel: str
    files: tuple[str, ...]
    total_chars: int
    pending_chars: int = 0
    region: str = ""
    project: str = ""
    verification_rounds: int = 1
    confirmed_at: float = field(default_factory=time.time)
    _state: str = field(default="nova", repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def for_plan(
        cls, channel: str, plan: SendPlan, *, region: str = "", project: str = ""
    ) -> "RunPermit":
        return cls(
            channel=channel,
            files=plan.names,
            total_chars=plan.total_chars,
            pending_chars=plan.pending_chars,
            region=region,
            project=project,
            verification_rounds=plan.verification_rounds,
        )

    @property
    def state(self) -> str:
        return self._state

    @property
    def active(self) -> bool:
        return self._state == "em andamento"

    def start(self) -> None:
        with self._lock:
            if self._state != "nova":
                raise GateError(
                    "Esta confirmacao ja foi usada ou expirou: cada rodada com LLM precisa de uma "
                    "confirmacao nova (canal, arquivos e caracteres)."
                )
            self._state = "em andamento"

    def finish(self) -> None:
        with self._lock:
            self._state = "expirada"

    def require_active(self) -> None:
        if not self.active:
            raise GateError(
                "Envio ao provedor sem a confirmacao desta rodada (a escolha do motor LLM nao fica "
                "armada: confirme canal, arquivos e caracteres a cada rodada)."
            )

    def audit_lines(self) -> list[str]:
        """Linhas da AUDITORIA: so contagens (o nome do arquivo nao vai na AUDITORIA exportavel)."""

        stamp = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(self.confirmed_at))
        lines = [
            f"Confirmacao desta rodada: feita pelo Operador em {stamp}; vale so para esta rodada.",
            f"Canal confirmado: {self.channel}",
        ]
        if self.region:
            lines.append(f"Regiao do Vertex AI confirmada: {self.region}")
        lines.append(
            f"Enviado a confirmar: {len(self.files)} arquivo(s), {self.total_chars} caractere(s) na "
            f"descoberta ({self.pending_chars} fora do cache local); a conferencia reenvia o texto ja "
            f"tokenizado ({self.verification_rounds} rodada(s))."
        )
        return lines


# ---------------------------------------------------------------------------------------------------
# I4: o portao reconferido no ponto da rede
# ---------------------------------------------------------------------------------------------------

GEMINI_HOST = "generativelanguage.googleapis.com"
_MULTI_REGIONAL = frozenset({"us", "eu"})
_REGION = re.compile(r"^(?:global|us|eu|[a-z]{2,}(?:-[a-z]+[0-9]+)?)$")
_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,62}$")
# O unico formato de host aceito, por provedor (alem de ser o esperado para o canal e a regiao).
_ALLOWED_HOST = {
    "gemini": re.compile(r"^generativelanguage\.googleapis\.com$"),
    "vertex": re.compile(
        r"^(?:aiplatform\.googleapis\.com|[a-z]+(?:-[a-z]+[0-9]+)-aiplatform\.googleapis\.com|"
        r"aiplatform\.(?:us|eu)\.rep\.googleapis\.com)$"
    ),
}

# Variaveis que o SDK do Google le sozinho e que redirecionam o trafego para outro host: recusadas.
REDIRECT_ENV = ("GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL")
# I5: o SDK tem um modo de gravar (record) e reproduzir (replay) as chamadas em disco para teste dele:
# as requisicoes, com o TEXTO DA PECA, iriam para uma pasta qualquer, fora da raiz privada. Recusadas.
REPLAY_ENV = ("GOOGLE_GENAI_CLIENT_MODE", "GOOGLE_GENAI_REPLAYS_DIRECTORY", "GOOGLE_GENAI_REPLAY_ID")
# Proxy e certificado proprio: legitimos em rede institucional, mas o trafego passa por quem os controla;
# o app avisa (confirmacao e AUDITORIA) e nao recusa.
PROXY_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
CERT_ENV = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


# ---------------------------------------------------------------------------------------------------
# H8: canal local (Ollama em loopback)
# ---------------------------------------------------------------------------------------------------

OLLAMA_PORT = 11434
OLLAMA_HOST_IP = "127.0.0.1"
# Os unicos enderecos aceitos, comparados como TEXTO (o I7 retirou o canal porque o "so localhost" usava um
# parser de URL e o cliente HTTP outro). Sem caminho, sem https, sem outra porta.
OLLAMA_LOOPBACK_URLS = (
    f"http://127.0.0.1:{OLLAMA_PORT}",
    f"http://localhost:{OLLAMA_PORT}",
    f"http://[::1]:{OLLAMA_PORT}",
)
# Variaveis do ambiente que dizem onde esta o Ollama (a do servidor e a que o LangExtract le sozinho).
OLLAMA_ENV = ("OLLAMA_HOST", "OLLAMA_BASE_URL")
_OLLAMA_HOST_FORM = re.compile(
    rf"^(?:http://)?(?:127\.0\.0\.1|localhost|\[::1\]|::1)(?::{OLLAMA_PORT})?/?$", re.IGNORECASE
)
_OLLAMA_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def is_ollama_loopback_url(value: object) -> bool:
    """``True`` so para um dos tres enderecos fixos (comparacao de texto, uma barra final tolerada)."""

    if not isinstance(value, str):
        return False
    return value.strip().rstrip("/").lower() in OLLAMA_LOOPBACK_URLS


def ollama_refusals(env: Mapping[str, str] | None = None) -> list[str]:
    """Recusas do canal local pelo ambiente: variavel que aponta o Ollama para fora do loopback e proxy
    (o cliente HTTP, ``requests``, honra proxy). Diz so o NOME da variavel, nunca o valor."""

    environment = _environment(env)
    refusals: list[str] = []
    for name in OLLAMA_ENV:
        for variant in (name, name.lower()):
            value = (environment.get(variant) or "").strip()
            if value and not _OLLAMA_HOST_FORM.match(value):
                refusals.append(
                    f"A variavel {variant} aponta o Ollama para fora desta maquina (o canal local so "
                    "aceita loopback, porta 11434): remova-a ou ajuste-a antes de usar o canal."
                )
    refusals.extend(
        f"A variavel de proxy {name} esta definida: o cliente HTTP do canal local a honraria e o texto "
        "poderia sair da maquina. Remova-a (ou esvazie-a) antes de usar o canal."
        for name in _defined(environment, PROXY_ENV)
    )
    return list(dict.fromkeys(refusals))


def ollama_model_refusal(model_id: str) -> str:
    """Modelo recusado no canal local (vazio = aceito). O Ollama roda os modelos ``:cloud`` na nuvem dele."""

    name = (model_id or "").strip()
    if not _OLLAMA_MODEL.match(name):
        return "Nome de modelo do Ollama invalido (use, por exemplo, qwen3:4b)."
    if "cloud" in name.casefold():
        return (
            "Modelo com 'cloud' no nome e recusado: o Ollama roda esse modelo na nuvem dele, e o canal "
            "local nao envia texto para fora desta maquina."
        )
    return ""


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _defined(env: Mapping[str, str], names: tuple[str, ...]) -> list[str]:
    found = []
    for name in names:
        for variant in (name, name.lower()):
            if (env.get(variant) or "").strip():
                found.append(variant)
    return list(dict.fromkeys(found))


def env_refusals(env: Mapping[str, str] | None = None) -> list[str]:
    """Variaveis de ambiente que desviam o trafego do LLM. Diz so o NOME (o valor pode ter URL com segredo)."""

    environment = _environment(env)
    refusals = [
        f"A variavel {name} redireciona o trafego do SDK do Google para outro endereco: remova-a "
        "antes de enviar texto de peca (o app so fala com o host oficial do canal)."
        for name in _defined(environment, REDIRECT_ENV)
    ]
    refusals.extend(
        f"A variavel {name} liga a gravacao/reproducao de chamadas do SDK do Google em disco (com o texto "
        "da peca, fora da pasta privada): remova-a antes de enviar texto de peca."
        for name in _defined(environment, REPLAY_ENV)
    )
    return refusals


def env_warnings(env: Mapping[str, str] | None = None) -> list[str]:
    """Avisos (nao bloqueio): proxy e certificado proprio no ambiente."""

    environment = _environment(env)
    warnings = []
    proxies = _defined(environment, PROXY_ENV)
    if proxies:
        warnings.append(
            "Variavel de proxy definida (" + ", ".join(proxies) + "): o trafego com o provedor passa "
            "pelo proxy da rede. Confirme que ele e o da instituicao."
        )
    certs = _defined(environment, CERT_ENV)
    if certs:
        warnings.append(
            "Certificado de autoridade proprio definido (" + ", ".join(certs) + "): a conexao com o "
            "provedor confia nessa autoridade."
        )
    return warnings


def expected_host(provider: str, location: str = "") -> str:
    """O host oficial da Google para o provedor (e, no Vertex AI, para a regiao)."""

    if provider == "gemini":
        return GEMINI_HOST
    if provider == "vertex":
        if location == "global":
            return "aiplatform.googleapis.com"
        if location in _MULTI_REGIONAL:
            return f"aiplatform.{location}.rep.googleapis.com"
        return f"{location}-aiplatform.googleapis.com"
    raise GateError("Provedor desconhecido: so existem a Gemini API paga e o Vertex AI.")


@dataclass(frozen=True, slots=True)
class NetworkCheck:
    """O que o portao conferiu no ponto da rede. Vai para a AUDITORIA (so host, regiao e avisos)."""

    channel: str
    provider: str
    host: str
    base_url: str
    region: str = ""
    warnings: tuple[str, ...] = ()

    def audit_lines(self) -> list[str]:
        if self.provider == "ollama":
            return [
                f"Endpoint conferido no ponto da rede: {self.base_url} (canal {self.channel}); "
                "LLM local (Ollama, loopback), nada enviado.",
                *(f"Aviso de rede: {warning}" for warning in self.warnings),
            ]
        lines = [f"Endpoint conferido no ponto da rede: {self.host} (canal {self.channel})"]
        if self.region:
            lines.append(f"Regiao do Vertex AI usada na chamada: {self.region}")
        lines.extend(f"Aviso de rede: {warning}" for warning in self.warnings)
        return lines


def check_network_point(
    *,
    channel: str,
    provider: str,
    location: str = "",
    project: str = "",
    env: Mapping[str, str] | None = None,
    model_id: str = "",
    port: int = OLLAMA_PORT,
) -> NetworkCheck:
    """Reconfere, onde o texto sai da maquina: o canal e um dos cadastrados (e o provedor e o dele), o
    host e o oficial da Google para esse canal, e nenhuma variavel do SDK redireciona o trafego.
    Levanta ``GateError``. A regiao do Vertex AI nao e forcada: aparece na confirmacao e na AUDITORIA.

    Canal ``ollama-local`` (H8): o host e sempre ``127.0.0.1``; ``port`` so muda em teste (parametro
    explicito do chamador, nunca variavel de ambiente)."""

    if channel not in CHANNEL_PROVIDERS:
        raise GateError("Canal nao cadastrado: o texto so sai por gemini-pago ou vertex-sp.")
    if CHANNEL_PROVIDERS[channel] != provider:
        raise GateError(f"O provedor nao e o do canal {channel}: o envio foi recusado.")
    if provider == "ollama":
        refusals = ollama_refusals(env)
        model_problem = ollama_model_refusal(model_id) if model_id else ""
        if model_problem:
            refusals.append(model_problem)
        if refusals:
            raise GateError(" ".join(refusals))
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise GateError("Porta do Ollama invalida.")
        return NetworkCheck(
            channel=channel,
            provider=provider,
            host=OLLAMA_HOST_IP,
            base_url=f"http://{OLLAMA_HOST_IP}:{port}",
        )
    refusals = env_refusals(env)
    if refusals:
        raise GateError(" ".join(refusals))
    region = ""
    if provider == "vertex":
        region = (location or "").strip()
        if not _REGION.match(region):
            raise GateError("Regiao do Vertex AI invalida (use, por exemplo, southamerica-east1).")
        if not _PROJECT.match((project or "").strip()):
            raise GateError("Projeto do Google Cloud ausente ou invalido para o Vertex AI.")
    host = expected_host(provider, region)
    if not _ALLOWED_HOST[provider].match(host):
        raise GateError("O host calculado nao e um endereco oficial da Google para este canal.")
    return NetworkCheck(
        channel=channel,
        provider=provider,
        host=host,
        base_url=f"https://{host}/",
        region=region,
        warnings=tuple(env_warnings(env)),
    )


SDK_LOGGERS = ("google_genai", "google.genai", "httpx", "httpcore", "langextract", "absl")


def quiet_sdk_logging() -> None:
    """I5: o log das bibliotecas que falam com o provedor nao desce a DEBUG (que pode trazer corpo de
    requisicao) por causa de uma configuracao de log do processo."""

    for name in SDK_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET or logger.level < logging.WARNING:
            logger.setLevel(logging.WARNING)


def verify_no_disk_replay(model: object) -> None:
    """I5: o cliente criado nao esta em modo de gravar ou reproduzir chamadas em disco. Nao conseguir
    conferir (SDK mudou) tambem recusa."""

    client = getattr(model, "_client", None)
    config = getattr(client, "_debug_config", None)
    if client is None or config is None:
        raise GateError("Nao foi possivel confirmar que o SDK nao grava as chamadas em disco: envio recusado.")
    mode = getattr(config, "client_mode", None)
    directory = getattr(config, "replays_directory", None)
    if mode or directory:
        raise GateError(
            "O cliente do SDK esta em modo de gravacao/reproducao de chamadas em disco: o envio foi recusado."
        )


def verify_client_endpoint(model: object, check: NetworkCheck) -> None:
    """Depois de criar o modelo: o endereco que o cliente do SDK de fato vai usar e o host esperado, em
    https. Nao consegue ler (SDK mudou)? Recusa: sem confirmar o endereco, o texto nao sai."""

    client = getattr(model, "_client", None)
    api = getattr(client, "_api_client", None)
    options = getattr(api, "_http_options", None)
    base = getattr(options, "base_url", None)
    if not isinstance(base, str) or not base:
        raise GateError("Nao foi possivel confirmar o endereco do cliente do SDK: o envio foi recusado.")
    parsed = urlparse(base)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != check.host:
        raise GateError(
            f"O cliente do SDK apontava para {parsed.hostname or '?'} e nao para {check.host}: "
            "o envio foi recusado."
        )


# ---------------------------------------------------------------------------------------------------
# L2: o que vai para a AUDITORIA e para o log nunca leva a chave nem texto de peca
# ---------------------------------------------------------------------------------------------------

_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"ya29\.[0-9A-Za-z._\-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
HIDDEN = "<oculta>"


def scrub_secrets(text: str, *secrets: str) -> str:
    """Tira da linha a chave (valor conhecido) e tudo que tem cara de chave/token do Google."""

    cleaned = text
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, HIDDEN)
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(HIDDEN, cleaned)
    return cleaned


def safe_error(exc: BaseException, *secrets: str, keep_message: bool = False) -> str:
    """Resumo de uma excecao para AUDITORIA, log e tela: o TIPO, o codigo HTTP e o status da API. A
    mensagem crua de uma excecao do provedor pode trazer o texto da peca (e a chave, em URL ou cabecalho);
    so se mantem a mensagem de erro que o proprio app escreveu (``keep_message``), ja sem a chave."""

    parts = [type(exc).__name__]
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(exc, "status_code", None)
    if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
        parts.append(f"HTTP {code}")
    status = getattr(exc, "status", None)
    if isinstance(status, str) and re.fullmatch(r"[A-Z_]{3,40}", status):
        parts.append(status)
    summary = " ".join(parts)
    if keep_message:
        message = scrub_secrets(" ".join(str(exc).split()), *secrets)[:240]
        if message:
            summary += f": {message}"
    return summary
