"""Portao no ponto da rede e confirmacao por rodada do motor LLM.

Este modulo e o que o ``llm_discovery`` consulta no momento de falar com o provedor (o ``llm_engine``
decide antes, na tela e na CLI; aqui se confere de novo, onde o texto sai da maquina):

- ``RunPermit``: a confirmacao de UMA rodada. A escolha do motor LLM nao fica armada: cada rodada precisa
  de uma confirmacao (canal, regiao, arquivos, total de caracteres), que vale so enquanto a rodada
  roda e expira quando ela termina. Sem permissao ativa o modelo nem e criado.
- ``check_network_point`` (I4): o canal e um dos cadastrados, o host do endpoint e o oficial da Google
  para ele, e nenhuma variavel do SDK redireciona o trafego; ``verify_client_endpoint`` confere o
  endereco do cliente ja criado.

Nada aqui abre rede.
"""

from __future__ import annotations

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
# Proxy e certificado proprio: legitimos em rede institucional, mas o trafego passa por quem os controla;
# o app avisa (confirmacao e AUDITORIA) e nao recusa.
PROXY_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
CERT_ENV = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


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

    defined = _defined(_environment(env), REDIRECT_ENV)
    return [
        f"A variavel {name} redireciona o trafego do SDK do Google para outro endereco: remova-a "
        "antes de enviar texto de peca (o app so fala com o host oficial do canal)."
        for name in defined
    ]


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
) -> NetworkCheck:
    """Reconfere, onde o texto sai da maquina: o canal e um dos cadastrados (e o provedor e o dele), o
    host e o oficial da Google para esse canal, e nenhuma variavel do SDK redireciona o trafego.
    Levanta ``GateError``. A regiao do Vertex AI nao e forcada: aparece na confirmacao e na AUDITORIA."""

    if channel not in CHANNEL_PROVIDERS:
        raise GateError("Canal nao cadastrado: o texto so sai por gemini-pago ou vertex-sp.")
    if CHANNEL_PROVIDERS[channel] != provider:
        raise GateError(f"O provedor nao e o do canal {channel}: o envio foi recusado.")
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
