"""Servidor Ollama FALSO em loopback, so para teste (H9-b). Nenhum teste abre rede real.

Escuta em ``127.0.0.1`` numa porta livre (a porta do Ollama de verdade, 11434, nunca e usada) e responde
``/api/generate`` e ``/api/chat`` como o Ollama: um JSON de extracao do LangExtract com as entidades
conhecidas que aparecem no TRECHO do pedido (a parte depois dos exemplos do prompt).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# (classe, texto como esta no documento, papel, nome canonico)
KNOWN = (
    ("empresa", "ACME COMÉRCIO DE GRÃOS LTDA", "parte", "Acme Comércio de Grãos Ltda"),
    ("pessoa", "JOÃO DA SILVA SAURO", "socio", "João da Silva Sauro"),
    ("pessoa", "João da Silva Sauro", "socio", "João da Silva Sauro"),
    ("endereco", "Rua das Palmeiras, 45", "socio", "Rua das Palmeiras, 45"),
)


def extraction_for(prompt: str, known=KNOWN) -> str:
    """O JSON de extracao do LangExtract para o trecho do pedido (a ultima pergunta do prompt)."""

    chunk = prompt.rsplit("Q:", 1)[-1]
    items = [
        {kind: value, f"{kind}_attributes": {"papel": role, "nome_canonico": canonical}}
        for kind, value, role, canonical in known
        if value in chunk
    ]
    return json.dumps({"extractions": items}, ensure_ascii=False)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - API do http.server
        size = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(size) or b"{}")
        server: FakeOllama = self.server  # type: ignore[assignment]
        server.requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        if server.status != 200:
            self.send_response(server.status)
            self.end_headers()
            return
        if self.path == "/api/generate":
            prompt = body.get("prompt", "")
            payload = {"model": body.get("model"), "response": server.answer(prompt), "done": True}
        elif self.path == "/api/chat":
            prompt = body["messages"][-1]["content"]
            payload = {"model": body.get("model"), "message": {"role": "assistant", "content": server.answer(prompt)},
                       "done": True}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:  # silencioso
        pass


class FakeOllama(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, answer=extraction_for, status: int = 200) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.answer = answer
        self.status = status
        self.requests: list[dict] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def __enter__(self) -> "FakeOllama":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)
