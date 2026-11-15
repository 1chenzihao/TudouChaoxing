# -*- coding: utf-8 -*-
"""api/llm.py 与 api/solver.py 的离线自测脚本（不联网、不写缓存文件）。

用法：
    cd SuperStar-main && python tools/selftest_solver.py

用 transport 注入假模型替代真实接口，覆盖：选项解析、单选/多选/判断投票、
填空聚类、非法字母丢弃、升级救回、全垃圾兜底、缓存命中、配置与环境变量优先级。
退出码 0 表示全部通过，非 0 表示有失败用例。
"""

from __future__ import annotations

import configparser
import json
import os
import sys
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_loguru_stub() -> None:
    """本机没装 loguru 时塞一个最小替身，保证能导入 api.logger 而不写日志文件。"""
    try:
        import loguru  # noqa: F401
        return
    except ImportError:
        pass

    module = types.ModuleType("loguru")

    class _StubLogger:
        def add(self, *args, **kwargs):
            return 0

        def _log(self, *args, **kwargs):
            return None

        trace = debug = info = success = warning = error = critical = _log

        def exception(self, *args, **kwargs):
            return None

    module.logger = _StubLogger()
    sys.modules["loguru"] = module


_install_loguru_stub()

from api.llm import LLMClient, LLMConfig, LLMError  # noqa: E402
from api.solver import Question, QuestionSolver, cache_key_for, parse_options  # noqa: E402


TESTS = []
C = None  # 由 main() 初始化


class Checker:
    """极简断言计数器。"""

    def __init__(self) -> None:
        self.count = 0

    def ok(self, condition, message: str) -> None:
        self.count += 1
        if not condition:
            raise AssertionError(message)

    def eq(self, actual, expected, message: str) -> None:
        self.count += 1
        if actual != expected:
            raise AssertionError(f"{message}（期望 {expected!r}，实际 {actual!r}）")


def test(name):
    def decorator(func):
        TESTS.append((name, func))
        return func

    return decorator


# ---------------------------------------------------------------- 测试用假模型


class ScriptedTransport:
    """按顺序吐预设回复；回复可以是 str / Exception / callable(messages, **kw)。"""

    def __init__(self, replies=None, default=None) -> None:
        self.replies = list(replies or [])
        self.default = default
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": dict(kwargs)})
        item = self.replies.pop(0) if self.replies else self.default
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(messages, **kwargs)
        if item is None:
            raise LLMError("自测假模型没有更多回复")
        return item


def ans(*values) -> str:
    return json.dumps({"answer": list(values), "reason": "自测用预设答案"}, ensure_ascii=False)


def make_client(transport, **overrides) -> LLMClient:
    conf = dict(
        endpoint="http://127.0.0.1:9/v1",
        api_key="selftest-key",
        model="selftest-model",
        timeout=1.0,
        max_retries=1,
        min_interval=0.0,
        temperature=0.6,
    )
    conf.update(overrides)
    return LLMClient(LLMConfig(**conf), transport=transport)


DEFAULT_OPTIONS = "A. 甲\nB. 乙\nC. 丙\nD. 丁"


def make_question(options=DEFAULT_OPTIONS, qtype="single", title="下列说法正确的是"):
    return Question.from_dict(
        {
            "id": "q1",
            "title": title,
            "options": options,
            "type": qtype,
            "answerField": {"answerq1": "", "answertypeq1": "0"},
        }
    )


# ---------------------------------------------------------------- 用例


@test("选项解析：6 种写法 + 自动补字母")
def test_parse_options():
    cases = [
        ("A. 甲", "A", "甲"),
        ("A、甲", "A", "甲"),
        ("A 甲", "A", "甲"),
        ("A．甲", "A", "甲"),
        ("Ｚ．甲", "Z", "甲"),
        ("甲", "A", "甲"),
    ]
    for line, expected_key, expected_text in cases:
        options = parse_options(line)
        C.eq(len(options), 1, f"单行解析行数 {line!r}")
        C.eq(options[0].key, expected_key, f"字母 {line!r}")
        C.eq(options[0].text, expected_text, f"正文 {line!r}")
        C.eq(options[0].raw, line.strip(), f"原始行 {line!r}")

    mixed = make_question(options="A. 甲\nB、乙\nC 丙\nD．丁\n戊")
    C.eq([option.key for option in mixed.options], ["A", "B", "C", "D", "E"], "混合选项字母")
    C.eq([option.text for option in mixed.options], ["甲", "乙", "丙", "丁", "戊"], "混合选项正文")

    dotted = parse_options("A. 1.5 倍速")
    C.eq(dotted[0].text, "1.5 倍速", "选项正文里带句点")

    no_prefix = parse_options("苹果\n香蕉")
    C.eq([option.key for option in no_prefix], ["A", "B"], "无前缀按行序补字母")


_ENV_KEYS = (
    "CHX_LLM_ENDPOINT",
    "CHX_LLM_KEY",
    "CHX_LLM_MODEL",
    "CHX_LLM_PROXY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "CHX_LLM_TIMEOUT",
    "CHX_LLM_MAX_RETRIES",
    "CHX_LLM_MIN_INTERVAL",
    "CHX_LLM_TEMPERATURE",
    "CHX_LLM_MAX_TOKENS",
    "CHX_LLM_SEED",
)


@test("配置：conf > 环境变量 > 默认值")
def test_config_from_mapping():
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["CHX_LLM_ENDPOINT"] = "http://env.example/v1"
        os.environ["OPENAI_API_KEY"] = "env-key"
        os.environ["CHX_LLM_MODEL"] = "env-model"
        cfg = LLMConfig.from_mapping({})
        C.eq(cfg.endpoint, "http://env.example/v1", "环境变量 endpoint")
        C.eq(cfg.api_key, "env-key", "环境变量 OPENAI_API_KEY")
        C.eq(cfg.model, "env-model", "环境变量 model")
        C.eq(cfg.timeout, 90.0, "默认 timeout")
        C.eq(cfg.min_interval, 1.0, "默认 min_interval")
        C.eq(cfg.max_retries, 4, "默认 max_retries")

        conf = {
            "endpoint": "http://conf.example/v1",
            "key": "conf-key",
            "model": "conf-model",
            "http_proxy": "http://127.0.0.1:7890",
            "timeout": "12.5",
            "max_retries": "2",
            "min_interval_seconds": "0.5",
            "temperature": "0.15",
            "max_tokens": "256",
        }
        cfg2 = LLMConfig.from_mapping(conf)
        C.eq(cfg2.endpoint, "http://conf.example/v1", "conf 覆盖 endpoint")
        C.eq(cfg2.api_key, "conf-key", "conf key")
        C.eq(cfg2.model, "conf-model", "conf model")
        C.eq(cfg2.http_proxy, "http://127.0.0.1:7890", "conf proxy")
        C.eq(cfg2.timeout, 12.5, "conf timeout")
        C.eq(cfg2.max_retries, 2, "conf max_retries")
        C.eq(cfg2.min_interval, 0.5, "conf min_interval_seconds")
        C.eq(cfg2.temperature, 0.15, "conf temperature")
        C.eq(cfg2.max_tokens, 256, "conf max_tokens")

        os.environ.pop("CHX_LLM_MODEL", None)
        parser = configparser.ConfigParser()
        parser.read_string("[llm]\nendpoint = http://ini.example/v1\nkey = ini-key\n")
        cfg3 = LLMConfig.from_mapping(parser["llm"])
        C.eq(cfg3.endpoint, "http://ini.example/v1", "configparser 段 endpoint")
        C.eq(cfg3.api_key, "ini-key", "configparser 段 key")
        C.eq(cfg3.model, "gpt-4o-mini", "清掉环境变量后回落默认 model")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@test("chat_json：Markdown 剥壳 / 抓大括号 / 校验器")
def test_chat_json():
    fence = chr(96) * 3
    wrapped = fence + "json\n" + json.dumps({"answer": ["B"]}, ensure_ascii=False) + "\n" + fence
    noisy = '好的，我的答案是：{"answer": ["C"], "reason": "因为"} 以上。'
    transport = ScriptedTransport(
        [wrapped, noisy, "完全不是 JSON", json.dumps({"answer": ["A"]})],
        default="兜底",
    )
    client = make_client(transport)
    messages = [{"role": "user", "content": "题"}]

    data, raw = client.chat_json(messages)
    C.eq(data, {"answer": ["B"]}, "Markdown 包裹剥壳")
    C.ok(raw.startswith(fence), "返回模型原始文本")
    data2, _ = client.chat_json(messages)
    C.eq(data2, {"answer": ["C"], "reason": "因为"}, "从混杂文本里抓第一个 JSON 对象")
    data3, _ = client.chat_json(messages)
    C.eq(data3, None, "非 JSON 文本解析失败返回 None")
    data4, _ = client.chat_json(messages, validator=lambda payload: False)
    C.eq(data4, None, "校验器返回 False 视为失败")
    C.eq(client.stats.calls, 4, "统计调用次数")


@test("chat：不支持 response_format 时自动降级一次")
def test_json_mode_degradation():
    class Unsupported(Exception):
        def __init__(self):
            super().__init__("response_format is not supported by this API")
            self.status_code = 400

    calls = []

    def transport(messages, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("json_mode"):
            raise Unsupported()
        return json.dumps({"answer": ["A"]})

    client = make_client(transport)
    text = client.chat([{"role": "user", "content": "题"}])
    C.eq(json.loads(text)["answer"], ["A"], "降级后拿到内容")
    C.eq(len(calls), 2, "降级共两次调用")
    C.ok(calls[0].get("json_mode") is True, "第一次带 json_mode")
    C.ok("json_mode" not in calls[1], "降级后不带 json_mode")


@test("chat：网络异常重试后抛 LLMError，401 不重试")
def test_retry_and_error():
    attempts = []

    def flaky(messages, **kwargs):
        attempts.append(1)
        raise ConnectionError("连接被重置")

    client = make_client(flaky, max_retries=2)
    try:
        client.chat([{"role": "user", "content": "题"}])
    except LLMError:
        pass
    else:
        raise AssertionError("重试耗尽后应抛 LLMError")
    C.eq(len(attempts), 2, "重试后共两次尝试")
    C.eq(client.stats.calls, 2, "统计 calls")
    C.eq(client.stats.failures, 2, "统计 failures")
    C.eq(client.stats.retries, 1, "统计 retries")

    class AuthError(Exception):
        def __init__(self):
            super().__init__("invalid api key")
            self.status_code = 401

    def unauthorized(messages, **kwargs):
        raise AuthError()

    client2 = make_client(unauthorized, max_retries=4)
    try:
        client2.chat([{"role": "user", "content": "题"}])
    except LLMError:
        pass
    else:
        raise AssertionError("401 应直接抛 LLMError")
    C.eq(client2.stats.calls, 1, "不可重试错误只尝试一次")


@test("单选投票：3 次里 2 次对，取唯一票王")
def test_single_vote():
    transport = ScriptedTransport([ans("C"), ans("B"), ans("B")], default=ans("B"))
    client = make_client(transport)
    solver = QuestionSolver(client, votes=3, temperature=0.6, confidence=0.6)
    result = solver.solve(make_question())
    C.eq(result.answer, "B", "单选票王")
    C.ok(result.confident, "达到置信度")
    C.eq(result.votes, {"B": 2, "C": 1}, "票型")
    C.eq(result.samples, 3, "有效样本数")
    C.eq(result.source, "llm_vote", "来源 llm_vote")
    C.eq(client.stats.calls, 3, "调用次数等于采样次数")


@test("单选投票：2/3 直接采信（整数票数比较，不被 0.6667 小于 0.67 卡住）")
def test_single_escalation_by_confidence():
    transport = ScriptedTransport([ans("C"), ans("B"), ans("B")], default=ans("B"))
    solver = QuestionSolver(make_client(transport), votes=3)
    result = solver.solve(make_question())
    C.eq(result.answer, "B", "票王 B")
    C.ok(result.confident, "2/3 达到默认置信度")
    C.eq(result.source, "llm_vote", "第一轮直接采信，不进入升级轮")
    C.eq(result.samples, 3, "只用 3 次采样")
    C.eq(len(transport.calls), 3, "没有额外调用")


@test("单选投票：阈值调高时确实升级，且升级轮跨轮累计票而不是丢弃")
def test_single_escalation_when_strict():
    transport = ScriptedTransport(
        [ans("C"), ans("B"), ans("B"), ans("B"), ans("B"), ans("B"), ans("B"), ans("B"), ans("B")],
        default=ans("B"),
    )
    solver = QuestionSolver(make_client(transport), votes=3, confidence=0.9)
    result = solver.solve(make_question())
    C.eq(result.answer, "B", "升级后答案 B")
    C.ok(result.confident, "升级后达 0.9 置信度")
    C.eq(result.source, "llm_retry", "来源 llm_retry")
    C.eq(result.samples, 9, "跨轮累计有效采样（3 加 6），不是只算最后一轮")
    C.eq(result.votes, {"C": 1, "B": 8}, "升级轮累计票型")
    C.ok(transport.calls[-1]["kwargs"]["temperature"] > 0.5, "升级轮不降到 0 温")


@test("多选投票：选项级过半入选")
def test_multiple_vote():
    transport = ScriptedTransport(
        [ans("A", "C"), ans("A", "C"), ans("A", "B")],
        default=ans("A", "C"),
    )
    solver = QuestionSolver(make_client(transport), votes=3)
    result = solver.solve(make_question(qtype="multiple", title="下列哪些属于水果（多选）"))
    C.eq(result.answer, "AC", "多选答案（排序后拼接）")
    C.eq(result.votes, {"A": 3, "C": 2, "B": 1}, "选项票型")
    C.ok(result.confident, "多选置信")
    C.eq(result.source, "llm_vote", "来源 llm_vote")


@test("判断投票：正确/错误文本与字母都能映射")
def test_judgement_vote():
    transport = ScriptedTransport([ans("正确"), ans("A"), ans("对")], default=ans("正确"))
    solver = QuestionSolver(make_client(transport), votes=3)
    q = make_question(qtype="judgement", title="判断：地球是圆的", options="A. 正确\nB. 错误")
    result = solver.solve(q)
    C.eq(result.answer, "true", "判断题答案")
    C.eq(result.votes, {"true": 3, "false": 0}, "判断票型")
    C.ok(result.confident, "判断置信")


@test("填空聚类：相似答案并簇，不同答案分簇")
def test_completion_cluster():
    transport = ScriptedTransport(
        [ans("光合作用"), ans("光合作物"), ans("细胞呼吸")],
        default=ans("光合作用"),
    )
    solver = QuestionSolver(make_client(transport), votes=3, confidence=0.6)
    q = Question.from_dict(
        {
            "id": "c1",
            "title": "植物把光能转成化学能的过程叫____。",
            "options": "",
            "type": "completion",
        }
    )
    result = solver.solve(q)
    C.eq(result.answer, "光合作用", "填空代表答案")
    C.eq(result.votes, {"光合作用": 2, "细胞呼吸": 1}, "簇计数")
    C.ok(result.confident, "填空置信")
    C.eq(result.source, "llm_vote", "来源 llm_vote")


@test("非法字母被丢弃，有效票单独成立")
def test_invalid_letters():
    transport = ScriptedTransport([ans("Z"), ans("Z"), ans("B")], default=ans("B"))
    solver = QuestionSolver(make_client(transport), votes=3)
    result = solver.solve(make_question())
    C.eq(result.samples, 1, "只有 1 个有效样本")
    C.eq(result.votes, {"B": 1}, "非法字母不计票")
    C.eq(result.answer, "B", "答案 B")
    C.ok(result.confident, "1/1 达置信度")
    C.eq(result.source, "llm_vote", "来源 llm_vote")


@test("升级重试救回：首轮全垃圾，升级后给出正确答案")
def test_escalation_rescue():
    def rules(messages, **kwargs):
        user = messages[-1]["content"]
        if "再仔细核对" in user:
            return ans("D")
        return "我不知道，随便猜一个"

    client = make_client(ScriptedTransport([], default=rules))
    solver = QuestionSolver(client, votes=3)
    result = solver.solve(make_question())
    C.eq(result.answer, "D", "升级后拿到 D")
    C.eq(result.source, "llm_retry", "来源 llm_retry")
    C.ok(result.confident, "升级后置信")
    C.eq(result.samples, 6, "升级后 6 次有效采样")
    C.eq(client.stats.calls, 9, "3 次垃圾 + 6 次有效")


@test("全垃圾兜底：按题型给合法随机答案")
def test_fallback_random():
    solver = QuestionSolver(
        make_client(ScriptedTransport([], default="这不是 JSON")),
        votes=2,
        max_escalations=1,
    )
    result = solver.solve(make_question())
    C.eq(result.source, "fallback_random", "单选兜底来源")
    C.ok(result.confident is False, "兜底不置信")
    C.ok(result.answer in {"A", "B", "C", "D"}, "单选兜底落在合法字母内")

    solver_multi = QuestionSolver(
        make_client(ScriptedTransport([], default="垃圾内容")),
        votes=2,
        max_escalations=1,
    )
    q_multi = make_question(qtype="multiple", title="下列哪些是水果（多选）")
    result_multi = solver_multi.solve(q_multi)
    C.eq(result_multi.source, "fallback_random", "多选兜底来源")
    C.ok(len(result_multi.answer) >= 2, "多选兜底至少 2 项")
    C.ok(set(result_multi.answer) <= set(q_multi.keys), "多选兜底字母合法")

    solver_judge = QuestionSolver(
        make_client(ScriptedTransport([], default="垃圾内容")),
        votes=2,
        max_escalations=0,
    )
    q_judge = make_question(qtype="judgement", title="判断：1+1=2", options="A. 正确\nB. 错误")
    result_judge = solver_judge.solve(q_judge)
    C.eq(result_judge.source, "fallback_random", "判断兜底来源")
    C.ok(result_judge.answer in ("true", "false"), "判断兜底合法")


@test("模型直接答选项正文时也能映射成字母")
def test_text_option_mapping():
    transport = ScriptedTransport(
        [ans("苹果"), ans("苹果、香蕉"), ans("苹果，香蕉")],
        default=ans("苹果"),
    )
    solver = QuestionSolver(make_client(transport), votes=3)
    q = make_question(qtype="multiple", title="下列哪些是水果（多选）", options="A. 苹果\nB. 香蕉\nC. 石头")
    result = solver.solve(q)
    C.eq(result.answer, "AB", "选项正文映射成字母")
    C.eq(result.votes, {"A": 3, "B": 2}, "正文映射后的票型")

    single_transport = ScriptedTransport([ans("乙")], default=ans("乙"))
    single_solver = QuestionSolver(make_client(single_transport), votes=1, max_escalations=0)
    single_result = single_solver.solve(make_question())
    C.eq(single_result.answer, "B", "单选按正文字面反查")


@test("限速：min_interval 生效")
def test_throttle():
    stamps = []

    def transport(messages, **kwargs):
        stamps.append(time.monotonic())
        return ans("A")

    client = make_client(transport, min_interval=0.2)
    client.chat([{"role": "user", "content": "1"}])
    client.chat([{"role": "user", "content": "2"}])
    C.eq(len(stamps), 2, "两次调用都发生")
    C.ok(stamps[1] - stamps[0] >= 0.18, f"两次调用间隔 {stamps[1] - stamps[0]:.3f}s 应不小于 min_interval")


@test("缓存：命中直接返回、置信结果回写")
def test_cache():

    class FakeCache:
        def __init__(self):
            self.data = {}

        def get_cache(self, key):
            return self.data.get(key)

        def add_cache(self, key, value):
            self.data[key] = value

    q = make_question()
    cache = FakeCache()
    cache.data[cache_key_for(q)] = "B"
    transport = ScriptedTransport([], default=ans("A"))
    client = make_client(transport)
    solver = QuestionSolver(client, votes=3, cache=cache)
    result = solver.solve(q)
    C.eq(result.source, "cache", "命中缓存来源")
    C.eq(result.answer, "B", "缓存答案")
    C.ok(result.confident, "缓存视为置信")
    C.eq(len(transport.calls), 0, "命中缓存不调用模型")
    C.eq(client.stats.calls, 0, "命中缓存不产生调用统计")

    q2 = make_question(title="下面哪个是质数")
    cache2 = FakeCache()
    solver2 = QuestionSolver(
        make_client(ScriptedTransport([], default=ans("D"))),
        votes=3,
        confidence=0.6,
        cache=cache2,
    )
    result2 = solver2.solve(q2)
    C.eq(result2.answer, "D", "求解结果")
    C.eq(cache2.data.get(cache_key_for(q2)), "D", "置信结果回写缓存")


@test("缺选项的伪选择题按文本题处理，不崩")
def test_no_options():
    transport = ScriptedTransport([], default=ans("这题没有选项"))
    solver = QuestionSolver(make_client(transport), votes=2, confidence=0.6)
    q = Question.from_dict(
        {"id": "x", "title": "简述光合作用的意义", "options": "", "type": "single"}
    )
    C.eq(q.type, "single", "显式题型保留")
    result = solver.solve(q)
    C.eq(result.answer, "这题没有选项", "无选项时走文本路径")
    C.ok(result.confident, "文本路径置信")


def main() -> int:
    global C
    C = Checker()
    total = len(TESTS)
    failed = []
    for index, (name, func) in enumerate(TESTS, start=1):
        started = time.time()
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - 用例失败要记录后继续跑
            failed.append(name)
            print(f"[FAIL] {index}/{total} {name} -> {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"[PASS] {index}/{total} {name}（{time.time() - started:.2f}s）")

    if failed:
        print(f"FAILED {len(failed)}/{total}，断言 {C.count} 次")
        return 1
    print(f"OK {total}/{total} passed，断言 {C.count} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main())
