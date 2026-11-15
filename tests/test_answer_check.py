# -*- coding: utf-8 -*-
"""答案校验与判断题判定的离线回归测试。

关于 cut() 的契约（重要，别按直觉改）：
cut() 的切分符列表里 "\n" 排第一，并且**第一个能切出非空段的字符就直接返回**。
只要字符串里没有 "\n"，split 出来的就是整串本身（非空），于是逗号、顿号、竖线等
永远不会被用到。api/base.py 的 multi_cut 注释写明这是有意设计：选项正文里经常带逗号，
按逗号优先切会把一个选项切成两段，多选题准确率反而下降。
副作用是单字母校验对 "A,B" 是宽松的（视为一个 token），这是既有行为，本文件把它固定下来。
"""
import pytest

from api.answer import Tiku
from api.answer_check import (
    check_answer,
    check_completion,
    check_judgement,
    check_multiple,
    check_single,
    cut,
)


class FakeTiku:
    """只带 true_list / false_list 的题库替身，check_answer 用到的就这些。"""

    def __init__(self, true_list=None, false_list=None):
        self.true_list = true_list if true_list is not None else ["正确", "对", "√", "是"]
        self.false_list = false_list if false_list is not None else ["错误", "错", "×", "否"]


@pytest.fixture
def tiku():
    return FakeTiku()


# ---------------------------------------------------------------- cut 切分


def test_cut_按换行切分():
    assert cut("A\nB") == ["A", "B"]


def test_cut_过滤空白段():
    assert cut("A\n\nB") == ["A", "B"]
    assert cut("A\n  \nB") == ["A", "B"]


def test_cut_没有换行时不做二次切分():
    """换行优先是有意设计：避免选项正文里的逗号/顿号把选项切坏。"""
    assert cut("A,B") == ["A,B"]
    assert cut("A，B") == ["A，B"]
    assert cut("A、B") == ["A、B"]


def test_cut_换行优先且保留逗号内容_契约测试():
    """固定契约：有换行就只按换行切，其他分隔符一律不参与。"""
    assert cut("A,B\nC") == ["A,B", "C"]


def test_cut_空答案返回None():
    assert cut("") is None
    assert cut("   ") is None


def test_cut_单字符答案():
    assert cut("A") == ["A"]


# ---------------------------------------------------------------- 单题校验


@pytest.mark.parametrize("answer", ["A", "B", "ABCD", "a"])
def test_check_single_单token通过(answer):
    assert check_single(answer) is True


@pytest.mark.parametrize("answer", ["A,B", "A,B,C", "A，B"])
def test_check_single_带逗号也通过_因换行优先(answer):
    """无换行时整串算一个 token，长度 1，所以判为合法单选。宽松但不会误伤。"""
    assert check_single(answer) is True


@pytest.mark.parametrize("answer", ["", "A\nB", "\n"])
def test_check_single_拒绝(answer):
    assert check_single(answer) is False


@pytest.mark.parametrize("answer", ["A", "A,B", "ABCD", "A\nB"])
def test_check_multiple_通过(answer):
    assert check_multiple(answer) is True


@pytest.mark.parametrize("answer", ["", "   "])
def test_check_multiple_拒绝(answer):
    assert check_multiple(answer) is False


def test_check_completion_通过与拒绝():
    assert check_completion("传输层") is True
    assert check_completion("") is False


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("正确", 1),
        ("对", 1),
        ("错误", 0),
        ("×", 0),
        ("A", -1),
        ("", -1),
    ],
)
def test_check_judgement_三态判定(answer, expected):
    tiku = FakeTiku()
    assert check_judgement(answer, tiku.true_list, tiku.false_list) == expected


# ---------------------------------------------------------------- 综合校验


@pytest.mark.parametrize("answer", ["A", "B", "D"])
def test_check_answer_单选题通过(tiku, answer):
    assert check_answer(answer, "single", tiku) is True


@pytest.mark.parametrize("answer", ["", "A\nB", "正确"])
def test_check_answer_单选题拒绝(tiku, answer):
    """判断题词表里的值不能被当成单选答案；空答案与多 token 也不行。"""
    assert check_answer(answer, "single", tiku) is False


@pytest.mark.parametrize("answer", ["AB", "A,B", "A"])
def test_check_answer_多选题通过(tiku, answer):
    assert check_answer(answer, "multiple", tiku) is True


@pytest.mark.parametrize("answer", ["", "正确"])
def test_check_answer_多选题拒绝(tiku, answer):
    assert check_answer(answer, "multiple", tiku) is False


@pytest.mark.parametrize("answer", ["传输层", "应用层/传输层"])
def test_check_answer_填空题通过(tiku, answer):
    assert check_answer(answer, "completion", tiku) is True


def test_check_answer_填空题拒绝(tiku):
    assert check_answer("", "completion", tiku) is False


@pytest.mark.parametrize("answer", ["正确", "错误", "对", "×"])
def test_check_answer_判断题通过(tiku, answer):
    assert check_answer(answer, "judgement", tiku) is True


@pytest.mark.parametrize("answer", ["A", "B", ""])
def test_check_answer_判断题拒绝(tiku, answer):
    assert check_answer(answer, "judgement", tiku) is False


def test_check_answer_未知题型放行(tiku):
    """简答题等未知题型不做校验，一律放行。"""
    assert check_answer("任意内容", "shortanswer", tiku) is True
    assert check_answer("", "unknown", tiku) is True


def test_check_answer_自定义真假列表生效():
    tiku = FakeTiku(true_list=["T"], false_list=["F"])
    assert check_answer("T", "judgement", tiku) is True
    assert check_answer("F", "judgement", tiku) is True
    assert check_answer("正确", "judgement", tiku) is False


# ---------------------------------------------------------------- 判断题判定


@pytest.mark.parametrize("answer", ["正确", "对", "√", "是"])
def test_judgement_select_判定为真(answer):
    tiku = Tiku()
    tiku.true_list = ["正确", "对", "√", "是"]
    tiku.false_list = ["错误", "错", "×", "否"]
    assert tiku.judgement_select(answer) is True


@pytest.mark.parametrize("answer", ["错误", "错", "×", "否"])
def test_judgement_select_判定为假(answer):
    tiku = Tiku()
    tiku.true_list = ["正确", "对", "√", "是"]
    tiku.false_list = ["错误", "错", "×", "否"]
    assert tiku.judgement_select(answer) is False


def test_judgement_select_去首尾空格():
    tiku = Tiku()
    tiku.true_list = ["正确"]
    tiku.false_list = ["错误"]
    assert tiku.judgement_select("  正确  ") is True


def test_judgement_select_无法判断时随机且仍是布尔值():
    tiku = Tiku()
    tiku.true_list = ["正确"]
    tiku.false_list = ["错误"]
    results = {tiku.judgement_select("看不懂的答案") for _ in range(50)}
    assert results <= {True, False}
    assert isinstance(tiku.judgement_select("看不懂的答案"), bool)


def test_judgement_select_题库停用时返回False():
    tiku = Tiku()
    tiku.DISABLE = True
    tiku.true_list = ["正确"]
    tiku.false_list = ["错误"]
    assert tiku.judgement_select("正确") is False


def test_get_submit_params_提交与保存两种模式():
    tiku = Tiku()
    tiku.SUBMIT = True
    assert tiku.get_submit_params() == ""
    tiku.SUBMIT = False
    assert tiku.get_submit_params() == "1"
