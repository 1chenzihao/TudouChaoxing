# -*- coding: utf-8 -*-
"""离线端到端验证：题目 HTML -> 解析 -> 大模型投票作答 -> 答案合法性断言

不联网、不需要 API key：用假的 transport 顶替 OpenAI 接口，并按设计掺入噪声
（错误字母 / markdown 包裹 / 少选），验证投票与兜底确实把答案救回来了。

跑法： cd 仓库根 && python tools/e2e_offline.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.answer import TikuLLM, CacheDAO
from api.base import Chaoxing
from api.decode import decode_questions_info
from api.llm import LLMClient, LLMConfig
from api.solver import Question, QuestionSolver

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "questions_page.html"

# 每道题的"真值"与噪声剧本：按题干关键词匹配，第 n 次采样给出第 n 个剧本
SCRIPTS = {
    "计算机网络的最终目的": {
        "truth": {"answer": ["B"]},
        "noise": [
            {"answer": ["A"]},
            None,
            "```json\n{\"answer\": [\"B\"], \"reason\": \"资源共享是核心目的\"}\n```",
        ],
    },
    "传输层协议": {
        "truth": {"answer": ["A", "B", "C"]},
        "noise": [{"answer": ["A", "B"]}, {"answer": ["A", "B", "C", "D"]}, {"answer": ["A", "C"]}],
    },
    "无连接、不可靠": {
        "truth": {"answer": ["正确"]},
        "noise": [{"answer": ["错误"]}, {"answer": ["正确"]}, {"answer": ["正确"]}],
    },
    "TCP/IP 参考模型": {
        "truth": {"answer": ["传输层"]},
        "noise": [{"answer": ["传输层"]}, {"answer": ["传输层"]}, {"answer": ["网络层"]}],
    },
}

calls = {}


def fake_transport(messages, **kwargs):
    """假模型：同一道题第 1 次给噪声、后面给真值，模拟真实模型的不稳定输出。"""
    user = ""
    for message in messages:
        if message.get("role") == "user":
            user = message.get("content", "")
            break
    for keyword, script in SCRIPTS.items():
        if keyword in user:
            index = calls.get(keyword, 0)
            calls[keyword] = index + 1
            noise = script["noise"]
            if index < len(noise) and noise[index] is not None:
                payload = noise[index]
            else:
                payload = script["truth"]
            if isinstance(payload, str):
                return payload
            return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"answer": ["A"]}, ensure_ascii=False)


CONF = {
    "provider": "TikuLLM",
    "submit": "false",
    "cover_rate": "0.8",
    "true_list": "正确,对,√,是",
    "false_list": "错误,错,×,否,不对,不正确",
    "endpoint": "https://example.invalid/v1",
    "key": "sk-offline-test",
    "model": "offline-fake",
    "votes": "3",
    "temperature": "0.6",
    "confidence": "0.67",
    "max_escalations": "2",
    "min_interval_seconds": "0",
    "cache_enabled": "false",
}


def build_chaoxing():
    tiku = TikuLLM()
    tiku.config_set(CONF)
    tiku.init_tiku()
    # 把真实客户端换成注入 transport 的离线客户端，其余流程一字不改
    tiku.client = LLMClient(LLMConfig.from_mapping(CONF), transport=fake_transport)
    tiku.solver = QuestionSolver(
        tiku.client, votes=3, temperature=0.6, confidence=0.67, max_escalations=2, cache=None
    )
    return tiku


def main():
    html = FIXTURE.read_text(encoding="utf-8")
    decoded = decode_questions_info(html)
    questions = decoded["questions"]
    assert len(questions) == 4, f"题目数量不对: {len(questions)}"
    print(f"[1] 解析到 {len(questions)} 道题")

    single = questions[0]
    assert single["type"] == "single"
    assert "B. 资源共享" in single["options"], single["options"]
    multiple = questions[1]
    assert multiple["type"] == "multiple"
    for key in ("A. TCP", "B. SCTP", "C. UDP", "D. ICMP"):
        assert key in multiple["options"], (key, multiple["options"])
    print("[2] 选项解析正确（多选已按字母重排）")

    chaoxing = Chaoxing(tiku=build_chaoxing())
    stats = chaoxing._solve_questions(questions, context="离线端到端")

    answers = {q["id"]: q["answerField"][f"answer{q['id']}"] for q in questions}
    sources = {q["id"]: q[f"answerSource{q['id']}"] for q in questions}
    print("[3] 作答结果:")
    for q in questions:
        print(f"    {q['id']} [{q['type']}] -> {answers[q['id']]!r}  ({sources[q['id']]})")

    # ---- 正确率断言：噪声被投票吃掉，最终答案必须是对的 ----
    assert answers["8001"] == "B", f"单选投票失败: {answers['8001']}"
    assert set(answers["8002"]) == {"A", "B", "C"}, f"多选投票失败: {answers['8002']}"
    assert answers["8003"] == "true", f"判断投票失败: {answers['8003']}"
    assert "传输层" in answers["8004"], f"填空聚类失败: {answers['8004']}"

    # ---- 合法性断言：答案必须能直接提交 ----
    assert re.fullmatch(r"[A-Z]", answers["8001"]), answers["8001"]
    assert re.fullmatch(r"[A-Z]{2,}", answers["8002"]), answers["8002"]
    assert answers["8003"] in ("true", "false"), answers["8003"]
    assert answers["8004"].strip(), answers["8004"]

    assert stats["coverage"] == 100.0, stats
    assert stats["sources"]["random"] == 0, stats
    print(f"[4] 统计: {stats}")
    print(f"[5] 大模型调用次数: {sum(calls.values())} 次（含噪声采样）")

    # ---- 缓存层：同一道题第二次应命中 ----
    cache_file = Path(__file__).resolve().parent / ".e2e_cache.json"
    cache_file.unlink(missing_ok=True)
    cache = CacheDAO(str(cache_file))
    solver = QuestionSolver(
        LLMClient(LLMConfig.from_mapping({}), transport=fake_transport),
        votes=3,
        cache=cache,
    )
    q = Question.from_dict(questions[0])
    first = solver.solve(q)
    second = solver.solve(q)
    assert second.source == "cache", second.source
    assert second.answer == first.answer, (first.answer, second.answer)
    cache_file.unlink(missing_ok=True)
    print(f"[6] 缓存命中验证通过: {first.answer} -> {second.answer} (source={second.source})")

    print("")
    print("OK 离线端到端全部通过：解析 -> 投票作答 -> 答案合法 -> 覆盖率 100% -> 缓存命中")


if __name__ == "__main__":
    main()
