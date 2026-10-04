from __future__ import annotations

import unittest
from asyncio import proactor_events

from anonimizador_app.compat import patch_proactor_connection_lost

_TRANSPORT = proactor_events._ProactorBasePipeTransport


class _Socket:
    def __init__(self, error: OSError | None = None) -> None:
        self.error = error
        self.closed = False

    def fileno(self) -> int:
        return 3

    def shutdown(self, how: int) -> None:
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


class _Server:
    def __init__(self) -> None:
        self.detached: list[object] = []

    def _detach(self, transport: object) -> None:
        self.detached.append(transport)


class _Protocol:
    def __init__(self, error: OSError | None = None) -> None:
        self.error = error

    def connection_lost(self, exc: BaseException | None) -> None:
        if self.error is not None:
            raise self.error


class _Transport(_TRANSPORT):
    """So o que _call_connection_lost usa; sem o __init__ nem o aviso de __del__ do asyncio."""

    def __init__(self, sock: _Socket, server: _Server, protocol: _Protocol | None = None) -> None:
        self._called_connection_lost = False
        self._protocol = protocol or _Protocol()
        self._sock = sock
        self._server = server

    def __del__(self) -> None:
        pass


class ProactorConnectionLostTests(unittest.TestCase):
    """Conexao derrubada pela outra ponta (WinError 10054) nao prende o servidor ao fechar."""

    def setUp(self) -> None:
        self.current = _TRANSPORT._call_connection_lost
        self.original = getattr(self.current, "__wrapped__", self.current)
        _TRANSPORT._call_connection_lost = self.original
        patch_proactor_connection_lost()

    def tearDown(self) -> None:
        _TRANSPORT._call_connection_lost = self.current

    def test_asyncio_alone_keeps_the_dropped_connection(self) -> None:
        sock, server = _Socket(ConnectionResetError(10054, "reset")), _Server()
        transport = _Transport(sock, server)
        try:
            self.original(transport, None)
        except ConnectionResetError:
            pass
        else:
            self.skipTest("o asyncio ja corrige isto; o ajuste em compat.py pode sair")
        # O defeito: o servidor segue contando a conexao e Server.wait_closed() nunca termina.
        self.assertEqual(server.detached, [])
        self.assertFalse(sock.closed)

    def test_dropped_connection_still_frees_the_server(self) -> None:
        sock, server = _Socket(ConnectionResetError(10054, "reset")), _Server()
        transport = _Transport(sock, server)
        transport._call_connection_lost(None)
        self.assertEqual(server.detached, [transport])
        self.assertTrue(sock.closed)
        self.assertIsNone(transport._sock)
        self.assertTrue(transport._called_connection_lost)

    def test_normal_close_is_unchanged(self) -> None:
        sock, server = _Socket(), _Server()
        transport = _Transport(sock, server)
        transport._call_connection_lost(None)
        self.assertEqual(server.detached, [transport])
        self.assertTrue(sock.closed)

    def test_error_after_complete_cleanup_is_not_hidden(self) -> None:
        sock, server = _Socket(), _Server()
        transport = _Transport(sock, server, _Protocol(OSError("protocolo")))
        with self.assertRaises(OSError):
            transport._call_connection_lost(None)
        self.assertEqual(server.detached, [transport])

    def test_patch_is_applied_once(self) -> None:
        patched = _TRANSPORT._call_connection_lost
        patch_proactor_connection_lost()
        self.assertIs(_TRANSPORT._call_connection_lost, patched)
        self.assertIs(patched.__wrapped__, self.original)


if __name__ == "__main__":
    unittest.main()
