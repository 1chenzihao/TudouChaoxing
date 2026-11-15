# -*- coding: utf-8 -*-
"""风控识别与 jobCount 提速的回归测试。"""
import pytest

from api.base import Chaoxing
from api.exceptions import AntiSpiderError


class _Resp:
    def __init__(self, status=200, url="https://mooc1.chaoxing.com/x", text=""):
        self.status_code = status
        self.url = url
        self.text = text


def test_looks_blocked_by_202():
    assert Chaoxing._looks_blocked(_Resp(status=202)) is True


def test_looks_blocked_by_url():
    assert Chaoxing._looks_blocked(_Resp(url="https://mooc1.chaoxing.com/antispiderShowVerify.ac")) is True


def test_looks_blocked_by_text():
    assert Chaoxing._looks_blocked(_Resp(text="提示页面【9010】操作异常，请输入图片中的验证码")) is True


def test_normal_page_not_blocked():
    assert Chaoxing._looks_blocked(_Resp(text="<html>mArg={}</html>")) is False


class _Stub(Chaoxing):
    def __init__(self):
        pass


class _Session:
    def get(self, url, *a, **kw):
        return _Resp()


def test_get_job_list_raises_on_block(monkeypatch):
    def fake_once(self, *a, **kw):
        raise AntiSpiderError("blocked")

    monkeypatch.setattr(Chaoxing, "_fetch_job_list_once", fake_once)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    with pytest.raises(AntiSpiderError):
        Chaoxing.get_job_list(_Stub(), "1", "2", "3", "4")


def test_job_count_limits_requests(monkeypatch):
    """已知 jobCount=1 时，拿到 1 个任务点就不该再打 num=1..6。"""
    calls = {"n": 0}

    def fake_decode(html, include_passed=False):
        return ([{"jobid": "1", "type": "video"}], {})

    monkeypatch.setattr("api.base.init_session", lambda *a, **kw: _Session())
    monkeypatch.setattr("api.base.decode_course_card", fake_decode)
    monkeypatch.setattr(Chaoxing, "_looks_blocked", staticmethod(lambda resp: False))

    jobs, info = Chaoxing._fetch_job_list_once(_Stub(), "1", "2", "3", "4", job_count=1)
    assert len(jobs) == 1

def test_soft_block_empty_cards_raises(monkeypatch):
    """页面正常但卡片为 0、而 jobCount>0 → 判定软风控。"""
    from api.exceptions import AntiSpiderError

    class _S:
        def get(self, url, *a, **kw):
            return _Resp(text="mArg={};")

    monkeypatch.setattr("api.base.init_session", lambda *a, **kw: _S())
    monkeypatch.setattr("api.base.decode_course_card", lambda html, include_passed=False: ([], {}))
    monkeypatch.setattr(Chaoxing, "_looks_blocked", staticmethod(lambda resp: False))

    with pytest.raises(AntiSpiderError):
        Chaoxing._fetch_job_list_once(_Stub(), "1", "2", "3", "4", job_count=2)


def test_empty_chapter_without_jobcount_is_fine(monkeypatch):
    """jobCount 未知（0）时，空卡片仍按"空章节"处理，不能误判风控。"""

    class _S:
        def get(self, url, *a, **kw):
            return _Resp(text="mArg={};")

    monkeypatch.setattr("api.base.init_session", lambda *a, **kw: _S())
    monkeypatch.setattr("api.base.decode_course_card", lambda html, include_passed=False: ([], {}))
    monkeypatch.setattr(Chaoxing, "_looks_blocked", staticmethod(lambda resp: False))

    jobs, info = Chaoxing._fetch_job_list_once(_Stub(), "1", "2", "3", "4", job_count=0)
    assert jobs == []
    assert not info.get("invalid")
