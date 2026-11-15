# -*- coding: utf-8 -*-
"""
超星学习通数据解析模块

该模块负责解析超星学习通平台的课程、章节、任务点等各种数据，
并转换为程序内部使用的结构化数据格式。
"""
import re
import json
from typing import List, Dict, Tuple, Any, Optional
from bs4 import BeautifulSoup, NavigableString
from api.logger import logger
from api.font_decoder import FontDecoder


def _soup(html_text: str):
    """统一的 HTML 解析入口。

    lxml 在某些 Python 版本上没有现成 wheel，装不上不该让整个程序起不来；
    解析器缺了就退回标准库的 html.parser（慢一点，但能跑）。
    """
    try:
        return _soup(html_text)
    except Exception:  # bs4.FeatureNotFound / ImportError
        logger.warning("lxml 解析器不可用，已退回 html.parser（建议重装 lxml）")
        return BeautifulSoup(html_text, "html.parser")


def decode_course_list(html_text: str) -> List[Dict[str, str]]:
    """
    解析课程列表页面，提取课程信息
    
    Args:
        html_text: 课程列表页面的HTML内容
        
    Returns:
        课程信息列表，每个课程包含id、title、teacher等信息
    """
    logger.trace("开始解码课程列表...")
    soup = _soup(html_text)
    raw_courses = soup.select("div.course")
    course_list = []
    
    for course in raw_courses:
        # 跳过未开放课程
        if course.select_one("a.not-open-tip") or course.select_one("div.not-open-tip"):
            continue
        
        course_detail = {
            "id": course.attrs["id"],
            "info": course.attrs["info"],
            "roleid": course.attrs["roleid"],
            "clazzId": course.select_one("input.clazzId").attrs["value"],
            "courseId": course.select_one("input.courseId").attrs["value"],
            "cpi": re.findall(r"cpi=(.*?)&", course.select_one("a").attrs["href"])[0],
            "title": course.select_one("span.course-name").attrs["title"],
            "desc": course.select_one("p.margint10").attrs["title"] if course.select_one("p.margint10") else "",
            "teacher": course.select_one("p.color3").attrs["title"]
        }
        course_list.append(course_detail)
    
    return course_list


def decode_course_folder(html_text: str) -> List[Dict[str, str]]:
    """
    解析二级课程列表页面，提取文件夹信息
    
    Args:
        html_text: 二级课程列表页面的HTML内容
        
    Returns:
        课程文件夹信息列表
    """
    logger.trace("开始解码二级课程列表...")
    soup = _soup(html_text)
    raw_courses = soup.select("ul.file-list>li")
    course_folder_list = []
    
    for course in raw_courses:
        if not course.attrs.get("fileid"):
            continue
            
        course_folder_detail = {
            "id": course.attrs["fileid"],
            "rename": course.select_one("input.rename-input").attrs["value"]
        }
        course_folder_list.append(course_folder_detail)
    
    return course_folder_list


def decode_course_point(html_text: str) -> Dict[str, Any]:
    """
    解析章节列表页面，提取章节点信息
    
    Args:
        html_text: 章节列表页面的HTML内容
        
    Returns:
        章节信息字典，包含是否锁定状态和章节点列表
    """
    logger.trace("开始解码章节列表...")
    soup = _soup(html_text)
    course_point = {
        "hasLocked": False,  # 用于判断该课程任务是否是需要解锁
        "points": [],
    }

    for chapter_unit in soup.find_all("div", class_="chapter_unit"):
        points = _extract_points_from_chapter(chapter_unit)
        # 检查是否有锁定内容
        for point in points:
            if point.get("need_unlock", False):
                course_point["hasLocked"] = True
                
        course_point["points"].extend(points)
    
    return course_point


def _extract_points_from_chapter(chapter_unit) -> List[Dict[str, Any]]:
    """
    从章节单元中提取章节点信息
    
    Args:
        chapter_unit: BeautifulSoup对象，表示一个章节单元
        
    Returns:
        章节点信息列表
    """
    point_list = []
    raw_points = chapter_unit.find_all("li")
    
    for raw_point in raw_points:
        point = raw_point.div
        if "id" not in point.attrs:
            continue
            
        point_id = re.findall(r"^cur(\d{1,20})$", point.attrs["id"])[0]
        point_title = point.select_one("a.clicktitle").text.replace("\n", "").strip()
        
        # 提取任务数量
        job_count = 1  # 默认为1
        need_unlock = False
        if point.select_one("input.knowledgeJobCount"):
            job_count = point.select_one("input.knowledgeJobCount").attrs["value"]
        elif point.select_one("span.bntHoverTips") and "解锁" in point.select_one("span.bntHoverTips").text:
            need_unlock = True
            
        # 判断是否已完成
        is_finished = False
        if point.select_one("span.bntHoverTips") and "已完成" in point.select_one("span.bntHoverTips").text:
            is_finished = True
            
        point_detail = {
            "id": point_id,
            "title": point_title,
            "jobCount": job_count,
            "has_finished": is_finished,
            "need_unlock": need_unlock
        }
        point_list.append(point_detail)
        
    return point_list


def decode_course_card(
    html_text: str, include_passed: bool = False
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    解析任务点列表页面，提取任务点信息

    Args:
        html_text: 任务点列表页面的HTML内容
        include_passed: 是否把「已通过」的任务点也收集到 job_info["passed_jobs"] 里。
                        默认 False，行为与旧版完全一致；巡检/日志提示时传 True。

    Returns:
        任务点列表和任务信息的元组
    """
    logger.trace("开始解码任务点列表...")
    job_list = []

    # 检查章节是否未开放
    if "章节未开放" in html_text:
        return [], {"notOpen": True}

    # 提取mArg参数
    temp = re.findall(r"mArg=\{(.*?)\};", html_text.replace(" ", ""))
    if not temp:
        # 没有 mArg 说明这个页面不是正常的任务点页（登录态抖动、风控、空白页都会这样）。
        # 绝不能当成"这章没有任务"——那会让整章被静默跳过，显示成"已完成"。
        logger.warning("任务点页面缺少 mArg，判定为异常页面")
        return [], {"invalid": True}
        
    # 解析JSON数据（页面被截断/编码错乱时会解析失败，不能让它抛穿整章）
    try:
        cards_data = json.loads("{" + temp[0] + "}")
    except ValueError as exc:
        logger.warning(f"mArg 解析失败，判定为异常页面: {exc}")
        return [], {"invalid": True}
    if not cards_data:
        return [], {}

    # 提取任务信息
    job_info = _extract_job_info(cards_data)
    
    # 处理所有附件任务
    cards = cards_data.get("attachments", [])
    job_list, passed_jobs = _process_attachment_cards(cards)
    if include_passed:
        # 已通过的任务点单独返回，供日志提示与巡检展示，不参与执行
        job_info["passed_jobs"] = passed_jobs

    return job_list, job_info


def _extract_job_info(cards_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从卡片数据中提取任务基本信息
    
    Args:
        cards_data: 卡片数据字典
        
    Returns:
        任务基本信息字典
    """
    defaults = cards_data.get("defaults", {})
    if not defaults:
        return {}
        
    return {
        "ktoken": defaults.get("ktoken", ""),
        "mtEnc": defaults.get("mtEnc", ""),
        "reportTimeInterval": defaults.get("reportTimeInterval", 60),
        "defenc": defaults.get("defenc", ""),
        "cardid": defaults.get("cardid", ""),
        "cpi": defaults.get("cpi", ""),
        "qnenc": defaults.get("qnenc", ""),
        "knowledgeid": defaults.get("knowledgeid", "")
    }


def _describe_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """把一个任务卡片压成一行摘要，用于日志与巡检展示（不参与执行）。

    已通过的任务点在执行侧会被直接跳过，这里只负责"说清楚跳过了什么"。
    """
    prop = card.get("property") or {}
    card_type = card.get("type") or "unknown"
    if card.get("job", True) is False:
        card_type = "read"
    name = (
        prop.get("name")
        or prop.get("title")
        or prop.get("objectName")
        or card.get("name")
        or ""
    )
    return {
        "type": card_type,
        "name": str(name),
        "jobid": str(card.get("jobid", "")),
        "objectid": str(card.get("objectId", "")),
        "is_passed": bool(card.get("isPassed", False)),
        "duration": prop.get("duration"),
    }


def _process_attachment_cards(cards: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    处理所有附件任务卡片

    Args:
        cards: 附件任务卡片列表

    Returns:
        (待执行的任务列表, 已通过的任务点摘要列表)
    """
    job_list = []
    passed_list = []

    for card in cards:
        # 已完成的任务点不再执行，但要记下来，便于告诉用户"这个已经看过了"
        if card.get("isPassed", False):
            passed_list.append(_describe_card(card))
            continue
            
        # 处理不同类型的任务
        if card.get("job", False) == False:
            # 处理阅读类型任务
            read_job = _process_read_task(card)
            if read_job:
                job_list.append(read_job)
            continue
            
        # 根据任务类型处理
        card_type = card.get("type", "")
        if card_type == "video":
            video_job = _process_video_task(card)
            if video_job:
                job_list.append(video_job)
        elif card_type == "document":
            doc_job = _process_document_task(card)
            if doc_job:
                job_list.append(doc_job)
        elif card_type == "workid":
            work_job = _process_work_task(card)
            if work_job:
                job_list.append(work_job)

    return job_list, passed_list


def _process_read_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理阅读类型任务"""
    if not (card.get("type") == "read" and not card.get("property", {}).get("read", False)):
        return None
        
    return {
        "title": card.get("property", {}).get("title", ""),
        "type": "read",
        "id": card.get("property", {}).get("id", ""),
        "jobid": card.get("jobid", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def _process_video_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理视频类型任务"""
    try:
        return {
            "type": "video",
            "jobid": card.get("jobid", ""),
            "name": card.get("property", {}).get("name", ""),
            "otherinfo": card.get("otherInfo", ""),
            "mid": card["mid"],  # 必须字段，如果不存在会抛出异常
            "objectid": card.get("objectId", ""),
            "aid": card.get("aid", "")
        }
    except KeyError:
        logger.warning("出现转码失败视频，已跳过...")
        return None


def _process_document_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理文档类型任务"""
    return {
        "type": "document",
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", ""),
        "objectid": card.get("property", {}).get("objectid", "")
    }


def _process_work_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理作业类型任务"""
    return {
        "type": "workid",
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def decode_questions_info(html_content: str) -> Dict[str, Any]:
    """
    解析题目信息，提取表单数据和问题列表
    
    Args:
        html_content: 题目页面HTML内容
        
    Returns:
        包含表单数据和问题列表的字典; 页面无 form 或零题目时 questions 为空列表
    """
    soup = _soup(html_content)
    form_data = _extract_form_data(soup)
    
    # 检查是否存在字体加密
    has_font_encryption = bool(soup.find("style", id="cxSecretStyle"))
    font_decoder = None
    
    if has_font_encryption:
        font_decoder = FontDecoder(html_content)
    else:
        logger.warning("未找到字体文件，可能是未加密的题目不进行解密")
    
    # 处理所有问题
    # 考试页整卷可能不在同一个 form 里(由 JS 拼 body), 这里必须空判, 否则直接 AttributeError
    questions = []
    form_tag = soup.find("form")
    if form_tag is None:
        logger.warning("题目页面未找到 form 节点, 无法解析题目, 返回空题目列表")
        question_tags = []
    else:
        question_tags = form_tag.find_all("div", class_="singleQuesId")
        if not question_tags:
            logger.warning("题目页面未找到任何题目节点(div.singleQuesId)")
    
    for div_tag in question_tags:
        question = _process_question(div_tag, font_decoder)
        if question:
            questions.append(question)
    
    # 更新表单数据
    form_data["questions"] = questions
    if questions:
        form_data["answerwqbid"] = ",".join([q["id"] for q in questions]) + ","
    else:
        # 零题目时不要生成畸形值 "," 污染提交体
        form_data["answerwqbid"] = ""
    
    return form_data


def _extract_form_data(soup: BeautifulSoup) -> Dict[str, Any]:
    """从BeautifulSoup对象中提取表单数据"""
    form_data = {}
    form_tag = soup.find("form")
    
    if not form_tag:
        return form_data
    
    # 提取所有非答案字段的input
    for input_tag in form_tag.find_all("input"):
        if "name" not in input_tag.attrs or "answer" in input_tag.attrs["name"]:
            continue
        form_data[input_tag.attrs["name"]] = input_tag.attrs.get("value", "")
    
    return form_data


def _process_question(div_tag, font_decoder=None) -> Optional[Dict[str, Any]]:
    """
    处理单个问题
    
    题目结构不完整(qid 为空 / 缺 div.TiMu)时返回 None, 由调用方跳过该题,
    不让一道畸形题目崩掉整个任务点
    """
    # 提取问题ID和题目类型
    question_id = str(div_tag.attrs.get("data", "") or "").strip()
    if not question_id:
        logger.warning("题目缺少 qid(data 属性), 已跳过该题")
        return None
    timu_tag = div_tag.find("div", class_="TiMu")
    if timu_tag is None:
        logger.warning(f"题目 {question_id} 缺少 div.TiMu 节点, 已跳过该题")
        return None
    q_type_code = timu_tag.attrs.get("data", "")
    q_type = _get_question_type(q_type_code)
    
    # 提取题目内容和选项
    title_div = div_tag.find("div", class_="Zy_TItle")
    options_list = div_tag.find("ul").find_all("li") if div_tag.find("ul") else []
    
    # 解析题目和选项
    q_title = _extract_title(title_div, font_decoder)
    q_options = []
    for li in options_list:
        q_options.append(_extract_choices(li, font_decoder))
    # 排序选项
    q_options.sort()
    q_options = '\n'.join(q_options)
    
    return {
        "id": question_id,
        "title": q_title,
        "options": q_options,
        "type": q_type,
        "answerField": {
            f"answer{question_id}": "",
            f"answertype{question_id}": q_type_code,
        },
    }


def _get_question_type(type_code: str) -> str:
    """根据题型代码返回题型名称"""
    type_map = {
        "0": "single",      # 单选题
        "1": "multiple",    # 多选题
        "2": "completion",  # 填空题
        "3": "judgement",   # 判断题
        "4": "shortanswer", # 简答题
    }
    
    if type_code in type_map:
        return type_map[type_code]
    
    logger.info(f"未知题型代码 -> {type_code}")
    return "unknown"


def _extract_title(element, font_decoder=None) -> str:
    """提取标题内容，支持解码加密字体"""
    if not element:
        return ""
        
    # 收集元素中的所有文本和图片
    content = []
    for item in element.descendants:
        if isinstance(item, NavigableString):
            content.append(item.string or "")
        elif item.name == "img":
            img_url = item.get("src", "")
            content.append(f'<img src="{img_url}">')
    
    raw_content = "".join(content)
    cleaned_content = raw_content.replace("\r", "").replace("\t", "").replace("\n", "")
    
    # 如果有字体解码器，进行解码
    if font_decoder:
        return font_decoder.decode(cleaned_content)
    
    return cleaned_content

def _extract_choices(element, font_decoder=None) -> str:
    """提取选项内容，支持解码加密字体"""
    if not element:
        return ""
        
    # 提取aria-label属性值作为选项，解决#474
    choice = element.get('aria-label')
    if not choice:
        # 部分页面(考试页 / 矩阵题 / 表格型选项)不给 aria-label, 退回节点文本, 避免 None.replace 崩掉整页
        choice = element.get_text()
    
    cleaned_content = choice.replace("\r", "").replace("\t", "").replace("\n", "")
    
    # 如果有字体解码器，进行解码
    if font_decoder:
        return font_decoder.decode(cleaned_content)
    
    return cleaned_content