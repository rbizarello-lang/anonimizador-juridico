"""Ajustes para o Python 3.14: bibliotecas que ainda nao o acompanharam e um defeito do asyncio no Windows."""

from __future__ import annotations

import importlib.util
import pkgutil
import sys


def patch_python314() -> None:
    if not hasattr(pkgutil, "find_loader"):
        def find_loader(name: str):
            spec = importlib.util.find_spec(name)
            return spec.loader if spec else None

        pkgutil.find_loader = find_loader  # type: ignore[attr-defined]
    if sys.platform == "win32":
        patch_proactor_connection_lost()


def patch_proactor_connection_lost() -> None:
    """Conexao derrubada pela outra ponta nao pode prender o servidor ao fechar.

    No Windows, o asyncio chama ``sock.shutdown()`` antes de fechar o socket e de soltar a
    conexao do servidor. Se a outra ponta ja derrubou a conexao (WinError 10054), a excecao
    pula esse resto: o servidor segue contando a conexao, ``Server.wait_closed()`` nunca
    termina, o uvicorn nao encerra e o processo fica vivo sem janela e sem porta, segurando a
    trava de instancia unica; todo clique seguinte esperava e desistia (26/09/2026, Python
    3.14.6). Aqui a limpeza termina mesmo com a excecao.
    """

    from asyncio import proactor_events

    transport = proactor_events._ProactorBasePipeTransport
    original = transport._call_connection_lost
    if getattr(original, "__wrapped__", None) is not None:
        return

    def _call_connection_lost(self, exc):
        try:
            original(self, exc)
        except OSError:
            if self._called_connection_lost:
                raise  # a limpeza terminou; o erro veio de outro ponto
            sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            server, self._server = self._server, None
            if server is not None:
                server._detach(self)
            self._called_connection_lost = True

    _call_connection_lost.__wrapped__ = original  # type: ignore[attr-defined]
    transport._call_connection_lost = _call_connection_lost
