# -*- coding: utf-8 -*-
"""检查大模型接口连通性：发一次真实请求，打印模型/延迟/用量/是否支持 json_mode。

用法：
    config.ini 里配好 [tiku] 的 endpoint/key/model 后： python tools/check_llm.py
    或者直接用环境变量：  CHX_LLM_KEY=sk-xxx python tools/check_llm.py
    （可选）CHX_LLM_ENDPOINT / CHX_LLM_MODEL 覆盖默认值
"""
import configparser
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.llm import LLMClient, LLMConfig, LLMError


def load_conf() -> dict:
    conf = {}
    path = Path("config.ini")
    if path.is_file():
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf8")
        if parser.has_section("tiku"):
            conf = dict(parser.items("tiku"))
            print(f"[配置] 读到 config.ini 的 [tiku] 段")
    else:
        print("[配置] 没有 config.ini，只用环境变量")
    return conf


def main() -> int:
    conf = load_conf()
    config = LLMConfig.from_mapping(conf)
    if not config.api_key:
        print("[失败] 没有拿到 API key：config.ini 的 [tiku] key 或环境变量 CHX_LLM_KEY 都没填")
        return 2

    masked = config.api_key[:6] + "..." + config.api_key[-4:] if len(config.api_key) > 12 else "***"
    print(f"[配置] endpoint = {config.endpoint}")
    print(f"[配置] model    = {config.model}")
    print(f"[配置] key      = {masked}")

    client = LLMClient(config)
    started = time.time()
    try:
        text = client.chat(
            [
                {"role": "system", "content": '你只输出 JSON，形如 {"ok": true, "name": "你的模型名"}'},
                {"role": "user", "content": "请回一句 JSON，name 填你实际的模型标识。"},
            ],
            temperature=0.0,
            max_tokens=100,
            json_mode=True,
        )
    except LLMError as exc:
        print(f"[失败] 调用失败：{exc}")
        client.close()
        return 1
    elapsed = time.time() - started
    print(f"[成功] 耗时 {elapsed:.2f}s")
    print(f"[成功] 原始返回：{text[:200]}")
    stats = client.stats
    print(
        f"[用量] calls={stats.calls} failures={stats.failures} retries={stats.retries} "
        f"prompt_tokens={stats.prompt_tokens} completion_tokens={stats.completion_tokens}"
    )
    client.close()
    print("[结论] 接口连通，可以开始答题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
