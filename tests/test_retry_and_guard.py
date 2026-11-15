# -*- coding: utf-8 -*-
"""重试清单 / 异常页守卫 / 图形工具定时写入 的回归测试。"""
import importlib.util
import json
import os
import re
import sys

import pytest

import main as main_module
from api.base import Chaoxing
from api.decode import decode_course_card


@pytest.fixture()
def pending_file(tmp_path, monkeypatch):
    path = tmp_path / "retry_pending.json"
    monkeypatch.setattr(main_module, "PENDING_FILE", str(path))
    return path


def test_pending_roundtrip(pending_file):
    course = {"courseId": "100", "title": "测试课"}
    point = {"id": "200", "title": "1.1 绪论"}
    job = {"jobid": "300", "name": "1.1.mp4", "type": "video"}

    main_module._add_pending(course, point, job)
    data = main_module._load_pending()
    assert data["100"]["chapters"]["200"]["jobs"] == ["300"]

    main_module._drop_pending(course, point, job)
    assert main_module._load_pending() == {}


def test_pending_bad_json_is_tolerated(pending_file):
    pending_file.write_text("{ 这不是 json", encoding="utf-8")
    assert main_module._load_pending() == {}


def test_decode_marks_invalid_when_no_marg():
    jobs, info = decode_course_card("<html><body>请稍后再试</body></html>")
    assert jobs == []
    assert info.get("invalid") is True


class _Stub(Chaoxing):
    """继承 Chaoxing 但跳过 __init__（不登录），_fetch_job_list_once 由 monkeypatch 提供。"""

    def __init__(self):
        pass


def test_get_job_list_retries_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def fake_once(self, *a, **kw):
        calls["n"] += 1
        return [], {"invalid": True}

    monkeypatch.setattr(Chaoxing, "_fetch_job_list_once", fake_once)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    jobs, info = Chaoxing.get_job_list(_Stub(), "1", "2", "3", "4")
    assert jobs == []
    assert info.get("invalid") is True
    assert calls["n"] == 3


def test_get_job_list_stops_retrying_when_valid(monkeypatch):
    state = {"n": 0}

    def fake_once(self, *a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return [], {"invalid": True}
        return [{"jobid": "1", "type": "video"}], {}

    monkeypatch.setattr(Chaoxing, "_fetch_job_list_once", fake_once)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    jobs, info = Chaoxing.get_job_list(_Stub(), "1", "2", "3", "4")
    assert len(jobs) == 1
    assert info == {}
    assert state["n"] == 2


def _load_gui(repo_root):
    path = os.path.join(repo_root, "配置工具.pyw")
    spec = importlib.util.spec_from_file_location("tudou_gui_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gui_write_workflow_replaces_whole_schedule(tmp_path, monkeypatch):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gui = _load_gui(repo)
    wf = tmp_path / "main.yml"
    wf.write_text(
        "name: t\non:\n  schedule:\n    - cron: '0 17 * * *'\n    - cron: '0 4 * * *'\n"
        "  workflow_dispatch:\njobs:\n  a:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gui, "WORKFLOW", str(wf))
    ok, _msg = gui.write_workflow(2, 30, 300, True, 0)
    text = wf.read_text(encoding="utf-8")
    assert ok
    assert len(re.findall(r"^\s*-\s*cron:", text, re.M)) == 1, "只应剩一条 cron"
    assert "30 18 * * *" in text, "北京 02:30 应换算成 UTC 18:30"
    assert "timeout-minutes: 300" in text
    assert "\x01" not in text, "不能写入控制字符"


def test_gui_write_workflow_disabled_comments_it_out(tmp_path, monkeypatch):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gui = _load_gui(repo)
    wf = tmp_path / "main.yml"
    wf.write_text("name: t\non:\n  workflow_dispatch:\njobs:\n  a:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
    monkeypatch.setattr(gui, "WORKFLOW", str(wf))
    gui.write_workflow(1, 0, 350, False)
    text = wf.read_text(encoding="utf-8")
    assert not re.search(r"^\s*-\s*cron:", text, re.M), "关闭后不应有生效的 cron"
    assert "# schedule" in text or "#   - cron" in text

def test_gui_write_workflow_two_rounds(tmp_path, monkeypatch):
    """gap>0 时应写两条 cron：视频轮 + 测验轮。"""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gui = _load_gui(repo)
    wf = tmp_path / "main.yml"
    wf.write_text("name: t\non:\n  schedule:\n    - cron: '0 17 * * *'\n  workflow_dispatch:\njobs:\n  a:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
    monkeypatch.setattr(gui, "WORKFLOW", str(wf))
    ok, msg = gui.write_workflow(2, 30, 300, True, 6)
    text = wf.read_text(encoding="utf-8")
    crons = re.findall(r"^\s*-\s*cron:\s*'([^']+)'", text, re.M)
    assert ok
    assert crons == ["30 18 * * *", "30 0 * * *"], crons
    assert "视频轮" in msg and "测验轮" in msg
    assert "\x01" not in text
