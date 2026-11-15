# -*- coding: utf-8 -*-
"""api.base.Chaoxing._solve_questions 的离线回归测试。

用假题库对象（stub 出 solve_structured / query / get_submit_params / COVER_RATE）
直接调私有方法，不建 session、不发请求。
"""
from types import SimpleNamespace

import pytest

from api.answer_check import check_judgement
from api.base import Chaoxing
from api.decode import decode_questions_info


class FakeSolverTiku:
    """题库替身：结构化解题、旧搜题、提交参数、覆盖率全都有。"""

    DISABLE = False
    SUBMIT = True
    COVER_RATE = 0.8
    true_list = ["正确", "对", "√", "是"]
    false_list = ["错误", "错", "×", "否"]

    def __init__(self, structured=None, legacy=None, submit_params=""):
        self.structured = dict(structured or {})
        self.legacy = dict(legacy or {})
        self.submit_params = submit_params
        self.structured_calls = []
        self.queries = []

    def solve_structured(self, q):
        self.structured_calls.append(q["id"])
        value = self.structured.get(q["id"])
        if isinstance(value, BaseException):
            raise value
        return value

    def query(self, q):
        self.queries.append(q["id"])
        return self.legacy.get(q["id"])

    def judgement_select(self, answer):
        return check_judgement(answer, self.true_list, self.false_list) == 1

    def get_submit_params(self):
        return self.submit_params


def solver_result(answer, confident=True):
    """与 api.solver.SolveResult 同构的轻量结果对象。"""
    return SimpleNamespace(answer=answer, confident=confident, votes={}, source="llm_vote")


@pytest.fixture
def questions(questions_html):
    """每次取一份新的解析结果，避免用例之间互相污染。"""
    return decode_questions_info(questions_html)["questions"]


def make_chaoxing(tiku):
    return Chaoxing(account=None, tiku=tiku, query_delay=0)


def test_全部题目走大模型(questions):
    tiku = FakeSolverTiku(
        structured={
            "8001": solver_result("B"),
            "8002": solver_result("AB"),
            "8003": solver_result("true"),
            "8004": solver_result("传输层"),
        }
    )
    stats = make_chaoxing(tiku)._solve_questions(questions, context="单测")

    answers = {q["id"]: q["answerField"][f"answer{q['id']}"] for q in questions}
    assert answers == {"8001": "B", "8002": "AB", "8003": "true", "8004": "传输层"}
    assert stats["total"] == 4
    assert stats["solved"] == 4
    assert stats["random"] == 0
    assert stats["sources"] == {"llm": 4, "llm_low": 0, "tiku": 0, "random": 0}
    assert stats["coverage"] == 100.0


def test_answerField每题都被填且键名齐全(questions):
    tiku = FakeSolverTiku(structured={"8001": solver_result("B")})
    make_chaoxing(tiku)._solve_questions(questions, context="单测")

    for q in questions:
        qid = q["id"]
        assert f"answer{qid}" in q["answerField"]
        assert f"answertype{qid}" in q["answerField"]
        assert isinstance(q["answerField"][f"answer{qid}"], str)
    # 没被大模型解出来的题也要有答案字段（随机兜底或留空），不能留 None
    assert questions[0]["answerField"]["answer8001"] == "B"


def test_来源统计覆盖大模型低可信题库与随机(questions):
    """8001 大模型 / 8002 大模型低可信 / 8003 题库 / 8004 随机。"""
    tiku = FakeSolverTiku(
        structured={
            "8001": solver_result("B", confident=True),
            "8002": solver_result("AB", confident=False),
        },
        legacy={"8003": "正确"},
    )
    stats = make_chaoxing(tiku)._solve_questions(questions, context="单测")

    assert stats["sources"] == {"llm": 1, "llm_low": 1, "tiku": 1, "random": 1}
    assert stats["solved"] == 3
    assert stats["random"] == 1
    assert stats["coverage"] == 75.0
    # 判断题走题库路径, 必须归一化成 true/false
    assert questions[2]["answerField"]["answer8003"] == "true"
    # 填空题没有任何选项, 随机也拿不到答案 -> 留空并按随机计数
    assert questions[3]["answerField"]["answer8004"] == ""
    assert questions[3]["answerSource8004"] == "random"


def test_大模型答案不是选项字母时回退题库(questions):
    tiku = FakeSolverTiku(
        structured={"8001": solver_result("完全不是选项的内容")},
        legacy={"8001": "B. 资源共享"},
    )
    stats = make_chaoxing(tiku)._solve_questions(questions, context="单测")

    assert questions[0]["answerField"]["answer8001"] == "B"
    assert questions[0]["answerSource8001"] == "tiku"
    assert "8001" in tiku.queries
    assert stats["sources"]["tiku"] == 1


def test_结构化解题抛异常不影响整题(questions):
    tiku = FakeSolverTiku(
        structured={"8001": RuntimeError("模型挂了")},
        legacy={"8001": "B. 资源共享"},
    )
    stats = make_chaoxing(tiku)._solve_questions(questions, context="单测")

    assert questions[0]["answerField"]["answer8001"] == "B"
    assert questions[0]["answerSource8001"] == "tiku"
    assert stats["total"] == 4


def test_题库停用时不调用旧搜题(questions):
    class DisabledTiku(FakeSolverTiku):
        DISABLE = True

    tiku = DisabledTiku(legacy={"8001": "B"})
    stats = make_chaoxing(tiku)._solve_questions(questions, context="单测")

    assert tiku.queries == []
    assert stats["sources"]["tiku"] == 0


def test_空题目列表不抛异常():
    stats = make_chaoxing(FakeSolverTiku())._solve_questions([])

    assert stats["total"] == 0
    assert stats["solved"] == 0
    assert stats["coverage"] == 0.0


def test_题库对象没有结构化解题也能跑(questions):
    """老题库实现（只有 query）不应因为缺少 solve_structured 而报错。"""
    class LegacyOnlyTiku:
        DISABLE = False
        COVER_RATE = 0.8
        true_list = ["正确"]
        false_list = ["错误"]

        def __init__(self):
            self.queries = []

        def query(self, q):
            self.queries.append(q["id"])
            return "B. 资源共享"

        def get_submit_params(self):
            return ""

    tiku = LegacyOnlyTiku()
    stats = make_chaoxing(tiku)._solve_questions(questions[:1], context="单测")

    assert questions[0]["answerField"]["answer8001"] == "B"
    assert stats["coverage"] == 100.0
