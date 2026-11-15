# -*- coding: utf-8 -*-
"""章节间隔可配置（chapter_delay_min / chapter_delay_max）：填错不许崩，也不能不生效。"""
import main as main_module
from api.base import Chaoxing

COURSE = {"courseId": "1", "clazzId": "2", "cpi": "3", "title": "测试课程"}


class FakeChaoxing:
    """只记录被调用了什么，不发任何请求。"""

    def __init__(self):
        self.called = []
        self.tiku = None
        self.StudyResult = Chaoxing.StudyResult

    def get_job_list(self, *args, **kwargs):
        self.called.append(("get_job_list",))
        return [], {}

    def study_emptypage(self, *args, **kwargs):
        self.called.append(("study_emptypage",))


class RecordingLogger:
    """替身 logger：把 warning 收起来，方便断言"确实打了警告"。"""

    def __init__(self):
        self.warnings = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))

    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def trace(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def test_章节间隔能按配置读出来(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[common]\nusername = 13800000000\npassword = x\n"
        "chapter_delay_min = 20\nchapter_delay_max = 60\n",
        encoding="utf-8",
    )
    common, _tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["chapter_delay_min"] == 20
    assert common["chapter_delay_max"] == 60
    assert isinstance(common["chapter_delay_min"], float)


def test_min大于max时回退3到9并打warning(monkeypatch, tmp_path):
    logger = RecordingLogger()
    monkeypatch.setattr(main_module, "logger", logger)

    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[common]\nusername = 13800000000\npassword = x\n"
        "chapter_delay_min = 30\nchapter_delay_max = 10\n",
        encoding="utf-8",
    )
    common, _tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["chapter_delay_min"] == 3.0
    assert common["chapter_delay_max"] == 9.0
    assert any("chapter_delay" in w for w in logger.warnings)


def test_章节间隔填了非数字也回退(monkeypatch, tmp_path):
    logger = RecordingLogger()
    monkeypatch.setattr(main_module, "logger", logger)

    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[common]\nusername = 13800000000\npassword = x\n"
        "chapter_delay_min = abc\nchapter_delay_max = 20\n",
        encoding="utf-8",
    )
    common, _tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["chapter_delay_min"] == 3.0
    assert common["chapter_delay_max"] == 9.0
    assert any("chapter_delay" in w for w in logger.warnings)


def test_没配章节间隔时默认3到9(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[common]\nusername = 13800000000\npassword = x\n", encoding="utf-8")
    common, _tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["chapter_delay_min"] == 3.0
    assert common["chapter_delay_max"] == 9.0


def test_process_chapter用配置的间隔(monkeypatch):
    """写死的 random.uniform(3, 9) 必须真的换成配置值。"""
    slept = []
    monkeypatch.setattr(main_module.random, "uniform", lambda a, b: slept.append((a, b)) or 0)
    monkeypatch.setattr(main_module.time, "sleep", lambda seconds: None)

    chaoxing = FakeChaoxing()
    point = {"id": "p1", "title": "第一章", "has_finished": False}
    main_module.process_chapter(
        chaoxing, COURSE, point, main_module.RollBackManager(), "retry", 2.0,
        chapter_delay_min=20, chapter_delay_max=60,
    )
    assert (20, 60) in slept
