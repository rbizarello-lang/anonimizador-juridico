from __future__ import annotations

import socket
import unittest

from anonimizador_app.netblock import NetworkBlocked, block_network


class NetBlockTests(unittest.TestCase):
    def test_connection_resolution_and_datagram_are_refused_and_recorded(self) -> None:
        with block_network() as attempts:
            with self.assertRaises(NetworkBlocked):
                socket.create_connection(("example.invalid", 443), timeout=1)
            with self.assertRaises(NetworkBlocked):
                socket.getaddrinfo("example.invalid", 443)
            with self.assertRaises(NetworkBlocked):
                socket.gethostbyname("example.invalid")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with self.assertRaises(NetworkBlocked):
                    sock.connect(("192.0.2.1", 443))
                with self.assertRaises(NetworkBlocked):
                    sock.connect_ex(("192.0.2.1", 443))
            finally:
                sock.close()
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                with self.assertRaises(NetworkBlocked):
                    udp.sendto(b"x", ("192.0.2.1", 53))
            finally:
                udp.close()
        self.assertEqual(len(attempts), 6)
        self.assertTrue(attempts[0].startswith("create_connection"))

    def test_loopback_is_refused_unless_allowed(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            with block_network() as attempts:
                with self.assertRaises(NetworkBlocked):
                    socket.create_connection(("127.0.0.1", port), timeout=1)
            self.assertEqual(len(attempts), 1)
            with block_network(allow_loopback=True) as attempts:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
            self.assertEqual(attempts, [])
        finally:
            server.close()

    def test_everything_is_restored_afterwards(self) -> None:
        before = (socket.create_connection, socket.getaddrinfo, socket.gethostbyname)
        with block_network():
            pass
        self.assertEqual((socket.create_connection, socket.getaddrinfo, socket.gethostbyname), before)
        socket.getaddrinfo("127.0.0.1", 80)


if __name__ == "__main__":
    unittest.main()
