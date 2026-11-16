# -*- coding: utf-8 -*-
"""cloud_deploy（配置工具第⑥页）的离线测试。

全部用假 session，不联网。重点验两件事：
    1. GitHub 的 401/403/404 有没有翻译成人话
    2. deploy() 的动作顺序对不对、该跳过的有没有跳过
"""
import base64
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import cloud_deploy as cd  # noqa: E402


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """按 (METHOD, url 结尾) 路由的假 session，并记录所有请求"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls.append((method, url, kw.get("json")))
        for (m, pattern), resp in self.routes.items():
            if m != method:
                continue
            # "*xxx" = 子串匹配（secrets 的 URL 末尾是 Secret 名称，没法用 endswith）
            hit = pattern[1:] in url if pattern.startswith("*") else url.endswith(pattern)
            if hit:
                return resp(url, kw.get("json")) if callable(resp) else resp
        return FakeResp(404, {"message": "Not Found"})


def _client(routes):
    s = FakeSession(routes)
    logs = []
    return cd.GitHubDeploy("me", "repo", "tok-123", session=s, log=logs.append), s, logs


# ---------------- 基础 ----------------

def test_headers_carry_token():
    h = cd._headers("abc")
    assert h["Authorization"] == "Bearer abc"
    assert h["Accept"] == "application/vnd.github+json"


def test_translate_401():
    msg = cd.GitHubDeploy._translate(FakeResp(401, {"message": "Bad credentials"}))
    assert "Token 无效" in msg


def test_translate_403_mentions_workflow_scope():
    msg = cd.GitHubDeploy._translate(FakeResp(403, {"message": "Resource not accessible"}))
    assert "workflow" in msg


def test_translate_404():
    assert "找不到这个仓库" in cd.GitHubDeploy._translate(FakeResp(404, {"message": "Not Found"}))


def test_translate_unknown_status():
    msg = cd.GitHubDeploy._translate(FakeResp(418, {"message": "I am a teapot"}))
    assert "418" in msg


def test_profile_roundtrip(tmp_path):
    p = str(tmp_path / ".cloud.json")
    assert cd.load_profile(p) == {}
    assert cd.save_profile(p, {"owner": "a", "repo": "b", "token": "t"}) is True
    assert cd.load_profile(p)["owner"] == "a"


def test_profile_broken_file_returns_empty(tmp_path):
    p = tmp_path / ".cloud.json"
    p.write_text("not json", encoding="utf-8")
    assert cd.load_profile(str(p)) == {}


# ---------------- 错误传播 ----------------

def test_401_raises_deploy_error():
    client, s, _ = _client({("GET", "/user"): FakeResp(401, {"message": "Bad credentials"})})
    with pytest.raises(cd.DeployError) as exc:
        client.repo_info()
    assert "Token" in str(exc.value)


def test_connection_error_is_wrapped():
    import requests

    class Boom:
        def request(self, *a, **kw):
            raise requests.exceptions.ConnectionError("dns fail")

    client = cd.GitHubDeploy("me", "repo", "tok", session=Boom())
    with pytest.raises(cd.DeployError) as exc:
        client.whoami()
    assert "连不上 GitHub" in str(exc.value)


# ---------------- deploy 流程 ----------------

def _happy_routes(existing_workflow=None, with_key=True):
    """搭一套"一切正常"的假 GitHub"""
    routes = {
        ("GET", "/user"): FakeResp(200, {"login": "me"}),
        ("GET", "repos/me/repo"): FakeResp(200, {
            "full_name": "me/repo", "default_branch": "main", "fork": True, "has_actions": True}),
        ("GET", "/actions/secrets/public-key"): FakeResp(200, {"key_id": "kid", "key": base64.b64encode(b"x" * 32).decode()}),
        ("GET", "/actions/secrets"): FakeResp(200, {"secrets": []}),
        ("POST", "/dispatches"): FakeResp(204, None, text=""),
        ("PUT", "*/actions/secrets/"): FakeResp(204, None, text=""),
        ("PUT", "/contents/.github/workflows/main.yml"): FakeResp(200, {"commit": {"sha": "c1"}}),
    }

    def get_contents(url, payload):
        if existing_workflow is None:
            return FakeResp(404, {"message": "Not Found"})
        body = {"encoding": "base64",
                "content": base64.b64encode(existing_workflow.encode()).decode(),
                "sha": "blobsha"}
        return FakeResp(200, body)

    routes[("GET", "/contents/.github/workflows/main.yml")] = get_contents
    return routes


def test_deploy_happy_path_order():
    client, s, logs = _client(_happy_routes(existing_workflow="old: yml"))
    ok, steps = client.deploy(
        secrets={"CHX_USER": "13800000000", "CHX_PASS": "pw", "CHX_LLM_KEY": "sk-x"},
        workflow_text="new: yml",
        run_now=True,
    )
    assert ok is True
    methods = [m for m, _u, _j in s.calls]
    # 先验身份，再传 secret，最后触发
    assert methods[0] == "GET"
    assert "PUT" in methods and "POST" in methods
    assert any("Secret 已写入" in x for x in steps)
    assert any("workflow 已上传" in x for x in steps)
    assert any("已触发一次运行" in x for x in steps)


def test_deploy_skips_identical_workflow():
    same = "name: x\non: push\n"
    client, s, _ = _client(_happy_routes(existing_workflow=same))
    ok, steps = client.deploy(secrets={"CHX_USER": "1", "CHX_PASS": "2", "CHX_LLM_KEY": "3"},
                              workflow_text=same, run_now=False)
    assert ok is True
    assert not any(m == "PUT" and u.endswith("main.yml") for m, u, _j in s.calls)
    assert any("跳过上传" in x for x in steps)


def test_deploy_requires_account():
    client, _s, _ = _client(_happy_routes())
    with pytest.raises(cd.DeployError) as exc:
        client.deploy(secrets={"CHX_USER": "", "CHX_PASS": "", "CHX_LLM_KEY": ""}, run_now=False)
    assert "账号或密码" in str(exc.value)


def test_deploy_warns_when_no_llm_key():
    client, _s, _ = _client(_happy_routes())
    _ok, steps = client.deploy(secrets={"CHX_USER": "138", "CHX_PASS": "pw", "CHX_LLM_KEY": ""},
                               run_now=False)
    assert any("随机作答" in x for x in steps)


def test_deploy_skips_none_secrets():
    """课程 ID 可以是空，空值不该被写成 'None' 字符串"""
    client, s, _ = _client(_happy_routes())
    client.deploy(secrets={"CHX_USER": "1", "CHX_PASS": "2", "CHX_LLM_KEY": "3", "CHX_COURSE": None},
                  run_now=False)
    secret_puts = [u for m, u, _j in s.calls if m == "PUT" and "/actions/secrets/" in u]
    assert len(secret_puts) == 3


def test_dispatch_failure_does_not_fail_deploy():
    """自动触发失败（比如 Actions 被限流）不该把整个部署判死"""
    routes = _happy_routes()
    routes[("POST", "/dispatches")] = FakeResp(403, {"message": "rate limited"})
    client, _s, _ = _client(routes)
    ok, steps = client.deploy(secrets={"CHX_USER": "1", "CHX_PASS": "2", "CHX_LLM_KEY": "3"},
                              run_now=True)
    assert ok is True
    assert any("手动 Run workflow" in x for x in steps)


# ---------------- 一键登录（Device Flow） ----------------

class PostSession:
    """只实现 post 的假 session，按顺序吐出预设响应"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = 0

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        self.posts += 1
        if not self.responses:
            return FakeResp(200, {"error": "expired_token"})
        return self.responses.pop(0)


def test_device_start_parses_fields():
    s = PostSession([FakeResp(200, {
        "user_code": "WDJB-MJHT", "device_code": "dev123",
        "verification_uri": "https://github.com/login/device",
        "interval": 5, "expires_in": 900})])
    d = cd.device_start(session=s)
    assert d["user_code"] == "WDJB-MJHT"
    assert d["device_code"] == "dev123"
    assert d["interval"] == 5
    assert d["verification_uri"].endswith("/login/device")


def test_device_start_http_error():
    s = PostSession([FakeResp(500, {"message": "boom"})])
    with pytest.raises(cd.DeviceFlowError) as exc:
        cd.device_start(session=s)
    assert "500" in str(exc.value)


def test_device_poll_returns_token_after_pending(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    s = PostSession([
        FakeResp(200, {"error": "authorization_pending"}),
        FakeResp(200, {"error": "authorization_pending"}),
        FakeResp(200, {"access_token": "gho_real_token"}),
    ])
    token = cd.device_poll("dev123", interval=5, expires_in=60, session=s)
    assert token == "gho_real_token"
    assert s.posts == 3


def test_device_poll_slow_down_then_ok(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    ticks = []
    s = PostSession([
        FakeResp(200, {"error": "slow_down"}),
        FakeResp(200, {"access_token": "tok"}),
    ])
    token = cd.device_poll("dev", interval=5, expires_in=60, session=s, on_tick=ticks.append)
    assert token == "tok"
    assert ticks  # 有提示回调


def test_device_poll_expired():
    import time as _t
    s = PostSession([FakeResp(200, {"error": "expired_token"})])
    with pytest.raises(cd.DeviceFlowError) as exc:
        cd.device_poll("dev", interval=5, expires_in=60, session=s)
    assert "过期" in str(exc.value)


def test_device_poll_denied():
    s = PostSession([FakeResp(200, {"error": "access_denied"})])
    with pytest.raises(cd.DeviceFlowError) as exc:
        cd.device_poll("dev", interval=5, expires_in=60, session=s)
    assert "取消" in str(exc.value)


def test_device_poll_cancel():
    s = PostSession([FakeResp(200, {"error": "authorization_pending"})] * 5)
    with pytest.raises(cd.DeviceFlowError) as exc:
        cd.device_poll("dev", interval=5, expires_in=60, session=s, should_stop=lambda: True)
    assert "取消" in str(exc.value)


def test_device_poll_does_not_sleep_in_tests(monkeypatch):
    """确认 device_poll 走的是 time.sleep，测试里能被打桩（不然 CI 会真等）"""
    calls = {"n": 0}

    def fake_sleep(sec):
        calls["n"] += 1
        assert sec >= 5

    monkeypatch.setattr("time.sleep", fake_sleep)
    s = PostSession([FakeResp(200, {"error": "authorization_pending"}),
                     FakeResp(200, {"access_token": "t"})])
    assert cd.device_poll("d", interval=5, expires_in=60, session=s) == "t"
    assert calls["n"] == 1


def test_actions_url():
    client = cd.GitHubDeploy("me", "repo", "t")
    assert client.actions_url() == "https://github.com/me/repo/actions"
