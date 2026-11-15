# -*- coding: utf-8 -*-
"""任务点分派：只做章节测验与作业，考试任务点必须零请求放过。"""
import main as main_module
from api.base import Chaoxing

COURSE = {"courseId": "1", "clazzId": "2", "cpi": "3", "title": "测试课程"}
JOB_INFO = {"knowledgeid": "9", "ktoken": "k", "cpi": "3"}


def make_job(jobid, job_type="workid"):
    return {"type": job_type, "jobid": jobid, "enc": "enc", "otherinfo": "nodeId_1-2"}


class FakeChaoxing:
    """只记录被调用了什么，不发任何请求。"""

    def __init__(self):
        self.called = []
        self.StudyResult = Chaoxing.StudyResult

    def study_work(self, course, job, job_info):
        self.called.append(("study_work", job["jobid"]))
        return self.StudyResult.SUCCESS

    def study_video(self, *args, **kwargs):
        self.called.append(("study_video",))
        return self.StudyResult.SUCCESS

    def study_document(self, *args, **kwargs):
        self.called.append(("study_document",))
        return self.StudyResult.SUCCESS

    def strdy_read(self, *args, **kwargs):
        self.called.append(("strdy_read",))
        return self.StudyResult.SUCCESS


def test_work前缀走章节测验():
    chaoxing = FakeChaoxing()
    main_module.process_job(chaoxing, COURSE, make_job("work-abc"), JOB_INFO, 1.0)
    assert chaoxing.called == [("study_work", "work-abc")]


def test_exam前缀零请求():
    """考试任务点不进 study_work，也不触发任何下游调用。"""
    chaoxing = FakeChaoxing()
    main_module.process_job(chaoxing, COURSE, make_job("exam-123456"), JOB_INFO, 1.0)
    assert chaoxing.called == []


def test_solve_only只放行章节测验():
    chaoxing = FakeChaoxing()
    main_module.process_job(chaoxing, COURSE, make_job("work-1"), JOB_INFO, 1.0, solve_only=True)
    main_module.process_job(chaoxing, COURSE, make_job("v1", "video"), JOB_INFO, 1.0, solve_only=True)
    main_module.process_job(chaoxing, COURSE, make_job("d1", "document"), JOB_INFO, 1.0, solve_only=True)
    assert chaoxing.called == [("study_work", "work-1")]


def test_study_work收到exam任务点直接放过(monkeypatch):
    """base.study_work 的兜底保险：绝不拿 exam 的 workId 去请求测验接口。"""

    def boom(*args, **kwargs):
        raise AssertionError("考试任务点不该发起任何请求")

    monkeypatch.setattr("api.base.init_session", boom)

    class StubTiku:
        DISABLE = False
        COVER_RATE = 0.8
        SUBMIT = True
        true_list = ["正确", "对"]
        false_list = ["错误", "错"]

        def get_submit_params(self):
            return "1"

    chaoxing = Chaoxing(account=None, tiku=StubTiku())
    result = chaoxing.study_work(COURSE, make_job("exam-999"), JOB_INFO)
    assert result == Chaoxing.StudyResult.SUCCESS
