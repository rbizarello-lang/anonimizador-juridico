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


def load_key(*, env: Mapping[str, str] | None = None, vault: Vault | None = None) -> ApiKey | None:
    """A chave guardada, ou ``None``. Cofre com defeito conta como ausente (nao quebra a tela)."""

    environment = os.environ if env is None else env
    from_env = (environment.get(ENV_VAR) or "").strip()
    if from_env:
        return ApiKey(from_env, SOURCE_ENV)
    store = vault if vault is not None else system_vault()
    if store is None:
        return None
    try:
        stored = store.get_password(VAULT_SERVICE, VAULT_USER)
    except Exception:  # noqa: BLE001 - sem backend, cofre bloqueado etc.
        return None
    if stored and stored.strip():
        return ApiKey(stored, SOURCE_VAULT)
    return None


def store_key(value: str, *, vault: Vault | None = None) -> ApiKey:
    """Guarda a chave no Gerenciador de Credenciais. Nunca em arquivo do projeto."""

    cleaned = (value or "").strip()
    if not cleaned:
        raise CredentialsError("Chave vazia: nada foi guardado.")
    store = vault if vault is not None else system_vault()
    if store is None:
        raise CredentialsError(
            "Gerenciador de Credenciais indisponivel (pacote keyring ausente). "
            f"Defina a variavel de ambiente {ENV_VAR}."
        )
    try:
        store.set_password(VAULT_SERVICE, VAULT_USER, cleaned)
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


def describe_source(key: ApiKey | None) -> str:
    """Texto da interface: onde a chave esta, sem o valor."""

    return f"chave encontrada ({key.source})" if key is not None else "nenhuma chave guardada"
