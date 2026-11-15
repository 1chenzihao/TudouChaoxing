# -*- coding: utf-8 -*-
"""只刷视频模式（--video-only）：视频照刷，章节测验与作业一个请求都不发。

分两轮刷的收尾保险：
    第一轮 --video-only 只把视频/文档/阅读走完，测验全部留给第二轮 --solve-only。
    这里用假 chaoxing 打桩，全程不联网、不发请求。
"""
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


def test_video_only不调用study_work():
    """video_only=True 时，测验任务点必须零请求放过（留给下一轮 solve_only）。"""
    chaoxing = FakeChaoxing()
    main_module.process_job(chaoxing, COURSE, make_job("work-abc"), JOB_INFO, 1.0, video_only=True)
    main_module.process_job(chaoxing, COURSE, make_job("work-def"), JOB_INFO, 1.0, video_only=True)
    assert chaoxing.called == []


def test_video_only仍然处理视频和文档阅读():
    """只刷视频不是"什么都不刷"：视频/文档/阅读照常处理，测验跳过。"""
    chaoxing = FakeChaoxing()
    main_module.process_job(chaoxing, COURSE, make_job("v1", "video"), JOB_INFO, 1.0, video_only=True)
    main_module.process_job(chaoxing, COURSE, make_job("d1", "document"), JOB_INFO, 1.0, video_only=True)
    main_module.process_job(chaoxing, COURSE, make_job("r1", "read"), JOB_INFO, 1.0, video_only=True)
    main_module.process_job(chaoxing, COURSE, make_job("work-1"), JOB_INFO, 1.0, video_only=True)
    assert chaoxing.called == [("study_video",), ("study_document",), ("strdy_read",)]
    assert not any(call[0] == "study_work" for call in chaoxing.called)


def test_不加开关时两者都照常做():
    """默认（两个开关都关）行为不能被收尾改动带坏。"""
    chaoxing = FakeChaoxing()
    main_module.process_job(chaoxing, COURSE, make_job("v1", "video"), JOB_INFO, 1.0)
    main_module.process_job(chaoxing, COURSE, make_job("work-1"), JOB_INFO, 1.0)
    assert chaoxing.called == [("study_video",), ("study_work", "work-1")]


def test_两个参数同时给出时video_only生效(monkeypatch):
    """--video-only 与 --solve-only 同时给：以 video_only 为准，solve_only 被压掉。"""
    monkeypatch.setattr(main_module.sys, "argv", ["main.py", "--video-only", "--solve-only"])
    args = main_module.parse_args()
    # argparse 的 dest 必须是 video_only，main.py 里的 getattr 才拿得到
    assert args.video_only is True
    assert args.solve_only is True

    common_config, _, _ = main_module.build_config_from_args(args)
    assert common_config["video_only"] is True
    assert common_config["solve_only"] is False


def test_配置文件里的solve_only被命令行video_only压掉(monkeypatch, tmp_path):
    """带 -c 跑时，命令行 --video-only 必须盖住 config.ini 里的 solve_only = true。"""
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[common]\nusername = 13800000000\npassword = pw\nsolve_only = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        main_module.sys, "argv", ["main.py", "-c", str(config_path), "--video-only"]
    )
    common_config, _, _ = main_module.init_config()
    assert common_config["video_only"] is True
    assert common_config["solve_only"] is False
