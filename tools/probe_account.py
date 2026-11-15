# -*- coding: utf-8 -*-
"""只读探针：登录学习通 -> 列课程 -> 列章节 -> 列第一个未完成章节的任务点。

**不发任何学习/提交请求**，只做 GET，用来确认账号、课程 ID、任务点形态是否正确。
账号密码从环境变量读，避免落盘：
    CHX_USER=手机号 CHX_PASS=密码 python tools/probe_account.py -l 课程ID
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.base import Chaoxing, Account


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--list", default="", help="课程 ID，留空则列全部课程")
    parser.add_argument("--chapters", type=int, default=5, help="最多列几个章节")
    parser.add_argument("--scan", action="store_true", help="扫描所有课程，报告未完成章节数与首章任务点类型")
    args = parser.parse_args()

    username = os.environ.get("CHX_USER", "")
    password = os.environ.get("CHX_PASS", "")
    if not username or not password:
        print("[失败] 请用环境变量 CHX_USER / CHX_PASS 传账号密码")
        return 2

    chaoxing = Chaoxing(account=Account(username, password))
    state = chaoxing.login()
    if not state.get("status"):
        print(f"[失败] 登录失败：{state.get('msg')}")
        return 1
    print(f"[1] 登录成功")

    courses = chaoxing.get_course_list()
    print(f"[2] 课程数：{len(courses)}")
    for course in courses:
        mark = " <== 目标" if args.list and course["courseId"] == args.list else ""
        print(f"    {course['courseId']:>12}  {course['title'][:34]:<36} 教师:{course.get('teacher', '')[:10]}{mark}")

    if args.scan:
        print("\n[扫描] 找出还有未完成章节的课程（每门课只读一次章节列表）：")
        import time as _time
        rows = []
        for course in courses:
            _time.sleep(0.4)
            try:
                pl = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
            except Exception as exc:  # noqa: BLE001
                print(f"    {course['courseId']:>12} 读取失败: {exc}")
                continue
            points = pl.get("points", [])
            pending = [p for p in points if not p["has_finished"]]
            if not pending:
                continue
            first = pending[0]
            kinds = ""
            try:
                _time.sleep(0.3)
                jobs, _info = chaoxing.get_job_list(
                    course["clazzId"], course["courseId"], course["cpi"], first["id"]
                )
                kinds = ",".join(sorted({j.get("type", "?") for j in jobs})) or "空"
            except Exception as exc:  # noqa: BLE001
                kinds = f"读取失败:{type(exc).__name__}"
            rows.append((len(pending), len(points), course, first, kinds))
        rows.sort(key=lambda item: (item[0], item[1]))
        if not rows:
            print("    所有课程都没有未完成章节")
        for pending_count, total, course, first, kinds in rows:
            print(
                f"    {course['courseId']:>12} {course['title'][:26]:<28} "
                f"未完成 {pending_count:>2}/{total:<3} 首章《{first['title'][:18]}》任务点类型: {kinds}"
            )
        print("\n[结论] 扫描完成，未发送任何学习或提交请求")
        return 0

    targets = [c for c in courses if c["courseId"] == args.list] if args.list else courses[:1]
    if not targets:
        print("[失败] 没找到目标课程")
        return 1

    for course in targets:
        print(f"\n[3] 课程《{course['title']}》章节：")
        point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
        points = point_list.get("points", [])
        print(f"    共 {len(points)} 个章节点，need_unlock={point_list.get('hasLocked')}")
        pending = []
        for point in points[: args.chapters]:
            flag = "已完成" if point["has_finished"] else "未完成"
            unlock = " [需解锁]" if point.get("need_unlock") else ""
            print(f"    - {point['id']:>12} {point['title'][:30]:<32} 任务点{point['jobCount']} {flag}{unlock}")
        pending = [p for p in points if not p["has_finished"]]
        if not pending:
            print("    （全部已完成）")
            continue
        first = pending[0]
        print(f"\n[4] 第一个未完成章节《{first['title']}》的任务点：")
        jobs, job_info = chaoxing.get_job_list(
            course["clazzId"], course["courseId"], course["cpi"], first["id"]
        )
        if job_info.get("notOpen"):
            print("    章节未开放（可能上一章测验未提交）")
        for job in jobs:
            print(f"    - type={job.get('type'):<10} jobid={job.get('jobid', '')[:40]}")
        if not jobs:
            print("    （没有未完成的任务点）")
    print("\n[结论] 只读探测完成，未发送任何学习或提交请求")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
