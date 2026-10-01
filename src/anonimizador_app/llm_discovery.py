"""Descoberta de entidades por LLM com LangExtract (Gemini API paga ou Vertex AI).

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
    GateError,
    NetworkCheck,
    RunPermit,
    check_network_point,
    quiet_sdk_logging,
    verify_client_endpoint,
    verify_no_disk_replay,
)
from .roster import Mention

PROMPT_VERSION = "2026-10-01.1"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
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
            "Defiro a penhora via SISBAJUD. Intime-se o executado JOSÉ CARLOS NUNES na "
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
    max_workers: int = 16
    chunk_chars: int = 4000
    max_output_tokens: int = 8192
    retries: int = 1

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
                    reason = f"{type(exc).__name__}: {exc}"[:300]
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
    ) -> None:
        self.config = config or LlmConfig()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._model_factory = model_factory
        self.offline = offline
        # B2: a confirmacao da rodada. Sem ela ativa, o modelo (e a rede) nao e criado.
        self.permit = permit
        # I4: o que o portao conferiu no ponto da rede na ultima vez que o modelo foi criado.
        self.network_check: NetworkCheck | None = None

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
        if self._model_factory is None:
            # Ponto da rede: so com a confirmacao desta rodada ativa (B2). A escolha do motor, sozinha,
            # nao autoriza o envio.
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
                " | ".join(report.failure_reasons[:3]),
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
            )
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
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
        model = lx.factory.create_model(
            config=config,
            examples=build_examples(),
            use_schema_constraints=True,
            fence_output=False,
        )
        try:
            verify_client_endpoint(model, check)
            verify_no_disk_replay(model)
        except GateError as exc:
            raise LlmConfigError(str(exc)) from None
        quiet_sdk_logging()
        self.network_check = check
        return model

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
