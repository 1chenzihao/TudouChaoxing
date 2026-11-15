# -*- coding: utf-8 -*-
import json
import os
import random
import re
import time
from enum import Enum
from hashlib import md5

import requests
from requests.adapters import HTTPAdapter

from api.answer import *
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.cookies import save_cookies, use_cookies
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)
from api.process import show_progress
from api.exceptions import MaxRetryExceeded, AntiSpiderError


def get_timestamp():
    return str(int(time.time() * 1000))


def get_random_seconds():
    return random.randint(30, 90)


DEFAULT_HTTP_TIMEOUT = 30


class TimeoutHTTPAdapter(HTTPAdapter):
    """给所有请求兜一个默认超时。

    requests 不传 timeout 会无限等待，学习通偶发不响应时整个流程会挂死
    （表现为「程序还在跑，但日志再也不动了」）。这里统一兜 30 秒。
    """

    def __init__(self, timeout: float = DEFAULT_HTTP_TIMEOUT, *args, **kwargs):
        self._default_timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, timeout=None, **kwargs):
        return super().send(request, timeout=timeout or self._default_timeout, **kwargs)


# 全局请求节流：真人不会在一分钟内打开几十个页面，命中超星风控(9010)多半就是节奏太急。
# 这里给所有请求加一个最小间隔，让整体 RPM 稳在一个不吃风控的区间。
_MIN_REQUEST_GAP = 5.0      # 秒。实测超星窗口很窄：连发 8 个就翻脸，宁可慢也别被封
_last_request_at = 0.0


class ThrottleSession(requests.Session):
    """给每个请求加最小间隔的 Session。"""

    def request(self, method, url, **kwargs):
        global _last_request_at
        gap = _MIN_REQUEST_GAP + random.uniform(0, 2.0)
        wait = _last_request_at + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()
        return super().request(method, url, **kwargs)


def init_session(isVideo: bool = False, isAudio: bool = False):
    _session = ThrottleSession()
    _session.verify = False
    _session.mount("http://", TimeoutHTTPAdapter(max_retries=3))
    _session.mount("https://", TimeoutHTTPAdapter(max_retries=3))
    if isVideo:
        _session.headers = gc.VIDEO_HEADERS
    elif isAudio:
        _session.headers = gc.AUDIO_HEADERS
    else:
        _session.headers = gc.HEADERS
    _session.cookies.update(use_cookies())
    return _session


def random_answer(q: dict) -> str:
    """
    按题型随机作答, 仅用于兜底

    参数:
        q: 题目字典, 需要 type / options 字段
    返回:
        str: 可直接提交的答案; 单选/多选返回选项字母, 判断题返回 true/false
    """
    options = q.get("options", "") or ""
    if not options:
        return ""
    q_type = q.get("type", "")
    answer = ""
    if q_type == "multiple":
        logger.debug(f"当前选项列表[cut前] -> {options}")
        _op_list = multi_cut(options)
        logger.debug(f"当前选项列表[cut后] -> {_op_list}")
        if not _op_list:
            logger.error("选项为空, 未能正确提取题目选项信息! 请反馈并提供以上信息")
            return ""
        available_options = len(_op_list)
        if available_options <= 1:
            select_count = available_options
        else:
            max_possible = min(4, available_options)
            min_possible = min(2, available_options)
            weights_map = {
                2: [1.0],
                3: [0.3, 0.7],
                4: [0.1, 0.5, 0.4],
                5: [0.1, 0.4, 0.3, 0.2],
            }
            weights = weights_map.get(max_possible, [0.3, 0.4, 0.3])
            possible_counts = list(range(min_possible, max_possible + 1))
            weights = weights[: len(possible_counts)]
            weights_sum = sum(weights)
            if weights_sum > 0:
                weights = [w / weights_sum for w in weights]
            select_count = random.choices(possible_counts, weights=weights, k=1)[0]
        selected_options = random.sample(_op_list, select_count) if select_count > 0 else []
        for option in selected_options:
            answer += option[:1]  # 取首字为答案，例如A或B
        answer = "".join(sorted(answer))
    elif q_type == "single":
        answer = random.choice(options.split("\n"))[:1]  # 取首字为答案, 例如A或B
    elif q_type == "judgement":
        answer = "true" if random.choice([True, False]) else "false"
    logger.info(f"随机选择 -> {answer}")
    return answer


def multi_cut(answer, origin_html: str = ""):
    """
    将多选题答案字符串按特定字符进行切割, 并返回切割后的答案列表

    参数:
        answer: 待切割的答案字符串
        origin_html: 原始网页源码, 仅在切割失败时打进日志便于排查
    返回:
        list[str] | None: 切割后的答案列表, 无法切割时返回 None
    """
    res = cut(answer)
    if res is None:
        logger.warning(
            f"未能从网页中提取题目信息, 以下为相关信息：\n\t{answer}\n\n{origin_html}\n"
        )
        logger.warning("未能正确提取题目选项信息! 请反馈并提供以上信息")
        return None
    return res


def clean_res(res):
    """去掉答案里的选项字母与标点, 便于和选项做子序列匹配"""
    cleaned_res = []
    if isinstance(res, str):
        res = [res]
    for c in res:
        cleaned_res.append(re.sub(r'^[A-Za-z]|[.,!?;:，。！？；：]', '', c))
    return cleaned_res


def is_subsequence(a, o):
    """判断 a 是否为 o 的子序列, 用于把答案文本对到选项上"""
    iter_o = iter(o)
    return all(c in iter_o for c in a)


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None

    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class Chaoxing:
    class StudyResult(Enum):
        SUCCESS = 0
        FORBIDDEN = 1  # 403
        ERROR = 2
        TIMEOUT = 3

        @staticmethod
        def is_success(result):
            return result == Chaoxing.StudyResult.SUCCESS

        @staticmethod
        def is_failure(result):
            return result != Chaoxing.StudyResult.SUCCESS

    def __init__(self, account: Account = None, tiku: Tiku = None,**kwargs):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.kwargs = kwargs
        self.rollback_times = 0

    def login(self):
        _session = requests.session()
        _session.verify = False
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        resp = _session.post(_url, headers=gc.HEADERS, data=_data)
        if resp and resp.json()["status"] == True:
            save_cookies(_session)
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}
        else:
            return {"status": False, "msg": str(resp.json()["msg2"])}

    def get_fid(self):
        _session = init_session()
        return _session.cookies.get("fid")

    def get_uid(self):
        _session = init_session()
        return _session.cookies.get("_uid")

    def get_course_list(self):
        _session = init_session()
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")
        # 接口突然抽风, 增加headers
        _headers = {
            "Host": "mooc2-ans.chaoxing.com",
            "sec-ch-ua-platform": '"Windows"',
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Accept": "text/html, */*; q=0.01",
            "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://mooc2-ans.chaoxing.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
        }
        _resp = _session.post(_url, headers=_headers, data=_data)
        # logger.trace(f"原始课程列表内容:\n{_resp.text}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url)
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = _session.post(_url, data=_data)
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = init_session()
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)
        # logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        return decode_course_point(_resp.text)

    @staticmethod
    def _looks_blocked(resp) -> bool:
        """判断这次响应是不是被风控拦了（要求输图形验证码）。"""
        try:
            if getattr(resp, "status_code", 200) == 202:
                return True
            url = getattr(resp, "url", "") or ""
            if "antispiderShowVerify" in url:
                return True
            text = getattr(resp, "text", "") or ""
            return "操作异常" in text and "验证码" in text
        except Exception:
            return False

    def get_job_list(self, _clazzid, _courseid, _cpi, _knowledgeid, include_passed: bool = False, job_count: int = 0):
        # 页面异常（登录态抖动 / 风控 / 空白页）时不能当成"没有任务"，否则整章会被静默跳过
        last_info = {}
        for _attempt in range(3):
            job_list, job_info = self._fetch_job_list_once(
                _clazzid, _courseid, _cpi, _knowledgeid, include_passed, job_count
            )
            if not job_info.get("invalid"):
                return job_list, job_info
            last_info = job_info
            logger.warning(f"任务点页面异常（第 {_attempt + 1}/3 次），稍后重试...")
            time.sleep(1.5 + _attempt * 1.5)
        logger.error("任务点页面连续 3 次异常，本次不当作已完成")
        return [], last_info

    def _fetch_job_list_once(self, _clazzid, _courseid, _cpi, _knowledgeid, include_passed: bool = False, job_count: int = 0):
        _session = init_session()
        job_list = []
        job_info = {}
        passed_jobs = []
        valid_seen = False
        for _possible_num in [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
        ]:  # 学习界面任务卡片数, 很少有3个的, 但是对于章节解锁任务点少一个都不行, 可以从API /mooc-ans/mycourse/studentstudyAjax获取值, 或者干脆直接加, 但二者都会造成额外的请求
            _url = f"https://mooc1.chaoxing.com/mooc-ans/knowledge/cards?clazzid={_clazzid}&courseid={_courseid}&knowledgeid={_knowledgeid}&num={_possible_num}&ut=s&cpi={_cpi}&v=20160407-3&mooc2=1"
            logger.trace("开始读取章节所有任务点...")
            _resp = _session.get(_url)
            if self._looks_blocked(_resp):
                raise AntiSpiderError("任务点页面被风控拦截，需要输入图形验证码")
            _job_list, _job_info = decode_course_card(_resp.text, include_passed=include_passed)
            if _job_info.get("invalid", False):
                # 这一页不是正常的任务点页，换下一个 num 再试，不能因此判定"这章没任务"
                continue
            valid_seen = True
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info
            job_list += _job_list
            passed_jobs.extend(_job_info.pop("passed_jobs", []) or [])
            job_info.update(_job_info)
            # 已知这章有几个任务点，凑够就不用再打后面的 num —— 少发请求 = 少触发风控
            if job_count and len(job_list) + len(passed_jobs) >= job_count:
                break
            # if _job_list and len(_job_list) != 0:
            #     break
        # logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        if not valid_seen:
            return [], {"invalid": True}
        # 软风控：页面正常（200、有 mArg）但卡片是 0 个，而课程页明确说这章有任务点。
        # 这种最坑——看起来像"这章没任务"，其实是超星把卡片抽走了。
        if job_count and not job_list and not passed_jobs:
            raise AntiSpiderError("任务点页面返回空卡片(软风控)，本章实际有 %d 个任务点" % job_count)
        # num=0..6 每轮都会带回同一批卡片, 同一个任务点会重复出现, 必须去重
        seen_job = set()
        unique_jobs = []
        for item in job_list:
            key = (item.get("jobid", ""), item.get("objectid", ""), item.get("type", ""))
            if key in seen_job:
                continue
            seen_job.add(key)
            unique_jobs.append(item)
        job_list = unique_jobs
        if include_passed:
            # num=0..6 每轮都会带回同一批卡片, 同一个已通过任务点会重复出现, 按 jobid+name 去重
            seen = set()
            unique_passed = []
            for item in passed_jobs:
                dedup_key = (item.get("jobid", ""), item.get("name", ""), item.get("type", ""))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                unique_passed.append(item)
            job_info["passed_jobs"] = unique_passed
        logger.info("章节任务点读取成功...")
        return job_list, job_info

    def refresh_session(self) -> bool:
        """登录态被踢（表现为播放上报 403 / 任务点页面返回空页）时重新登录。

        403 大多是 cookie 失效引起的，换一份新会话往往就能继续上报，
        比直接跳过任务点有效得多。
        """
        try:
            state = self.login()
            if isinstance(state, dict):
                return bool(state.get("status"))
            return bool(state)
        except Exception as exc:  # noqa: BLE001 - 重登失败不该打断整门课
            logger.warning(f"重新登录失败: {exc}")
            return False

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()

    def video_progress_log(
        self,
        _session,
        _course,
        _job,
        _job_info,
        _dtoken,
        _duration,
        _playingTime,
        _type: str = "Video",
    ):
        if "courseId" in _job["otherinfo"]:
            _mid_text = f"otherInfo={_job['otherinfo']}&"
        else:
            _mid_text = f"otherInfo={_job['otherinfo']}&courseId={_course['courseId']}&"
        _success = False
        _state = 403
        for _possible_rt in ["0.9", "1"]:
            _url = (
                f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
                f"{_course['cpi']}/"
                f"{_dtoken}?"
                f"clazzId={_course['clazzId']}&"
                f"playingTime={_playingTime}&"
                f"duration={_duration}&"
                f"clipTime=0_{_duration}&"
                f"objectId={_job['objectid']}&"
                f"{_mid_text}"
                f"jobid={_job['jobid']}&"
                f"userid={self.get_uid()}&"
                f"isdrag=3&"
                f"view=pc&"
                f"enc={self.get_enc(_course['clazzId'], _job['jobid'], _job['objectid'], _playingTime, _duration, self.get_uid())}&"
                f"rt={_possible_rt}&"
                f"dtype={_type}&"
                f"_t={get_timestamp()}"
            )
            resp = _session.get(_url)
            _state = resp.status_code
            if resp.status_code == 200:
                _success = True
                break  # 如果返回为200正常, 则跳出循环
            elif resp.status_code == 403:
                continue  # 如果出现403无权限报错, 则继续尝试不同的rt参数
        if _success:
            try:
                return resp.json(), 200
            except ValueError:
                # 200 但返回的不是 JSON（被风控塞了 HTML 页面），不能当成"已通过"
                logger.warning("视频上报返回非 JSON 内容，按失败处理")
                return {"isPassed": False}, 502
        # 只有真正的 403 才代表"登录态/风控"问题；其它状态码（500/502/超时）只是网络抖动，
        # 不能混为一谈，否则会把一次网络故障升级成 5 分钟风控冷却。
        if _state == 403:
            logger.warning("出现403报错, 尝试修复无效, 正在跳过当前任务点...")
        else:
            logger.warning(f"视频上报失败(HTTP {_state}), 正在跳过当前任务点...")
        return {"isPassed": False}, _state  # 返回一个字典和当前状态
    def study_video(
        self, _course, _job, _job_info, _speed: float = 1.0, _type: str = "Video"
    ) -> StudyResult:
        if _type == "Video":
            _session = init_session(isVideo=True)
        else:
            _session = init_session(isAudio=True)
        _session.headers.update()
        _info_url = f"https://mooc1.chaoxing.com/ananas/status/{_job['objectid']}?k={self.get_fid()}&flag=normal"
        _info_resp = _session.get(_info_url)
        if self._looks_blocked(_info_resp):
            raise AntiSpiderError("读取视频信息时被风控拦截，需要输入图形验证码")
        try:
            _video_info = _info_resp.json()
        except ValueError:
            # 这一层以前直接 .json() + ["status"]，一旦被塞 HTML 就抛 KeyError/ValueError，
            # 整个任务点被当成"章节异常"跳过，日志里还看不出原因
            logger.warning("视频信息接口返回非 JSON（疑似风控或登录态失效），本任务点跳过")
            return self.StudyResult.ERROR
        if not isinstance(_video_info, dict):
            return self.StudyResult.ERROR
        if _video_info.get("status") == "success":
            _dtoken = _video_info.get("dtoken", "")
            _duration = _video_info.get("duration") or 0
            _crc = _video_info.get("crc", "")
            _key = _video_info.get("key", "")
            if not _dtoken or not _duration:
                logger.warning("视频信息缺 dtoken/duration，本任务点跳过")
                return self.StudyResult.ERROR
            _isPassed = False
            _isFinished = False
            _playingTime = 0
            logger.info(f"开始任务: {_job['name']}, 总时长: {_duration}秒")
            state = 200
            while not _isFinished:
                if _isFinished:
                    _playingTime = _duration
                _isPassed, state = self.video_progress_log(
                    _session,
                    _course,
                    _job,
                    _job_info,
                    _dtoken,
                    _duration,
                    _playingTime,
                    _type,
                )
                if not _isPassed or _isPassed.get("isPassed"):
                    break
                if not _isPassed.get("isPassed") and state == 403:
                    return self.StudyResult.FORBIDDEN
                _wait_time = get_random_seconds()
                if _playingTime + _wait_time >= int(_duration):
                    _wait_time = int(_duration) - _playingTime
                    _isPassed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, _duration, _duration, _type)
                    if _isPassed.get("isPassed"):
                        _isFinished = True
                # 播放进度条
                show_progress(_job["name"], _playingTime, _wait_time, _duration, _speed)
                _playingTime += _wait_time
            print("\r", end="", flush=True)
            logger.info(f"任务完成: {_job['name']}")
            return self.StudyResult.SUCCESS
        else:
            return self.StudyResult.ERROR
    def study_document(self, _course, _job) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            requests.Response: Response object from the GET request

        Note:
            This method requires the following helper functions:
            - init_session(): To initialize a new session
            - get_timestamp(): To get current timestamp
            - re module for regular expression matching
        """
        _session = init_session()
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        _resp = _session.get(_url)
        if self._looks_blocked(_resp):
            raise AntiSpiderError("文档任务提交被风控拦截，需要输入图形验证码")
        if _resp.status_code != 200:
            logger.warning(f"文档任务提交失败(HTTP {_resp.status_code})")
            return self.StudyResult.ERROR
        return self.StudyResult.SUCCESS

    def _normalize_solver_answer(self, q: dict, answer: str) -> str:
        """
        把大模型返回的答案规整成可直接提交的格式

        single -> "B"; multiple -> "ABD"; judgement -> "true"/"false"; 其它保持文本(限 2000 字符)
        规整不出来(比如模型没按格式给字母)时返回空串, 由调用方回退旧搜题流程, 保证正确率
        """
        answer = (answer or "").strip()
        if not answer:
            return ""
        q_type = q.get("type", "")
        if q_type in ("single", "multiple"):
            letters = "".join(re.findall(r"[A-Ha-h]", answer))
            leftover = re.sub(r"[A-Ha-h\s,，、]", "", answer)
            if not letters or leftover:
                # 答案里混了非选项内容, 说明不是标准选项字母
                logger.warning(f"大模型答案不是标准选项字母, 已弃用 -> {answer}")
                return ""
            letters = letters.upper()
            if q_type == "single":
                letters = letters[:1]
            else:
                letters = "".join(sorted(set(letters)))
            option_count = len([o for o in str(q.get("options", "") or "").split("\n") if o.strip()])
            if option_count and any((ord(c) - ord("A")) >= option_count for c in letters):
                logger.warning(f"大模型答案超出选项范围(共 {option_count} 项), 已弃用 -> {answer}")
                return ""
            return letters
        if q_type == "judgement":
            low = answer.strip().lower()
            if low in ("true", "正确", "对", "√", "t", "1", "yes", "是"):
                return "true"
            if low in ("false", "错误", "错", "×", "f", "0", "no", "否", "不对", "不正确"):
                return "false"
            logger.warning(f"大模型判断题答案无法识别, 已弃用 -> {answer}")
            return ""
        if q_type == "completion":
            # 填空题答案为文本, 多空按模型给出的顺序拼接
            return re.sub(r"\n\s*", "", answer).strip()
        # 简答题与未知题型: 直接使用答案, 做长度保护
        return answer.strip()[:2000]

    def _map_legacy_answer(self, q: dict, res, *, context: str = "") -> str:
        """
        旧搜题路径的题型映射: 把题库返回的文本答案对到题目选项上

        返回可直接提交的答案字符串, 无法匹配时返回空串(调用方会随机兜底)
        """
        answer = ""
        q_type = q.get("type", "")
        if q_type == "multiple":
            # 多选处理
            options_list = multi_cut(q.get("options", ""), context)
            res_list = multi_cut(res, context)
            if res_list is not None and options_list is not None:
                for _a in clean_res(res_list):
                    for o in options_list:
                        if is_subsequence(_a, o):
                            answer += o[:1]
                # 对答案进行排序, 否则会提交失败
                answer = "".join(sorted(answer))
            # 若分割失败则落到调用方的随机兜底
        elif q_type == "single":
            # 单选也进行切割, 主要是防止返回的答案有异常字符
            options_list = multi_cut(q.get("options", ""), context)
            if options_list is not None:
                t_res = clean_res(res)
                if t_res:
                    for o in options_list:
                        if is_subsequence(t_res[0], o):
                            answer = o[:1]
                            break
        elif q_type == "judgement":
            answer = "true" if self.tiku.judgement_select(res) else "false"
        elif q_type == "completion":
            # 修复旧 bug: 原实现 "".join(answer) 是把空串自己拼接, 多空填空题答案会被丢光
            answer = self._normalize_text_answer(res)
        else:
            # 简答题 / 未知题型: 题库原文可以用, 但必须规整后再当成功; 空答案交给调用方按随机计数
            answer = self._normalize_text_answer(res)
        return answer

    @staticmethod
    def _normalize_text_answer(res) -> str:
        """
        把题库/模型返回的文本答案规整成可提交的单行答案

        去首尾空白、去换行、截断 2000 字符; list 形态按顺序拼接(不再丢答案)
        """
        if isinstance(res, (list, tuple)):
            text = "".join(str(_part) for _part in res)
        else:
            text = str(res)
        text = re.sub(r"[\r\n]+", "", text).strip()
        return text[:2000]

    def _solve_one_question(self, q: dict, *, context: str = "") -> tuple:
        """
        解一道题, 返回 (答案, 来源标记)

        来源标记: llm(大模型高可信) / llm_low(大模型低可信) / tiku(题库) / random(随机)
        """
        # 1. 大模型结构化解题旁路
        solve_structured = getattr(self.tiku, "solve_structured", None)
        if callable(solve_structured) and not getattr(self.tiku, "DISABLE", False):
            try:
                solver_result = solve_structured(q)
            except Exception as e:
                logger.warning(f"结构化解题旁路异常, 本题回退旧搜题流程: {type(e).__name__}: {e}")
                solver_result = None
            if solver_result is not None:
                _raw = getattr(solver_result, "answer", "")
                _answer = self._normalize_solver_answer(q, _raw if isinstance(_raw, str) else "")
                if _answer:
                    _confident = bool(getattr(solver_result, "confident", False))
                    logger.info(
                        f"采用大模型答案 -> {_answer} (confident={_confident}, "
                        f"votes={getattr(solver_result, 'votes', None)}, "
                        f"source={getattr(solver_result, 'source', None)})"
                    )
                    if _confident:
                        return _answer, "llm"
                    _solver_source = str(getattr(solver_result, "source", "") or "")
                    # 随机兜底出来的答案不能算"答出来了", 否则覆盖率虚高会把交卷闸门顶开
                    return _answer, ("random" if _solver_source == "fallback_random" else "llm_low")
                logger.warning(f"大模型答案不可直接提交, 本题回退旧搜题流程 -> {_raw}")

        # 2. 旧搜题路径
        query = getattr(self.tiku, "query", None)
        res = None
        if callable(query) and not getattr(self.tiku, "DISABLE", False):
            res = query(q)
        if res:
            answer = self._map_legacy_answer(q, res, context=context)
            if answer:
                logger.info(f"成功获取到答案：{answer}")
                return answer, "tiku"
            logger.warning(f"找到答案但答案未能匹配 -> {res}\t随机选择答案")

        # 3. 随机兜底; 随机也拿不到答案时留空但仍按 random 计数, 保证覆盖率不被虚高
        answer = random_answer(q)
        if not answer:
            logger.warning(f"本题无可用答案(题型 {q.get('type')}), 答案留空并按随机计数")
            return "", "random"
        return answer, "random"

    def _solve_questions(self, questions: list, *, context: str = "") -> dict:
        """
        就地把答案写进 q["answerField"], 返回统计

        参数:
            questions: decode_questions_info 解析出来的题目列表
            context: 日志上下文, 例如 "章节检测 work-xxx"
        返回:
            dict: {total, solved, random, coverage, sources}
                  coverage 为覆盖率百分比(0-100), sources 为各来源命中数
        """
        total = len(questions)
        sources = {"llm": 0, "llm_low": 0, "tiku": 0, "random": 0}
        query_delay = self.kwargs.get("query_delay", 0) or 0
        for q in questions:
            logger.debug(f"当前题目信息 -> {q}")
            # 添加搜题延迟 #428 - 默认0s延迟
            if query_delay:
                time.sleep(query_delay)
            answer, source = self._solve_one_question(q, context=context)
            # 填充答案; 来源键名沿用旧实现, 提交逻辑依赖它
            q["answerField"][f'answer{q["id"]}'] = answer
            q[f'answerSource{q["id"]}'] = source
            sources[source] = sources.get(source, 0) + 1
            logger.info(f'{q["title"]} 填写答案为 {answer}')
        solved = total - sources["random"]
        coverage = (solved / total * 100) if total else 0.0
        stats = {
            "total": total,
            "solved": solved,
            "random": sources["random"],
            "coverage": coverage,
            "sources": sources,
        }
        logger.info(
            f"答题统计[{context or '未命名'}]: 共 {total} 题, 答出 {solved} 题, "
            f"随机 {sources['random']} 题, 覆盖率 {coverage:.0f}% "
            f"(大模型 {sources['llm']} / 大模型低可信 {sources['llm_low']} / "
            f"题库 {sources['tiku']} / 随机 {sources['random']})"
        )
        return stats

    def study_work(self, _course, _job, _job_info) -> StudyResult:
        if not self.tiku or getattr(self.tiku, "DISABLE", False):
            return self.StudyResult.SUCCESS
        # 保险: 考试任务点(workId 是 exam- 前缀)不走章节测验这条链路, 直接放过
        if str((_job or {}).get("jobid", "")).startswith("exam-"):
            logger.warning(f"考试任务点不处理, 已跳过 -> {_job.get('jobid')}")
            return self.StudyResult.SUCCESS
        _ORIGIN_HTML_CONTENT = ""  # 保留原始网页源码, 排查题目解析问题时用

        def with_retry(max_retries=3, delay=1):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    retries = 0
                    while retries < max_retries:
                        try:
                            _resp = func(*args, **kwargs)
                            if self._looks_blocked(_resp):
                                raise AntiSpiderError("章节测验页面被风控拦截，需要输入图形验证码")
                            
                            # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
                            if '教师未创建完成该测验' in _resp.text:
                                raise PermissionError("教师未创建完成该测验")

                            questions = decode_questions_info(_resp.text)
                    
                            if _resp.status_code == 200 and questions.get("questions"):
                                return (_resp, questions)
                    
                            logger.warning(f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中... ({retries+1}/{max_retries})")
                
                        except requests.exceptions.RequestException as e:
                            logger.warning(f"请求失败: {str(e)[:50]}, 重试中... ({retries+1}/{max_retries})")
                        retries += 1
                        time.sleep(delay * (2 ** retries))
                    raise MaxRetryExceeded(f"超过最大重试次数 ({max_retries})")
                return wrapper
            return decorator

        # 学习通这里根据参数差异能重定向至两个不同接口, 需要定向至https://mooc1.chaoxing.com/mooc-ans/workHandle/handle
        _session = init_session()
        headers = {
            "Host": "mooc1.chaoxing.com",
            "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
        }
        cookies = _session.cookies.get_dict()

        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        @with_retry(max_retries=3, delay=1)
        def fetch_response():
            # 以前这里是裸 requests.get，绕过了 ThrottleSession 的全局间隔，
            # 等于"刷视频有节流、抓测验没有"，测验轮最容易撞风控的就是这一下
            return _session.get(
                    _url,
                    headers=headers,
                    verify=False,
                    params={
                        "api": "1",
                        "workId": _job["jobid"].replace("work-", ""),
                        "jobid": _job["jobid"],
                        "originJobId": _job["jobid"],
                        "needRedirect": "true",
                        "skipHeader": "true",
                        "knowledgeid": str(_job_info["knowledgeid"]),
                        "ktoken": _job_info["ktoken"],
                        "cpi": _job_info["cpi"],
                        "ut": "s",
                        "clazzId": _course["clazzId"],
                        "type": "",
                        "enc": _job["enc"],
                        "mooc2": "1",
                        "courseid": _course["courseId"],
                    }
            )

        final_resp = {}
        questions = {}

        try:
            final_resp, questions = fetch_response()
        except AntiSpiderError:
            raise  # 风控要上抛给主循环走冷却，不是"这次没答成"这么简单
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return self.StudyResult.ERROR
        
        _ORIGIN_HTML_CONTENT = final_resp.text  # 保留原始网页源码, 排查题目解析问题时用
        logger.debug(f"章节检测网页源码长度: {len(_ORIGIN_HTML_CONTENT)}")

        # 搜题: 大模型结构化解题 -> 题库搜题 -> 随机兜底, 优先级在 _solve_questions 里保证
        solve_stats = self._solve_questions(
            questions["questions"], context=f"章节检测 {_job['jobid']}"
        )
        cover_rate = solve_stats["coverage"]
        _src_count = solve_stats["sources"]
        logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")
        logger.info(
            f"章节检测答题来源: 大模型(高可信) {_src_count['llm']} 题, "
            f"大模型(低可信) {_src_count['llm_low']} 题, 题库 {_src_count['tiku']} 题, "
            f"随机 {_src_count['random']} 题; "
            f"confidence 分布: confident={_src_count['llm']} / low={_src_count['llm_low']}"
        )
        # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
        if self.tiku.get_submit_params() == "1":
            questions["pyFlag"] = "1"
        elif cover_rate >= self.tiku.COVER_RATE*100 or self.rollback_times >= 1:
            questions["pyFlag"] = ""
        else:
            questions["pyFlag"] = "1"
            logger.info(f"章节检测题库覆盖率低于{self.tiku.COVER_RATE*100:.0f}%，不予提交")
        # 组建提交表单
        # 保存模式只写"有把握"的答案: 大模型低可信与随机作答一律留空, 让用户自己补;
        # 提交模式才全写(反正不写也是错)
        answered_sources = ("cover", "llm", "llm_low", "tiku")
        keep_sources = answered_sources if questions["pyFlag"] != "1" else ("cover", "llm", "tiku")
        if questions["pyFlag"] == "1":
            for q in questions["questions"]:
                _answer_source = q.get(f'answerSource{q["id"]}', "")
                questions.update(
                    {
                        f'answer{q["id"]}':
                            q["answerField"][f'answer{q["id"]}'] if _answer_source in keep_sources else '',
                        f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                    }
                )
        else:
            for q in questions["questions"]:
                questions.update(
                    {
                        f'answer{q["id"]}': q["answerField"][f'answer{q["id"]}'],
                        f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                    }
                )

        del questions["questions"]

        res = _session.post(
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            data=questions,
            headers={
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": '"Windows"',
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                # "Referer": "https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId=246831735&workAnswerId=52680423&workId=37778125&api=1&knowledgeid=913820156&classId=107515845&oldWorkId=07647c38d8de4c648a9277c5bed7075a&jobid=work-07647c38d8de4c648a9277c5bed7075a&type=&isphone=false&submit=false&enc=1d826aab06d44a1198fc983ed3d243b1&cpi=338350298&mooc2=1&skipHeader=true&originJobId=work-07647c38d8de4c648a9277c5bed7075a",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
            },
        )
        if self._looks_blocked(res):
            raise AntiSpiderError("提交章节测验时被风控拦截，需要输入图形验证码")
        if res.status_code == 200:
            try:
                res_json = res.json()
            except ValueError:
                logger.error("提交应答不是 JSON（疑似风控页面），本次答题结果未知")
                return self.StudyResult.ERROR
            if res_json.get("status"):
                logger.info(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题成功 -> {res_json["msg"]}')
            else:
                logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res_json["msg"]}')
                return self.StudyResult.ERROR
        else:
            logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res.text}')
            return self.StudyResult.ERROR
        return self.StudyResult.SUCCESS

    def strdy_read(self, _course, _job, _job_info) -> StudyResult:
        """
        阅读任务学习, 仅完成任务点, 并不增长时长
        """
        _session = init_session()
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": _job["jobid"],
                "knowledgeid": _job_info["knowledgeid"],
                "jtoken": _job["jtoken"],
                "courseid": _course["courseId"],
                "clazzid": _course["clazzId"],
            },
        )
        if self._looks_blocked(_resp):
            raise AntiSpiderError("阅读任务提交被风控拦截，需要输入图形验证码")
        if _resp.status_code != 200:
            logger.error(f"阅读任务学习失败 -> [{_resp.status_code}]{(_resp.text or '')[:200]}")
            return self.StudyResult.ERROR
        try:
            _resp_json = _resp.json()
        except ValueError:
            logger.error("阅读任务返回非 JSON（疑似风控页面），本次结果未知")
            return self.StudyResult.ERROR
        logger.info(f"阅读任务学习 -> {_resp_json.get('msg', '')}")
        return self.StudyResult.SUCCESS

    def study_emptypage(self, _course, _chapterId):
        _session = init_session()
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": _chapterId['id'],
                "cpi": 0,
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
        )
        if _resp.status_code != 200:
            logger.error(f"空页面任务失败 -> [{_resp.status_code}]{_chapterId['title']}")
            return self.StudyResult.ERROR
        else:
            logger.info(f"空页面任务完成 -> {_chapterId['title']}")
            return self.StudyResult.SUCCESS