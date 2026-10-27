"""Chave do provedor de LLM: fora do projeto, fora do log, fora do ``config.json``.

A chave vem de duas fontes, nesta ordem:

1. variavel de ambiente ``ANONIMIZADOR_GEMINI_PAGO_CHAVE``;
2. Gerenciador de Credenciais do Windows (servico
   ``AnonimizadorJuridico/gemini-pago``), via ``keyring``.

Propositalmente **nao** le ``GEMINI_API_KEY`` nem ``GOOGLE_API_KEY``: sao os
nomes que o Google e o LangExtract procuram sozinhos, e quase sempre guardam a
chave gratuita de outro projeto. Esta chave tem de ser escolhida de proposito,
para um projeto com faturamento.

``ApiKey`` esconde o valor em ``repr``, ``str`` e ``format``; so ``reveal()``
o devolve, e so o adaptador do provedor o chama. A interface mostra apenas a
fonte ("variavel de ambiente" ou "Gerenciador de Credenciais").
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

ENV_VAR = "ANONIMIZADOR_GEMINI_PAGO_CHAVE"
VAULT_SERVICE = "AnonimizadorJuridico/gemini-pago"
VAULT_USER = "chave-api"

SOURCE_ENV = "variavel de ambiente"
SOURCE_VAULT = "Gerenciador de Credenciais"


class CredentialsError(RuntimeError):
    """Cofre indisponivel ou chave invalida. A mensagem nunca traz o valor."""


class ApiKey:
    """Chave de API que nao aparece em log, repr, traceback nem f-string."""

    __slots__ = ("_value", "source")

    def __init__(self, value: str, source: str) -> None:
        value = (value or "").strip()
        if not value:
            raise CredentialsError("Chave vazia.")
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "source", source)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ApiKey e imutavel.")

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"ApiKey(<oculta>, fonte={self.source!r})"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __bool__(self) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ApiKey) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("ApiKey", self.source))

    def __reduce__(self):
        raise TypeError("ApiKey nao pode ser serializada.")

    def __copy__(self) -> "ApiKey":
        return self

    def __deepcopy__(self, memo: dict) -> "ApiKey":
        return self


class Vault(Protocol):
    """O que o app usa do cofre (``keyring`` tem a mesma assinatura)."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


def system_vault() -> Vault | None:
    """O cofre do sistema (Windows: Credential Manager); ``None`` se ``keyring`` nao existir."""

    try:
        import keyring
    except ImportError:
        return None
    return keyring  # type: ignore[return-value]


def load_key(
    *,
    env: Mapping[str, str] | None = None,
    vault: Vault | None = None,
    env_var: str = ENV_VAR,
    service: str = VAULT_SERVICE,
) -> ApiKey | None:
    """A chave guardada, ou ``None``. Cofre com defeito conta como ausente (nao quebra a tela)."""

    environment = os.environ if env is None else env
    from_env = (environment.get(env_var) or "").strip()
    if from_env:
        return ApiKey(from_env, SOURCE_ENV)
    store = vault if vault is not None else system_vault()
    if store is None:
        return None
    try:
        stored = store.get_password(service, VAULT_USER)
    except Exception:  # noqa: BLE001 - sem backend, cofre bloqueado etc.
        return None
    if stored and stored.strip():
        return ApiKey(stored, SOURCE_VAULT)
    return None


def store_key(
    value: str, *, vault: Vault | None = None, env_var: str = ENV_VAR, service: str = VAULT_SERVICE
) -> ApiKey:
    """Guarda a chave no Gerenciador de Credenciais. Nunca em arquivo do projeto."""

    cleaned = (value or "").strip()
    if not cleaned:
        raise CredentialsError("Chave vazia: nada foi guardado.")
    store = vault if vault is not None else system_vault()
    if store is None:
        raise CredentialsError(
            "Gerenciador de Credenciais indisponivel (pacote keyring ausente). "
            f"Defina a variavel de ambiente {env_var}."
        )
    try:
        store.set_password(service, VAULT_USER, cleaned)
    except Exception as exc:  # noqa: BLE001
        raise CredentialsError(
            f"Nao foi possivel guardar no Gerenciador de Credenciais ({type(exc).__name__})."
        ) from None
    return ApiKey(cleaned, SOURCE_VAULT)


def delete_key(*, vault: Vault | None = None) -> bool:
    """Apaga a chave do Gerenciador de Credenciais. ``False`` se nao havia."""

    store = vault if vault is not None else system_vault()
    if store is None:
        raise CredentialsError("Gerenciador de Credenciais indisponivel (pacote keyring ausente).")
    try:
        if not store.get_password(VAULT_SERVICE, VAULT_USER):
            return False
        store.delete_password(VAULT_SERVICE, VAULT_USER)
    except Exception as exc:  # noqa: BLE001
        raise CredentialsError(
            f"Nao foi possivel apagar do Gerenciador de Credenciais ({type(exc).__name__})."
        ) from None
    return True


# H9-p: chave do OpenRouter (canal openrouter-teste, SO TESTE com o documento sintetico). Mesmo cofre, outro servico.
OPENROUTER_ENV_VAR = "ANONIMIZADOR_OPENROUTER_CHAVE"
OPENROUTER_VAULT_SERVICE = "AnonimizadorJuridico/openrouter"


def load_openrouter_key(*, env: Mapping[str, str] | None = None, vault: Vault | None = None) -> ApiKey | None:
    return load_key(env=env, vault=vault, env_var=OPENROUTER_ENV_VAR, service=OPENROUTER_VAULT_SERVICE)


def store_openrouter_key(value: str, *, vault: Vault | None = None) -> ApiKey:
    return store_key(value, vault=vault, env_var=OPENROUTER_ENV_VAR, service=OPENROUTER_VAULT_SERVICE)


def describe_source(key: ApiKey | None) -> str:
    """Texto da interface: onde a chave esta, sem o valor."""

    return f"chave encontrada ({key.source})" if key is not None else "nenhuma chave guardada"


# ---------------------------------------------------------------------------------------------------
# Vertex AI (canal vertex-sp): projeto e credencial proprios, cadastraveis como a chave do gemini-pago
# ---------------------------------------------------------------------------------------------------
#
# O Vertex AI nao usa chave de API. A credencial e uma de duas:
#   1. o ARQUIVO de conta de servico do projeto institucional (guardado so o CAMINHO: o app nunca copia
#      o conteudo, a chave privada fica onde o Google Cloud a entregou, fora do projeto);
#   2. a credencial padrao do gcloud (``gcloud auth application-default login``).
# O projeto do Google Cloud nao e segredo, mas fica no mesmo cofre, longe do ``config.json``.
# Fontes, nesta ordem: variavel de ambiente, depois o Gerenciador de Credenciais.

VERTEX_VAULT_SERVICE = "AnonimizadorJuridico/vertex-sp"
VERTEX_PROJECT_USER = "projeto"
VERTEX_CREDENTIAL_USER = "arquivo-credencial"
ENV_VERTEX_PROJECT = "ANONIMIZADOR_VERTEX_PROJETO"
ENV_VERTEX_CREDENTIAL = "ANONIMIZADOR_VERTEX_CREDENCIAL"

SOURCE_GCLOUD = "gcloud (credencial padrao)"
SOURCE_SERVICE_ACCOUNT = "arquivo de conta de servico"


@dataclass(frozen=True, slots=True)
class VertexSetup:
    """O que esta cadastrado para o Vertex AI. ``credential_path`` vazio = credencial padrao do gcloud."""

    project: str = ""
    credential_path: str = ""
    project_source: str = ""
    credential_source: str = ""

    @property
    def credential_label(self) -> str:
        return SOURCE_SERVICE_ACCOUNT if self.credential_path else SOURCE_GCLOUD


def validate_service_account_file(path: str | os.PathLike[str]) -> str:
    """Mensagem de erro (vazia se esta tudo certo). So confere que e um JSON de conta de servico; nunca
    le nem mostra a chave privada."""

    import json
    from pathlib import Path

    candidate = Path(str(path)).expanduser()
    if not candidate.is_file():
        return "O arquivo de credencial do Vertex AI nao existe."
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "O arquivo de credencial do Vertex AI nao e um JSON legivel."
    if not isinstance(payload, dict) or payload.get("type") != "service_account":
        return "O arquivo nao e uma chave de conta de servico do Google Cloud (type: service_account)."
    if not payload.get("client_email"):
        return "O arquivo de conta de servico nao traz o e-mail da conta."
    return ""


def _stored(store: Vault | None, user: str) -> str:
    if store is None:
        return ""
    try:
        return (store.get_password(VERTEX_VAULT_SERVICE, user) or "").strip()
    except Exception:  # noqa: BLE001 - cofre com defeito conta como ausente
        return ""


def load_vertex_setup(*, env: Mapping[str, str] | None = None, vault: Vault | None = None) -> VertexSetup:
    """Projeto e credencial cadastrados (variavel de ambiente, depois cofre). Cofre com defeito = ausente."""

    environment = os.environ if env is None else env
    store = vault if vault is not None else system_vault()
    project, project_source = (environment.get(ENV_VERTEX_PROJECT) or "").strip(), SOURCE_ENV
    if not project:
        project, project_source = _stored(store, VERTEX_PROJECT_USER), SOURCE_VAULT
    credential, credential_source = (environment.get(ENV_VERTEX_CREDENTIAL) or "").strip(), SOURCE_ENV
    if not credential:
        credential, credential_source = _stored(store, VERTEX_CREDENTIAL_USER), SOURCE_VAULT
    return VertexSetup(
        project=project,
        credential_path=credential,
        project_source=project_source if project else "",
        credential_source=credential_source if credential else "",
    )


def store_vertex_setup(project: str, credential_path: str = "", *, vault: Vault | None = None) -> VertexSetup:
    """Guarda o projeto (e, se houver, o CAMINHO do arquivo de conta de servico) no Gerenciador de
    Credenciais. Credencial vazia = usar a do gcloud (apaga a anterior)."""

    cleaned_project = (project or "").strip()
    if not cleaned_project:
        raise CredentialsError("Projeto vazio: nada foi guardado.")
    cleaned_path = (credential_path or "").strip().strip('"')
    if cleaned_path:
        problem = validate_service_account_file(cleaned_path)
        if problem:
            raise CredentialsError(problem)
    store = vault if vault is not None else system_vault()
    if store is None:
        raise CredentialsError(
            "Gerenciador de Credenciais indisponivel (pacote keyring ausente). Defina as variaveis "
            f"{ENV_VERTEX_PROJECT} e {ENV_VERTEX_CREDENTIAL}."
        )
    try:
        store.set_password(VERTEX_VAULT_SERVICE, VERTEX_PROJECT_USER, cleaned_project)
        if cleaned_path:
            store.set_password(VERTEX_VAULT_SERVICE, VERTEX_CREDENTIAL_USER, cleaned_path)
        elif _stored(store, VERTEX_CREDENTIAL_USER):
            store.delete_password(VERTEX_VAULT_SERVICE, VERTEX_CREDENTIAL_USER)
    except Exception as exc:  # noqa: BLE001
        raise CredentialsError(
            f"Nao foi possivel guardar no Gerenciador de Credenciais ({type(exc).__name__})."
        ) from None
    return VertexSetup(
        project=cleaned_project,
        credential_path=cleaned_path,
        project_source=SOURCE_VAULT,
        credential_source=SOURCE_VAULT if cleaned_path else "",
    )


def delete_vertex_setup(*, vault: Vault | None = None) -> bool:
    """Apaga projeto e caminho de credencial do Vertex AI. ``False`` se nao havia nada."""

    store = vault if vault is not None else system_vault()
    if store is None:
        raise CredentialsError("Gerenciador de Credenciais indisponivel (pacote keyring ausente).")
    removed = False
    try:
        for user in (VERTEX_PROJECT_USER, VERTEX_CREDENTIAL_USER):
            if _stored(store, user):
                store.delete_password(VERTEX_VAULT_SERVICE, user)
                removed = True
    except Exception as exc:  # noqa: BLE001
        raise CredentialsError(
            f"Nao foi possivel apagar do Gerenciador de Credenciais ({type(exc).__name__})."
        ) from None
    return removed


def describe_vertex(setup: VertexSetup) -> str:
    """Texto da interface: o que esta cadastrado, sem caminho completo nem conteudo."""

    if not setup.project:
        return "nenhum projeto cadastrado"
    return f"projeto cadastrado ({setup.project_source}); credencial: {setup.credential_label}"
