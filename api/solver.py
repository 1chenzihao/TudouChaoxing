# -*- coding: utf-8 -*-
"""题目求解器：题型归一化 / 选项解析 / 多采样投票 / 升级重试 / 随机兜底。

职责：
- 把 decode_questions_info 出来的题目 dict 归一化成 Question / Option；
- 按题型构造提示词，要求模型只输出 JSON；
- 单选 / 多选 / 判断走逐选项自一致性投票，填空 / 简答走相似度聚类；
- 投票不够自信时升级重试（加票数、降到 0 温、追加核对提示），最终兜底随机。

本模块不联网、不写文件，模型调用全部走 api.llm.LLMClient（可注入 transport 离线自测）。
"""

from __future__ import annotations

import difflib
import hashlib
import math
import random
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from api.llm import LLMClient, LLMError
from api.logger import logger

__all__ = [
    "QUESTION_TYPES",
    "Option",
    "Question",
    "SolveResult",
    "QuestionSolver",
    "normalize_type",
    "parse_options",
    "cache_key_for",
]


QUESTION_TYPES = ("single", "multiple", "completion", "judgement", "shortanswer", "unknown")

TYPE_LABELS = {
    "single": "单选题",
    "multiple": "多选题",
    "completion": "填空题",
    "judgement": "判断题",
    "shortanswer": "简答题",
    "unknown": "未知题型",
}

_LETTER_SEQUENCE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_TRUE_WORDS = ("正确", "对", "√", "✓", "是", "真", "true", "t", "yes", "y", "right", "correct")
_FALSE_WORDS = ("错误", "错", "×", "✗", "否", "假", "不对", "不正确", "false", "f", "no", "n", "wrong", "incorrect")

# 选项前缀：A. / A、/ A．/ A：/ A xxx / 全角字母 / 只有字母
_PREFIX_PATTERNS = (
    re.compile(r"^\s*[\(（\[【]?\s*([A-Za-zＡ-Ｚａ-ｚ])\s*[\)）\]】]?\s*[.．、,，:：]\s*(.*)$", re.S),
    re.compile(r"^\s*[\(（\[【]?\s*([A-Za-zＡ-Ｚａ-ｚ])\s*[\)）\]】]\s*(.*)$", re.S),
    re.compile(r"^\s*([A-Za-zＡ-Ｚａ-ｚ])\s+(.*)$", re.S),
    re.compile(r"^\s*([A-Za-zＡ-Ｚａ-ｚ])\s*$", re.S),
)

_LEADING_LETTER_RE = re.compile(r"^[\(（\[【]?\s*([A-Za-z])\s*[\)）\]】]?\s*(?:[.．、,，:：]\s*|\s+|$)")
_BARE_LETTERS_RE = re.compile(r"^[A-Za-z]{1,8}$")
_MULTIPLE_HINT_RE = re.compile(r"多选|多项选择|不定项|下列.{0,12}?正确的是|以下.{0,12}?正确的是|哪些")

_SIMILARITY_THRESHOLD = 0.75
_ESCALATION_HINT = "再仔细核对题目与选项，给出最可能的答案，仍然只输出 JSON。"

_TYPE_TEMPERATURE_OFFSET = {
    "single": 0.0,
    "multiple": 0.15,
    "judgement": -0.3,
    "completion": 0.0,
    "shortanswer": 0.1,
    "unknown": 0.1,
}

_SYSTEM_BASE = (
    "你是一位严谨的答题专家。只输出一个 JSON 对象，不要输出 Markdown 代码块，也不要输出任何额外说明。"
    '输出结构：{"answer": [...], "reason": "一句话理由"}。'
)

_SYSTEM_BY_TYPE = {
    "single": '\n本题是单选题：answer 数组只能有 1 个元素，内容是正确选项的字母（大写，例如 "B"），不要输出选项正文。',
    "multiple": "\n本题是多选题：answer 数组要列出全部正确选项的字母（大写，至少 2 个），按字母顺序排列，不要输出选项正文。",
    "judgement": '\n本题是判断题：answer 数组只能有 1 个元素，内容是 "正确" 或 "错误"，不要输出字母，不要输出解释。',
    "completion": "\n本题是填空题：answer 数组按空格出现的先后顺序，每个空一个元素，内容是要填入该空的原文；不要加序号，不要解释。只有一个空时数组只有 1 个元素。",
    "shortanswer": "\n本题是简答题：answer 数组给出完整答案，可以按段落拆成多个元素，不要加序号，不要解释。",
    "unknown": "\n本题题型未知：请根据题干判断题型，并按最合适的形式给出 answer 数组。",
}

_TYPE_ALIASES = {
    "single": "single",
    "单选题": "single",
    "单选": "single",
    "单项选择": "single",
    "radio": "single",
    "multiple": "multiple",
    "多选题": "multiple",
    "多选": "multiple",
    "多项选择": "multiple",
    "checkbox": "multiple",
    "judgement": "judgement",
    "判断题": "judgement",
    "判断": "judgement",
    "judge": "judgement",
    "truefalse": "judgement",
    "completion": "completion",
    "填空题": "completion",
    "填空": "completion",
    "fill": "completion",
    "blank": "completion",
    "shortanswer": "shortanswer",
    "简答题": "shortanswer",
    "简答": "shortanswer",
    "essay": "shortanswer",
    "unknown": "unknown",
}

_ANSWER_KEYS = ("answer", "answers", "ans", "result", "content", "答案", "选项")


# ---------------------------------------------------------------- 文本与选项工具


def _norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\s+", " ", text).strip()


def _bare_key(value: Any) -> str:
    text = _norm_text(value)
    return text.strip(' \t。.，,；;:：!！?？"\'“”‘’()（）[]【】').lower()


def _split_option(line: str) -> tuple[str | None, str]:
    """把一行选项切成 (字母, 正文)，切不出来返回 (None, 整行)。"""
    for pattern in _PREFIX_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        key = unicodedata.normalize("NFKC", match.group(1)).upper()
        if len(match.groups()) == 1:
            return key, ""
        return key, (match.group(2) or "").strip()
    return None, line.strip()


def parse_options(raw_options: Any) -> list[Option]:
    """解析选项文本。

    支持 A. xxx / A、xxx / A xxx / A．xxx / 全角字母前缀 / 无字母前缀（按行序补 A,B,C…）。
    """
    if raw_options is None:
        lines: list[str] = []
    elif isinstance(raw_options, (list, tuple)):
        lines = [str(item) for item in raw_options]
    else:
        lines = str(raw_options).replace("\r\n", "\n").replace("\r", "\n").split("\n")

    parsed: list[tuple[str | None, str, str]] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        key, text = _split_option(raw)
        # 注: 无前缀的行按行序补字母（parse_options 的公开契约）。decode 出来的 options
        # 是逐个 aria-label 用 \n 拼的，本身不含换行，所以不存在"选项被换行劈开"的形态。
        parsed.append((key, text, raw))

    used = {key for key, _, _ in parsed if key}
    options: list[Option] = []
    cursor = 0
    for key, text, raw in parsed:
        if not key:
            while cursor < len(_LETTER_SEQUENCE) and _LETTER_SEQUENCE[cursor] in used:
                cursor += 1
            if cursor >= len(_LETTER_SEQUENCE):
                logger.warning("选项数量超过 26 个，多余的行已丢弃")
                continue
            key = _LETTER_SEQUENCE[cursor]
            used.add(key)
            cursor += 1
        options.append(Option(key=key, text=text, raw=raw))
    return options


_NEGATION_PREFIXES = ("不", "非", "未", "没")
_TRUE_CORE = ("正确", "对", "√", "✓", "是", "真", "true", "t", "yes", "y", "right", "correct")
_FALSE_CORE = ("错误", "错", "×", "✗", "否", "假", "wrong", "incorrect", "false", "f", "no", "n")


def _judgement_value(value: Any) -> bool | None:
    """把「正确 / 对 / √ / 不是 / 没错」这类文本判成 True / False / None。

    先剥掉否定前缀再查词表，避免「不是」「没错」「未必正确」被子串匹配反转成相反结论。
    """
    text = _bare_key(value)
    if not text:
        return None
    negated = text.startswith(_NEGATION_PREFIXES)
    core = text.lstrip("".join(_NEGATION_PREFIXES)) if negated else text
    if core in _TRUE_CORE:
        return not negated
    if core in _FALSE_CORE:
        return negated
    if text in _TRUE_CORE:
        return True
    if text in _FALSE_CORE:
        return False
    # 子串兜底：真词与假词同时出现说明有歧义，直接放弃判读
    hit_true = any(word in core for word in ("正确", "√", "✓", "right", "correct"))
    hit_false = any(word in core for word in ("错误", "×", "✗", "wrong", "incorrect"))
    if hit_true and not hit_false:
        return not negated
    if hit_false and not hit_true:
        return not negated
    return None


def _looks_like_judgement(options: Sequence["Option"]) -> bool:
    if len(options) != 2:
        return False
    values = {_judgement_value(option.text) for option in options}
    return values == {True, False}


def normalize_type(raw_type: Any, title: str, options: Sequence["Option"]) -> str:
    """题型归一化：已知题型直接用，未知时按选项与题干猜。"""
    key = _bare_key(raw_type)
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    if _looks_like_judgement(options):
        return "judgement"
    if len(options) >= 2:
        if _MULTIPLE_HINT_RE.search(title or ""):
            return "multiple"
        return "single"
    if len(options) == 1:
        return "single"
    return "shortanswer"


_PREFIX_NOISE_RE = re.compile(r"^(?:答案|正确选项|选项|选)\s*[:：是为]?\s*")
_STRIP_CHARS = " \t。.，,；;:：!！?？\"'“”‘’()（）[]【】{}、"
_MULTI_LETTERS_RE = re.compile(r"^[A-Ha-h][A-Ha-h\s,，、;；/和与及]*$")


def _letters_in(values: Sequence[Any]) -> list[str]:
    """从答案值里抽选项字母（"B" / "B." / "A、B" / "ABD" / "答案：B" 都能抽）。"""
    letters: list[str] = []
    for value in values:
        text = _norm_text(value)
        if not text:
            continue
        if _BARE_LETTERS_RE.match(text):
            letters.extend(char.upper() for char in text)
            continue
        bare = _PREFIX_NOISE_RE.sub("", text).strip(_STRIP_CHARS)
        if not bare:
            continue
        if _BARE_LETTERS_RE.match(bare):
            letters.extend(char.upper() for char in bare)
            continue
        # "A、B" / "A,B" / "A B" / "A和B"：多个字母塞在同一个字符串里
        if _MULTI_LETTERS_RE.match(bare):
            letters.extend(char.upper() for char in bare if char.upper() in _LETTER_SEQUENCE[:8])
            continue
        match = _LEADING_LETTER_RE.match(bare)
        if match:
            letters.append(match.group(1).upper())
    ordered: list[str] = []
    for letter in letters:
        if letter not in ordered:
            ordered.append(letter)
    return ordered


def _match_option_keys(values: Sequence[Any], options: Sequence["Option"]) -> list[str]:
    """把模型返回的答案值映射到选项字母。

    匹配顺序经过专门设计，避免"错答案被当成正答"：
    1. 与某个选项正文**完全一致** —— 优先级最高，"不对"不会被当成单字判断；
    2. 纯字母 / "A、B" 这种字母序列；
    3. 正文子串匹配，**两侧长度都要 >= 2 且取最长匹配**，避免 "2" 抢走 "24"；
    4. 纯数字当序号（1 起）。
    """
    matched: list[str] = []
    option_pairs = [(_bare_key(option.text), option.key) for option in options if _bare_key(option.text)]
    for value in values:
        key = _bare_key(value)
        if not key:
            continue
        exact = [option_key for option_text, option_key in option_pairs if option_text == key]
        if exact:
            matched.append(exact[0])
            continue
        letters = _letters_in([value])
        if letters:
            matched.extend(letters)
            continue
        if len(key) >= 2:
            hits = [
                (len(option_text), option_key)
                for option_text, option_key in option_pairs
                if len(option_text) >= 2 and (option_text in key or key in option_text)
            ]
            if hits:
                # 只保留最长的一批: "苹果、香蕉" 两个选项都命中且等长 -> 都算；
                # "24" 只命中 "24" 而不命中被覆盖的短选项 "2"
                longest = max(length for length, _ in hits)
                for length, option_key in hits:
                    if length == longest:
                        matched.append(option_key)
                continue
        if key.isdigit() and 1 <= int(key) <= len(options):
            matched.append(options[int(key) - 1].key)
    ordered: list[str] = []
    for letter in matched:
        if letter not in ordered:
            ordered.append(letter)
    return ordered


def _extract_answer_values(data: dict) -> list[str]:
    """从模型返回的 JSON 里取 answer 数组（兼容 Answer / answers / ans 等键名）。"""
    if not isinstance(data, dict):
        return []
    lowered = {str(key).strip().lower(): value for key, value in data.items()}
    raw: Any = None
    for key in _ANSWER_KEYS:
        if key in lowered:
            raw = lowered[key]
            break
    if raw is None:
        return []
    return _stringify_values(raw)


def _stringify_values(raw: Any) -> list[str]:
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, (list, tuple)):
        values: list[str] = []
        for item in raw:
            if isinstance(item, (dict, list, tuple)):
                values.extend(_stringify_values(item))
            else:
                text = str(item).strip()
                if text:
                    values.append(text)
        return values
    if isinstance(raw, dict):
        items = sorted(raw.items(), key=lambda pair: str(pair[0]))
        return _stringify_values([value for _, value in items])
    text = str(raw).strip()
    return [text] if text else []


def _similarity_key(text: str) -> str:
    normalized = _norm_text(text).lower()
    return re.sub(r"[\s。,，.．;；:：、\"'“”‘’()（）\[\]【】]", "", normalized)


def _similarity(left: str, right: str) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    return difflib.SequenceMatcher(None, left, right).ratio() >= _SIMILARITY_THRESHOLD


_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_FRACTION_RE = re.compile(r"^\d+/\d+$")


def _similarity_compatible(left: str, right: str) -> bool:
    """两条候选答案能不能算"同一个答案"。

    除了字符相似度，还要过两道闸：一正一负（"正确"/"不正确"）绝不并簇；
    纯数值/分数只认全等，否则 "24" 与 "2.4" 会被 0.75 的相似度并到一起。
    """
    if left == right:
        return True
    negated_left = left.startswith(_NEGATION_PREFIXES)
    negated_right = right.startswith(_NEGATION_PREFIXES)
    if negated_left != negated_right:
        return False
    if (_NUMERIC_RE.match(left) and _NUMERIC_RE.match(right)) or (
        _FRACTION_RE.match(left) and _FRACTION_RE.match(right)
    ):
        return False
    return _similarity(left, right)


def _cluster_candidates(candidates: Sequence[str]) -> list[list[str]]:
    """按字符级相似度聚类（簇内任意一条相似即并入，且必须通过一致性闸门）。"""
    clusters: list[list[str]] = []
    keys: list[list[str]] = []
    for text in candidates:
        key = _similarity_key(text)
        placed = False
        for index, cluster_keys in enumerate(keys):
            if any(_similarity_compatible(key, other) for other in cluster_keys):
                clusters[index].append(text)
                cluster_keys.append(key)
                placed = True
                break
        if not placed:
            clusters.append([text])
            keys.append([key])
    return clusters


def _representative(items: Sequence[str]) -> str:
    """簇内代表答案：出现次数最多 > 与簇内平均相似度最高 > 更长（信息更全）。"""
    keys = [_similarity_key(item) for item in items]
    best = items[0]
    best_score: tuple[int, float, int] | None = None
    for index, item in enumerate(items):
        same = sum(1 for key in keys if key == keys[index])
        average = sum(
            difflib.SequenceMatcher(None, keys[index], key).ratio() for key in keys
        ) / max(1, len(keys))
        score = (same, round(average, 6), len(item))
        if best_score is None or score > best_score:
            best = item
            best_score = score
    return best


def cache_key_for(question: "Question") -> str:
    """缓存键：md5(题型 + 题干 + 原始选项)，和现有 cache.json 的 str->str 结构兼容。"""
    payload = f"{question.type}|{question.title}|{question.raw_options}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 数据结构


@dataclass
class Option:
    """一个选项。"""

    key: str
    text: str
    raw: str


@dataclass
class Question:
    """一道归一化后的题目。"""

    id: str
    type: str
    title: str
    options: list[Option]
    raw_options: str

    @classmethod
    def from_dict(cls, q: dict) -> "Question":
        """从 decode_questions_info 的题目 dict 构造。"""
        data = dict(q or {})
        question_id = str(data.get("id") or "").strip()
        title = str(data.get("title") or "").strip()
        raw_options = data.get("options")
        if isinstance(raw_options, (list, tuple)):
            raw_options = "\n".join(str(item) for item in raw_options)
        raw_options = "" if raw_options is None else str(raw_options)
        options = parse_options(raw_options)
        question_type = normalize_type(data.get("type"), title, options)
        return cls(
            id=question_id,
            type=question_type,
            title=title,
            options=options,
            raw_options=raw_options,
        )

    @property
    def keys(self) -> list[str]:
        return [option.key for option in self.options]


@dataclass
class SolveResult:
    """一道题的求解结果。"""

    answer: str
    confident: bool
    votes: dict
    samples: int
    source: str
    detail: str


# ---------------------------------------------------------------- 求解器


class QuestionSolver:
    """按题型多采样投票求解。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        votes: int = 3,
        temperature: float = 0.6,
        confidence: float = 0.67,
        max_escalations: int = 2,
        cache=None,
    ) -> None:
        self.client = client
        self.votes = max(1, int(votes))
        self.temperature = float(temperature)
        self.confidence = float(confidence)
        self.max_escalations = max(0, int(max_escalations))
        self.cache = cache
        self._random = random.Random()
        self.cache_hits = 0

    # ------------------------------------------------------------ 主流程

    def solve(self, q: Question) -> SolveResult:
        """求解一道题，任何单次调用失败都不会把整题带崩。"""
        if isinstance(q, dict):
            q = Question.from_dict(q)
        key = cache_key_for(q)

        cached = self._cache_get(key)
        if cached:
            self.cache_hits += 1
            logger.info(f"命中答题缓存：{q.title[:40]} -> {cached}")
            return SolveResult(
                answer=cached,
                confident=True,
                votes={},
                samples=0,
                source="cache",
                detail="命中缓存",
            )

        # 跨轮累计票型：升级轮不丢弃上一轮的票，避免"低温重复同一个错误答案"制造假共识
        aggregated: dict = {}
        total_valid = 0
        last: SolveResult | None = None
        for index, (vote_count, temperature, hint, escalated) in enumerate(self._attempt_plan(q), start=1):
            logger.info(
                f"第 {index} 轮求解（{TYPE_LABELS.get(q.type, q.type)}，采样 {vote_count} 次，"
                f"temperature={temperature:.2f}）：{q.title[:40]}"
            )
            source = "llm_retry" if escalated else ("llm_vote" if vote_count > 1 else "llm")
            try:
                round_votes, round_valid, question_type = self._collect(q, vote_count, temperature, hint)
            except Exception as exc:  # noqa: BLE001 - 兜底，绝不让整题崩掉
                logger.error(f"第 {index} 轮求解出现未预期异常：{exc}")
                last = SolveResult(
                    answer="",
                    confident=False,
                    votes=dict(aggregated),
                    samples=total_valid,
                    source=source,
                    detail=f"求解异常：{exc}",
                )
                continue
            for vote_key, count in round_votes.items():
                aggregated[vote_key] = aggregated.get(vote_key, 0) + count
            total_valid += round_valid
            result = self._decide(q, question_type, aggregated, total_valid, source)
            last = result
            if result.confident and result.answer:
                self._cache_put(key, result.answer, result)
                logger.info(f"求解成功（{result.source}）：{q.title[:40]} -> {result.answer}")
                return result
            logger.warning(f"第 {index} 轮未达置信度：{result.detail}")

        answer = self._fallback_answer(q)
        detail = "所有采样均未达置信度，已按题型随机兜底"
        if last is not None and last.detail:
            detail = f"{detail}；最后一轮：{last.detail}"
        logger.warning(f"求解失败后兜底（{TYPE_LABELS.get(q.type, q.type)}）-> {answer!r}")
        return SolveResult(
            answer=answer,
            confident=False,
            votes=dict(aggregated),
            samples=total_valid,
            source="fallback_random",
            detail=detail,
        )

    # ------------------------------------------------------------ 轮次与采样

    def _attempt_plan(self, q: Question) -> list[tuple[int, float, str, bool]]:
        """第一轮正常投票，之后每轮加票数、降到 0 温、追加核对提示。"""
        plan: list[tuple[int, float, str, bool]] = [
            (self.votes, self._temperature_for(q.type), "", False),
        ]
        vote_count = self.votes
        for _ in range(self.max_escalations):
            vote_count = min(7, vote_count * 2)
            # 升级轮提高采样多样性而不是降到 0 温：0 温会让模型复读同一个错误答案，制造假共识
            escalate_temperature = min(1.2, max(0.7, self.temperature + 0.2))
            plan.append((vote_count, escalate_temperature, _ESCALATION_HINT, True))
        return plan

    def _temperature_for(self, question_type: str) -> float:
        offset = _TYPE_TEMPERATURE_OFFSET.get(question_type, 0.1)
        return max(0.0, min(1.5, self.temperature + offset))

    def _messages_for(self, q: Question, question_type: str, hint: str) -> list[dict]:
        system = _SYSTEM_BASE + _SYSTEM_BY_TYPE.get(question_type, _SYSTEM_BY_TYPE["shortanswer"])
        parts = [f"题目：{q.title}"]
        if question_type in ("single", "multiple", "judgement") and q.options:
            parts.append("选项：")
            for option in q.options:
                parts.append(f"{option.key}. {option.text}" if option.text else option.key)
        if hint:
            parts.append(hint)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(parts)},
        ]

    def _sample_answers(
        self,
        q: Question,
        question_type: str,
        count: int,
        temperature: float,
        hint: str,
    ) -> list[list[str]]:
        """采样 count 次，返回每次采样解析出的 answer 数组（失败的采样直接跳过）。"""
        messages = self._messages_for(q, question_type, hint)
        samples: list[list[str]] = []
        for index in range(count):
            try:
                data, raw = self.client.chat_json(
                    messages,
                    temperature=temperature,
                    json_mode=True,
                )
            except LLMError as exc:
                logger.warning(f"第 {index + 1}/{count} 次采样调用失败：{exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - 单次采样失败不能带崩整题
                logger.warning(f"第 {index + 1}/{count} 次采样出现未预期异常：{exc}")
                continue
            if data is None:
                logger.debug(f"第 {index + 1}/{count} 次采样未能解析出 JSON：{str(raw)[:120]}")
                continue
            values = _extract_answer_values(data)
            if not values:
                logger.debug(f"第 {index + 1}/{count} 次采样缺少 answer 字段：{str(raw)[:120]}")
                continue
            samples.append(values)
        return samples

    # ------------------------------------------------------------ 计票

    def _tally_single(self, q: Question, samples: Sequence[Sequence[str]]) -> tuple[dict, int]:
        legal = set(q.keys)
        votes: dict = {}
        valid = 0
        for values in samples:
            letters = [letter for letter in _match_option_keys(values, q.options) if letter in legal]
            if len(set(letters)) != 1:
                logger.debug(f"单选题采样被丢弃：{values}")
                continue
            valid += 1
            candidate = letters[0]
            votes[candidate] = votes.get(candidate, 0) + 1
        return votes, valid

    def _tally_multiple(self, q: Question, samples: Sequence[Sequence[str]]) -> tuple[dict, int]:
        legal = set(q.keys)
        votes: dict = {}
        valid = 0
        for values in samples:
            letters = {letter for letter in _match_option_keys(values, q.options) if letter in legal}
            if not letters:
                logger.debug(f"多选题采样被丢弃：{values}")
                continue
            valid += 1
            for letter in letters:
                votes[letter] = votes.get(letter, 0) + 1
        return votes, valid

    def _tally_judgement(self, q: Question, samples: Sequence[Sequence[str]]) -> tuple[dict, int]:
        votes: dict = {"true": 0, "false": 0}
        valid = 0
        letter_values: dict = {}
        for option in q.options:
            value = _judgement_value(option.text)
            if value is not None:
                letter_values[option.key] = value
        if not letter_values and len(q.options) == 2:
            # 选项文本判不了（字体解密错字/非常规写法）：学习通判断题固定「正确在前、错误在后」
            letter_values = {q.options[0].key: True, q.options[1].key: False}
        for values in samples:
            decided: bool | None = None
            for value in values:
                decided = _judgement_value(value)
                if decided is not None:
                    break
            if decided is None:
                letters = _letters_in(values)
                if len(letters) == 1 and letters[0] in letter_values:
                    decided = letter_values[letters[0]]
            if decided is None:
                logger.debug(f"判断题采样被丢弃：{values}")
                continue
            valid += 1
            votes["true" if decided else "false"] += 1
        return votes, valid

    def _tally_text(self, q: Question, samples: Sequence[Sequence[str]]) -> tuple[dict, int]:
        candidates: list[str] = []
        for values in samples:
            text = "\n".join(values).strip()
            if text:
                candidates.append(text)
        if not candidates:
            return {}, 0
        votes: dict = {}
        for cluster in _cluster_candidates(candidates):
            votes[_representative(cluster)] = len(cluster)
        return votes, len(candidates)

    # ------------------------------------------------------------ 单轮求解

    def _collect(self, q: Question, vote_count: int, temperature: float, hint: str) -> tuple[dict, int, str]:
        """跑一轮采样并计票，返回 (票型, 有效样本数, 实际使用的题型)。"""
        question_type = q.type if q.type in QUESTION_TYPES else "shortanswer"
        if question_type in ("single", "multiple", "judgement") and not q.options:
            logger.warning(f"题目缺少可解析的选项，按文本题处理：{q.title[:40]}")
            question_type = "shortanswer"

        samples = self._sample_answers(q, question_type, vote_count, temperature, hint)
        if question_type == "single":
            votes, valid = self._tally_single(q, samples)
        elif question_type == "multiple":
            votes, valid = self._tally_multiple(q, samples)
        elif question_type == "judgement":
            votes, valid = self._tally_judgement(q, samples)
        else:
            votes, valid = self._tally_text(q, samples)
        return votes, valid, question_type

    def _decide(self, q: Question, question_type: str, votes: dict, valid: int, source: str) -> SolveResult:
        """按题型裁决累计票型。"""
        if question_type == "single":
            return self._decide_exclusive(votes, valid, source, "单选")
        if question_type == "multiple":
            return self._decide_multiple(q, votes, valid, source)
        if question_type == "judgement":
            return self._decide_exclusive(votes, valid, source, "判断")
        return self._decide_text(votes, valid, source)

    def _need(self, valid: int) -> int:
        """达到置信度所需的最低票数。

        用整数比较，避免 "2/3 = 0.6667 < 0.67" 这种浮点坑导致明明是多数票却必然升级。
        """
        return max(1, int(math.floor(valid * self.confidence + 0.5)))

    def _decide_exclusive(self, votes: dict, valid: int, source: str, label: str) -> SolveResult:
        """单选 / 判断题：取唯一票王，票数达到 need 才算可信。"""
        if valid <= 0 or not votes:
            return SolveResult("", False, dict(votes), valid, source, f"{label}没有可用的有效采样")
        best = max(votes.values())
        leaders = [key for key, count in votes.items() if count == best]
        if len(leaders) != 1:
            return SolveResult("", False, dict(votes), valid, source, f"{label}最高票不唯一：{sorted(leaders)}")
        winner = leaders[0]
        need = self._need(valid)
        detail = f"{label}票型 {votes}，票王 {best}/{valid}（需 {need} 票）"
        return SolveResult(winner, best >= need, dict(votes), valid, source, detail)

    def _decide_multiple(self, q: Question, votes: dict, valid: int, source: str) -> SolveResult:
        """多选题：每个选项独立**达到半数**（不是严格过半）即入选。

        多选题只给一个选项必然判错，所以入选不足 2 项时一律标记为低可信，
        不把"单字母"当成高可信答案放出去。
        """
        if valid <= 0 or not votes:
            return SolveResult("", False, dict(votes), valid, source, "多选没有可用的有效采样")
        threshold = max(1, int(math.ceil(valid * 0.5)))
        selected = [key for key in q.keys if votes.get(key, 0) >= threshold]
        if not selected:
            top = max(votes.values())
            selected = [key for key in q.keys if votes.get(key, 0) == top][:1]
        selected = sorted(selected)
        answer = "".join(selected)
        need = self._need(valid)
        support = min((votes.get(key, 0) / valid) for key in selected) if selected else 0.0
        confident = len(selected) >= 2 and all(votes.get(key, 0) >= need for key in selected)
        detail = (
            f"多选选项票型 {votes}，入选 {selected}"
            f"（门槛 {threshold}/{valid}，需 {need} 票，最低支持率 {support:.2f}）"
        )
        return SolveResult(answer, confident, dict(votes), valid, source, detail)

    def _decide_text(self, votes: dict, valid: int, source: str) -> SolveResult:
        """填空 / 简答：取最大簇的代表答案，票数达到 need 才算可信。"""
        if valid <= 0 or not votes:
            return SolveResult("", False, dict(votes), valid, source, "文本题没有可用的有效采样")
        best = max(votes.values())
        leaders = [key for key, count in votes.items() if count == best]
        leaders.sort(key=len, reverse=True)
        winner = leaders[0]
        need = self._need(valid)
        preview = winner if len(winner) <= 40 else winner[:40] + "…"
        detail = f"文本聚类 {len(votes)} 簇，最大簇 {best}/{valid}（需 {need} 票）：{preview}"
        return SolveResult(winner, best >= need, dict(votes), valid, source, detail)

    # ------------------------------------------------------------ 兜底与缓存

    def _fallback_answer(self, q: Question) -> str:
        """按题型给一个合法的随机答案。"""
        keys = q.keys
        question_type = q.type if q.type in QUESTION_TYPES else "shortanswer"
        if question_type == "single":
            if not keys:
                return ""
            return self._random.choice(keys)
        if question_type == "multiple":
            if not keys:
                return ""
            if len(keys) < 2:
                return "".join(sorted(keys))
            picked = self._random.sample(keys, 2)
            return "".join(sorted(picked))
        if question_type == "judgement":
            return "true" if self._random.random() < 0.5 else "false"
        return ""

    def _cache_get(self, key: str) -> str | None:
        if self.cache is None:
            return None
        getter = getattr(self.cache, "get_cache", None)
        if not callable(getter):
            return None
        try:
            value = getter(key)
        except Exception as exc:  # noqa: BLE001 - 缓存坏了不该让答题崩掉
            logger.warning(f"读取答题缓存失败：{exc}")
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _cache_put(self, key: str, answer: str, result: SolveResult | None = None) -> None:
        """只缓存票型干净的结论：样本太少或存在低票项时宁可不缓存，避免把假置信永久固化。"""
        if self.cache is None or not answer:
            return
        if result is not None:
            if not result.confident or result.samples < 2:
                return
            need = self._need(result.samples)
            if result.votes and min(result.votes.values()) < need:
                return
        adder = getattr(self.cache, "add_cache", None)
        if not callable(adder):
            return
        try:
            adder(key, answer)
        except Exception as exc:  # noqa: BLE001 - 缓存写入失败不影响本次答题
            logger.warning(f"写入答题缓存失败：{exc}")
