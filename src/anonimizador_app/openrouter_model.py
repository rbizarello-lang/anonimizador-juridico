"""H9-p, SO TESTE: modelo do LangExtract que fala com o OpenRouter (API compativel com a da OpenAI).

Nao usa o pacote ``openai`` (nao esta no ambiente do app): um ``requests.Session`` sem proxy do ambiente
(``trust_env`` desligado) posta so em ``https://openrouter.ai/api/v1/chat/completions``. A chave fica dentro de
``ApiKey`` e so e revelada no cabecalho de cada pedido; ``repr`` e ``str`` nao a mostram.

Cada pedido leva as preferencias de privacidade do OpenRouter: ``provider.data_collection = "deny"`` (so
provedores que nao guardam nem treinam com o texto). O canal so roda com o documento sintetico
(``llm_gate.synthetic_lock_refusals``).
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from typing import Any

from .credentials import ApiKey
from .llm_gate import OPENROUTER_BASE_URL, GateError, safe_error, scrub_secrets

_SYSTEM = "You are a helpful assistant that responds in JSON format."
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
# Raciocinio (``--raciocinio``): minimo = desligado; baixo = esforco baixo; padrao = o do modelo.
REASONING = {"minimo": {"enabled": False}, "baixo": {"effort": "low"}, "padrao": None}
PRIVACY = {"data_collection": "deny"}


class OpenRouterError(RuntimeError):
    """Falha do pedido ao OpenRouter. A mensagem traz so tipo, codigo HTTP e motivo curto."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def clean_output(text: str) -> str:
    """Tira bloco de raciocinio e cerca de codigo: o LangExtract espera JSON puro (fence desligado)."""

    return _FENCE.sub("", _THINK.sub("", text or "")).strip()


def build_openrouter_model(
    *,
    model_id: str,
    api_key: ApiKey,
    max_output_tokens: int,
    thinking: str,
    meter: Any,
    timeout: float = 180.0,
    base_url: str = OPENROUTER_BASE_URL,
    session: Any = None,
) -> Any:
    if not isinstance(api_key, ApiKey):
        raise GateError("Sem chave do OpenRouter (Gerenciador de Credenciais, servico AnonimizadorJuridico/openrouter).")
    if base_url != OPENROUTER_BASE_URL:
        raise GateError("O canal de teste so fala com https://openrouter.ai/api/v1: endereco recusado.")
    from langextract.core import base_model
    from langextract.core import types as lx_types

    if session is None:
        import requests

        session = requests.Session()
        session.trust_env = False

    class OpenRouterLanguageModel(base_model.BaseLanguageModel):
        def __init__(self) -> None:
            super().__init__()
            self.model_id = model_id
            self._key = api_key
            self._url = f"{base_url}/chat/completions"
            self._session = session
            self._reasoning = REASONING.get(thinking)
            self.set_fence_output(False)

        def __repr__(self) -> str:
            return f"OpenRouterLanguageModel(model_id={model_id!r}, <credenciais ocultas>)"

        __str__ = __repr__

        def body(self, prompt: str) -> dict:
            payload: dict[str, Any] = {
                "model": model_id,
                "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": max_output_tokens,
                "response_format": {"type": "json_object"},
                "provider": dict(PRIVACY),
                "usage": {"include": True},
            }
            if self._reasoning is not None and not getattr(meter, "thinking_rejected", False):
                payload["reasoning"] = dict(self._reasoning)
            return payload

        def _post(self, payload: dict) -> Any:
            headers = {
                "Authorization": "Bearer " + self._key.reveal(),
                "Content-Type": "application/json",
                "X-Title": "Anonimizador Juridico (teste sintetico)",
            }
            return self._session.post(self._url, json=payload, headers=headers, timeout=timeout)

        def _one(self, prompt: str) -> str:
            payload = self.body(prompt)
            delay = 2.0
            for attempt in range(3):
                try:
                    response = self._post(payload)
                except Exception as exc:  # noqa: BLE001 - so o tipo sai daqui
                    if attempt < 2:
                        time.sleep(delay)
                        delay *= 2
                        continue
                    meter.record_failure()
                    raise OpenRouterError(f"Falha de rede no OpenRouter ({safe_error(exc)})") from None
                status = getattr(response, "status_code", 0)
                if status == 400 and "reasoning" in payload and _mentions_reasoning(response):
                    meter.thinking_rejected = True
                    payload.pop("reasoning", None)
                    continue
                if status in _RETRY_STATUS and attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                if status != 200:
                    meter.record_failure()
                    raise OpenRouterError(f"OpenRouter HTTP {status}{_reason(response)}", status_code=status)
                try:
                    data = response.json()
                except ValueError:
                    meter.record_failure()
                    raise OpenRouterError("OpenRouter devolveu resposta que nao e JSON") from None
                if isinstance(data, dict) and data.get("error"):
                    meter.record_failure()
                    code = data["error"].get("code") if isinstance(data["error"], dict) else None
                    raise OpenRouterError(f"OpenRouter erro {code if isinstance(code, int) else '?'}")
                meter.record_openrouter(data)
                choices = data.get("choices") or []
                message = (choices[0] or {}).get("message") or {} if choices else {}
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    reason = (choices[0] or {}).get("finish_reason") if choices else None
                    raise OpenRouterError(f"OpenRouter sem conteudo (finish_reason={reason})")
                return clean_output(content)
            meter.record_failure()
            raise OpenRouterError("OpenRouter: tentativas esgotadas")

        def infer(self, batch_prompts: Sequence[str], **kwargs: Any):
            for prompt in batch_prompts:
                yield [lx_types.ScoredOutput(score=1.0, output=self._one(prompt))]

    return OpenRouterLanguageModel()


def _mentions_reasoning(response: Any) -> bool:
    try:
        text = str(response.text)[:2000].casefold()
    except Exception:  # noqa: BLE001
        return False
    return "reasoning" in text or "thinking" in text


def _reason(response: Any) -> str:
    """Motivo curto do erro do OpenRouter (so a mensagem do proprio OpenRouter, sem texto de peca)."""

    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return ""
    return ": " + scrub_secrets(re.sub(r"\s+", " ", message))[:160]
