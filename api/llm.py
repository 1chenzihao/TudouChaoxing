# -*- coding: utf-8 -*-
"""大模型调用封装：配置解析 / 限速 / 重试 / JSON 解析 / 用量统计。

本模块只提供能力，不主动联网：只有未注入 transport 且真正调用 chat() 时才会
延迟导入 openai 并创建客户端。单测与自测注入 transport 即可完全离线运行。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from api.logger import logger

__all__ = [
    "LLMConfig",
    "LLMStats",
    "LLMClient",
    "LLMError",
    "LLMEmptyContentError",
    "strip_markdown_json",
]


class LLMError(Exception):
    """大模型调用最终失败：重试耗尽，或遇到不可重试的错误。"""


class LLMEmptyContentError(LLMError):
    """接口返回 200 但内容为空。

    中转站/流式接口偶发空响应很常见，是最该重试的情形，所以单独一个类型。
    """


DEFAULT_ENDPOINT = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 90.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_MIN_INTERVAL = 1.0

# Markdown 代码块围栏（用 chr(96) 拼，避免源码里出现连续反引号）
_FENCE = chr(96) * 3
_MD_JSON_RE = re.compile(r"^\s*" + _FENCE + r"(?:json)?\s*(.*?)\s*" + _FENCE + r"\s*$", re.DOTALL)

# 值得重试的 HTTP 状态码（网络抖动 / 限流 / 服务端临时故障）
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
# 明确不可重试的状态码（参数错 / 鉴权错 / 不存在 / 校验失败）
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 405, 413, 415, 422})

# 无状态码时按异常类型判断是否可重试
_RETRYABLE_EXC_NAMES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "APIConnectionTimeoutError",
    "InternalServerError",
    "ServiceUnavailableError",
    "APIStatusError",
    "TimeoutException",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "TransportError",
    "TimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
})


def _text(value: Any) -> str:
    """把任意配置值转成去掉首尾空白的字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_float(value: Any, default: float) -> float:
    text = _text(value)
    if not text:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        logger.warning(f"配置项不是合法数字 -> {text!r}，改用默认值 {default}")
        return default


def _as_int(value: Any, default: int) -> int:
    text = _text(value)
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        logger.warning(f"配置项不是合法整数 -> {text!r}，改用默认值 {default}")
        return default


def _mapping_value(conf: Any, *names: str) -> str:
    """从 configparser 段 / dict 等映射里按顺序取第一个非空值。"""
    if not conf:
        return ""
    for name in names:
        try:
            value = conf.get(name)
        except Exception:
            value = None
        text = _text(value)
        if text:
            return text
    return ""


def _env_value(*names: str) -> str:
    """按顺序取第一个非空环境变量。"""
    for name in names:
        text = _text(os.environ.get(name))
        if text:
            return text
    return ""


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_retryable(exc: BaseException) -> bool:
    """网络异常 / 5xx / 429 可重试；参数与鉴权类错误直接失败。"""
    status = _status_code(exc)
    if status is not None:
        if status in _NON_RETRYABLE_STATUS:
            return False
        if status in _RETRYABLE_STATUS or status >= 500:
            return True
        return False
    if type(exc).__name__ in _RETRYABLE_EXC_NAMES:
        return True
    if isinstance(exc, LLMEmptyContentError):
        return True
    if isinstance(exc, LLMError):
        return False
    # 兜底不重试：自家代码的 TypeError/KeyError 重试也不会好，白等退避时间
    return False


def _is_response_format_unsupported(exc: BaseException) -> bool:
    """接口不支持 response_format={"type":"json_object"} 时返回 True。

    必须先看状态码：鉴权错误（401/403）的报文里也可能出现 json_object 字样，
    那种情况不是"能力不支持"，降级重发只会把日志带偏。
    """
    status = _status_code(exc)
    if status is not None and status not in (400, 422):
        return False
    message = str(exc).lower()
    if "response_format" in message or "response format" in message:
        return True
    if "json_object" in message:
        return True
    if status in (400, 422):
        for hint in ("unsupported", "unrecognized", "unknown field", "invalid_request", "not supported"):
            if hint in message:
                return True
    return False


def _looks_like_signature_error(exc: TypeError) -> bool:
    message = str(exc).lower()
    hints = (
        "unexpected keyword",
        "positional argument",
        "takes 1 positional",
        "takes 2 positional",
        "got an unexpected",
    )
    return any(hint in message for hint in hints)


def _iter_balanced_objects(text: str):
    """按大括号配平扫描文本中所有的 {...} 片段（忽略字符串里的括号与转义）。"""
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "{":
            index += 1
            continue
        depth = 0
        in_string = False
        escaped = False
        end = -1
        for cursor in range(index, length):
            char = text[cursor]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = cursor
                    break
        if end < 0:
            return
        yield text[index:end + 1]
        index = end + 1


def _extract_first_json_object(text: str) -> dict | None:
    """从混杂文本里找出第一个能解析成 dict 的 JSON 对象。"""
    for fragment in _iter_balanced_objects(text):
        try:
            parsed = json.loads(fragment)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _repair_truncated_json(text: str) -> dict | None:
    """给被 max_tokens 截断的 JSON 补结尾括号。

    只补括号、不猜内容：文本必须以 { 开头，才逐级尝试常见结尾。
    解析不了就返回 None，交给外层判失败。
    """
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    candidate = stripped.rstrip().rstrip(",")
    for tail in ('"}', '"]}', "}", "]}", '"]}}'):
        try:
            data = json.loads(candidate + tail)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            logger.warning(f"大模型返回的 JSON 被截断, 补全结尾 {tail!r} 后解析成功")
            return data
    return None


def strip_markdown_json(text: str) -> str:
    """剥掉 Markdown 代码块包裹，返回里面的正文。"""
    raw = _text(text)
    match = _MD_JSON_RE.match(raw)
    if match:
        return match.group(1).strip()
    return raw


@dataclass
class LLMConfig:
    """大模型调用配置。"""

    endpoint: str = DEFAULT_ENDPOINT
    api_key: str = ""
    model: str = DEFAULT_MODEL
    http_proxy: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    min_interval: float = DEFAULT_MIN_INTERVAL
    temperature: float = 0.3
    max_tokens: int = 1024
    seed: int | None = None
    extra_headers: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, conf) -> "LLMConfig":
        """从 configparser 段（或 os.environ 风格映射）构造配置。

        键名约定：endpoint / key / model / http_proxy / timeout / max_retries /
        min_interval_seconds / temperature / max_tokens / seed / extra_headers。
        优先级：conf 中的非空值 > 环境变量 > 类默认值。
        """
        config = cls()

        endpoint = _mapping_value(conf, "endpoint", "base_url", "api_base") or _env_value(
            "CHX_LLM_ENDPOINT", "OPENAI_BASE_URL"
        )
        if endpoint:
            config.endpoint = endpoint

        api_key = _mapping_value(conf, "key", "api_key", "token") or _env_value("CHX_LLM_KEY", "OPENAI_API_KEY")
        if api_key:
            config.api_key = api_key

        model = _mapping_value(conf, "model") or _env_value("CHX_LLM_MODEL", "OPENAI_MODEL")
        if model:
            config.model = model

        config.http_proxy = _mapping_value(conf, "http_proxy", "proxy") or _env_value("CHX_LLM_PROXY") or ""

        config.timeout = max(
            1.0,
            _as_float(_mapping_value(conf, "timeout") or _env_value("CHX_LLM_TIMEOUT"), config.timeout),
        )
        config.max_retries = max(
            1,
            _as_int(
                _mapping_value(conf, "max_retries") or _env_value("CHX_LLM_MAX_RETRIES"),
                config.max_retries,
            ),
        )
        config.min_interval = max(
            0.0,
            _as_float(
                _mapping_value(conf, "min_interval_seconds", "min_interval") or _env_value("CHX_LLM_MIN_INTERVAL"),
                config.min_interval,
            ),
        )
        config.temperature = min(
            2.0,
            max(
                0.0,
                _as_float(
                    _mapping_value(conf, "temperature") or _env_value("CHX_LLM_TEMPERATURE"),
                    config.temperature,
                ),
            ),
        )
        config.max_tokens = max(
            1,
            _as_int(
                _mapping_value(conf, "max_tokens") or _env_value("CHX_LLM_MAX_TOKENS"),
                config.max_tokens,
            ),
        )

        seed_text = _mapping_value(conf, "seed") or _env_value("CHX_LLM_SEED")
        if seed_text:
            config.seed = _as_int(seed_text, 0)

        headers_text = _mapping_value(conf, "extra_headers", "headers")
        if headers_text:
            try:
                headers = json.loads(headers_text)
            except (json.JSONDecodeError, ValueError):
                headers = None
                logger.warning("extra_headers 不是合法 JSON，已忽略")
            if isinstance(headers, dict):
                config.extra_headers = {str(key): str(value) for key, value in headers.items()}
        return config


@dataclass
class LLMStats:
    """调用统计。"""

    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retries": self.retries,
        }


class LLMClient:
    """大模型客户端：限速 + 重试 + JSON 解析 + 用量统计。

    transport: 可选依赖注入，签名 callable(messages, **kw) -> str。
    注入后完全不走网络，也不导入 openai，便于离线自测。
    """

    def __init__(self, config: LLMConfig, *, transport: Callable[..., str] | None = None) -> None:
        self.config = config
        self._transport = transport
        self._stats = LLMStats()
        self._lock = threading.RLock()
        self._client = None
        self._last_request_at = 0.0

    # ------------------------------------------------------------------ 对外接口

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = True,
        seed: int | None = None,
    ) -> str:
        """发起一次对话补全，返回纯文本内容。

        max_retries 作为「总尝试次数上限」使用：1 表示只尝试一次、不重试。
        """
        if not messages:
            raise LLMError("messages 为空，无法调用大模型")

        temperature_value = self.config.temperature if temperature is None else float(temperature)
        max_tokens_value = self.config.max_tokens if max_tokens is None else int(max_tokens)
        seed_value = self.config.seed if seed is None else seed
        attempts = max(1, int(self.config.max_retries))

        last_error: BaseException | None = None
        for attempt in range(attempts):
            if attempt > 0:
                delay = min(20.0, 1.5 * (2 ** (attempt - 1)))
                with self._lock:
                    self._stats.retries += 1
                logger.info(f"大模型调用失败，{delay:.1f} 秒后重试（第 {attempt + 1}/{attempts} 次尝试）")
                time.sleep(delay)
            self._throttle()
            with self._lock:
                self._stats.calls += 1
            try:
                return self._invoke(
                    messages,
                    temperature=temperature_value,
                    max_tokens=max_tokens_value,
                    json_mode=json_mode,
                    seed=seed_value,
                )
            except Exception as exc:  # noqa: BLE001 - 统一转成 LLMError
                with self._lock:
                    self._stats.failures += 1
                last_error = exc
                if not _is_retryable(exc):
                    logger.error(f"大模型调用失败（不可重试）：{exc}")
                    raise LLMError(f"大模型调用失败：{exc}") from exc
                logger.warning(f"大模型调用异常（第 {attempt + 1}/{attempts} 次尝试）：{exc}")

        raise LLMError(f"大模型调用失败，已尝试 {attempts} 次：{last_error}") from last_error

    def chat_json(self, messages: list[dict], *, validator=None, **kw) -> tuple[dict | None, str]:
        """要求模型返回 JSON，尽量把结果捞出来。

        返回 (解析后的 dict 或 None, 模型原始文本)。
        """
        raw = ""
        try:
            raw = self.chat(messages, **kw)
        except LLMError as exc:
            logger.error(f"大模型调用失败，无法得到 JSON：{exc}")
            return None, raw
        except Exception as exc:  # noqa: BLE001 - 任何异常都不该把调用方带崩
            logger.error(f"大模型调用出现未预期异常：{exc}")
            return None, raw

        cleaned = strip_markdown_json(raw)
        loaded = None
        try:
            loaded = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            data = loaded
        else:
            data = _extract_first_json_object(cleaned)

        if data is None:
            data = _repair_truncated_json(cleaned)
        if data is None:
            logger.warning(f"大模型返回内容无法解析成 JSON：{raw[:200]}")
            return None, raw

        if validator is not None:
            try:
                passed = bool(validator(data))
            except Exception as exc:  # noqa: BLE001 - 校验器自身出错按校验失败处理
                logger.warning(f"JSON 校验器执行出错：{exc}")
                passed = False
            if not passed:
                logger.warning(f"大模型返回 JSON 未通过校验：{raw[:200]}")
                return None, raw
        return data, raw

    @property
    def stats(self) -> LLMStats:
        return self._stats

    def close(self) -> None:
        """释放底层 httpx 连接池（若创建过）。"""
        with self._lock:
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不影响主流程
            logger.debug(f"关闭大模型客户端时出错：{exc}")

    # ------------------------------------------------------------------ 内部实现

    def _throttle(self) -> None:
        """两次请求之间至少间隔 min_interval 秒（进程内串行，跨线程安全）。"""
        interval = float(self.config.min_interval or 0.0)
        with self._lock:
            now = time.monotonic()
            if interval > 0 and self._last_request_at > 0:
                wait = self._last_request_at + interval - now
                if wait > 0:
                    logger.debug(f"大模型请求间隔不足，等待 {wait:.2f} 秒")
                    time.sleep(wait)
                    now = time.monotonic()
            self._last_request_at = now

    def _invoke(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        seed: int | None,
    ) -> str:
        if self._transport is not None:
            return self._invoke_transport(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                seed=seed,
            )
        return self._invoke_openai(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            seed=seed,
        )

    def _invoke_transport(self, messages, *, temperature, max_tokens, json_mode, seed) -> str:
        kwargs: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens}
        if seed is not None:
            kwargs["seed"] = seed
        try:
            output = self._transport(messages, json_mode=json_mode, **kwargs)
        except TypeError as exc:
            if not _looks_like_signature_error(exc):
                raise
            logger.debug("注入的 transport 不接受额外关键字参数，改为只传 messages")
            output = self._transport(messages)
        except Exception as exc:  # noqa: BLE001 - 接口不支持 response_format 时降级一次
            if json_mode and _is_response_format_unsupported(exc):
                logger.warning("接口不支持 response_format，降级为普通文本模式重试一次")
                output = self._transport(messages, **kwargs)
            else:
                raise
        text = _text(output)
        if not text:
            raise LLMEmptyContentError("transport 返回了空内容")
        return text

    def _invoke_openai(self, messages, *, temperature, max_tokens, json_mode, seed) -> str:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            kwargs["seed"] = seed
        if self.config.extra_headers:
            kwargs["extra_headers"] = dict(self.config.extra_headers)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 只对 response_format 降级，其它异常上抛走重试
            if json_mode and _is_response_format_unsupported(exc):
                logger.warning("接口不支持 response_format，降级为普通文本模式重试一次")
                kwargs.pop("response_format", None)
                response = client.chat.completions.create(**kwargs)
            else:
                raise

        self._account_usage(response)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMEmptyContentError("接口返回内容为空（choices 为空）")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        text = _text(content)
        if not text:
            raise LLMEmptyContentError("接口返回内容为空（content 为空）")
        return text

    def _account_usage(self, response) -> None:
        """尽力从 response.usage 记 token，取不到就跳过。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        with self._lock:
            if isinstance(prompt_tokens, int):
                self._stats.prompt_tokens += prompt_tokens
            if isinstance(completion_tokens, int):
                self._stats.completion_tokens += completion_tokens

    def _get_client(self):
        """延迟创建 openai 客户端，避免 transport 模式下强制依赖 openai。"""
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMError("缺少 openai 依赖，无法发起真实大模型请求") from exc

            kwargs: dict[str, Any] = {
                "base_url": self.config.endpoint or None,
                "api_key": self.config.api_key or "EMPTY",
                "timeout": self.config.timeout,
            }
            if self.config.http_proxy:
                try:
                    import httpx
                except ImportError as exc:
                    raise LLMError("配置了 http_proxy，但缺少 httpx 依赖") from exc
                kwargs["http_client"] = httpx.Client(
                    proxy=self.config.http_proxy,
                    timeout=self.config.timeout,
                )
            self._client = OpenAI(**kwargs)
            return self._client
