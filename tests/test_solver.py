# -*- coding: utf-8 -*-
"""api.solver.QuestionSolver 的离线投票回归测试。

全部通过注入假 transport 跑：LLMClient 拿到 transport 后完全不碰网络、不 import openai。
"""
import json

import pytest

from api.llm import LLMClient, LLMConfig
from api.solver import Question, QuestionSolver, cache_key_for, parse_options


class ScriptedTransport:
    """假 transport：按调用顺序吐出预设内容，用完之后一直重复最后一条。

    接口签名与 LLMClient 期望一致：callable(messages, **kw) -> str
    """

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        index = min(len(self.calls) - 1, len(self.payloads) - 1)
        payload = self.payloads[index]
        if isinstance(payload, BaseException):
            raise payload
        if callable(payload):
            return json.dumps(payload(messages), ensure_ascii=False)
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False)


class DictCache:
    """最小缓存替身：接口与 api.answer.CacheDAO 的 get_cache / add_cache 一致。"""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.puts = []

    def get_cache(self, key):
        return self.data.get(key)

    def add_cache(self, key, value):
        self.puts.append((key, value))
        self.data[key] = value


def make_solver(payloads, *, votes=3, confidence=0.6, cache=None, max_escalations=0):
    config = LLMConfig(
        endpoint="https://llm.invalid/v1",
        api_key="test-key",
        model="test-model",
        min_interval=0.0,
        max_retries=1,
        max_tokens=256,
        temperature=0.3,
    )
    transport = ScriptedTransport(payloads)
    client = LLMClient(config, transport=transport)
    solver = QuestionSolver(
        client,
        votes=votes,
        temperature=0.3,
        confidence=confidence,
        max_escalations=max_escalations,
        cache=cache,
    )
    return solver, transport


SINGLE_Q = {
    "id": "8001",
    "type": "single",
    "title": "计算机网络的最终目的是实现什么？",
    "options": "A. 提高系统可靠性\nB. 资源共享\nC. 分布处理\nD. 均衡负荷",
}
MULTIPLE_Q = {
    "id": "8002",
    "type": "multiple",
    "title": "下列属于传输层协议的有哪些？",
    "options": "A. TCP\nB. SCTP\nC. UDP\nD. ICMP",
}
JUDGEMENT_Q = {
    "id": "8003",
    "type": "judgement",
    "title": "IP 协议提供的是无连接、不可靠的数据报服务。",
    "options": "A. 正确\nB. 错误",
}
COMPLETION_Q = {
    "id": "8004",
    "type": "completion",
    "title": "负责端到端通信的层次是____。",
    "options": "",
}


def test_选项解析出字母与正文():
    options = parse_options(MULTIPLE_Q["options"])
    assert [option.key for option in options] == ["A", "B", "C", "D"]
    assert options[0].text == "TCP"


def test_单选多轮投票取票王():
    solver, transport = make_solver([{"Answer": ["B"]}])
    result = solver.solve(Question.from_dict(SINGLE_Q))

    assert result.answer == "B"
    assert result.confident is True
    assert result.source == "llm_vote"
    assert result.votes == {"B": 3}
    assert len(transport.calls) == 3
    # 采样温度与 json_mode 透传给了 transport
    assert transport.calls[0]["kwargs"]["json_mode"] is True


def test_单选票型分散时不确认():
    """两次 B、一次 C：B 领先比例 2/3 ≈ 0.67，把阈值抬到 0.9 就不该算确认。"""
    solver, _ = make_solver(
        [{"Answer": ["B"]}, {"Answer": ["B"]}, {"Answer": ["C"]}], confidence=0.9
    )
    result = solver.solve(Question.from_dict(SINGLE_Q))

    assert result.votes == {"B": 2, "C": 1}
    assert result.confident is False


def test_多选按选项级投票():
    solver, transport = make_solver([{"Answer": ["A", "B"]}])
    result = solver.solve(Question.from_dict(MULTIPLE_Q))

    assert result.answer == "AB"
    assert result.votes == {"A": 3, "B": 3}
    assert result.confident is True
    assert len(transport.calls) == 3


def test_多选选项级投票部分命中():
    """两次 A+B、一次只有 A：B 拿到 2/3 票，仍然过半，应入选。"""
    solver, _ = make_solver(
        [{"Answer": ["A", "B"]}, {"Answer": ["A", "B"]}, {"Answer": ["A"]}]
    )
    result = solver.solve(Question.from_dict(MULTIPLE_Q))

    assert result.answer == "AB"
    assert result.votes == {"A": 3, "B": 2}


def test_判断题正确():
    solver, _ = make_solver([{"Answer": ["正确"]}])
    result = solver.solve(Question.from_dict(JUDGEMENT_Q))

    assert result.answer == "true"
    assert result.votes == {"true": 3, "false": 0}
    assert result.confident is True


def test_判断题错误():
    solver, _ = make_solver([{"Answer": ["错误"]}])
    result = solver.solve(Question.from_dict(JUDGEMENT_Q))

    assert result.answer == "false"
    assert result.confident is True


def test_判断题用选项字母也能判():
    solver, _ = make_solver([{"Answer": ["A"]}])
    result = solver.solve(Question.from_dict(JUDGEMENT_Q))

    assert result.answer == "true"


def test_填空答案按聚类取最大簇():
    solver, _ = make_solver(
        [{"Answer": ["传输层"]}, {"Answer": ["传输层"]}, {"Answer": ["应用层"]}]
    )
    result = solver.solve(Question.from_dict(COMPLETION_Q))

    assert result.answer == "传输层"
    assert result.confident is True
    assert result.votes["传输层"] == 2


def test_非法字母被丢弃后走兜底():
    """模型给出选项表里不存在的字母时，采样被丢弃，最终必须走合法兜底。"""
    solver, transport = make_solver([{"Answer": ["Z"]}], max_escalations=1)
    result = solver.solve(Question.from_dict(SINGLE_Q))

    assert result.answer in {"A", "B", "C", "D"}
    assert result.confident is False
    assert result.source == "fallback_random"
    assert result.votes == {}
    # 第一轮 3 次 + 升级轮 6 次
    assert len(transport.calls) == 9


@pytest.mark.parametrize("bad_payload", ["我不确定", "NOT JSON AT ALL", '{"foo": 1}', ""])
def test_全部垃圾答案时兜底仍返回合法答案(bad_payload):
    solver, _ = make_solver([bad_payload])
    result = solver.solve(Question.from_dict(SINGLE_Q))

    assert result.answer in {"A", "B", "C", "D"}
    assert result.source == "fallback_random"


def test_多选兜底返回两个合法字母():
    solver, _ = make_solver(["完全不是 JSON"])
    result = solver.solve(Question.from_dict(MULTIPLE_Q))

    assert result.answer, "多选兜底不能返回空答案"
    assert set(result.answer) <= {"A", "B", "C", "D"}
    assert result.answer == "".join(sorted(result.answer))
    assert len(result.answer) == 2


def test_判断兜底返回true或false():
    solver, _ = make_solver(["看不懂"])
    result = solver.solve(Question.from_dict(JUDGEMENT_Q))

    assert result.answer in {"true", "false"}


def test_transport抛异常也不带崩整题():
    solver, _ = make_solver([RuntimeError("网络断了")])
    result = solver.solve(Question.from_dict(SINGLE_Q))

    assert result.source == "fallback_random"
    assert result.answer in {"A", "B", "C", "D"}


def test_缓存命中直接返回且不再调用模型():
    question = Question.from_dict(SINGLE_Q)
    key = cache_key_for(question)
    cache = DictCache({key: "C"})
    solver, transport = make_solver([{"Answer": ["B"]}], cache=cache)

    result = solver.solve(question)

    assert result.answer == "C"
    assert result.source == "cache"
    assert result.confident is True
    assert transport.calls == []


def test_解出答案后写入缓存():
    question = Question.from_dict(SINGLE_Q)
    cache = DictCache()
    solver, _ = make_solver([{"Answer": ["B"]}], cache=cache)

    solver.solve(question)

    assert cache.puts == [(cache_key_for(question), "B")]


def test_缓存键与题型题干选项绑定():
    key_a = cache_key_for(Question.from_dict(SINGLE_Q))
    key_b = cache_key_for(Question.from_dict(dict(SINGLE_Q, title="换了个题干")))
    assert key_a != key_b
    assert len(key_a) == 32


def test_缓存实现损坏时不影响答题():
    class BrokenCache:
        def get_cache(self, key):
            raise OSError("磁盘坏了")

        def add_cache(self, key, value):
            raise OSError("磁盘坏了")

    solver, _ = make_solver([{"Answer": ["B"]}], cache=BrokenCache())
    result = solver.solve(Question.from_dict(SINGLE_Q))

    assert result.answer == "B"
    assert result.source == "llm_vote"
