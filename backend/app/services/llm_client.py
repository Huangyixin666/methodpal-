import os
from typing import Any, Dict, List, Optional

import certifi
import requests
from dotenv import load_dotenv


load_dotenv()


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip().rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "60"))

# Windows / PyCharm / 代理环境里，系统代理有时会导致 SSL EOF。
# 默认不读取系统代理。如果你必须走代理，可以在 .env 里设为 true。
DEEPSEEK_TRUST_ENV = os.getenv("DEEPSEEK_TRUST_ENV", "false").lower() == "true"

# 临时调试 SSL 时可以设为 false，但正式使用建议 true。
DEEPSEEK_VERIFY_SSL = os.getenv("DEEPSEEK_VERIFY_SSL", "true").lower() == "true"


class LLMError(RuntimeError):
    def __init__(self, message: str, code: str = "provider_error", status_code: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def deepseek_enabled() -> bool:
    return bool(DEEPSEEK_API_KEY)


def _chat_url() -> str:
    base = DEEPSEEK_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def call_deepseek_chat(
    messages: List[Dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 1200,
    timeout: Optional[float] = None,
    attempts: int = 3,
    response_format: Optional[Dict[str, str]] = None,
) -> str:
    if not DEEPSEEK_API_KEY:
        raise LLMError("DEEPSEEK_API_KEY is not configured.", code="not_configured")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # Opt-in only: explanations and reports must remain ordinary Markdown.
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "Authorization": "Bearer {}".format(DEEPSEEK_API_KEY),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    session = requests.Session()
    session.trust_env = DEEPSEEK_TRUST_ENV

    verify = certifi.where() if DEEPSEEK_VERIFY_SSL else False

    last_error = None
    try:
        for attempt in range(max(1, min(attempts, 3))):
            try:
                response = session.post(
                    _chat_url(), json=payload, headers=headers,
                    timeout=timeout or DEEPSEEK_TIMEOUT, verify=verify,
                )
                if response.status_code >= 400:
                    # Error bodies may contain credentials or sensitive context.
                    raise LLMError("DeepSeek HTTP error: {}".format(response.status_code),
                                   code="http_error", status_code=response.status_code)
                try:
                    data = response.json()
                    choice = data["choices"][0]
                    content = choice["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError):
                    raise LLMError("DeepSeek returned an invalid response envelope.",
                                   code="invalid_provider_response", status_code=response.status_code) from None
                if response_format and choice.get("finish_reason") == "length":
                    raise LLMError("Structured response was truncated.", code="truncated_output", status_code=response.status_code)
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("DeepSeek returned empty content.", code="empty_output", status_code=response.status_code)
                return content.strip()
            except LLMError:
                raise
            except requests.Timeout:
                last_error = LLMError("DeepSeek request timed out.", code="timeout")
            except requests.RequestException:
                last_error = LLMError("DeepSeek connection failed.", code="connection_error")
        raise last_error or LLMError("DeepSeek request failed.")
    finally:
        session.close()


def generate_text(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1200,
) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    return call_deepseek_chat(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def generate_text_mock(prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
    return "这是 mock LLM 回复。后续可在 app/services/llm_client.py 中接入 DeepSeek API。"
