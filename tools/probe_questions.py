# -*- coding: utf-8 -*-
"""课程可刷性探测：取一门课的章节测验，统计「含图片公式的题」占比。

文本模型看不到图片，图片题只能留空；占比重了这课就没法自动刷。
只读：取题页面 GET，绝不提交。

用法： CHX_USER=手机号 CHX_PASS=密码 python tools/probe_questions.py 227576953 266887717 ...
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from api.base import Account, Chaoxing, init_session
from api.config import GlobalConst as gc
from api.decode import decode_questions_info


def fetch_questions(chaoxing, course, job, job_info):
    """复刻 study_work 的取题请求（只 GET 题目页，不提交任何东西）"""
    session = init_session()
    headers = {
        "Host": "mooc1.chaoxing.com",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": gc.HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    resp = session.get(
        "https://mooc1.chaoxing.com/mooc-ans/api/work",
        headers=headers,
        cookies=session.cookies.get_dict(),
        verify=False,
        params={
            "api": "1",
            "workId": job["jobid"].replace("work-", ""),
            "jobid": job["jobid"],
            "originJobId": job["jobid"],
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": str(job_info["knowledgeid"]),
            "ktoken": job_info["ktoken"],
            "cpi": job_info["cpi"],
            "ut": "s",
            "clazzId": course["clazzId"],
            "type": "",
            "enc": job["enc"],
            "mooc2": "1",
            "courseid": course["courseId"],
        },
    )
    if resp.status_code != 200:
        return None
    return decode_questions_info(resp.text)


def main() -> int:
    course_ids = [a for a in sys.argv[1:] if a.strip()]
    if not course_ids:
        print("用法: python tools/probe_questions.py 课程ID [课程ID ...]")
        return 2
    username = os.environ.get("CHX_USER", "")
    password = os.environ.get("CHX_PASS", "")
    if not username or not password:
        print("请用环境变量 CHX_USER / CHX_PASS 传账号密码")
        return 2

    chaoxing = Chaoxing(account=Account(username, password))
    if not chaoxing.login().get("status"):
        print("登录失败")
        return 1

    courses = {c["courseId"]: c for c in chaoxing.get_course_list()}
    print(f"{'课程ID':>12}  {'题数':>4} {'图片题':>5} {'占比':>6}  结论")
    print("-" * 78)
    for cid in course_ids:
        course = courses.get(cid)
        if not course:
            print(f"{cid:>12}  课程不在列表里")
            continue
        point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
        pending = [p for p in point_list.get("points", []) if not p.get("has_finished")]
        probed = False
        for point in pending[:8]:
            jobs, job_info = chaoxing.get_job_list(
                course["clazzId"], course["courseId"], course["cpi"], point["id"]
            )
            works = [j for j in jobs if j.get("type") == "workid"]
            if not works or not job_info.get("knowledgeid"):
                continue
            data = fetch_questions(chaoxing, course, works[0], job_info)
            if not data or not data.get("questions"):
                continue
            questions = data["questions"]
            imgs = sum(
                1 for q in questions
                if "<img" in f"{q.get('title', '')}{q.get('options', '')}"
            )
            ratio = imgs / len(questions) * 100
            verdict = "可刷" if ratio <= 20 else ("慎刷(图片题多)" if ratio <= 60 else "不建议(几乎全是图片题)")
            print(f"{cid:>12}  {len(questions):>4} {imgs:>5} {ratio:>5.0f}%  {verdict}   《{course['title'][:18]}》 {point['title'][:16]}")
            probed = True
            break
        if not probed:
            print(f"{cid:>12}  未找到可探测的测验章节   《{course['title'][:18]}》")
    print("-" * 78)
    print("说明：只读取了题目页做统计，没有提交任何答案")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
