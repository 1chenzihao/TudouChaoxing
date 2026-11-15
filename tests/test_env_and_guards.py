# -*- coding: utf-8 -*-
"""环境安装脚本 + 本轮 bug 修复的回归测试。

对应这几件事：
    * 前端「环境」页用的 env_setup.py（镜像挑选、包名映射、输出压缩）
    * --solve-only 必须能压掉配置里的 video_only
    * 长休息参数解析、课程 ID 去空格、重试清单路径固定
    * 视频上报状态码不能被一律当成 403
    * 测验抓页必须走节流 session
"""
import inspect
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import env_setup  # noqa: E402
import main as main_mod  # noqa: E402
from api.base import Chaoxing  # noqa: E402
from api.exceptions import AntiSpiderError  # noqa: E402


# ---------------- env_setup ----------------

def test_module_table_has_pip_names():
    """每个必需包都要有 pip 名，否则装环境时会漏装。"""
    mods = env_setup.MODULES
    assert mods, "模块表不能为空"
    for item in mods:
        assert len(item) == 4
        imp, pip_name, desc, required = item
        assert imp and pip_name and desc
        assert isinstance(required, bool)
    names = {m[1] for m in mods}
    for need in ("requests", "pyaes", "beautifulsoup4", "lxml", "loguru",
                 "fonttools", "openai", "httpx"):
        assert need in names


def test_stdlib_not_listed_as_dependency():
    """argparse 是标准库，不能出现在依赖表里（PyPI 同名包会污染标准库）。"""
    names = {m[1] for m in env_setup.MODULES}
    assert "argparse" not in names


def test_missing_modules_shape():
    rows = env_setup.missing_modules([("sys", "sys", "标准库", True)])
    assert rows[0]["installed"] is True
    assert rows[0]["required"] is True
    rows = env_setup.missing_modules([("not_exist_xyz", "not-exist-xyz", "假的", False)])
    assert rows[0]["installed"] is False


def test_mirrors_are_domestic_first():
    """第一个镜像必须是国内的，官方 PyPI 只放最后兜底。"""
    urls = [m[2] for m in env_setup.MIRRORS]
    assert "pypi.org" in urls[-1]
    assert all("pypi.org" not in u for u in urls[:-1])
    assert len(urls) >= 4


def test_resolve_mirror_by_key_and_name():
    assert env_setup.resolve_mirror("tsinghua", log=None)[1].startswith("https://pypi.tuna")
    assert env_setup.resolve_mirror("清华 TUNA", log=None)[1].startswith("https://pypi.tuna")


def test_host_strips_scheme_and_path():
    assert env_setup._host("https://repo.huaweicloud.com/repository/pypi/simple/") == "repo.huaweicloud.com"
    assert env_setup._host("") == ""


def test_short_pip_compacts_version():
    assert env_setup._short_pip("pip 26.2.2 from C:\\x\\y (python 3.14)") == "pip 26.2.2"
    assert env_setup._short_pip("") == ""


def test_python_urls_are_domestic():
    for name, tpl in env_setup.PYTHON_URLS:
        assert "python.org" not in tpl, name
        assert "{v}" in tpl


def test_pip_cmd_uses_mirror_and_trusted_host():
    cmd = env_setup._pip_cmd(packages=["six"], mirror_url="https://pypi.tuna.tsinghua.edu.cn/simple")
    assert "-i" in cmd and "https://pypi.tuna.tsinghua.edu.cn/simple" in cmd
    assert "--trusted-host" in cmd
    assert cmd[cmd.index("--trusted-host") + 1] == "pypi.tuna.tsinghua.edu.cn"
    assert "--prefer-binary" in cmd


def test_pip_cmd_skips_retry_loops_without_mirror():
    cmd = env_setup._pip_cmd(packages=["six"])
    assert "-i" not in cmd


def test_report_compact_fits_gui():
    """前端那个框只能放十来行，压缩模式必须短。"""
    lines = []
    info = env_setup.check_all(include_web=True)
    env_setup.report(info, log=lines.append, compact=True)
    assert len(lines) <= 12, lines


# ---------------- 运行模式优先级 ----------------

def _write_cfg(tmp_path, text):
    p = tmp_path / "c.ini"
    p.write_text(text, encoding="utf-8")
    return p


def test_solve_only_overrides_video_only_in_file(tmp_path, monkeypatch):
    """命令行给 --solve-only 时，配置里的 video_only 必须被压掉，否则会静默刷视频。"""
    cfg = _write_cfg(tmp_path, "[common]\nusername=13800000000\npassword=x\nvideo_only = true\nsolve_only = false\n")
    monkeypatch.setattr(sys, "argv", ["main.py", "-c", str(cfg), "--solve-only"])
    common, _, _ = main_mod.init_config()
    assert common["solve_only"] is True
    assert common["video_only"] is False


def test_video_only_overrides_solve_only_in_file(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path, "[common]\nusername=13800000000\npassword=x\nsolve_only = true\n")
    monkeypatch.setattr(sys, "argv", ["main.py", "-c", str(cfg), "--video-only"])
    common, _, _ = main_mod.init_config()
    assert common["video_only"] is True
    assert common["solve_only"] is False


def test_rest_defaults_when_missing(tmp_path):
    cfg = _write_cfg(tmp_path, "[common]\nusername=a\npassword=b\n")
    common, _, _ = main_mod.load_config_from_file(str(cfg))
    assert common["rest_every"] == main_mod.DEFAULT_REST_EVERY
    assert common["rest_min"] == main_mod.DEFAULT_REST_MIN
    assert common["rest_max"] == main_mod.DEFAULT_REST_MAX


def test_rest_parsed_from_config(tmp_path):
    cfg = _write_cfg(
        tmp_path,
        "[common]\nusername=a\npassword=b\nrest_every = 3\nrest_min = 10\nrest_max = 20\n",
    )
    common, _, _ = main_mod.load_config_from_file(str(cfg))
    assert (common["rest_every"], common["rest_min"], common["rest_max"]) == (3, 10.0, 20.0)


def test_rest_junk_falls_back_to_defaults(tmp_path):
    cfg = _write_cfg(tmp_path, "[common]\nusername=a\npassword=b\nrest_every = 很多\nrest_max = 1\nrest_min = 99\n")
    common, _, _ = main_mod.load_config_from_file(str(cfg))
    assert common["rest_every"] == main_mod.DEFAULT_REST_EVERY
    assert common["rest_min"] <= common["rest_max"]


def test_pending_file_is_absolute_next_to_source():
    """重试清单必须固定跟着代码目录走，不能依赖 cwd（云端 cache 才对得上）。"""
    assert os.path.isabs(main_mod.PENDING_FILE)
    assert os.path.dirname(main_mod.PENDING_FILE) == os.path.dirname(os.path.abspath(main_mod.__file__))


# ---------------- 课程筛选 ----------------

def test_filter_courses_strips_spaces(capsys):
    courses = [{"courseId": "111", "title": "A"}, {"courseId": "222", "title": "B"}]
    picked = main_mod.filter_courses(courses, [" 222 "])
    assert [c["courseId"] for c in picked] == ["222"]


def test_filter_courses_falls_back_to_all_with_warning():
    courses = [{"courseId": "111", "title": "A"}]
    picked = main_mod.filter_courses(courses, ["999"])
    assert [c["courseId"] for c in picked] == ["111"]


def test_filter_courses_empty_list_asks_then_uses_input(monkeypatch):
    """course_list 为空是"交互式选课"，不是 bug：喂一行输入应该按输入筛。"""
    courses = [{"courseId": "111", "title": "A"}, {"courseId": "222", "title": "B"}]
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "222")
    picked = main_mod.filter_courses(courses, None)
    assert [c["courseId"] for c in picked] == ["222"]


# ---------------- 视频上报状态码 ----------------

class _Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _VideoSession:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    def get(self, url, *a, **kw):
        self.calls += 1
        return _Resp(status=self.status)


class _Stub(Chaoxing):
    def __init__(self):
        pass

    def get_uid(self):
        return "1"

    def get_fid(self):
        return "1"


def _phone_job():
    return {"jobid": "v1", "objectid": "o1", "otherinfo": "nodeId_1-x",
            "name": "1.1.mp4", "jtoken": "jt", "enc": "e"}


def _course():
    return {"cpi": "1", "clazzId": "2", "courseId": "3"}


def test_video_log_500_is_not_reported_as_403():
    """500 只是网络抖动，报成 403 会把一次抖动升级成 5 分钟风控冷却。"""
    stub = _Stub()
    payload, state = stub.video_progress_log(
        _VideoSession(500), _course(), _phone_job(), {}, "dt", 100, 0
    )
    assert state == 500
    assert payload["isPassed"] is False


def test_video_log_403_still_forbidden():
    stub = _Stub()
    _, state = stub.video_progress_log(_VideoSession(403), _course(), _phone_job(), {}, "dt", 100, 0)
    assert state == 403


def test_video_log_200_non_json_is_failure():
    """200 但返回 HTML（风控页）时不能当成上报成功。"""
    stub = _Stub()
    payload, state = stub.video_progress_log(_VideoSession(200), _course(), _phone_job(), {}, "dt", 100, 0)
    assert state == 502
    assert payload["isPassed"] is False


# ---------------- 测验抓页必须节流 ----------------

def test_study_work_uses_throttle_session():
    """曾经这里用的是裸 requests.get，绕过了全局 5 秒间隔。"""
    src = inspect.getsource(Chaoxing.study_work)
    assert "requests.get(" not in src
    assert "_session.get(" in src


def test_strdy_read_raises_on_block(monkeypatch):
    class _S:
        def get(self, *a, **kw):
            return _Resp(status=200, text="提示页面【9010】操作异常，请输入图片中的验证码")

    monkeypatch.setattr("api.base.init_session", lambda *a, **kw: _S())
    with pytest.raises(AntiSpiderError):
        Chaoxing.strdy_read(_Stub(), _course(), _phone_job(), {"knowledgeid": "1"})


# ---------------- lxml 缺失兜底 ----------------

def test_soup_falls_back_without_lxml(monkeypatch):
    import bs4
    from api import decode

    real = bs4.BeautifulSoup

    def fake(html, features=None, **kw):
        if features == "lxml":
            raise bs4.FeatureNotFound("no lxml")
        return real(html, "html.parser")

    monkeypatch.setattr(decode, "BeautifulSoup", fake)
    soup = decode._soup("<html><body><p>土豆</p></body></html>")
    assert "土豆" in soup.get_text()
