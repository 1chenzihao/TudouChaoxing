# -*- coding: utf-8 -*-
"""任务点巡检：已通过的任务点要被识别出来、单独收集，且不参与执行。"""
from pathlib import Path

import pytest

from api.base import Chaoxing
from api.decode import decode_course_card

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def cards_html():
    return (FIXTURES / "cards_page.html").read_text(encoding="utf-8")


def test_已通过任务点被单独收集(cards_html):
    jobs, info = decode_course_card(cards_html, include_passed=True)
    passed = info["passed_jobs"]
    assert len(passed) == 2, "样本里有 1 个已看完的视频和 1 个已读的阅读材料"
    by_id = {item["jobid"]: item for item in passed}
    assert by_id["v-passed"]["is_passed"] is True
    assert by_id["v-passed"]["type"] == "video"
    assert by_id["v-passed"]["name"] == "1.1已看完的视频.mp4"
    assert by_id["read-1"]["type"] == "read"
    # 已通过的任务点绝不能出现在待执行列表里
    assert {job["jobid"] for job in jobs} == {"v-todo", "work-abc"}


def test_默认不返回已通过任务点(cards_html):
    """向后兼容：不传 include_passed 时行为与旧版一致。"""
    jobs, info = decode_course_card(cards_html)
    assert "passed_jobs" not in info
    assert len(jobs) == 2


def test_巡检对重复卡片去重(cards_html, monkeypatch):
    """cards 接口 num=0..6 会重复带回同一批卡片，巡检结果不能重复。"""

    class FakeSession:
        verify = False
        headers = {}
        cookies = {}

        def get(self, url, **kwargs):
            return type("Resp", (), {"text": cards_html, "status_code": 200})()

    monkeypatch.setattr("api.base.init_session", lambda *a, **kw: FakeSession())
    chaoxing = Chaoxing(account=None, tiku=None)
    jobs, info = chaoxing.get_job_list("c1", "co1", "cpi1", "kid1", include_passed=True)
    assert len(info["passed_jobs"]) == 2, "7 轮请求带回同一批卡片，去重后应只剩 2 条"
    assert {item["jobid"] for item in info["passed_jobs"]} == {"v-passed", "read-1"}


def test_不传include_passed时job_info里没有该键(cards_html, monkeypatch):
    class FakeSession:
        verify = False
        headers = {}
        cookies = {}

        def get(self, url, **kwargs):
            return type("Resp", (), {"text": cards_html, "status_code": 200})()

    monkeypatch.setattr("api.base.init_session", lambda *a, **kw: FakeSession())
    chaoxing = Chaoxing(account=None, tiku=None)
    jobs, info = chaoxing.get_job_list("c1", "co1", "cpi1", "kid1")
    assert "passed_jobs" not in info
