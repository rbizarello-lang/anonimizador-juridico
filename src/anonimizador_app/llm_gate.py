"""Portao no ponto da rede e confirmacao por rodada do motor LLM.

Este modulo e o que o ``llm_discovery`` consulta no momento de falar com o provedor (o ``llm_engine``
decide antes, na tela e na CLI; aqui se confere de novo, onde o texto sai da maquina):

- ``RunPermit``: a confirmacao de UMA rodada. A escolha do motor LLM nao fica armada: cada rodada precisa
  de uma confirmacao (canal, regiao, arquivos, total de caracteres), que vale so enquanto a rodada
  roda e expira quando ela termina. Sem permissao ativa o modelo nem e criado.
- (I4/I5, mais abaixo) canal registrado, host do endpoint e variaveis do SDK que desviam o trafego ou
  gravam o texto em disco.

Nada aqui abre rede.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


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
