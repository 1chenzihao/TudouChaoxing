# -*- coding: utf-8 -*-
"""准确率探针：用真实大模型跑一组有确定标准答案的题，统计答对率。

题目是固定写死的（网络基础常识，答案无争议），选项顺序也写死，
所以每题的正确答案是确定的，能直接算出准确率并打印每题细节。

用法：
    CHX_LLM_KEY=sk-xxx python tools/accuracy_probe.py
    CHX_LLM_KEY=sk-xxx CHX_LLM_MODEL=deepseek-chat python tools/accuracy_probe.py --votes 1

参数：
    --votes N        每题采样次数（默认 3，和 config.ini 默认一致）
    --confidence F   可信阈值（默认 0.67）
    --json           额外输出机器可读的结果
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.llm import LLMClient, LLMConfig
from api.solver import Question, QuestionSolver

# 每题：题型 / 题干 / 选项 / 标准答案（提交格式）
CASES = [
    ("single", "HTTP 协议默认使用的端口号是多少？",
     "A. 21\nB. 80\nC. 443\nD. 8080", "B"),
    ("single", "TCP 三次握手中，第二步客户端收到的报文标志位是什么？",
     "A. 只有 SYN\nB. 只有 ACK\nC. SYN 和 ACK 同时置位\nD. FIN 置位", "C"),
    ("single", "OSI 七层参考模型中，第三层是哪一层？",
     "A. 数据链路层\nB. 网络层\nC. 传输层\nD. 会话层", "B"),
    ("single", "DNS 服务默认使用的端口号是多少？",
     "A. 53\nB. 67\nC. 110\nD. 161", "A"),
    ("multiple", "下列哪些属于面向连接的传输层协议？",
     "A. TCP\nB. UDP\nC. SCTP\nD. IP", "AC"),
    ("multiple", "下列哪些属于应用层协议？",
     "A. HTTP\nB. FTP\nC. TCP\nD. DNS", "ABD"),
    ("multiple", "下列哪些是 IPv4 的私有地址段？",
     "A. 10.0.0.0/8\nB. 172.16.0.0/12\nC. 192.168.0.0/16\nD. 8.8.8.0/24", "ABC"),
    ("judgement", "UDP 协议提供可靠的、面向连接的数据传输服务。",
     "A. 正确\nB. 错误", "false"),
    ("judgement", "IP 协议提供的是无连接、不可靠的数据报服务。",
     "A. 正确\nB. 错误", "true"),
    ("judgement", "交换机默认工作在 OSI 模型的数据链路层。",
     "A. 正确\nB. 错误", "true"),
    ("completion", "在 TCP/IP 参考模型中，负责端到端通信的层次是________。", "", "传输层"),
    ("completion", "一个 IPv4 地址的长度是________位。", "", "32"),
]


def build_solver(votes: int, confidence: float) -> QuestionSolver:
    conf = {
        "endpoint": os.environ.get("CHX_LLM_ENDPOINT", "https://api.deepseek.com/v1"),
        "key": os.environ.get("CHX_LLM_KEY", ""),
        "model": os.environ.get("CHX_LLM_MODEL", "deepseek-chat"),
        "min_interval_seconds": "0.2",
        "max_retries": "3",
        "timeout": "90",
    }
    config = LLMConfig.from_mapping(conf)
    if not config.api_key:
        raise SystemExit("没有 API key：请设置环境变量 CHX_LLM_KEY")
    client = LLMClient(config)
    return QuestionSolver(client, votes=votes, temperature=0.6, confidence=confidence, cache=None)


def norm_completion(text: str) -> str:
    return str(text).replace(" ", "").replace("\n", "").replace("位", "") if "位" in str(text) else str(text).replace(" ", "").replace("\n", "")


def check(case_type: str, got: str, expected: str) -> bool:
    if case_type == "multiple":
        return set(got.upper()) == set(expected.upper())
    if case_type == "judgement":
        return got.lower() == expected.lower()
    if case_type == "completion":
        return norm_completion(got) == norm_completion(expected)
    return got.strip().upper() == expected.strip().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--votes", type=int, default=3)
    parser.add_argument("--confidence", type=float, default=0.67)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    solver = build_solver(args.votes, args.confidence)
    print(f"[开始] 共 {len(CASES)} 题, votes={args.votes}, confidence={args.confidence}")
    print(f"[模型] {solver.client.config.model} @ {solver.client.config.endpoint}")
    print("-" * 92)

    rows = []
    correct = 0
    started = time.time()
    for index, (case_type, title, options, expected) in enumerate(CASES, start=1):
        question = Question.from_dict(
            {"id": f"q{index}", "title": title, "options": options, "type": case_type}
        )
        began = time.time()
        result = solver.solve(question)
        cost = time.time() - began
        ok = check(case_type, result.answer, expected)
        correct += 1 if ok else 0
        rows.append(
            {
                "no": index,
                "type": case_type,
                "title": title,
                "expected": expected,
                "got": result.answer,
                "ok": ok,
                "confident": result.confident,
                "source": result.source,
                "samples": result.samples,
                "cost": round(cost, 2),
            }
        )
        print(
            f"{'✅' if ok else '❌'} {index:>2}. [{case_type:<10}] 标准={expected!r:<8} 模型={result.answer!r:<12} "
            f"confident={str(result.confident):<5} samples={result.samples} {cost:.1f}s"
        )
        print(f"      题: {title}")
        if not ok:
            print(f"      票型: {result.votes}  说明: {result.detail[:70]}")

    total_time = time.time() - started
    stats = solver.client.stats
    print("-" * 92)
    print(f"[结果] 答对 {correct}/{len(CASES)} = {correct / len(CASES) * 100:.1f}%")
    print(f"[耗时] {total_time:.1f}s（平均 {total_time / len(CASES):.1f}s/题）")
    print(
        f"[用量] API 调用 {stats.calls} 次, 失败 {stats.failures} 次, 重试 {stats.retries} 次, "
        f"prompt_tokens={stats.prompt_tokens}, completion_tokens={stats.completion_tokens}"
    )
    wrong = [row for row in rows if not row["ok"]]
    if wrong:
        print("[错题]")
        for row in wrong:
            print(f"  第{row['no']}题 标准={row['expected']!r} 模型={row['got']!r} 票型={row['confident']}")
    else:
        print("[错题] 无")
    if args.json:
        print(json.dumps({"rows": rows, "accuracy": correct / len(CASES)}, ensure_ascii=False))
    solver.client.close()
    return 0 if correct == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
