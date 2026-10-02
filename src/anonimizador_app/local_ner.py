"""NER local (GLiNER; spaCy PT de reserva). O modelo NUNCA vai a rede sozinho (endurecimento I2).

``HF_HUB_OFFLINE`` e ``TRANSFORMERS_OFFLINE`` ficam em ``1`` por padrao, ja aqui, antes de qualquer
import do gliner/transformers/huggingface_hub (eles leem a variavel ao serem importados), e o modelo
e fixado na revisao que esta no cache local. Baixar o modelo pela primeira vez e um passo explicito e
separado: ``python tools/instalar_modelo_ner.py`` (e a unica coisa do app que usa a rede do Hub).
"""

from __future__ import annotations

import os

# Antes de importar qualquer coisa que traga huggingface_hub/transformers. setdefault: quem exporta
# HF_HUB_OFFLINE=0 de proposito (o instalador do modelo faz isso) continua podendo.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from anonimizador_juridico import EntitySpan, resolve_overlaps

from .catalog import NER_LOCAL_RECOGNIZER
from .gemini_detector import (
    ContextualEngine,
    is_kept_term,
    is_too_generic,
    locate_mentions,
)
from .ner_cache import cache_key, load_spans, save_spans

GLINER_MODEL = "urchade/gliner_multi-v2.1"
# Revisao (commit do repositorio do modelo no Hub) que esta no cache local; sem ela, uma revisao nova
# publicada no Hub trocaria o modelo em silencio e a deteccao mudaria sem DETECTION_VERSION mudar.
# Trocar de revisao e decisao: mude tambem o id usado na chave do cache de NER (``ner_cache``).
GLINER_REVISION = "443d26d654e0324125a96bebd8e796c14ff2efe6"
# O tokenizador do GLiNER (mdeberta) vem de outro repositorio do Hub; offline, le o que esta no cache.
GLINER_TOKENIZER_MODEL = "microsoft/mdeberta-v3-base"
GLINER_TOKENIZER_REVISION = "a0484667b22365f84929a935b5e50a51f71f159d"
GLINER_LABELS = ("pessoa", "empresa privada", "logradouro")
SPACY_MODELS = ("pt_core_news_md", "pt_core_news_lg", "pt_core_news_sm")
DEFAULT_THRESHOLD = 0.75
_CHUNK_CHARS = 1200
_CHUNK_OVERLAP = 100

_LABEL_MAP = {
    "PESSOA": "PERSON",
    "PERSON": "PERSON",
    "PER": "PERSON",
    "EMPRESA PRIVADA": "ORGANIZATION",
    "ORGANIZACAO": "ORGANIZATION",
    "ORGANIZAÇÃO": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
    "ORG": "ORGANIZATION",
    "LOGRADOURO": "LOCATION",
    "LOCAL": "LOCATION",
    "LOCATION": "LOCATION",
    "LOC": "LOCATION",
}

_LOG = logging.getLogger(__name__)
_ACTIVE_BACKEND = ""
PredictFn = Callable[[str], Sequence[dict]]


@dataclass(frozen=True, slots=True)
class NerStatus:
    """Qual motor de NER roda nesta maquina (I-N3). ``fallback_error`` nao vazio = o GLiNER nao pode ser usado
    e o spaCy PT (recall menor) o substituiu."""

    backend: str  # "gliner" ou "spacy_pt"
    model: str
    revision: str
    fallback_error: str = ""

    @property
    def identity(self) -> str:
        """Id do motor na chave do cache de NER: nunca o mesmo para o GLiNER e para o spaCy."""

        return f"{self.backend}:{self.model}@{self.revision}"

    @property
    def fallback(self) -> bool:
        return bool(self.fallback_error)


# Resultado do que ja foi carregado de fato neste processo (vence a sondagem) e da sondagem barata
# (sem carregar o modelo), usada para calcular a chave do cache antes de decidir se o modelo precisa subir.
_ACTIVE_STATUS: NerStatus | None = None
_PROBED_STATUS: NerStatus | None = None
# O NER rodou (ou foi consultado no cache) desde ``reset_ner_status``: a AUDITORIA so fala dele se foi usado.
_NER_USED = False


def reset_ner_status() -> None:
    """Chamado no inicio de cada lote: a AUDITORIA daquele lote so descreve o NER que ele usou."""

    global _NER_USED
    _NER_USED = False


def ner_status() -> NerStatus | None:
    """O motor de NER do lote corrente, ou ``None`` se o lote nao usou o NER local."""

    return resolve_backend() if _NER_USED else None


def resolve_backend() -> NerStatus:
    """O motor que vale nesta maquina: o que foi carregado, ou o que a sondagem barata indica (sem carregar)."""

    global _PROBED_STATUS
    if _ACTIVE_STATUS is not None:
        return _ACTIVE_STATUS
    if _PROBED_STATUS is None:
        reason = _gliner_unavailable_reason()
        _PROBED_STATUS = (
            NerStatus("gliner", GLINER_MODEL, GLINER_REVISION) if reason is None else _spacy_status(reason)
        )
    return _PROBED_STATUS


def _gliner_unavailable_reason() -> str | None:
    """``None`` se o GLiNER (e o tokenizador) estao disponiveis; senao, o motivo (sem rede: so olha o cache)."""

    import importlib.util

    try:
        if importlib.util.find_spec("gliner") is None:
            return "pacote gliner nao instalado"
    except Exception as exc:  # noqa: BLE001
        return f"pacote gliner ilegivel ({type(exc).__name__})"
    if not hub_offline():
        return None
    try:
        _force_hub_offline(True)
        from huggingface_hub import snapshot_download

        snapshot_download(GLINER_MODEL, revision=GLINER_REVISION, local_files_only=True)
        snapshot_download(GLINER_TOKENIZER_MODEL, revision=GLINER_TOKENIZER_REVISION, local_files_only=True)
    except Exception as exc:  # noqa: BLE001 - falta de arquivo local, nunca rede
        return f"modelo {GLINER_MODEL} na revisao fixada ausente do cache local ({type(exc).__name__})"
    return None


def _spacy_status(reason: str) -> NerStatus:
    from importlib import metadata

    for name in SPACY_MODELS:
        try:
            return NerStatus("spacy_pt", name, metadata.version(name), f"GLiNER indisponivel: {reason}")
        except metadata.PackageNotFoundError:
            continue
    return NerStatus("spacy_pt", "nenhum-modelo", "n/d", f"GLiNER indisponivel: {reason}")


def ner_status_lines() -> list[str]:
    """Linhas da AUDITORIA sobre o motor de NER do lote (vazio se o lote nao usou o NER local)."""

    status = ner_status()
    if status is None:
        return []
    if status.backend == "gliner":
        return [f"Motor de NER local: GLiNER {status.model} @ {status.revision[:12]}"]
    return [
        f"Motor de NER local: spaCy PT ({status.model} {status.revision}), de RESERVA: recall menor que o do GLiNER",
        f"fallback_error: {status.fallback_error}",
    ]


def ner_fallback_warning() -> str:
    """Aviso para a tela e a CLI quando o spaCy substituiu o GLiNER neste lote; vazio caso contrario."""

    status = ner_status()
    if status is None or not status.fallback:
        return ""
    return (
        f"O GLiNER nao esta disponivel nesta maquina ({status.fallback_error.removeprefix('GLiNER indisponivel: ')}); "
        "os nomes foram detectados pelo spaCy PT, de recall menor. Confira o texto com mais cuidado e, para usar o "
        "GLiNER, rode `python tools/instalar_modelo_ner.py` (passo explicito, o unico que usa a rede) e refaca o lote."
    )


class LocalNerEngine:
    """GLiNER nesta maquina; spaCy PT so entra se o GLiNER nao estiver instalado."""

    provider = "local"

    def __init__(
        self,
        *,
        extra_keep: Iterable[str] = (),
        threshold: float = DEFAULT_THRESHOLD,
        predict: PredictFn | None = None,
        shadow_case: bool = False,
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ) -> None:
        self.extra_keep = tuple(extra_keep)
        self.threshold = threshold
        self.shadow_case = shadow_case
        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._predict = predict
        self._pipeline: PredictFn | None = None

    def detect(self, text: str) -> list[EntitySpan]:
        key = self._cache_key(text) if self._cache_enabled() else None
        if key is None:
            self._note_use()
        if key is not None and self.cache_dir is not None:
            loaded = load_spans(self.cache_dir, key)
            if loaded is not None:
                return loaded
        spans = self._detect_fresh(text)
        if key is not None and self.cache_dir is not None:
            # O modelo so sobe aqui, e pode ser outro que o da sondagem (o GLiNER falhou ao carregar): a chave
            # e refeita com o motor que de fato rodou, para o spaCy nunca ficar gravado sob a chave do GLiNER.
            key = self._cache_key(text)
            save_spans(
                self.cache_dir,
                key,
                spans,
                text=text,
                model=self._cache_model_id(),
                threshold=self.threshold,
                labels=GLINER_LABELS,
                shadow_case=self.shadow_case,
                chunk_chars=_CHUNK_CHARS,
                chunk_overlap=_CHUNK_OVERLAP,
            )
        return spans

    def _detect_fresh(self, text: str) -> list[EntitySpan]:
        mentions = _mentions_from_predictions(
            self._predict_all(text),
            threshold=self.threshold,
        )
        return locate_mentions(
            text,
            mentions,
            extra_keep=self.extra_keep,
            recognizer=NER_LOCAL_RECOGNIZER,
        )

    def _cache_enabled(self) -> bool:
        return self.use_cache and self.cache_dir is not None

    def _cache_model_id(self) -> str:
        if self._predict is not None:
            return "predict-custom"
        self._note_use()
        return resolve_backend().identity

    def _note_use(self) -> None:
        global _NER_USED
        if self._predict is None:
            _NER_USED = True

    def _cache_key(self, text: str) -> str:
        return cache_key(
            text,
            model=self._cache_model_id(),
            threshold=self.threshold,
            labels=GLINER_LABELS,
            shadow_case=self.shadow_case,
            chunk_chars=_CHUNK_CHARS,
            chunk_overlap=_CHUNK_OVERLAP,
            extra_keep=self.extra_keep,
        )

    def scan_residual(self, text: str) -> list[str]:
        if self._cache_enabled():
            # A passada residual no texto ja tokenizado reexecutaria o GLiNER
            # (~mesmos 8 min). O cache cobre detect no original; o aviso residual
            # fica vazio quando o cache esta ligado.
            return []
        return self._scan_residual_fresh(text)

    def _scan_residual_fresh(self, text: str) -> list[str]:
        leftovers: list[str] = []
        seen: set[str] = set()
        for entity_type, value, score in self._predict_all(text):
            if score < self.threshold:
                continue
            if value.startswith("[") or is_kept_term(value, self.extra_keep):
                continue
            if is_too_generic(entity_type, value):
                continue
            folded = value.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            leftovers.append(value)
        return leftovers

    def _predict_all(self, text: str) -> list[tuple[str, str, float]]:
        found: list[tuple[str, str, float]] = []
        predict = self._ensure_predict()
        source = shadow_case_copy(text) if self.shadow_case else text
        for chunk in _char_chunks(source, _CHUNK_CHARS, _CHUNK_OVERLAP):
            for item in predict(chunk):
                mapped = _map_label(item)
                if mapped is None:
                    continue
                value = str(item.get("word") or item.get("text") or "").strip()
                score = float(item.get("score") or 0.0)
                if value:
                    found.append((mapped, value, score))
        return found

    def _ensure_predict(self) -> PredictFn:
        if self._predict is not None:
            return self._predict
        if self._pipeline is None:
            self._pipeline = load_local_predict()
        return self._pipeline


def hub_offline() -> bool:
    """O Hub esta desligado nesta execucao (padrao). So ``HF_HUB_OFFLINE=0`` explicito o liga."""

    return os.environ.get("HF_HUB_OFFLINE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _force_hub_offline(offline: bool) -> None:
    """Se o huggingface_hub ja foi importado antes deste modulo (a variavel so e lida no import), ajusta a
    constante dele: o padrao offline vale mesmo nesse caso."""

    if not offline:
        return
    import sys

    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None and hasattr(constants, "HF_HUB_OFFLINE"):
        constants.HF_HUB_OFFLINE = True


def load_local_predict() -> PredictFn:
    global _ACTIVE_BACKEND, _ACTIVE_STATUS, _NER_USED
    _NER_USED = True
    try:
        predict = _load_gliner()
        _ACTIVE_BACKEND = "gliner"
        _ACTIVE_STATUS = NerStatus("gliner", GLINER_MODEL, GLINER_REVISION)
        return predict
    except Exception as exc:
        _LOG.warning(
            "GLiNER indisponivel (%s); usando spaCy PT, de recall menor. Se o modelo ainda nao foi baixado, rode "
            "`python tools/instalar_modelo_ner.py` (passo explicito, o unico que usa a rede).",
            exc,
        )
        predict = _load_spacy_pt()
        _ACTIVE_BACKEND = "spacy_pt"
        _ACTIVE_STATUS = _spacy_status(f"{type(exc).__name__}: {str(exc)[:100]}")
        return predict


def _load_gliner() -> PredictFn:
    from .torch_guard import apply_torch_windows_workaround

    apply_torch_windows_workaround()
    offline = hub_offline()
    _force_hub_offline(offline)
    from gliner import GLiNER

    model = GLiNER.from_pretrained(GLINER_MODEL, revision=GLINER_REVISION, local_files_only=offline)
    _LOG.info("Motor local: GLiNER (%s)", GLINER_MODEL)

    def predict(text: str) -> Sequence[dict]:
        hits = model.predict_entities(text, list(GLINER_LABELS), threshold=0.0)
        return [
            {
                "entity_group": item.get("label", ""),
                "word": item.get("text", ""),
                "score": float(item.get("score") or 0.0),
            }
            for item in hits
        ]

    return predict


def _load_spacy_pt() -> PredictFn:
    import spacy

    last_error: Exception | None = None
    nlp = None
    for name in SPACY_MODELS:
        try:
            nlp = spacy.load(name)
            break
        except Exception as exc:  # noqa: BLE001 - tentar o proximo modelo
            last_error = exc
    if nlp is None:
        raise ImportError(
            "GLiNER nao esta instalado e nenhum modelo spaCy em portugues foi encontrado "
            "(pt_core_news_md/lg/sm)."
        ) from last_error
    _LOG.info("Motor local: spaCy %s", nlp.meta.get("name", "pt"))

    def predict(text: str) -> Sequence[dict]:
        doc = nlp(text)
        return [
            {
                "entity_group": ent.label_,
                "word": ent.text,
                "score": 0.80,
            }
            for ent in doc.ents
        ]

    return predict


def _map_label(item: dict) -> str | None:
    raw = str(item.get("entity_group") or item.get("entity") or item.get("label") or "")
    compact = raw.split("-")[-1].strip().upper()
    return _LABEL_MAP.get(compact)


def _mentions_from_predictions(
    predictions: Sequence[tuple[str, str, float]],
    *,
    threshold: float,
) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for entity_type, value, score in predictions:
        if score < threshold:
            continue
        mentions.append((entity_type, value))
    return mentions


def shadow_case_copy(text: str) -> str:
    """Copia com trechos TODO-MAIUSCULOS em Title Case, comprimento intacto.

    O GLiNER roda nesta copia; locate_mentions aplica o span no original
    (busca indiferente a caixa). Um fator: mesmo modelo, mesmo limiar.
    """

    chars = list(text)
    index = 0
    length = len(chars)
    while index < length:
        if not chars[index].isalpha():
            index += 1
            continue
        cursor = index
        letters = 0
        last_letter = index
        while cursor < length:
            char = chars[cursor]
            if char.isalpha():
                if not char.isupper():
                    break
                letters += 1
                last_letter = cursor
                cursor += 1
                continue
            if char in " \t.-'/":
                cursor += 1
                continue
            break
        end = last_letter + 1
        if letters >= 4:
            new_word = True
            for pos in range(index, end):
                char = chars[pos]
                if char.isalpha():
                    chars[pos] = char.upper() if new_word else char.lower()
                    new_word = False
                elif char.isspace() or char in "-.'/":
                    new_word = True
        index = end if end > index else index + 1
    return "".join(chars)


def _char_chunks(text: str, size: int, overlap: int) -> Iterable[str]:
    if len(text) <= size:
        yield text
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        yield text[start:end]
        if end == len(text):
            break
        start = max(end - overlap, start + 1)


class CombinedEngine:
    provider = "combined"

    def __init__(self, engines: Sequence[ContextualEngine]) -> None:
        self.engines = tuple(engines)

    def detect(self, text: str) -> list[EntitySpan]:
        spans: list[EntitySpan] = []
        target_engine = None
        for engine in self.engines:
            spans.extend(engine.detect(text))
            if getattr(engine, "provider", "") == "targets":
                target_engine = engine
        if target_engine is not None:
            from .target_list import attach_list_canonicals

            spans = attach_list_canonicals(text, spans, target_engine)
        return resolve_overlaps(spans)

    def scan_residual(self, text: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for engine in self.engines:
            for item in engine.scan_residual(text):
                key = item.casefold()
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
        return found
