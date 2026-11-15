# -*- coding: utf-8 -*-
"""请求节流的回归测试。"""
import time

import api.base as base


def test_throttle_keeps_gap_between_requests(monkeypatch):
    stamps = []

    def fake_request(self, method, url, **kwargs):
        stamps.append(time.time())
        return None

    monkeypatch.setattr("requests.Session.request", fake_request)
    monkeypatch.setattr(base, "_MIN_REQUEST_GAP", 0.15)
    monkeypatch.setattr(base, "_last_request_at", 0.0)

    s = base.ThrottleSession()
    for _ in range(3):
        s.request("GET", "https://example.com")

    assert len(stamps) == 3
    gaps = [round(stamps[i + 1] - stamps[i], 3) for i in range(2)]
    assert all(g >= 0.15 for g in gaps), gaps