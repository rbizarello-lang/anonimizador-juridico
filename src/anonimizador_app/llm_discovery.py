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
import time
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
    ollama_model_refusal,
    quiet_sdk_logging,
    safe_error,
    scrub_secrets,
    verify_client_endpoint,
    verify_no_disk_replay,
)
from . import run_timing
from .roster import Mention

PROMPT_VERSION = "2026-10-01.1"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
# H9-b: canal local (Ollama em loopback). Modelo padrao e janela de contexto do pedido.
OLLAMA_DEFAULT_MODEL = "gemma4:e4b"
OLLAMA_DEFAULT_NUM_CTX = 4096
OLLAMA_CHUNK_CHARS = 2000
# Teto de saida por chamada no canal local. Sem ele o provedor manda num_predict = 8192, maior que a propria
# janela: um modelo pequeno em laco gerou 3.800 a 5.500 tokens por chamada (8 minutos cada) ate o Ollama
# devolver 500. Um trecho de 2000 caracteres cabe em ~2000 tokens de JSON; o laco e cortado em ~2 minutos.
OLLAMA_MAX_OUTPUT_TOKENS = 2000
# H9-o: prazo de cada chamada ao Ollama pelo tamanho do modelo. Um modelo de 12 a 26B rodando quase todo na CPU
# gera 2 a 6 tokens/s e ainda carrega 8 a 18 GB na primeira chamada: os 600 s fixos cortariam chamadas legitimas.
# Prazo = 150 s por bilhao de parametros, entre 600 s e 3600 s; modelo sem tamanho no nome: 1800 s.
OLLAMA_MIN_TIMEOUT = 600
OLLAMA_MAX_TIMEOUT = 3600
OLLAMA_UNKNOWN_TIMEOUT = 1800
OLLAMA_TIMEOUT_PER_BILLION = 150
# v0.4.13: prazo TOTAL de cada chamada ao Ollama (relogio de parede), alem do prazo de leitura do ``requests``. Na
# rodada do Qwen de 24/10 a chamada ficou 2h30 em "Conferindo" com a conexao aberta e o modelo ja descarregado: o
# prazo de leitura nao disparou. Passado o prazo + folga, a chamada e abandonada e o trecho vai ao motor local.
OLLAMA_DEADLINE_GRACE = 60
# Nova tentativa depois de tempo esgotado: UMA por trecho (o modelo pode ter sido descarregado e recarregado).
OLLAMA_TIMEOUT_RETRIES = 1
# Disjuntor: depois de tantas chamadas SEGUIDAS fora do prazo, o Ollama e dado como parado nesta rodada; os trechos
# restantes vao direto ao motor local (a rodada continua e termina, com aviso), em vez de esperar o prazo de cada um.
OLLAMA_BREAKER_TIMEOUTS = 3

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

    provider: str = "gemini"  # gemini (API paga) | vertex | ollama | openrouter (H9-p, so teste)
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
    # H9-j: o trecho que voltou vazio volta ao LLM em dois pedacos menores (B6 revisto; o reenvio do mesmo texto, com
    # temperatura 0, da a mesma resposta: 29 reenvios e 0 recuperados no teste de 16/10).
    retry_empty: bool = True
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
    # H9-i lote B6: trechos vazios reenviados uma vez, e quantos vieram com entidade na segunda vez.
    empty_retried: int = 0
    empty_recovered: int = 0
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


def split_in_halves(chunk: Chunk, minimum: int = 600) -> list[Chunk]:
    """O trecho em dois, no paragrafo, linha ou espaco mais perto do meio (H9-j). Trecho curto nao se divide."""

    text = chunk.text
    if len(text) < minimum:
        return []
    middle = len(text) // 2
    cut = -1
    for separator in (chr(10) * 2, chr(10), ". ", " "):
        before = text.rfind(separator, middle // 2, middle + 1)
        after = text.find(separator, middle, middle + middle // 2)
        options = [pos for pos in (before, after) if pos > 0]
        if options:
            best = min(options, key=lambda pos: abs(pos - middle))
            cut = best + len(separator)
            break
    if cut <= 0 or cut >= len(text):
        cut = middle
    return [
        Chunk(start=chunk.start, end=chunk.start + cut, text=text[:cut]),
        Chunk(start=chunk.start + cut, end=chunk.end, text=text[cut:]),
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


def is_timeout_error(exc: BaseException) -> bool:
    """H9-o: a chamada estourou o prazo (o provedor do Ollama embrulha o ``ReadTimeout`` do ``requests``)."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or "timeout" in type(current).__name__.casefold():
            return True
        if "timed out" in str(current).casefold():
            return True
        nested = getattr(current, "original", None)
        current = nested if isinstance(nested, BaseException) else (current.__cause__ or current.__context__)
    return False


def is_connection_error(exc: BaseException) -> bool:
    """H9-o: o Ollama nao atendeu (fora do ar, reiniciando). So este erro vale nova tentativa no canal local."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if is_timeout_error(current):
            return False
        if isinstance(current, ConnectionError) or type(current).__name__ in {"ConnectionError", "ConnectTimeout"}:
            return True
        nested = getattr(current, "original", None)
        current = nested if isinstance(nested, BaseException) else (current.__cause__ or current.__context__)
    return False


class CallDeadlineExceeded(TimeoutError):
    """v0.4.13: a chamada passou do prazo total (conexao aberta sem resposta) e foi abandonada."""


def call_with_deadline(
    fn: Callable[[], Any], deadline: float | None, on_abandon: Callable[[], None] | None = None
) -> Any:
    """Roda ``fn`` com prazo total (segundos, relogio de parede). Sem prazo, roda direto. Passado o prazo, chama
    ``on_abandon`` (fecha as conexoes abertas, se der) e levanta ``CallDeadlineExceeded``; a linha de execucao da
    chamada fica para tras (daemon) e nao segura a rodada nem o fim do processo."""

    if not deadline or deadline <= 0:
        return fn()
    box: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - repassada a quem chamou
            box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=target, name="llm-chamada", daemon=True)
    worker.start()
    if not done.wait(deadline):
        if on_abandon is not None:
            try:
                on_abandon()
            except Exception:  # noqa: BLE001 - so uma tentativa de soltar a conexao
                pass
        raise CallDeadlineExceeded(f"chamada sem resposta no prazo total ({int(deadline)} s)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class LocalCallGuard:
    """v0.4.13: disjuntor do canal local. Conta as chamadas SEGUIDAS fora do prazo (qualquer resposta zera); a partir
    de ``limit`` o Ollama e dado como parado e os trechos seguintes nem sao enviados (vao ao motor local)."""

    def __init__(self, limit: int = OLLAMA_BREAKER_TIMEOUTS) -> None:
        self._lock = threading.Lock()
        self.limit = max(1, limit)
        self.in_row = 0
        self.skipped = 0

    def tripped(self) -> bool:
        with self._lock:
            return self.in_row >= self.limit

    def timed_out(self) -> None:
        with self._lock:
            self.in_row += 1

    def answered(self) -> None:
        with self._lock:
            self.in_row = 0

    def skip(self) -> None:
        with self._lock:
            self.skipped += 1


BREAKER_REASON = "LLM local sem resposta em varias chamadas seguidas: trecho direto ao motor local"


def wrap_model(
    inner: Any,
    *,
    max_workers: int,
    retries: int,
    on_failure: Callable[[str, str], None],
    local: bool = False,
    deadline: float | None = None,
    guard: LocalCallGuard | None = None,
    meter: "UsageMeter | None" = None,
) -> Any:
    """Envolve o modelo do LangExtract: paralelismo, nova tentativa e falha por trecho.

    H9-o, canal local (``local=True``): nova tentativa depois de falha de conexao. Erro do servidor e resposta fora do
    formato JSON nao se repetem: com temperatura 0 o mesmo pedido da a mesma resposta, e num modelo grande na CPU
    cada repeticao custaria de novo dezenas de minutos. O trecho vai para o motor local (fail-closed), como qualquer
    trecho sem leitura.

    v0.4.13: ``deadline`` e o prazo TOTAL de cada chamada (``call_with_deadline``); tempo esgotado (do ``requests`` ou
    do prazo total) tem UMA nova tentativa no canal local (``OLLAMA_TIMEOUT_RETRIES``), salvo com o disjuntor
    (``guard``) ja aberto. ``meter`` conta as chamadas fora do prazo e as respostas fora do formato (AUDITORIA)."""

    abandon = getattr(inner, "abandon", None) if local else None

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
            attempts = 0
            timeout_retries = OLLAMA_TIMEOUT_RETRIES if local else 0
            while True:
                if guard is not None and guard.tripped():
                    # v0.4.13: o Ollama nao respondeu nas ultimas chamadas: nem envia (a rodada segue).
                    guard.skip()
                    reason = BREAKER_REASON
                    break
                begin = time.perf_counter()
                try:
                    scored = call_with_deadline(
                        lambda: next(iter(inner.infer([prompt], **kwargs))), deadline, abandon
                    )
                    output = scored[0].output or ""
                except Exception as exc:  # noqa: BLE001 - trecho isolado
                    run_timing.add("llm", time.perf_counter() - begin)  # H9-o: tempo por chamada
                    attempts += 1

                    # So o tipo, o codigo HTTP e o status: a mensagem crua pode ter a chave e texto da peca,
                    # e esta frase vai para a AUDITORIA (pasta exportavel) e para o log.
                    reason = safe_error(exc)
                    if is_timeout_error(exc):
                        reason += " (tempo esgotado)"
                        if meter is not None:
                            meter.record_timeout()
                        if guard is not None:
                            guard.timed_out()
                        if local:
                            # v0.4.13: uma nova tentativa (o modelo pode ter sido descarregado); depois, motor local.
                            if timeout_retries > 0 and not (guard is not None and guard.tripped()):
                                timeout_retries -= 1
                                continue
                            break
                    if local and not is_connection_error(exc):
                        break  # H9-o: sem repetir chamada longa (ver acima)
                    if attempts > max(0, retries):
                        break
                    continue
                run_timing.add("llm", time.perf_counter() - begin)
                attempts += 1
                if guard is not None:
                    guard.answered()
                if _parses(output):
                    # H9-p: sem modo JSON (Gemma na Gemini API, OpenRouter) a resposta pode vir em cerca ```json.
                    return _FENCE.sub("", output.strip())
                reason = "resposta fora do formato JSON"
                if meter is not None:
                    meter.record_unparsable()
                if local or attempts > max(0, retries):
                    break  # H9-o: temperatura 0, mesma entrada: a repeticao daria a mesma resposta
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
        # H9-p: canal openrouter-teste. SHA-256 dos arquivos ja conferidos contra a lista do documento sintetico
        # (``llm_gate.synthetic_lock_refusals``); vazio = o modelo do OpenRouter nao e criado.
        self.synthetic_lock: tuple[str, ...] = ()
        # v0.4.13: disjuntor do canal local (chamadas seguidas fora do prazo), da vida do descobridor (uma rodada).
        self.call_guard = LocalCallGuard()

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

    def output_stats_lines(self) -> list[str]:
        """v0.4.13: tokens gerados por chamada e respostas fora do formato (AUDITORIA exportavel e privada)."""

        deadline = self.config.timeout + OLLAMA_DEADLINE_GRACE if self.is_local else None
        return self.usage.output_lines(
            deadline=deadline, skipped=self.call_guard.skipped if self.is_local else 0
        )

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
                run_timing.count("llm_cache")  # H9-o
        if pending and self.offline:
            # Sem rede: trecho fora do cache nao e lido (fail-closed, como falha de API).
            report.failed = [(chunks[index].start, chunks[index].end) for index in pending]
            report.failure_reasons = ["sem rede: trecho fora do cache"]
            report.offline = True
        elif pending:
            report.sent = len(pending)
            failed = self._run(chunks, pending, found, report)
            # H9-j (B6 revisto): o trecho que voltou vazio (JSON valido sem entidade) volta ao LLM em DOIS PEDACOS
            # menores. Reenviar o mesmo texto com temperatura 0 da a mesma resposta (29 reenvios, 0 recuperados no
            # teste de 16/10); a entrada diferente pode dar outra. Cada metade tem cache proprio.
            retry = [index for index in pending if index not in failed and not found.get(index)]
            if retry and self.config.retry_empty:
                self._retry_empty_in_halves(chunks, retry, found, report)
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

    def _retry_empty_in_halves(
        self,
        chunks: Sequence[Chunk],
        retry: Sequence[int],
        found: dict[int, list[dict]],
        report: DiscoveryReport,
    ) -> None:
        subs: list[Chunk] = []
        owner: list[int] = []
        for index in retry:
            for half in split_in_halves(chunks[index]):
                subs.append(half)
                owner.append(index)
        if not subs:
            return
        report.empty_retried = len({index for index in owner})
        sub_found: dict[int, list[dict]] = {}
        sub_pending: list[int] = []
        for number, sub in enumerate(subs):
            cached = self._cache_load(sub)
            if cached is None:
                sub_pending.append(number)
            else:
                sub_found[number] = cached
        if sub_pending:
            reasons = list(report.failure_reasons)
            report.sent += len(sub_pending)
            self._run(subs, sub_pending, sub_found, report)
            report.failure_reasons = sorted({*reasons, *report.failure_reasons})
        recovered: set[int] = set()
        for number, sub in enumerate(subs):
            parent = chunks[owner[number]]
            shift = sub.start - parent.start
            for item in sub_found.get(number, []):
                moved = dict(item)
                if isinstance(moved.get("inicio"), int) and isinstance(moved.get("fim"), int):
                    moved["inicio"] += shift
                    moved["fim"] += shift
                found.setdefault(owner[number], []).append(moved)
                recovered.add(owner[number])
        report.empty_recovered = len(recovered)

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
            local=self.is_local,
            # v0.4.13: prazo total por chamada e disjuntor so no canal local (os SDKs da nuvem tem prazo proprio).
            deadline=(self.config.timeout + OLLAMA_DEADLINE_GRACE) if self.is_local else None,
            guard=self.call_guard if self.is_local else None,
            meter=self.usage,
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
        if cfg.provider == "openrouter":
            return self._build_openrouter_model(check)
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
                from .llm_gate import SYNTHETIC_SHA256, is_free_tier_gemma

                if is_free_tier_gemma(cfg.channel, cfg.model_id) and (
                    not self.synthetic_lock or any(digest not in SYNTHETIC_SHA256 for digest in self.synthetic_lock)
                ):
                    # v0.4.10: Gemma na Gemini API e nivel gratuito; so o documento sintetico sai por ele.
                    raise LlmConfigError(
                        "Modelo Gemma no gemini-pago e sempre nivel gratuito (o Google pode usar o texto): so o "
                        "documento sintetico pode ser enviado. Nada foi enviado."
                    )
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
        # v0.4.13: o ``eval_count`` de cada resposta (tokens gerados) vai ao medidor da rodada.
        inner._requests.meter = self.usage
        quiet_sdk_logging()
        self.network_check = check
        return _CountingModel(inner, self.usage)

    def _build_openrouter_model(self, check: NetworkCheck) -> Any:
        """H9-p, SO TESTE: OpenRouter, so depois da trava do documento sintetico (``synthetic_lock``)."""

        from .llm_gate import SYNTHETIC_SHA256
        from .openrouter_model import build_openrouter_model

        cfg = self.config
        if not self.synthetic_lock or any(digest not in SYNTHETIC_SHA256 for digest in self.synthetic_lock):
            raise LlmConfigError(
                "Canal openrouter-teste sem a trava do documento sintetico conferida: nada foi enviado."
            )
        if cfg.api_key is None:
            raise LlmConfigError(
                "Sem chave do OpenRouter. Guarde-a no Gerenciador de Credenciais "
                "(tools/anonimizar_llm.py --guardar-chave-openrouter)."
            )
        try:
            model = build_openrouter_model(
                model_id=cfg.model_id,
                api_key=cfg.api_key,
                max_output_tokens=cfg.max_output_tokens,
                thinking=cfg.thinking,
                meter=self.usage,
                base_url=check.base_url,
            )
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
        quiet_sdk_logging()
        self.network_check = check
        return model

    def _cache_path(self, chunk: Chunk) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256()
        parts = [PROMPT_VERSION, self.config.provider, self.config.model_id.strip()]
        if self.config.provider == "ollama" and self.config.num_ctx != OLLAMA_DEFAULT_NUM_CTX:
            # H9-o: outra janela de contexto pode dar outra resposta. A janela padrao fica fora da chave para o
            # cache ja gravado (gemma4:e4b, 4096) continuar valendo.
            parts.append(f"num_ctx={self.config.num_ctx}")
        parts.append(_TOKEN_NUMBER.sub(r"[\1]", chunk.text))
        for part in parts:
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
        # v0.4.13: medidor da rodada (tokens gerados por chamada, do ``eval_count`` do Ollama) e as sessoes abertas,
        # para a chamada abandonada pelo prazo total soltar a conexao.
        self.meter: "UsageMeter | None" = None
        self._open: set[Any] = set()
        self._lock = threading.Lock()

    def post(self, url: str, **kwargs: Any) -> Any:
        if not str(url).startswith(self._prefix):
            raise self._requests.exceptions.InvalidURL("endereco fora do loopback conferido: pedido recusado")
        with self._requests.Session() as session:
            session.trust_env = False
            with self._lock:
                self._open.add(session)
            try:
                response = session.post(url, **kwargs)
            finally:
                with self._lock:
                    self._open.discard(session)
        if self.meter is not None:
            self.meter.record_ollama(response)
        return response

    def close_all(self) -> None:
        """v0.4.13: fecha as sessoes ainda abertas (chamada abandonada pelo prazo total)."""

        with self._lock:
            sessions, self._open = list(self._open), set()
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - so uma tentativa
                pass

    def get(self, url: str, **kwargs: Any) -> Any:
        """H9-o: so para a lista de modelos (``/api/tags``), com a mesma trava de endereco e sem proxy."""

        if not str(url).startswith(self._prefix):
            raise self._requests.exceptions.InvalidURL("endereco fora do loopback conferido: pedido recusado")
        with self._requests.Session() as session:
            session.trust_env = False
            return session.get(url, **kwargs)


# ---------------------------------------------------------------------------------------------------
# H9-o: modelos instalados no Ollama local, prazo por tamanho e estimativa de tempo
# ---------------------------------------------------------------------------------------------------

_BILLIONS_IN_NAME = re.compile(r"(?<![a-z0-9.])(?:e)?(\d+(?:\.\d+)?)b(?![a-z0-9])", re.IGNORECASE)
_BILLIONS_IN_DETAIL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([BM])\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OllamaModel:
    """Um modelo baixado no Ollama desta maquina (so o que ``/api/tags`` diz; nada de texto de peca)."""

    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization: str = ""
    digest: str = ""

    def size_label(self) -> str:
        if not self.size_bytes:
            return "tamanho ?"
        return f"{self.size_bytes / 1024**3:.1f} GB".replace(".", ",")

    def label(self) -> str:
        extra = f", {self.parameter_size}" if self.parameter_size else ""
        return f"{self.name} ({self.size_label()}{extra})"

    def audit_line(self) -> str:
        parts = [self.size_label()]
        if self.parameter_size:
            parts.append(f"{self.parameter_size} parametros")
        if self.quantization:
            parts.append(self.quantization)
        if self.digest:
            parts.append(f"digest {self.digest[:12]}")
        return f"Modelo no Ollama: {self.name} ({', '.join(parts)})"


def model_billions(model_id: str, parameter_size: str = "") -> float | None:
    """Bilhoes de parametros pelo nome (``gemma4:12b`` -> 12; ``gemma4:e4b`` -> 4, o efetivo; ``26b-a4b`` -> 26)
    ou, sem numero no nome, pelo ``parameter_size`` do Ollama (``"11.9B"``). ``None`` se nao der para saber."""

    tag = (model_id or "").rsplit(":", 1)[-1]
    match = _BILLIONS_IN_NAME.search(tag) or _BILLIONS_IN_NAME.search(model_id or "")
    if match:
        return float(match.group(1))
    detail = _BILLIONS_IN_DETAIL.match(parameter_size or "")
    if detail:
        value = float(detail.group(1))
        return value / 1000 if detail.group(2).upper() == "M" else value
    return None


def ollama_timeout_for(model_id: str, parameter_size: str = "") -> int:
    """Prazo (segundos) de cada chamada ao Ollama para este modelo (ver ``OLLAMA_TIMEOUT_PER_BILLION``)."""

    billions = model_billions(model_id, parameter_size)
    if billions is None:
        return OLLAMA_UNKNOWN_TIMEOUT
    return int(min(OLLAMA_MAX_TIMEOUT, max(OLLAMA_MIN_TIMEOUT, OLLAMA_TIMEOUT_PER_BILLION * billions)))


# Segundos por trecho (chamada) nesta maquina (24 GB de RAM, RX 580 de 4 GB; 20/10/2026): o gemma4:e4b pela rodada
# real (157 chamadas em ~21 min, com o motor local junto; ~1h30 a frio); os outros pela razao de velocidade medida
# contra ele (entrada 111/41/32/36 tokens/s e saida 17,6/7,2/5,9/6,2 tokens/s). O que nao esta aqui sai de
# ``OLLAMA_SECONDS_PER_BILLION``. So para a estimativa mostrada antes da rodada.
OLLAMA_MEASURED_SECONDS = {
    "gemma4:e4b": 10.0,
    "gemma4:12b": 26.0,
    "ministral-3:14b": 30.0,
    "qwen3:14b": 32.0,
}
OLLAMA_SECONDS_PER_BILLION = 3.0


def ollama_seconds_per_chunk(model_id: str) -> float:
    """Tempo medio estimado de uma chamada (trecho de ``OLLAMA_CHUNK_CHARS`` caracteres) neste modelo."""

    name = (model_id or "").strip()
    if name in OLLAMA_MEASURED_SECONDS:
        return OLLAMA_MEASURED_SECONDS[name]
    billions = model_billions(name)
    return max(8.0, OLLAMA_SECONDS_PER_BILLION * (billions if billions is not None else 8.0))


def list_ollama_models(
    *, port: int = OLLAMA_PORT, env: Any = None, timeout: float = 2.0
) -> list[OllamaModel] | None:
    """Modelos baixados no Ollama local (``GET /api/tags``), so pelo loopback conferido pelo portao e sem proxy.

    Modelo ``:cloud`` (ou com ``cloud`` no nome) e modelo remoto (``remote_host``/``remote_model``) ficam de fora.
    ``None`` se o portao recusar o canal ou o Ollama nao responder: a tela volta ao campo de texto."""

    try:
        check = check_network_point(channel="ollama-local", provider="ollama", env=env, port=port)
    except GateError:
        return None
    try:
        client = _LoopbackRequests(check.base_url)
        response = client.get(f"{check.base_url}/api/tags", timeout=timeout)
        if getattr(response, "status_code", 0) != 200:
            return None
        payload = response.json()
    except Exception:  # noqa: BLE001 - Ollama fora do ar, resposta estranha: so volta ao campo de texto
        return None
    models: list[OllamaModel] = []
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name or ollama_model_refusal(name) or item.get("remote_host") or item.get("remote_model"):
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        models.append(
            OllamaModel(
                name=name,
                size_bytes=size,
                parameter_size=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
                digest=str(item.get("digest") or ""),
            )
        )
    return sorted(models, key=lambda model: model.name)


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

    def abandon(self) -> None:
        """v0.4.13: a chamada passou do prazo total; solta a conexao com o Ollama, se der."""

        client = getattr(self._inner, "_requests", None)
        closer = getattr(client, "close_all", None)
        if callable(closer):
            closer()

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
        # H9-p: modelo que recusa o modo JSON (Gemma na Gemini API): segue sem response_schema, pelo prompt.
        self.schema_rejected = False
        # H9-p: custo em dolares que o OpenRouter informa por chamada (``usage.cost``).
        self.cost_usd = 0.0
        # v0.4.13: tokens GERADOS em cada chamada (saida + raciocinio; no Ollama, o ``eval_count``), so das chamadas
        # em que o provedor informou; respostas fora do formato (JSON invalido) e chamadas fora do prazo.
        self.generated: list[int] = []
        self.unparsable = 0
        self.timeouts = 0

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
                output = int(getattr(usage, "candidates_token_count", 0) or 0)
                thinking = int(getattr(usage, "thoughts_token_count", 0) or 0)
                self.output_tokens += output
                self.thinking_tokens += thinking
                self.cached_tokens += int(getattr(usage, "cached_content_token_count", 0) or 0)
                self.generated.append(output + thinking)
            if any("MAX_TOKENS" in reason for reason in reasons):
                self.truncated += 1
            if any(reason in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"} for reason in reasons):
                self.blocked += 1

    def record_openrouter(self, data: Any) -> None:
        """Uma resposta do OpenRouter (formato OpenAI): tokens, raciocinio, custo e resposta cortada."""

        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        choices = data.get("choices") if isinstance(data, dict) else None
        reasons = {str((item or {}).get("finish_reason") or "") for item in (choices or []) if isinstance(item, dict)}
        details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}

        def number(value: Any) -> int:
            return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

        with self._lock:
            self.calls += 1
            self.prompt_tokens += number(usage.get("prompt_tokens"))
            reasoning = number(details.get("reasoning_tokens"))
            self.thinking_tokens += reasoning
            self.output_tokens += max(0, number(usage.get("completion_tokens")) - reasoning)
            self.cached_tokens += number(prompt_details.get("cached_tokens"))
            if "completion_tokens" in usage:
                self.generated.append(max(number(usage.get("completion_tokens")), reasoning))
            cost = usage.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                self.cost_usd += float(cost)
            if "length" in reasons:
                self.truncated += 1
            if "content_filter" in reasons:
                self.blocked += 1

    def record_failure(self) -> None:
        with self._lock:
            self.calls += 1
            self.failed += 1

    def record_local_call(self) -> None:
        with self._lock:
            self.calls += 1

    def record_ollama(self, response: Any) -> None:
        """v0.4.13: ``eval_count`` (tokens gerados) e ``prompt_eval_count`` de uma resposta do Ollama, se houver."""

        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - resposta sem JSON: o provedor trata o erro
            return
        if not isinstance(data, dict):
            return
        generated = data.get("eval_count")
        prompt = data.get("prompt_eval_count")
        with self._lock:
            if isinstance(generated, int) and not isinstance(generated, bool):
                self.generated.append(generated)
                self.output_tokens += generated
            if isinstance(prompt, int) and not isinstance(prompt, bool):
                self.prompt_tokens += prompt

    def record_unparsable(self) -> None:
        with self._lock:
            self.unparsable += 1

    def record_timeout(self) -> None:
        with self._lock:
            self.timeouts += 1

    def output_lines(self, *, deadline: float | None = None, skipped: int = 0) -> list[str]:
        """v0.4.13: quanto o modelo ESCREVE por chamada (o gargalo na CPU) e quantas respostas vieram fora do formato.
        Uma chamada e um trecho (ou a metade de um trecho reenviado). So contagens."""

        with self._lock:
            generated = list(self.generated)
            unparsable, timeouts = self.unparsable, self.timeouts
        if generated:
            total = sum(generated)
            mean = total / len(generated)
            head = (
                f"Tokens gerados por chamada (saida + raciocinio, como o provedor informa): media "
                f"{mean:.0f}, maximo {max(generated)}, total {total}, em {len(generated)} chamada(s) com contagem"
            )
        else:
            head = "Tokens gerados por chamada: o provedor nao informou (ou nenhuma chamada nesta rodada)"
        lines = [head + f"; respostas fora do formato (JSON invalido ou ilegivel): {unparsable}."]
        if timeouts or skipped:
            prazo = f" (prazo total por chamada: {int(deadline)} s)" if deadline else ""
            lines.append(
                f"ALERTA: {timeouts} chamada(s) ao LLM fora do prazo{prazo}; trechos nao enviados porque o LLM "
                f"parou de responder: {skipped}. Esses trechos foram tratados pelo motor local (a rodada seguiu)."
            )
        return lines

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
        if config.provider == "openrouter":
            lines.append(f"Custo informado pelo OpenRouter: US$ {self.cost_usd:.4f}.")
        if self.schema_rejected:
            lines.append("Modo JSON recusado pelo modelo: a rodada seguiu sem response_schema (formato so pelo prompt).")
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
        if self._meter.schema_rejected:
            for name in _SCHEMA_KEYS:
                call_config.pop(name, None)
        # Ate duas recusas de configuracao (raciocinio, modo JSON), cada uma uma vez: o trecho nao se perde por elas.
        for _attempt in range(3):
            try:
                response = self._models.generate_content(model=model, contents=contents, config=call_config, **kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - registrado e repassado ao LangExtract
                if "thinking_config" in call_config and _rejects_thinking(exc):
                    # Modelo que nao aceita o nivel de raciocinio: segue no padrao dele em vez de perder o trecho.
                    self._meter.thinking_rejected = True
                    call_config.pop("thinking_config", None)
                    continue
                if any(name in call_config for name in _SCHEMA_KEYS) and _rejects_json_mode(exc):
                    self._meter.schema_rejected = True
                    for name in _SCHEMA_KEYS:
                        call_config.pop(name, None)
                    continue
                self._meter.record_failure()
                raise
        else:  # pragma: no cover - as duas recusas ja foram tiradas
            self._meter.record_failure()
            raise LlmConfigError("Configuracao recusada pelo modelo.")
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


_SCHEMA_KEYS = ("response_mime_type", "response_schema", "response_json_schema")


def _rejects_json_mode(exc: Exception) -> bool:
    """H9-p: a Gemini API recusa o modo JSON em alguns modelos (Gemma): "JSON mode is not enabled for models/..."."""

    text = str(exc).casefold()
    if "400" not in text and "invalid" not in text:
        return False
    return "json mode" in text or "response_mime_type" in text or "response_schema" in text or "mime type" in text


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
