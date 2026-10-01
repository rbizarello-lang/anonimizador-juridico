"""Bloqueio de rede para testes e para o ensaio (L4 sem rede).

Dentro de ``block_network()`` qualquer tentativa de abrir conexao, resolver
nome ou enviar datagrama levanta ``NetworkBlocked`` e fica registrada. Serve
para provar que um trecho de codigo (o pipeline com LLM falso, a revisao do
elenco, a reaplicacao sem rede) nao fala com ninguem.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager


class NetworkBlocked(OSError):
    """Tentativa de rede enquanto o bloqueio estava ativo."""


@contextmanager
def block_network(*, allow_loopback: bool = False) -> Iterator[list[str]]:
    """Bloqueia sockets; devolve a lista (viva) das tentativas, ``"connect host:porta"`` etc."""

    attempts: list[str] = []

    def is_loopback(address: object) -> bool:
        host = address[0] if isinstance(address, tuple) and address else address
        return isinstance(host, str) and host in {"127.0.0.1", "::1", "localhost"}

    def refuse(what: str, detail: object) -> None:
        attempts.append(f"{what} {detail}")
        raise NetworkBlocked(f"rede bloqueada ({what})")

    original = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
    }

    def connect(self, address):
        if allow_loopback and is_loopback(address):
            return original["connect"](self, address)
        refuse("connect", address)

    def connect_ex(self, address):
        if allow_loopback and is_loopback(address):
            return original["connect_ex"](self, address)
        refuse("connect_ex", address)

    def sendto(self, *args):
        refuse("sendto", args[-1] if args else "")

    def create_connection(address, *args, **kwargs):
        if allow_loopback and is_loopback(address):
            return original["create_connection"](address, *args, **kwargs)
        refuse("create_connection", address)

    def getaddrinfo(host, *args, **kwargs):
        if allow_loopback and is_loopback((host,)):
            return original["getaddrinfo"](host, *args, **kwargs)
        refuse("getaddrinfo", host)

    def gethostbyname(host):
        if allow_loopback and is_loopback((host,)):
            return original["gethostbyname"](host)
        refuse("gethostbyname", host)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = sendto  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = gethostbyname  # type: ignore[assignment]
    try:
        yield attempts
    finally:
        socket.socket.connect = original["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = original["connect_ex"]  # type: ignore[method-assign]
        socket.socket.sendto = original["sendto"]  # type: ignore[method-assign]
        socket.create_connection = original["create_connection"]  # type: ignore[assignment]
        socket.getaddrinfo = original["getaddrinfo"]  # type: ignore[assignment]
        socket.gethostbyname = original["gethostbyname"]  # type: ignore[assignment]
