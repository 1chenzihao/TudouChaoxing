# -*- coding: utf-8 -*-
import argparse
import configparser
import json
import random
import time
import sys
import os
import traceback
from urllib3 import disable_warnings, exceptions

from api.logger import logger, PROJECT_NAME, add_file_sink
from api.base import Chaoxing, Account
from api.exceptions import LoginError, InputFormatError, MaxRollBackExceeded, AntiSpiderError
from api.answer import Tiku
from api.notification import Notification

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

# 每章之间的随机停顿默认区间(秒), 配置项 chapter_delay_min / chapter_delay_max 会覆盖它
DEFAULT_CHAPTER_DELAY_MIN = 3.0
DEFAULT_CHAPTER_DELAY_MAX = 9.0
# 防风控的长休息: 连刷 N 章后停一会儿，真人不会一节课接一节课秒开下一章
DEFAULT_REST_EVERY = 8
DEFAULT_REST_MIN = 120.0
DEFAULT_REST_MAX = 300.0
# 换课之间的停顿
COURSE_SWITCH_DELAY = (20.0, 60.0)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="土豆刷学习通 · 超星学习通自动刷课（视频 / 文档 / 阅读 / 章节测验，不做考试）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.0, help="视频播放倍速 (默认1, 最大2)"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry", 
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )
    parser.add_argument(
        "--solve-only",
        action="store_true",
        help="只做章节测验与作业, 跳过视频/文档/阅读任务 (和 --video-only 互斥, 后者优先)",
    )
    parser.add_argument(
        "--video-only",
        action="store_true",
        help="只刷视频/文档/阅读，跳过章节测验与作业（和 --solve-only 对称）",
    )
    parser.add_argument(
        "--scan-jobs",
        action="store_true",
        help="任务点巡检: 逐章列出每个任务点是否已完成, 一个学习请求都不发",
    )
    parser.add_argument(
        "--max-chapters",
        type=int,
        default=0,
        help="每门课最多处理 N 个未完成章节后停止(默认 0 = 不限制), 用于小步试跑",
    )

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def parse_bool(value, default=False) -> bool:
    """把配置里的布尔字符串解析成 bool, 无法识别时用默认值"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in ("1", "true", "yes", "on", "y", "t")


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf8")
    
    common_config = {}
    tiku_config = {}
    notification_config = {}
    
    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = common_config["course_list"].split(",")
        # 处理speed，将字符串转换为浮点数（填错了不能让人启动就崩）
        if "speed" in common_config:
            try:
                common_config["speed"] = min(2.0, max(1.0, float(common_config["speed"])))
            except (TypeError, ValueError):
                logger.warning("配置项 speed 非法, 已按 1.0 处理")
                common_config["speed"] = 1.0
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        # 处理布尔开关: solve_only 默认 false
        common_config["solve_only"] = parse_bool(common_config.get("solve_only"), False)
        # 只刷视频/文档/阅读，跳过章节测验与作业（和 solve_only 对称，用于分两轮刷）
        common_config["video_only"] = parse_bool(common_config.get("video_only"), False)
        # 任务点巡检开关(命令行 --scan-jobs 或配置项 scan_jobs)
        common_config["scan_jobs"] = parse_bool(common_config.get("scan_jobs"), False)
        # 每门课最多处理多少个未完成章节, 0 = 不限制
        try:
            common_config["max_chapters"] = int(common_config.get("max_chapters", 0) or 0)
        except (TypeError, ValueError):
            logger.warning("配置项 max_chapters 非法, 已按 0(不限制) 处理")
            common_config["max_chapters"] = 0
        # 每章之间的随机停顿(秒): 填错或 min>max 都退回 3~9, 不能让配置把程序卡死
        try:
            delay_min = float(common_config.get("chapter_delay_min", DEFAULT_CHAPTER_DELAY_MIN) or DEFAULT_CHAPTER_DELAY_MIN)
            delay_max = float(common_config.get("chapter_delay_max", DEFAULT_CHAPTER_DELAY_MAX) or DEFAULT_CHAPTER_DELAY_MAX)
        except (TypeError, ValueError):
            logger.warning("配置项 chapter_delay_min/chapter_delay_max 非法, 已按 3~9 秒处理")
            delay_min, delay_max = DEFAULT_CHAPTER_DELAY_MIN, DEFAULT_CHAPTER_DELAY_MAX
        if delay_min < 0 or delay_max < 0 or delay_min > delay_max:
            logger.warning(
                f"配置项 chapter_delay_min({delay_min}) / chapter_delay_max({delay_max}) 不合理"
                "(要求 0 <= min <= max), 已按 3~9 秒处理"
            )
            delay_min, delay_max = DEFAULT_CHAPTER_DELAY_MIN, DEFAULT_CHAPTER_DELAY_MAX
        common_config["chapter_delay_min"] = delay_min
        common_config["chapter_delay_max"] = delay_max
        # 长休息: 连刷 N 章后停 rest_min~rest_max 秒。填错就按默认 8 章 / 2~5 分钟
        try:
            rest_every = int(common_config.get("rest_every", DEFAULT_REST_EVERY) or DEFAULT_REST_EVERY)
            rest_min = float(common_config.get("rest_min", DEFAULT_REST_MIN) or DEFAULT_REST_MIN)
            rest_max = float(common_config.get("rest_max", DEFAULT_REST_MAX) or DEFAULT_REST_MAX)
        except (TypeError, ValueError):
            logger.warning("配置项 rest_every/rest_min/rest_max 非法, 已按 8 章 / 2~5 分钟处理")
            rest_every, rest_min, rest_max = DEFAULT_REST_EVERY, DEFAULT_REST_MIN, DEFAULT_REST_MAX
        if rest_every < 0 or rest_min < 0 or rest_max < rest_min:
            logger.warning("配置项 rest_every/rest_min/rest_max 不合理, 已按默认值处理")
            rest_every, rest_min, rest_max = DEFAULT_REST_EVERY, DEFAULT_REST_MIN, DEFAULT_REST_MAX
        common_config["rest_every"] = rest_every
        common_config["rest_min"] = rest_min
        common_config["rest_max"] = rest_max
    
    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换（同样是填错也不许崩）
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                try:
                    tiku_config[key] = float(tiku_config[key])
                except (TypeError, ValueError):
                    logger.warning(f"配置项 {key} 非法, 已忽略该值")
                    tiku_config.pop(key, None)

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))
    
    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置

    两个模式开关互斥: 同时给出 --video-only 与 --solve-only 时以 video_only 优先,
    用来保证"先刷视频、再做测验"的两轮跑法不会被一次误敲打乱。
    """
    solve_only = bool(getattr(args, "solve_only", False))
    video_only = bool(getattr(args, "video_only", False))
    if video_only and solve_only:
        solve_only = False
    common_config = {
        "username": args.username,
        "password": args.password,
        "course_list": args.list.split(",") if args.list else None,
        "speed": args.speed if args.speed else 1.0,
        "notopen_action": args.notopen_action if args.notopen_action else "retry",
        "solve_only": solve_only,
        "video_only": video_only,
        "max_chapters": int(getattr(args, "max_chapters", 0) or 0),
        "scan_jobs": bool(getattr(args, "scan_jobs", False)),
        "chapter_delay_min": DEFAULT_CHAPTER_DELAY_MIN,
        "chapter_delay_max": DEFAULT_CHAPTER_DELAY_MAX,
    }
    return common_config, {}, {}


def init_config():
    """初始化配置

    注意：用 -c 读配置文件时，命令行上**显式给出**的开关仍然生效（覆盖配置文件），
    否则 "python main.py -c config.ini --scan-jobs" 这种组合会静默失效、直接开始刷课。
    """
    args = parse_args()

    if args.config:
        common_config, tiku_config, notification_config = load_config_from_file(args.config)
        if getattr(args, "scan_jobs", False):
            common_config["scan_jobs"] = True
        if getattr(args, "solve_only", False):
            # 命令行显式说"只做测验"，就要盖掉配置文件里的 video_only，
            # 否则 main() 里 video_only 优先，这一轮又变成刷视频了（静默跑错模式）
            common_config["solve_only"] = True
            common_config["video_only"] = False
        if getattr(args, "video_only", False):
            # 只刷视频与只做测验互斥, 命令行给了 --video-only 就把 solve_only 压掉
            common_config["video_only"] = True
            common_config["solve_only"] = False
        if getattr(args, "max_chapters", 0):
            common_config["max_chapters"] = int(args.max_chapters)
        if getattr(args, "verbose", False):
            from api.logger import logger as _logger
            _logger.remove()
            add_file_sink("TRACE")
        return common_config, tiku_config, notification_config

    return build_config_from_args(args)


class RollBackManager:
    """课程回滚管理器，避免无限回滚"""
    def __init__(self):
        self.rollback_times = 0
        self.rollback_id = ""

    def add_times(self, id: str):
        """增加回滚次数"""
        if id == self.rollback_id and self.rollback_times == 3:
            raise MaxRollBackExceeded("回滚次数已达3次, 请手动检查学习通任务点完成情况")
        else:
            self.rollback_times += 1

    def new_job(self, id: str):
        """设置新任务，重置回滚次数"""
        if id != self.rollback_id:
            self.rollback_id = id
            self.rollback_times = 0


def init_chaoxing(common_config, tiku_config):
    """初始化超星实例"""
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    
    # 如果没有提供用户名密码，从命令行获取
    if not username or not password:
        if not sys.stdin or not sys.stdin.isatty():
            raise InputFormatError(
                "config.ini 里的 username / password 是空的，而当前环境没法交互输入"
                "（云端 Actions 就是这样，会一直卡在 input()）。"
                "请检查 config.ini 或仓库 Secrets 里的 CHX_USERNAME / CHX_PASSWORD。"
            )
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = input("请输入你的密码, 按回车确认\n密码:")
    
    account = Account(username, password)
    
    # 设置题库
    tiku = Tiku()
    tiku.config_set(tiku_config)  # 载入配置
    tiku = tiku.get_tiku_from_config()  # 载入题库
    tiku.init_tiku()  # 初始化题库
    
    # 获取查询延迟设置
    query_delay = tiku_config.get("delay", 0)

    # 实例化超星API
    chaoxing = Chaoxing(
        account=account,
        tiku=tiku,
        query_delay=query_delay,
    )
    
    return chaoxing


def handle_not_open_chapter(notopen_action, point, tiku, RB, auto_skip_notopen=False):
    """处理未开放章节"""
    if notopen_action == "retry":
        # 默认处理方式：重试
        # 针对题库启用情况
        if not tiku or tiku.DISABLE or not tiku.SUBMIT:
            # 未启用题库或未开启题库提交, 章节检测未完成会导致无法开始下一章, 直接退出
            logger.error(
                "章节未开启, 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                "请手动检查完成并提交再重试。或者在配置中配置(自动跳过关闭章节/开启题库并启用提交)"
            )
            return -1  # 退出标记
        RB.add_times(point["id"])
        return 0  # 重试上一章节
        
    elif notopen_action == "ask":
        # 询问模式 - 判断是否需要询问
        if not auto_skip_notopen:
            user_choice = input(f"章节 {point['title']} 未开放，是否继续检查后续章节？(y/n): ")
            if user_choice.lower() != 'y':
                # 用户选择停止
                logger.info("根据用户选择停止检查后续章节")
                return -1  # 退出标记
            # 用户选择继续，设置自动跳过标志
            logger.info("用户选择继续检查后续章节，将自动跳过连续的未开放章节")
            return 1, True  # 继续下一章节, 设置自动跳过
        else:
            logger.info(f"章节 {point['title']} 未开放，自动跳过")
            return 1, auto_skip_notopen  # 继续下一章节, 保持自动跳过状态
            
    else:  # notopen_action == "continue"
        # 继续模式，直接跳过当前章节
        logger.info(f"章节 {point['title']} 未开放，根据配置跳过此章节")
        return 1  # 继续下一章节


_JOB_TYPE_LABELS = {
    "video": "视频",
    "document": "文档",
    "workid": "测验/作业",
    "read": "阅读",
    "unknown": "未知",
}


def _job_label(item: dict) -> str:
    """任务点类型的中文标签"""
    return _JOB_TYPE_LABELS.get(str(item.get("type", "")), str(item.get("type", "")))


def _job_name(item: dict) -> str:
    """任务点的显示名：视频用文件名，其它用 jobid 兜底"""
    return str(item.get("name") or item.get("jobid") or "")


def scan_jobs(chaoxing, course_task):
    """任务点巡检：逐章列出每个任务点的完成状态。

    只读接口，不发任何视频进度上报或答题请求。用来回答"我到底还剩哪些没做"。
    """
    total_all = 0
    passed_all = 0
    pending_all = 0
    for course in course_task:
        print(f"\n==== 课程《{course['title']}》（{course['courseId']}） ====")
        point_list = chaoxing.get_course_point(
            course["courseId"], course["clazzId"], course["cpi"]
        )
        points = point_list.get("points", [])
        finished = sum(1 for p in points if p.get("has_finished"))
        print(f"      章节 {len(points)} 个, 章节级已完成 {finished} 个")
        for point in points:
            time.sleep(0.3)
            jobs, job_info = chaoxing.get_job_list(
                course["clazzId"], course["courseId"], course["cpi"], point["id"],
                include_passed=True,
            )
            if job_info.get("notOpen"):
                print(f"  · {point['title'][:34]:<36} [未开放]")
                continue
            passed = job_info.get("passed_jobs", []) or []
            passed_all += len(passed)
            pending_all += len(jobs)
            total_all += len(passed) + len(jobs)
            flag = "已完成" if point.get("has_finished") else "未完成"
            print(
                f"  · {point['title'][:34]:<36} [{flag}] "
                f"待办 {len(jobs)} / 已通过 {len(passed)}"
            )
            for item in passed:
                print(f"      ✅ {_job_label(item):<8} {_job_name(item)[:46]}")
            for job in jobs:
                print(f"      ⬜ {_job_label(job):<8} {_job_name(job)[:46]}")
    print(
        f"\n[巡检汇总] 任务点 {total_all} 个: 已通过 {passed_all}, 待办 {pending_all}"
    )
    print("[巡检汇总] 本次巡检未发送任何学习或提交请求")


# 重试清单跟脚本放一起。用相对路径的话，换个工作目录启动就"读不到上一轮"，
# 云端 workflow 的 cache 也会白恢复。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PENDING_FILE = os.path.join(BASE_DIR, "retry_pending.json")


def _load_pending() -> dict:
    """读取上一轮被 403 挡下、没能刷完的任务点。"""
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_pending(data: dict) -> None:
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=1)
    except OSError as exc:
        logger.warning(f"写 {PENDING_FILE} 失败: {exc}")


def _add_pending(course: dict, point: dict, job: dict) -> None:
    """把被跳过的任务点记下来，下一轮优先重试。"""
    data = _load_pending()
    cid = str(course.get("courseId", ""))
    entry = data.setdefault(cid, {"title": course.get("title", ""), "chapters": {}})
    ch = entry["chapters"].setdefault(str(point.get("id", "")), {
        "title": point.get("title", ""),
        "jobs": [],
    })
    jid = str(job.get("jobid", ""))
    if jid and jid not in ch["jobs"]:
        ch["jobs"].append(jid)
    _save_pending(data)
    logger.warning(f"已记入重试清单(下一轮优先): {point.get('title', '')} / {job.get('name', '')[:40]}")


def _drop_pending(course: dict, point: dict, job: dict) -> None:
    data = _load_pending()
    cid = str(course.get("courseId", ""))
    ch = (data.get(cid) or {}).get("chapters", {}).get(str(point.get("id", "")))
    if not ch:
        return
    jid = str(job.get("jobid", ""))
    if jid in ch["jobs"]:
        ch["jobs"].remove(jid)
    if not ch["jobs"]:
        data[cid]["chapters"].pop(str(point.get("id", "")), None)
    if not data.get(cid, {}).get("chapters"):
        data.pop(cid, None)
    _save_pending(data)


def process_job(chaoxing, course, job, job_info, speed, solve_only=False, point=None, video_only=False):
    """处理单个任务点"""
    job_type = job.get("type", "")
    job_id = str(job.get("jobid", ""))
    # 只做测验模式: 视频/文档/阅读跳过, 章节测验与作业照常处理
    if solve_only and job_type not in ("workid",):
        logger.info(f"已开启只做测验模式, 跳过 {job_type} 任务: {course['title']} 任务ID: {job_id}")
        return
    # 只刷视频模式：测验/作业全部跳过，留给第二轮单独刷
    if video_only and job_type == "workid":
        logger.info(f"已开启只刷视频模式, 跳过测验任务: {course['title']} 任务ID: {job_id}")
        return
    # 视频任务
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        # 超星的接口没有返回当前任务是否为Audio音频任务
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video"
        )
        if chaoxing.StudyResult.is_failure(video_result):
            # 403 基本都是 cookie 被踢, 换一份新会话再试, 比直接跳过有效
            logger.warning(f"视频上报被拒(疑似登录态失效), 刷新会话后重试: {job.get('name', '')[:40]}")
            if chaoxing.refresh_session():
                time.sleep(random.uniform(6, 12))
                video_result = chaoxing.study_video(
                    course, job, job_info, _speed=speed, _type="Video"
                )
        if chaoxing.StudyResult.is_failure(video_result):
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio")
        if chaoxing.StudyResult.is_failure(video_result):
            # 视频上报反复被拒（403）几乎都是风控，继续一个个试只会让风控更严
            if video_result == chaoxing.StudyResult.FORBIDDEN:
                raise AntiSpiderError("视频上报连续被拒(403)，疑似风控")
            logger.warning(
                f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
            )
            if point is not None:
                _add_pending(course, point, job)
        elif point is not None:
            _drop_pending(course, point, job)
    # 文档任务
    elif job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        chaoxing.study_document(course, job)
    # 章节测验 / 作业任务
    elif job["type"] == "workid":
        # 考试任务点(带 exam- 前缀)不做: 取卷/交卷接口未验证, 而且交卷不可逆
        if job_id.startswith("exam-"):
            logger.info(f"跳过考试任务点(本程序只做章节测验与作业) -> {job_id}")
            return
        logger.trace(f"识别到章节测验/作业任务, 任务章节: {course['title']} 任务ID: {job_id}")
        chaoxing.study_work(course, job, job_info)
    # 阅读任务
    elif job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        chaoxing.strdy_read(course, job, job_info)


def process_chapter(chaoxing, course, point, RB, notopen_action, speed, auto_skip_notopen=False,
                    solve_only=False, video_only=False,
                    chapter_delay_min=DEFAULT_CHAPTER_DELAY_MIN,
                    chapter_delay_max=DEFAULT_CHAPTER_DELAY_MAX):
    """处理单个章节"""
    logger.info(f'当前章节: {point["title"]}')
    
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return 1, auto_skip_notopen  # 继续下一章节
    
    # 随机等待，避免请求过快（云端 IP 更需要拉开节奏，防风控）
    # 区间来自配置 chapter_delay_min / chapter_delay_max，测验轮可以调大
    sleep_duration = random.uniform(chapter_delay_min, chapter_delay_max)
    logger.debug(f"本次随机等待时间: {sleep_duration:.3f}s")
    time.sleep(sleep_duration)
    
    # 获取当前章节的所有任务点
    jobs = []
    job_info = None
    jobs, job_info = chaoxing.get_job_list(
        course["clazzId"], course["courseId"], course["cpi"], point["id"],
        include_passed=True,
        job_count=int(point.get("jobCount") or 0),
    )
    # 已完成的任务点不再执行，但要明确告诉用户"这个已经做过了"
    for _passed in job_info.get("passed_jobs", []) or []:
        logger.info(
            f'任务点已完成, 跳过: [{_job_label(_passed)}] {_job_name(_passed)[:40]}'
        )

    # 任务点页面连续异常（登录态抖动/风控）时，不能当成"这章没任务"——那会让整章被静默跳过
    if job_info.get("invalid"):
        logger.error(
            f"任务点页面异常（重试后仍失败），本章本次跳过且不当作已完成: {point.get('title', '')}"
        )
        return 1, auto_skip_notopen

    # 发现未开放章节, 根据配置处理
    try:
        if job_info.get("notOpen", False):
            result = handle_not_open_chapter(
                notopen_action, point, chaoxing.tiku, RB, auto_skip_notopen
            )
            
            if isinstance(result, tuple):
                return result  # 返回继续标志和更新后的auto_skip_notopen
            else:
                return result, auto_skip_notopen
        
        # 遇到开放的章节，重置自动跳过状态
        auto_skip_notopen = False
        RB.new_job(point["id"])

    except MaxRollBackExceeded:
        logger.error("回滚次数已达3次, 请手动检查学习通任务点完成情况")
        # 跳过该课程
        return -1, auto_skip_notopen  # 退出标记
    
    chaoxing.rollback_times = RB.rollback_times
    
    # 可能存在章节无任何内容的情况
    if not jobs:
        if RB.rollback_times > 0:
            logger.trace(f"回滚中 尝试空页面任务, 任务章节: {course['title']}")
            chaoxing.study_emptypage(course, point)
        return 1, auto_skip_notopen  # 继续下一章节
    
    # 遍历所有任务点
    for job in jobs:
        process_job(chaoxing, course, job, job_info, speed, solve_only=solve_only, point=point, video_only=video_only)
    
    return 1, auto_skip_notopen  # 继续下一章节


def process_course(chaoxing, course, notopen_action, speed, solve_only=False, max_chapters=0, video_only=False,
                   chapter_delay_min=DEFAULT_CHAPTER_DELAY_MIN,
                   chapter_delay_max=DEFAULT_CHAPTER_DELAY_MAX,
                   rest_every=DEFAULT_REST_EVERY,
                   rest_min=DEFAULT_REST_MIN,
                   rest_max=DEFAULT_REST_MAX):
    """处理单个课程"""
    logger.info(f"开始学习课程: {course['title']}")
    
    # 获取当前课程的所有章节
    point_list = chaoxing.get_course_point(
        course["courseId"], course["clazzId"], course["cpi"]
    )

    # 上一轮被 403 挡下的章节排到最前面, 趁登录态新鲜先啃硬骨头
    _pend = _load_pending().get(str(course["courseId"]), {}).get("chapters", {})
    if _pend:
        _points = point_list["points"]
        _points.sort(key=lambda p: 0 if str(p["id"]) in _pend else 1)
        logger.info(f"本轮优先重试 {len(_pend)} 个上次失败章节")

    # 为了支持课程任务回滚, 采用下标方式遍历任务点
    __point_index = 0
    # 记录用户是否选择继续跳过连续的未开放任务点
    auto_skip_notopen = False
    # 初始化回滚管理器
    RB = RollBackManager()
    
    handled_chapters = 0
    anti_spider_hits = 0
    rest_done = 0

    def maybe_rest():
        """连刷 N 章就长休息一次。风控是按"持续活跃时长"累积的，光靠几秒间隔挡不住。"""
        nonlocal rest_done
        if not rest_every:
            return
        if handled_chapters and handled_chapters % rest_every == 0 and handled_chapters != rest_done:
            rest_done = handled_chapters
            seconds = random.uniform(rest_min, rest_max)
            logger.info(
                f"已连续处理 {handled_chapters} 章, 按防风控策略休息 {seconds / 60:.1f} 分钟"
            )
            time.sleep(seconds)

    while __point_index < len(point_list["points"]):
        point = point_list["points"][__point_index]
        logger.debug(f"当前章节 __point_index: {__point_index}")

        if max_chapters and handled_chapters >= max_chapters:
            logger.info(f"已达到本次试跑上限({max_chapters} 章), 停止处理课程: {course['title']}")
            break

        was_finished = point.get("has_finished", False)
        try:
            result, auto_skip_notopen = process_chapter(
                chaoxing, course, point, RB, notopen_action, speed, auto_skip_notopen,
                solve_only=solve_only, video_only=video_only,
                chapter_delay_min=chapter_delay_min, chapter_delay_max=chapter_delay_max,
            )
        except AntiSpiderError as exc:
            # 被风控拦了：不要硬撞，冷却一会儿再来；连着拦三次就停下并让用户手动过验证码
            anti_spider_hits += 1
            if anti_spider_hits > 3:
                logger.error(
                    "连续 %d 次被超星风控拦截（要求图形验证码），本次停止处理课程: %s\n"
                    "  处理办法：用浏览器登录学习通，随便打开一节课，把弹出的验证码输进去，"
                    "然后再跑一次即可（账号没坏，只是需要人工解锁）。" % (anti_spider_hits, course["title"])
                )
                return
            # 5 / 15 / 30 分钟阶梯 + 抖动；风控按时间窗口算，等太短等于没等
            wait_min = {1: 5, 2: 15}.get(anti_spider_hits, 30) + random.uniform(0, 2)
            logger.warning(
                "被超星风控拦截（%s），冷却 %d 分钟后重试本章（第 %d/3 次）：%s"
                % (str(exc)[:60], wait_min, anti_spider_hits, point.get("title", "")[:20])
            )
            time.sleep(wait_min * 60)
            continue
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - 单章故障不能拖垮整门课
            # 通宵跑时, 单个章节的网络抖动/页面改版不该让整门课停下来
            logger.error(
                "章节处理异常, 跳过该章继续: "
                + str(point.get("title", ""))[:30]
                + " -> "
                + type(exc).__name__
                + ": "
                + str(exc)
            )
            logger.debug(traceback.format_exc())
            # 只有真正需要处理的章节才计入上限，已完成的章节跳过时不该占名额
            if not was_finished:
                handled_chapters += 1
                maybe_rest()
            __point_index += 1
            continue
        # 这一章跑完了，说明风控没在拦，连击计数清零；
        # 以前是整门课累计，几个小时里零散撞两次就误判成"连续 3 次"提前收工
        anti_spider_hits = 0
        if not was_finished:
            handled_chapters += 1
            maybe_rest()

        if result == -1:  # 退出当前课程
            break
        elif result == 0:  # 重试前一章节
            # 以前直接 __point_index -= 1，第一章回滚时会变成 -1，
            # 取到 points[-1] 也就是"跳去最后一章"，整门课就乱序了
            __point_index = max(0, __point_index - 1)  # 默认第一个任务总是开放的
        else:  # 继续下一章节
            __point_index += 1


def filter_courses(all_course, course_list):
    """过滤要学习的课程"""
    if not course_list:
        # 手动输入要学习的课程ID列表
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 课程名: {course['title']}")
        print("*" * 28)
        try:
            course_list = input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198\n"
            ).split(",")
        except Exception as e:
            raise InputFormatError("输入格式错误") from e

    # 课程 ID 里带空格是常见的复制粘贴事故：config 里写 "266887717, 265891345"
    # 以前会因为 " 265891345" 匹配不上而静默变成"刷全部课程"
    wanted = [str(c).strip() for c in course_list if str(c).strip()]
    # 筛选需要学习的课程
    course_task = []
    matched = set()
    for course in all_course:
        if str(course["courseId"]).strip() in wanted:
            course_task.append(course)
            matched.add(str(course["courseId"]).strip())

    missing = [c for c in wanted if c not in matched]
    if missing:
        logger.warning(f"这些课程 ID 没在账号的课程列表里找到: {', '.join(missing)}")

    # 没有指定课程，或者一个都没匹配上（多半是 ID 抄错了），才退回"刷全部"
    if not course_task:
        if wanted:
            logger.warning("配置的课程 ID 一个都没匹配上, 本次改为处理全部课程（建议核对 ID）")
        course_task = all_course

    return course_task


def print_tiku_report(chaoxing):
    """程序收尾时打印题库统计(大模型解题调用次数/失败数/token/缓存命中)"""
    report = getattr(getattr(chaoxing, "tiku", None), "report", None)
    if not callable(report):
        return
    try:
        report()
    except Exception as e:
        logger.warning(f"输出题库统计失败: {type(e).__name__}: {e}")


def main():
    """主程序入口"""
    chaoxing = None
    try:
        # 初始化配置
        common_config, tiku_config, notification_config = init_config()
        
        # 规范化播放速度
        speed = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        notopen_action = common_config.get("notopen_action", "retry")
        solve_only = parse_bool(common_config.get("solve_only"), False)
        video_only = parse_bool(common_config.get("video_only"), False)
        max_chapters = int(common_config.get("max_chapters", 0) or 0)
        # 章节间隔（load_config_from_file 已经做过校验，这里再兜一次底）
        chapter_delay_min = float(common_config.get("chapter_delay_min", DEFAULT_CHAPTER_DELAY_MIN) or DEFAULT_CHAPTER_DELAY_MIN)
        chapter_delay_max = float(common_config.get("chapter_delay_max", DEFAULT_CHAPTER_DELAY_MAX) or DEFAULT_CHAPTER_DELAY_MAX)
        if chapter_delay_min < 0 or chapter_delay_max < 0 or chapter_delay_min > chapter_delay_max:
            chapter_delay_min, chapter_delay_max = DEFAULT_CHAPTER_DELAY_MIN, DEFAULT_CHAPTER_DELAY_MAX
        logger.info(f"本次章节间隔: {chapter_delay_min:.1f}~{chapter_delay_max:.1f} 秒")
        rest_every = int(common_config.get("rest_every", DEFAULT_REST_EVERY) or 0)
        rest_min = float(common_config.get("rest_min", DEFAULT_REST_MIN) or DEFAULT_REST_MIN)
        rest_max = float(common_config.get("rest_max", DEFAULT_REST_MAX) or DEFAULT_REST_MAX)
        if rest_every:
            logger.info(
                f"长休息: 每处理 {rest_every} 章停 {rest_min / 60:.1f}~{rest_max / 60:.1f} 分钟"
            )
        else:
            logger.info("长休息: 已关闭（rest_every=0）")
        # 两个模式开关互斥：都开着时以 video_only 优先，把测验留给下一轮
        if video_only and solve_only:
            logger.warning(
                "video_only 与 solve_only 同时为真, 本次以只刷视频优先; "
                "测验请用下一轮 --solve-only 单独跑"
            )
            solve_only = False
        if video_only:
            logger.info("本次模式: 只刷视频（测验留给下一轮）")
        elif solve_only:
            logger.info("本次模式: 只做测验（视频跳过）")
        else:
            logger.info("本次模式: 视频 + 测验一起刷")
        if solve_only:
            logger.info("已开启只做测验模式, 将跳过视频/文档/阅读任务")
        if max_chapters:
            logger.info(f"本次试跑上限: 每门课最多处理 {max_chapters} 个未完成章节")
        
        # 初始化超星实例
        chaoxing = init_chaoxing(common_config, tiku_config)
        
        # 设置外部通知
        notification = Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()
        
        # 检查当前登录状态
        _login_state = chaoxing.login()
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])
        
        # 获取所有的课程列表
        all_course = chaoxing.get_course_list()
        
        # 过滤要学习的课程
        course_task = filter_courses(all_course, common_config.get("course_list"))
        
        # 任务点巡检模式：只看不动
        if parse_bool(common_config.get("scan_jobs"), False):
            logger.info("已开启任务点巡检模式, 只读不写")
            scan_jobs(chaoxing, course_task)
            return

        # 开始学习
        logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")
        for _idx, course in enumerate(course_task):
            if _idx:
                # 换课之间也停一下：连续刷三门课在服务端看来就是"机器在跑"
                pause = random.uniform(*COURSE_SWITCH_DELAY)
                logger.info(f"切换到下一门课, 先休息 {pause:.0f} 秒")
                time.sleep(pause)
            process_course(
                chaoxing, course, notopen_action, speed,
                solve_only=solve_only, max_chapters=max_chapters, video_only=video_only,
                chapter_delay_min=chapter_delay_min, chapter_delay_max=chapter_delay_max,
                rest_every=rest_every, rest_min=rest_min, rest_max=rest_max,
            )
        
        logger.info("所有课程学习任务已完成")
        print_tiku_report(chaoxing)
        notification.send(f"{PROJECT_NAME} : 所有课程学习任务已完成")
        
    except SystemExit as e:
        if e.code != 0:
            logger.error(f"错误: 程序异常退出, 返回码: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt as e:
        logger.error(f"错误: 程序被用户手动中断, {e}")
        if chaoxing is not None:
            # 中断时也把题库统计打出来, 方便判断大模型调用是否正常
            print_tiku_report(chaoxing)
    except BaseException as e:
        logger.error(f"错误: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        try:
            notification.send(f"{PROJECT_NAME} : 出现错误 {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except Exception:
            pass  # 如果通知发送失败，忽略异常
        raise e


if __name__ == "__main__":
    main()