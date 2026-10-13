"""Descoberta de entidades por LLM com LangExtract (Gemini API paga, Vertex AI ou Ollama local).

O LLM le trechos do texto — ja com CPF, CNPJ e afins trocados por token nesta
maquina — e devolve quem aparece e com que papel. Nao reescreve nada: a troca
e deterministica (``propagation``) e o texto juridico nao passa pelo modelo
na volta.

Cada trecho e uma chamada independente. O paralelismo e as novas tentativas
ficam aqui, e nao no provedor: no LangExtract, um trecho com erro (bloqueio
de seguranca, JSON truncado) derruba o lote inteiro ou e descartado em
silencio. Aqui o trecho que falhar e registrado (fail-closed) e a auditoria
diz quais paginas nao foram lidas.

Este modulo nao le variavel de ambiente nem cofre: a chave chega pronta em
``LlmConfig.api_key`` (``credentials.ApiKey``) e so depois de o portao do
motor (``llm_engine.check_engine``) liberar. ``offline=True`` usa so o cache
(reaplica um elenco revisado sem rede).

H9-b: o canal ``ollama-local`` usa o provedor Ollama do LangExtract contra o loopback (nada sai da maquina):
nao exige ``RunPermit``, nao le variavel de ambiente (o endereco vai fixo) e nao usa chave.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .credentials import ApiKey
from .llm_gate import (
    HIDDEN,
    OLLAMA_PORT,
    GateError,
    NetworkCheck,
    RunPermit,
    check_network_point,
    quiet_sdk_logging,
    safe_error,
    scrub_secrets,
    verify_client_endpoint,
    verify_no_disk_replay,
)
from .roster import Mention

PROMPT_VERSION = "2026-10-01.1"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
# H9-b: canal local (Ollama em loopback). Modelo padrao e janela de contexto do pedido.
OLLAMA_DEFAULT_MODEL = "qwen3:4b"
OLLAMA_DEFAULT_NUM_CTX = 4096
OLLAMA_CHUNK_CHARS = 2000
# Teto de saida por chamada no canal local. Sem ele o provedor manda num_predict = 8192, maior que a propria
# janela: um modelo pequeno em laco gerou 3.800 a 5.500 tokens por chamada (8 minutos cada) ate o Ollama
# devolver 500. Um trecho de 2000 caracteres cabe em ~2000 tokens de JSON; o laco e cortado em ~2 minutos.
OLLAMA_MAX_OUTPUT_TOKENS = 2000

# Raciocinio do modelo (H6). O LangExtract nao repassa ``thinking_config`` ao Gemini: sem isto o modelo pensa no
# nivel padrao dele, cobrado como saida; num modelo que pensa muito, o raciocinio esgota o ``max_output_tokens``
# e a resposta volta cortada ("fora do formato JSON"), que foi o que derrubou o lote do 3.8 em 05/10.
THINKING_LEVELS = {"minimo": "MINIMAL", "baixo": "LOW", "padrao": None}
DEFAULT_THINKING = "baixo"
DEFAULT_LOCATION = "southamerica-east1"
_EMPTY_OUTPUT = '{"extractions": []}'
_PAGE_ANCHOR = re.compile(r"<!--\s*pagina-origem:\s*(\d+)\s*-->")
_LETTER = re.compile(r"[^\W\d_]")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
# Token ja aplicado: a numeracao muda de um lote para outro (um CPF novo na
# capa renumera todos os seguintes); a chave do cache ignora o numero.
_TOKEN_NUMBER = re.compile(r"\[([A-Z0-9_]+)_\d+(?:·G\d+)?\]")
# Pecas de execucao fiscal falam de fraude, crime e violencia; o filtro
# padrao bloquearia o trecho e a pagina ficaria sem leitura.
_SAFETY_OFF = [
    {"category": category, "threshold": "BLOCK_NONE"}
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

_LOG = logging.getLogger(__name__)

PROMPT = """\
Você está anonimizando autos de processo judicial brasileiro (execução fiscal, \
IDPJ, embargos, cautelar fiscal e afins). Leia o trecho e extraia TODA menção que \
identifique:
- pessoa: pessoa física — nome completo, parcial ou abreviado, inclusive em CAIXA \
ALTA ou com erro de OCR (ex.: "JOAO DA SlLVA");
- empresa: pessoa jurídica de direito privado — razão social, nome fantasia, marca, \
domínio de site ou e-mail corporativo;
- endereco: logradouro com número e complemento (rua, avenida, rodovia, linha, \
estrada, lote, km). Não extraia cidade, bairro, UF ou CEP soltos.

Regras:
1. Copie o texto exatamente como está no trecho, com a mesma grafia e os mesmos \
erros. Não corrija nem complete.
2. Extraia cada menção, inclusive repetições e formas parciais ("o sócio SOUZA").
3. Atributos obrigatórios:
   - papel: parte | socio | advogado | terceiro | magistrado | servidor_publico | \
procurador_publico | orgao_publico | autor_citado.
     parte = executado, réu, autor, embargante, devedor, contribuinte;
     socio = sócio, administrador, representante legal;
     terceiro = adquirente, cônjuge, testemunha, contador, empregado, perito ou \
qualquer outra pessoa ou empresa privada;
     magistrado = juiz, desembargador, ministro, relator;
     servidor_publico = oficial de justiça, diretor de secretaria, servidor;
     procurador_publico = Procurador da Fazenda Nacional, AGU, PGF, procurador de \
Estado ou Município;
     orgao_publico = ente, órgão, autarquia ou empresa estatal;
     autor_citado = doutrinador ou autor de obra citada.
   - nome_canonico: nome completo e normalizado da entidade, o mesmo para todas as \
grafias da mesma pessoa ou empresa (para endereço, o logradouro completo).
4. NÃO extraia: União, Fazenda Nacional, PGFN, Receita Federal, INSS, tribunais, \
varas, juízos, comarcas; datas; valores (R$); números de processo, evento, CDA ou \
folha; leis, artigos, súmulas, temas e teses; sistemas (SISBAJUD, RENAJUD, CNIB, \
INFOJUD, eproc); cargos sem nome.
5. Marcadores entre colchetes, como [CPF_1], [CNPJ_2] ou [PESSOA_3], já estão \
anonimizados: não os extraia.
"""


def _example_specs() -> list[tuple[str, list[tuple[str, str, str, str]]]]:
    """Exemplos fictícios (nome, número e endereço inventados)."""

    return [
        (
            "EXECUÇÃO FISCAL Nº 5009999-11.2025.4.04.7001/PR\n"
            "EXEQUENTE: UNIÃO - FAZENDA NACIONAL\n"
            "EXECUTADO: MADEIREIRA PINHAL VERDE LTDA ([CNPJ_1])\n"
            "ADVOGADO: RAFAEL TEIXEIRA ALBUQUERQUE ([OAB_1])\n"
            "Endereço: RODOVIA PR-445, KM 12, LOTE 7, ZONA RURAL",
            [
                ("empresa", "MADEIREIRA PINHAL VERDE LTDA", "parte", "Madeireira Pinhal Verde Ltda"),
                ("pessoa", "RAFAEL TEIXEIRA ALBUQUERQUE", "advogado", "Rafael Teixeira Albuquerque"),
                ("endereco", "RODOVIA PR-445, KM 12, LOTE 7", "parte", "Rodovia PR-445, Km 12, Lote 7"),
            ],
        ),
        (
            "A UNIÃO (FAZENDA NACIONAL), por sua Procuradoria, requer a inclusão de "
            "MARIA APARECIDA DOS SANTOS FERREIRA ([CPF_1]), sócia-administradora, com "
            "base no art. 135, III, do CTN e no Tema 962/STJ. A sócia Maria Aparecida "
            "transferiu o imóvel da matrícula [MATRICULA_IMOVEL_1] à TRANSPORTADORA RIO "
            "DO PEIXE EIRELI em 14/03/2023, por R$ 850.000,00 (evento 45).",
            [
                ("pessoa", "MARIA APARECIDA DOS SANTOS FERREIRA", "socio", "Maria Aparecida dos Santos Ferreira"),
                ("pessoa", "Maria Aparecida", "socio", "Maria Aparecida dos Santos Ferreira"),
                ("empresa", "TRANSPORTADORA RIO DO PEIXE EIRELI", "terceiro", "Transportadora Rio do Peixe Eireli"),
            ],
        ),
        (
            "Defiro a penhora pelo SISBAJUD. Intime-se o executado JOSÉ CARLOS NUNES na "
            "pessoa de seu advogado, Dr. Tiago Bortolotto. Aplica-se o REsp 1.999.001/RS "
            "(Rel. Min. Heitor Vilela Prado). Documento eletrônico assinado por "
            "LUCIANA MONTEFUSCO DE ALMEIDA, Juíza Federal.",
            [
                ("pessoa", "JOSÉ CARLOS NUNES", "parte", "José Carlos Nunes"),
                ("pessoa", "Tiago Bortolotto", "advogado", "Tiago Bortolotto"),
                ("pessoa", "Heitor Vilela Prado", "magistrado", "Heitor Vilela Prado"),
                ("pessoa", "LUCIANA MONTEFUSCO DE ALMEIDA", "magistrado", "Luciana Montefusco de Almeida"),
            ],
        ),
        (
            "CONTRATO SOCIAL - VIDRAÇARIA ALVORADA LTDA - ME\n"
            "Sócios: ANTONIO CARLOS BARRETO, residente na Rua Tupinambás, 1.520, apto 302, "
            "e CLAUDETE HOFFMElSTER BARRETO. Site: www.vidracariaalvorada.com.br",
            [
                ("empresa", "VIDRAÇARIA ALVORADA LTDA - ME", "parte", "Vidraçaria Alvorada Ltda"),
                ("pessoa", "ANTONIO CARLOS BARRETO", "socio", "Antonio Carlos Barreto"),
                ("endereco", "Rua Tupinambás, 1.520, apto 302", "socio", "Rua Tupinambás, 1.520, apto 302"),
                ("pessoa", "CLAUDETE HOFFMElSTER BARRETO", "socio", "Claudete Hoffmeister Barreto"),
                ("empresa", "www.vidracariaalvorada.com.br", "parte", "Vidraçaria Alvorada Ltda"),
            ],
        ),
    ]


def build_examples() -> list[Any]:
    lx = import_langextract()
    return [
        lx.data.ExampleData(
            text=text,
            extractions=[
                lx.data.Extraction(
                    extraction_class=kind,
                    extraction_text=value,
                    attributes={"papel": role, "nome_canonico": canonical},
                )
                for kind, value, role, canonical in items
            ],
        )
        for text, items in _example_specs()
    ]


class LlmConfigError(RuntimeError):
    """Configuracao do provedor ausente ou insegura."""


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """Provedor do LLM. Conta gratuita do Gemini e vedada: o conteudo treina o modelo.

    ``channel`` e o canal liberado pelo portao (``gemini-pago`` ou ``vertex-sp``).
    ``api_key`` nao aparece no ``repr``.
    """

    provider: str = "gemini"  # gemini (API paga) | vertex
    channel: str = ""
    api_key: ApiKey | None = field(default=None, repr=False, compare=False)
    model_id: str = DEFAULT_MODEL
    project: str = ""
    location: str = DEFAULT_LOCATION
    # Vertex AI: caminho do arquivo de conta de servico (vazio = credencial padrao do gcloud). So o
    # caminho; o conteudo e lido no momento de criar o cliente e nao e guardado.
    credentials_path: str = ""
    # Padrao 4 (H3/M5): numa chave nova, 16 chamadas simultaneas provocam 429 em massa e jogam folhas no motor
    # local. Sobe-se com --paralelo depois de ver que o limite da conta aguenta.
    max_workers: int = 4
    chunk_chars: int = 4000
    max_output_tokens: int = 8192
    retries: int = 1
    thinking: str = DEFAULT_THINKING
    # Canal local (Ollama): janela de contexto, quanto o modelo fica carregado depois da chamada (segundos) e
    # o prazo de cada chamada (segundos). Os outros provedores ignoram.
    num_ctx: int = OLLAMA_DEFAULT_NUM_CTX
    keep_alive: int = 120
    timeout: int = 600

    def describe(self) -> str:
        if self.channel:
            return f"{self.model_id} via {self.channel}"
        where = {
            "gemini": "Gemini API",
            "vertex": f"Vertex AI ({self.location})",
        }.get(self.provider, self.provider)
        return f"{self.model_id} via {where}"


@dataclass(frozen=True, slots=True)
class Chunk:
    start: int
    end: int
    text: str


@dataclass(slots=True)
class DiscoveryReport:
    chunks: int = 0
    cached: int = 0
    sent: int = 0
    failed: list[tuple[int, int]] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    ungrounded: int = 0
    # Trechos que o LLM respondeu com JSON valido mas SEM nenhuma entidade. Pode ser pagina sem nome, ou
    # resposta ruim: o LLM nao e quem decide. O pipeline manda o motor local conferir (B1/I6) e a resposta
    # vazia nunca vai para o cache.
    empty: list[tuple[int, int]] = field(default_factory=list)
    # Modo sem rede: trecho fora do cache nao foi lido (nao e falha do provedor).
    offline: bool = False


@dataclass(slots=True)
class Discovery:
    mentions: list[Mention]
    report: DiscoveryReport


class Discoverer(Protocol):
    def describe(self) -> str: ...

    def discover(self, text: str) -> Discovery: ...


def import_langextract():
    try:
        import langextract as lx
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise LlmConfigError(
            "LangExtract nao esta instalado. Rode: pip install langextract"
        ) from exc
    return lx


def split_chunks(text: str, max_chars: int) -> list[Chunk]:
    """Trechos alinhados a pagina (ancora ``pagina-origem``), ate ``max_chars``.

    Pagina maior que o limite e cortada em paragrafo, linha ou espaco. Trecho
    sem letra nao vai ao LLM. Mesma pagina, mesmo trecho, mesmo cache: um
    processo re-exportado com eventos novos so paga as paginas novas.
    """

    if max_chars < 200:
        raise ValueError("Trecho muito curto para o LLM (minimo 200 caracteres).")
    pieces: list[tuple[int, int]] = []
    for start, end in _page_blocks(text):
        pieces.extend(_split_block(text, start, end, max_chars))
    merged: list[tuple[int, int]] = []
    for start, end in pieces:
        if merged and end - merged[-1][0] <= max_chars:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [
        Chunk(start=start, end=end, text=text[start:end])
        for start, end in merged
        if _LETTER.search(text[start:end])
    ]


def page_of(text: str, position: int) -> int:
    """Pagina de origem de uma posicao (0 se o texto nao tiver ancora)."""

    # A ancora que comeca exatamente na posicao ja vale (trecho comeca nela).
    page = 0
    for match in _PAGE_ANCHOR.finditer(text, 0, min(len(text), max(0, position) + 64)):
        if match.start() > position:
            break
        page = int(match.group(1))
    return page


def page_ranges(text: str) -> dict[int, list[tuple[int, int]]]:
    """Faixas do texto por numero de pagina (``pagina-origem``); o que vem antes da primeira ancora e a
    pagina 0. Texto sem nenhuma ancora e uma pagina 0 so (o texto todo)."""

    ranges: dict[int, list[tuple[int, int]]] = {}
    for start, end in _page_blocks(text):
        ranges.setdefault(page_of(text, start), []).append((start, end))
    return ranges


def _page_blocks(text: str) -> list[tuple[int, int]]:
    bounds = sorted({0, len(text), *(match.start() for match in _PAGE_ANCHOR.finditer(text))})
    return [(start, end) for start, end in zip(bounds, bounds[1:]) if end > start]


def _split_block(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    while end - start > max_chars:
        limit = start + max_chars
        cut = -1
        for separator in ("\n\n", "\n", " "):
            found = text.rfind(separator, start + max_chars // 2, limit)
            if found > start:
                cut = found + len(separator)
                break
        if cut <= start:
            cut = limit
        pieces.append((start, cut))
        start = cut
    pieces.append((start, end))
    return pieces


def _parses(output: str) -> bool:
    try:
        payload = json.loads(_FENCE.sub("", output.strip()))
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("extractions"), list)


def wrap_model(
    inner: Any,
    *,
    max_workers: int,
    retries: int,
    on_failure: Callable[[str, str], None],
) -> Any:
    """Envolve o modelo do LangExtract: paralelismo, nova tentativa e falha por trecho."""

    from langextract.core import base_model
    from langextract.core import types as lx_types

    class ResilientModel(base_model.BaseLanguageModel):
        @property
        def schema(self):  # noqa: D401 - propriedade delegada
            return inner.schema

        def apply_schema(self, schema_instance) -> None:
            inner.apply_schema(schema_instance)

        def set_fence_output(self, fence_output) -> None:
            inner.set_fence_output(fence_output)

        @property
        def requires_fence_output(self) -> bool:
            return inner.requires_fence_output

        def infer(self, batch_prompts: Sequence[str], **kwargs):
            prompts = list(batch_prompts)
            workers = max(1, min(max_workers, len(prompts)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outputs = list(pool.map(lambda prompt: self._one(prompt, kwargs), prompts))
            for output in outputs:
                yield [lx_types.ScoredOutput(score=1.0, output=output)]

        def _one(self, prompt: str, kwargs: dict) -> str:
            reason = ""
            for _attempt in range(1 + max(0, retries)):
                try:
                    scored = next(iter(inner.infer([prompt], **kwargs)))
                    output = scored[0].output or ""
                except Exception as exc:  # noqa: BLE001 - trecho isolado
                    # So o tipo, o codigo HTTP e o status: a mensagem crua pode ter a chave e texto da peca,
                    # e esta frase vai para a AUDITORIA (pasta exportavel) e para o log.
                    reason = safe_error(exc)
                    continue
                if _parses(output):
                    return output
                reason = "resposta fora do formato JSON"
            on_failure(prompt, reason)
            return _EMPTY_OUTPUT

    return ResilientModel()


class LangExtractDiscoverer:
    """Descoberta via ``lx.extract``, com cache por trecho na pasta privada."""

    def __init__(
        self,
        config: LlmConfig | None = None,
        *,
        cache_dir: Path | None = None,
        model_factory: Callable[[], Any] | None = None,
        offline: bool = False,
        permit: RunPermit | None = None,
        loopback_port: int = OLLAMA_PORT,
    ) -> None:
        self.config = config or LlmConfig()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._model_factory = model_factory
        self.offline = offline
        # B2: a confirmacao da rodada. Sem ela ativa, o modelo (e a rede) nao e criado.
        self.permit = permit
        # I4: o que o portao conferiu no ponto da rede na ultima vez que o modelo foi criado.
        self.network_check: NetworkCheck | None = None
        # H6: tokens e respostas cortadas da rodada, para a AUDITORIA.
        self.usage = UsageMeter()
        # H9-b, canal local: SO TESTE. Porta do Ollama (falso) no loopback; o host nunca muda e nenhuma variavel
        # de ambiente chega aqui.
        self._loopback_port = loopback_port
        # H9-b: tentativas de rede fora do loopback que o bloqueio da rodada barrou. ``blocked_attempts`` e o
        # total ao fim da rodada; ``attempts_live`` e a lista viva (para a AUDITORIA escrita antes do fim).
        self.blocked_attempts: int | None = None
        self.attempts_live: list[str] | None = None

    @property
    def is_local(self) -> bool:
        """Canal local (Ollama em loopback): nada sai da maquina."""

        return self.config.provider == "ollama"

    def blocked_count(self) -> int | None:
        """Tentativas barradas pelo bloqueio da rodada (``None`` = rodada sem bloqueio)."""

        if self.attempts_live is not None:
            return len(self.attempts_live)
        return self.blocked_attempts

    def usage_lines(self) -> list[str]:
        return self.usage.lines(self.config)

    def describe(self) -> str:
        if self._model_factory is not None:
            return f"{self.config.model_id} (modelo injetado)"
        if self.offline:
            return f"{self.config.describe()} (sem rede: so o cache)"
        return self.config.describe()

    def plan(self, text: str) -> tuple[int, int, int, int]:
        """(trechos, caracteres, trechos ja no cache, caracteres que seriam enviados) do texto. So le o
        disco: serve para mostrar ao Operador o que a rodada vai mandar ao provedor."""

        chunks = split_chunks(text, self.config.chunk_chars)
        cached = sum(1 for chunk in chunks if self._cache_load(chunk) is not None)
        pending = sum(len(chunk.text) for chunk in chunks if self._cache_load(chunk) is None)
        return len(chunks), sum(len(chunk.text) for chunk in chunks), cached, pending

    def discover(self, text: str) -> Discovery:
        chunks = split_chunks(text, self.config.chunk_chars)
        report = DiscoveryReport(chunks=len(chunks))
        found: dict[int, list[dict]] = {}
        pending: list[int] = []
        for index, chunk in enumerate(chunks):
            cached = self._cache_load(chunk)
            if cached is None:
                pending.append(index)
            else:
                found[index] = cached
                report.cached += 1
        if pending and self.offline:
            # Sem rede: trecho fora do cache nao e lido (fail-closed, como falha de API).
            report.failed = [(chunks[index].start, chunks[index].end) for index in pending]
            report.failure_reasons = ["sem rede: trecho fora do cache"]
            report.offline = True
        elif pending:
            report.sent = len(pending)
            failed = self._run(chunks, pending, found, report)
            report.failed = [(chunks[index].start, chunks[index].end) for index in sorted(failed)]
            report.empty = [
                (chunks[index].start, chunks[index].end)
                for index in pending
                if index not in failed and not found.get(index)
            ]
        mentions: list[Mention] = []
        for index in sorted(found):
            for item in found[index]:
                mention = _to_mention(item, chunks[index], text)
                if not mention.anchored:
                    report.ungrounded += 1
                mentions.append(mention)
        return Discovery(mentions=mentions, report=report)

    def _run(
        self,
        chunks: Sequence[Chunk],
        pending: Sequence[int],
        found: dict[int, list[dict]],
        report: DiscoveryReport,
    ) -> set[int]:
        if self._model_factory is None and not self.is_local:
            # Ponto da rede: so com a confirmacao desta rodada ativa (B2). A escolha do motor, sozinha,
            # nao autoriza o envio. (O canal local, H9-b, nao envia nada: sem confirmacao.)
            if self.permit is None:
                raise LlmConfigError(
                    "Envio ao provedor sem a confirmacao desta rodada: confirme canal, arquivos e "
                    "caracteres antes de cada rodada com LLM."
                )
            try:
                self.permit.require_active()
            except GateError as exc:
                raise LlmConfigError(str(exc)) from None
        lx = import_langextract()
        from langextract import prompt_validation

        failures: list[tuple[str, str]] = []
        lock = threading.Lock()

        def on_failure(prompt: str, reason: str) -> None:
            with lock:
                failures.append((prompt, reason))

        inner = self._model_factory() if self._model_factory else self._build_model()
        model = wrap_model(
            inner,
            max_workers=self.config.max_workers,
            retries=self.config.retries,
            on_failure=on_failure,
        )
        documents = [
            lx.data.Document(text=chunks[index].text, document_id=f"trecho_{index}")
            for index in pending
        ]
        annotated = lx.extract(
            text_or_documents=documents,
            prompt_description=PROMPT,
            examples=build_examples(),
            model=model,
            max_char_buffer=self.config.chunk_chars,
            batch_length=max(2 * self.config.max_workers, 1),
            max_workers=self.config.max_workers,
            use_schema_constraints=False,
            show_progress=False,
            fetch_urls=False,
            # I5: nada de log de depuracao do LangExtract (traz prompts, ou seja, texto de peca).
            debug=False,
            prompt_validation_level=prompt_validation.PromptValidationLevel.OFF,
            resolver_params={"suppress_parse_errors": True},
        )
        annotated = list(annotated)
        failed = {
            index
            for index in pending
            if any(chunks[index].text.strip() in prompt for prompt, _reason in failures)
        }
        report.failure_reasons = sorted({reason for _prompt, reason in failures})
        if failed:
            # So o motivo: o trecho tem PII e o log nao e pasta privada.
            _LOG.warning(
                "LLM: %d trecho(s) sem leitura (%s)",
                len(failed),
                " | ".join(scrub_secrets(reason) for reason in report.failure_reasons[:3]),
            )
        for document in annotated:
            index = int(str(document.document_id).rsplit("_", 1)[1])
            if index in failed:
                continue
            items = [
                _to_item(extraction, chunks[index].text)
                for extraction in document.extractions or []
            ]
            found[index] = items
            # Resposta vazia nao e guardada: pode ser falha disfarcada (JSON valido sem nada), e o cache
            # a serviria para sempre como "lida". O motor local confere o que ficou sem entidade.
            if items:
                self._cache_save(chunks[index], items)
        return failed

    def _build_model(self) -> Any:
        lx = import_langextract()
        cfg = self.config
        if self.offline:
            raise LlmConfigError("Modo sem rede: o modelo nao pode ser criado.")
        if not cfg.channel:
            raise LlmConfigError(
                "Canal nao liberado: o provedor so e chamado depois do portao do motor "
                "(llm_engine.check_engine)."
            )
        # I4: o portao reconferido AQUI, onde o texto sai da maquina (nao basta ter passado pela tela).
        try:
            check = check_network_point(
                channel=cfg.channel,
                provider=cfg.provider,
                location=cfg.location,
                project=cfg.project,
                model_id=cfg.model_id if cfg.provider == "ollama" else "",
                port=self._loopback_port,
            )
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
        if cfg.provider == "ollama":
            return self._build_ollama_model(check)
        kwargs: dict[str, Any] = {"temperature": 0.0}
        if cfg.provider in {"gemini", "vertex"}:
            kwargs.update(
                max_workers=1,
                max_output_tokens=cfg.max_output_tokens,
                safety_settings=_SAFETY_OFF,
                # O endereco vai fixo, pelo canal: nenhuma variavel de ambiente o troca.
                http_options={"base_url": check.base_url},
            )
            if cfg.provider == "vertex":
                if not cfg.project:
                    raise LlmConfigError("Vertex AI exige o projeto do Google Cloud (--projeto).")
                kwargs.update(vertexai=True, project=cfg.project, location=cfg.location)
                if cfg.credentials_path:
                    kwargs["credentials"] = _service_account_credentials(cfg.credentials_path)
            else:
                if cfg.api_key is None:
                    raise LlmConfigError(
                        "Sem chave do Gemini pago. Guarde-a no Gerenciador de Credenciais "
                        "(tools/anonimizar_llm.py --guardar-chave). Chave gratuita nao: "
                        "o conteudo pode treinar o modelo."
                    )
                kwargs["api_key"] = cfg.api_key.reveal()
            provider = "gemini"
        else:
            raise LlmConfigError(f"Provedor desconhecido: {cfg.provider!r}")
        config = lx.factory.ModelConfig(
            model_id=cfg.model_id, provider=provider, provider_kwargs=kwargs
        )
        try:
            model = lx.factory.create_model(
                config=config,
                examples=build_examples(),
                use_schema_constraints=True,
                fence_output=False,
            )
        finally:
            # A chave nao fica em dicionario de configuracao (traceback, log %r) depois de o cliente a ter.
            kwargs.pop("api_key", None)
            if isinstance(getattr(config, "provider_kwargs", None), dict):
                config.provider_kwargs.pop("api_key", None)
        hide_credentials(model)
        try:
            verify_client_endpoint(model, check)
            verify_no_disk_replay(model)
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
        quiet_sdk_logging()
        install_meter(model, self.usage, cfg.thinking)
        self.network_check = check
        return model

    def _build_ollama_model(self, check: NetworkCheck) -> Any:
        """Modelo do canal local (H9-b): provedor Ollama do LangExtract contra o loopback, endereco fixo.

        Nenhuma variavel de ambiente decide o endereco (o LangExtract leria ``OLLAMA_BASE_URL``: por isso
        ``base_url`` e ``model_url`` vao os dois, iguais). ``think=False``, ``temperature=0``, janela de
        contexto e ``keep_alive`` seguem em cada pedido. Sem chave. Depois de criado, o endereco que o
        objeto guarda e conferido, e o cliente HTTP passa a ignorar proxy do ambiente."""

        lx = import_langextract()
        cfg = self.config
        kwargs: dict[str, Any] = {
            "model_url": check.base_url,
            "base_url": check.base_url,
            "temperature": 0.0,
            "think": False,
            "num_ctx": cfg.num_ctx,
            "keep_alive": cfg.keep_alive,
            "timeout": cfg.timeout,
            # Teto no ponto em que o modelo e criado, haja o que houver na configuracao (ver OLLAMA_MAX_OUTPUT_TOKENS).
            "max_output_tokens": min(cfg.max_output_tokens, OLLAMA_MAX_OUTPUT_TOKENS),
        }
        config = lx.factory.ModelConfig(model_id=cfg.model_id, provider="ollama", provider_kwargs=kwargs)
        try:
            inner = lx.factory.create_model(
                config=config,
                examples=build_examples(),
                use_schema_constraints=False,
                fence_output=False,
            )
        except Exception as exc:  # noqa: BLE001 - a mensagem crua nao interessa a ninguem aqui
            raise LlmConfigError(f"Nao foi possivel criar o modelo do Ollama local ({safe_error(exc)}).") from None
        try:
            verify_ollama_model(inner, check)
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
        quiet_sdk_logging()
        self.network_check = check
        return _CountingModel(inner, self.usage)

    def _cache_path(self, chunk: Chunk) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256()
        for part in (
            PROMPT_VERSION,
            self.config.provider,
            self.config.model_id,
            _TOKEN_NUMBER.sub(r"[\1]", chunk.text),
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return self.cache_dir / f"{digest.hexdigest()}.json"

    def _cache_load(self, chunk: Chunk) -> list[dict] | None:
        path = self._cache_path(chunk)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        items = payload.get("extracoes") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            # Cache de versao antiga com resposta vazia: nao vale (a regra agora e nao guardar vazio).
            return None
        return [_realign(item, chunk.text) for item in items if isinstance(item, dict)]

    def _cache_save(self, chunk: Chunk, items: list[dict]) -> None:
        path = self._cache_path(chunk)
        if path is None or not items:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "formato": 1,
            "prompt": PROMPT_VERSION,
            "modelo": self.config.model_id,
            "extracoes": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def _service_account_credentials(path: str) -> Any:
    """Credencial do arquivo de conta de servico (Vertex AI), criada so na hora de montar o cliente."""

    try:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            str(Path(path).expanduser()), scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except Exception as exc:  # noqa: BLE001 - a mensagem crua pode trazer pedacos do arquivo
        raise LlmConfigError(
            f"Nao foi possivel usar o arquivo de credencial do Vertex AI ({safe_error(exc)})."
        ) from None


class _LoopbackRequests:
    """O ``requests`` do provedor Ollama sem proxy do ambiente (``trust_env`` desligado) e que so posta no
    endereco conferido: o ``requests.post`` solto honraria HTTPS_PROXY e leria .netrc."""

    def __init__(self, base_url: str) -> None:
        import requests

        self._requests = requests
        self._prefix = base_url.rstrip("/") + "/"
        self.exceptions = requests.exceptions

    def post(self, url: str, **kwargs: Any) -> Any:
        if not str(url).startswith(self._prefix):
            raise self._requests.exceptions.InvalidURL("endereco fora do loopback conferido: pedido recusado")
        with self._requests.Session() as session:
            session.trust_env = False
            return session.post(url, **kwargs)


def verify_ollama_model(model: Any, check: NetworkCheck) -> None:
    """Depois de criar o modelo do Ollama: o endereco que o OBJETO guarda e o conferido (http, loopback,
    porta do Ollama), nao ha chave, e o cliente HTTP ignora o proxy do ambiente. Levanta ``GateError``."""

    url = getattr(model, "_model_url", None)
    if not isinstance(url, str) or not url:
        raise GateError("Nao foi possivel confirmar o endereco do modelo do Ollama: o canal local foi recusado.")
    if url.rstrip("/") != check.base_url:
        raise GateError("O modelo do Ollama apontava para um endereco diferente do loopback conferido: recusado.")
    if getattr(model, "_api_key", None):
        raise GateError("O modelo do Ollama recebeu chave: o canal local nao usa chave. Recusado.")
    if not hasattr(model, "_requests"):
        raise GateError("Nao foi possivel confirmar o cliente HTTP do Ollama (provedor mudou): recusado.")
    model._requests = _LoopbackRequests(check.base_url)


class _CountingModel:
    """Modelo do Ollama com contagem de chamadas e falhas por pedido (para a AUDITORIA); o resto passa direto."""

    def __init__(self, inner: Any, meter: "UsageMeter") -> None:
        self._inner = inner
        self._meter = meter

    def infer(self, batch_prompts: Sequence[str], **kwargs: Any):
        # O LangExtract repassa ``max_workers`` ao modelo e o provedor o poria nas opcoes do Ollama: tira.
        kwargs.pop("max_workers", None)
        for prompt in batch_prompts:
            try:
                outputs = list(self._inner.infer([prompt], **kwargs))
            except Exception:
                self._meter.record_failure()
                raise
            self._meter.record_local_call()
            yield from outputs

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _hidden_repr(self: Any) -> str:
    return f"{type(self).__mro__[1].__name__}(model_id={getattr(self, 'model_id', '?')!r}, <credenciais ocultas>)"


class UsageMeter:
    """Uso do provedor numa rodada (H6): chamadas, tokens e respostas cortadas, para a AUDITORIA explicar a
    conta. So contagens: nada de texto nem de chave. Seguro entre as chamadas paralelas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.failed = 0
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.thinking_tokens = 0
        self.cached_tokens = 0
        self.truncated = 0
        self.blocked = 0
        self.thinking_rejected = False

    def record(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        reasons = {
            str(getattr(getattr(candidate, "finish_reason", None), "name", getattr(candidate, "finish_reason", "")))
            for candidate in (getattr(response, "candidates", None) or ())
        }
        with self._lock:
            self.calls += 1
            if usage is not None:
                self.prompt_tokens += int(getattr(usage, "prompt_token_count", 0) or 0)
                self.output_tokens += int(getattr(usage, "candidates_token_count", 0) or 0)
                self.thinking_tokens += int(getattr(usage, "thoughts_token_count", 0) or 0)
                self.cached_tokens += int(getattr(usage, "cached_content_token_count", 0) or 0)
            if any("MAX_TOKENS" in reason for reason in reasons):
                self.truncated += 1
            if any(reason in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"} for reason in reasons):
                self.blocked += 1

    def record_failure(self) -> None:
        with self._lock:
            self.calls += 1
            self.failed += 1

    def record_local_call(self) -> None:
        with self._lock:
            self.calls += 1

    def lines(self, config: "LlmConfig") -> list[str]:
        if config.provider == "ollama":
            if not self.calls:
                return ["LLM local: nenhuma chamada nesta rodada (tudo veio do cache local)."]
            return [f"LLM local: {self.calls} chamada(s), {self.failed} com erro."]
        if not self.calls:
            return ["Uso do provedor: nenhuma chamada nesta rodada (tudo veio do cache local)."]
        thinking = config.thinking if not self.thinking_rejected else f"{config.thinking} (recusado pelo modelo: padrao)"
        lines = [
            f"Uso do provedor ({config.model_id}, raciocinio: {thinking}): {self.calls} chamada(s), "
            f"{self.failed} com erro; tokens: entrada {self.prompt_tokens} (do cache do provedor "
            f"{self.cached_tokens}), saida {self.output_tokens}, raciocinio {self.thinking_tokens} "
            "(cobrado como saida).",
        ]
        if self.truncated:
            lines.append(
                f"ALERTA: {self.truncated} resposta(s) cortada(s) pelo limite de saida ({config.max_output_tokens} "
                "tokens): viram trecho sem leitura. Baixe o raciocinio (--raciocinio minimo) ou o --trecho."
            )
        if self.blocked:
            lines.append(f"ALERTA: {self.blocked} resposta(s) bloqueada(s) pelo filtro de seguranca do provedor.")
        return lines


class _MeteredModels:
    """``client.models`` com o medidor e o nivel de raciocinio; o resto passa direto ao SDK."""

    def __init__(self, models: Any, meter: UsageMeter, thinking: str | None) -> None:
        self._models = models
        self._meter = meter
        self._thinking = thinking

    def generate_content(self, *, model: str, contents: Any, config: Any = None, **kwargs: Any) -> Any:
        call_config = dict(config or {})
        if self._thinking and not self._meter.thinking_rejected:
            call_config.setdefault("thinking_config", {"thinking_level": self._thinking})
        try:
            response = self._models.generate_content(model=model, contents=contents, config=call_config, **kwargs)
        except Exception as exc:  # noqa: BLE001 - registrado e repassado ao LangExtract
            if "thinking_config" in call_config and _rejects_thinking(exc):
                # Modelo que nao aceita o nivel de raciocinio: segue no padrao dele em vez de perder o trecho.
                self._meter.thinking_rejected = True
                call_config.pop("thinking_config", None)
                try:
                    response = self._models.generate_content(
                        model=model, contents=contents, config=call_config, **kwargs
                    )
                except Exception:
                    self._meter.record_failure()
                    raise
            else:
                self._meter.record_failure()
                raise
        self._meter.record(response)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._models, name)


class _MeteredClient:
    """O cliente do SDK com ``models`` medido. As conferencias do portao (endereco, gravacao em disco) leem o
    cliente original e rodam ANTES de ele ser envolvido."""

    def __init__(self, client: Any, meter: UsageMeter, thinking: str | None) -> None:
        self._inner = client
        self.models = _MeteredModels(client.models, meter, thinking)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _rejects_thinking(exc: Exception) -> bool:
    text = str(exc).casefold()
    return ("thinking" in text or "thought" in text) and ("400" in text or "invalid" in text or "not supported" in text)


def install_meter(model: Any, meter: UsageMeter, thinking: str) -> None:
    """Envolve o cliente do modelo do LangExtract com o medidor (H6). Sem cliente (SDK mudou): nao mede, avisa."""

    client = getattr(model, "_client", None)
    if client is None or not hasattr(client, "models"):
        _LOG.warning("LLM: cliente do SDK sem 'models'; uso de tokens nao sera medido nesta rodada.")
        return
    model._client = _MeteredClient(client, meter, THINKING_LEVELS.get(thinking))


def hide_credentials(model: Any) -> None:
    """O modelo do LangExtract (``GeminiLanguageModel``) e um dataclass que guarda ``api_key`` e a mostra no
    ``repr``: um traceback com variaveis, um log ``%r`` ou um ``print(model)`` vazaria a chave. O cliente do SDK
    ja a tem; aqui o campo vira um marcador e o repr/str deixam de listar campos."""

    if getattr(model, "api_key", None):
        model.api_key = HIDDEN
    cls = type(model)
    try:
        model.__class__ = type(cls.__name__, (cls,), {"__repr__": _hidden_repr, "__str__": _hidden_repr})
    except TypeError:  # classe com __slots__ ou imutavel: o campo ja foi ocultado acima
        pass


def _to_item(extraction: Any, chunk_text: str) -> dict:
    interval = getattr(extraction, "char_interval", None)
    start = getattr(interval, "start_pos", None) if interval is not None else None
    end = getattr(interval, "end_pos", None) if interval is not None else None
    grounded = isinstance(start, int) and isinstance(end, int) and 0 <= start < end
    return {
        "classe": str(extraction.extraction_class or ""),
        "texto": str(extraction.extraction_text or ""),
        "atributos": dict(extraction.attributes or {}),
        "inicio": start if grounded else None,
        "fim": end if grounded else None,
        "trecho": chunk_text[start:end] if grounded else "",
    }


def _realign(item: dict, chunk_text: str) -> dict:
    """Item do cache num trecho renumerado: reancora pelo texto, nao pela posicao."""

    start, end = item.get("inicio"), item.get("fim")
    piece = str(item.get("trecho") or "")
    if isinstance(start, int) and isinstance(end, int) and piece and chunk_text[start:end] == piece:
        return item
    needle = piece or str(item.get("texto") or "")
    match = re.search(re.escape(needle), chunk_text, re.IGNORECASE) if needle else None
    if match is None:
        return {**item, "inicio": None, "fim": None, "trecho": ""}
    return {**item, "inicio": match.start(), "fim": match.end(), "trecho": match.group(0)}


def _attribute(attributes: dict, name: str) -> str:
    value = attributes.get(name, "")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _to_mention(item: dict, chunk: Chunk, text: str) -> Mention:
    attributes = item.get("atributos") or {}
    start, end = item.get("inicio"), item.get("fim")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(chunk.text):
        absolute_start, absolute_end = chunk.start + start, chunk.start + end
        source_text = text[absolute_start:absolute_end]
    else:
        absolute_start = absolute_end = -1
        source_text = ""
    return Mention(
        kind=str(item.get("classe") or ""),
        text=str(item.get("texto") or ""),
        role=_attribute(attributes, "papel"),
        canonical=_attribute(attributes, "nome_canonico"),
        start=absolute_start,
        end=absolute_end,
        source_text=source_text,
    )
