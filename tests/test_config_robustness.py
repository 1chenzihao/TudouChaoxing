# -*- coding: utf-8 -*-
"""配置/解码 的健壮性回归：填错数字、页面被截断，都不许让程序崩。"""
import os

import pytest

import main as main_module
from api.decode import decode_course_card


def test_decode_truncated_marg_is_invalid_not_crash():
    html = '<script>mArg={"note":"a};b","attachments":[]};</script>'
    jobs, info = decode_course_card(html, include_passed=True)
    assert jobs == []
    assert info.get("invalid") is True


def test_decode_missing_marg_is_invalid():
    jobs, info = decode_course_card("<html>请稍后再试</html>")
    assert jobs == []
    assert info.get("invalid") is True


def test_load_config_tolerates_bad_numbers(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[common]\nusername = 13800000000\npassword = x\nspeed = abc\nmax_chapters = xyz\n"
        "[tiku]\nprovider = TikuLLM\nkey = sk-x\ncover_rate = oops\ndelay = nope\n",
        encoding="utf-8",
    )
    common, tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["speed"] == 1.0
    assert common["max_chapters"] == 0
    assert "cover_rate" not in tiku
    assert "delay" not in tiku


def test_load_config_keeps_valid_numbers(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[common]\nusername = 13800000000\npassword = x\nspeed = 2\nmax_chapters = 3\n"
        "[tiku]\nprovider = TikuLLM\nkey = sk-x\ncover_rate = 0.8\n",
        encoding="utf-8",
    )
    common, tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["speed"] == 2.0
    assert common["max_chapters"] == 3
    assert tiku["cover_rate"] == 0.8


def test_speed_is_clamped_to_1_2(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[common]\nusername = u\npassword = p\nspeed = 9\n", encoding="utf-8")
    common, _tiku, _noti = main_module.load_config_from_file(str(cfg))
    assert common["speed"] == 2.0