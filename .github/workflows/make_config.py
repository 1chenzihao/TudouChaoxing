# -*- coding: utf-8 -*-
"""从环境变量生成 config.ini —— GitHub Actions 专用（本地也能用）。

为什么需要这个脚本：
    账号密码、大模型 key 只放在仓库 Settings → Secrets and variables → Actions 里，
    不写进 YAML 字面量、也不写进代码；由脚本读环境变量落成 config.ini，
    顺带避免用 heredoc 拼文件时把密码回显到 Actions 日志。

环境变量分两类，同名信息同时存在时 **INPUT_* 优先**（手动触发填的参数压过 Secrets）：
    Secrets 侧：CHX_USER / CHX_PASS / CHX_LLM_KEY / CHX_LLM_ENDPOINT
                / CHX_LLM_MODEL / CHX_COURSE / CHX_SPEED
                / CHX_DELAY_MIN / CHX_DELAY_MAX（章节间隔秒数，可选，默认 3 / 9）
    inputs 侧 ：INPUT_COURSE_LIST / INPUT_MAX_CHAPTERS / INPUT_SOLVE_ONLY
                / INPUT_SCAN_JOBS / INPUT_SUBMIT

本地用法：
    set CHX_USER=手机号 && set CHX_PASS=密码 && set CHX_LLM_KEY=sk-xxx
    python .github/workflows/make_config.py
    （PowerShell 用 $env:CHX_USER="手机号" 这种写法）

退出码：
    0  生成成功（没有 key 只是警告，仍然退出 0，程序会退化成随机作答）
    1  缺账号或密码 —— 此时不写任何文件
"""
import os
import sys
from pathlib import Path

DEFAULT_CONFIG_PATH = "config.ini"
DEFAULT_ENDPOINT = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# 开关类输入的归一化表：GitHub Actions 的 choice 传进来是字符串 "true"/"false"，
# 本地手敲可能是 1/0/yes/on，统一收敛成 true / false 再写进配置。
_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}
_FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}

SECRETS_URL_HINT = (
    "仓库页面 → Settings → Secrets and variables → Actions → New repository secret"
)


def env(name: str, default: str = "") -> str:
    """读环境变量并去掉首尾空白；变量不存在时返回默认值。"""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def pick(*names: str, default: str = "") -> str:
    """按顺序取第一个非空的环境变量 —— 这就是 inputs 优先于 Secrets 的实现点。"""
    for name in names:
        value = env(name)
        if value:
            return value
    return default


def to_bool(raw: str, default: bool) -> str:
    """把任意写法的开关归一成 'true' / 'false' 字符串。"""
    text = str(raw or "").strip().lower()
    if text in _TRUE_VALUES:
        return "true"
    if text in _FALSE_VALUES:
        return "false"
    return "true" if default else "false"


def to_float(raw: str, default: float) -> float:
    """把章节间隔这类秒数归一；空值或非法值退回默认，不要让脚本崩掉。"""
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        print(f"[警告] 章节间隔填了非数字 {text!r}，按默认 {default:g} 秒处理")
        return default


def to_int(raw: str, default: int) -> int:
    """把章节上限这类数字归一；非法值退回默认值，不要让脚本崩掉。"""
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        print(f"[警告] 章节上限填了非数字 {text!r}，按 {default}（不限制）处理")
        return default


def ini_value(raw: str) -> str:
    """写进 ini 的值做最小保护。

    main.py 读配置用的是 configparser.ConfigParser()（默认开启 %(name)s 插值），
    密码里带 % 会让它在读取时报 InterpolationSyntaxError，所以这里把 % 转义成 %%，
    读出来仍然是原来的一个 %。
    """
    return str(raw or "").replace("%", "%%")


def build_config() -> tuple[str, dict]:
    """拼出 config.ini 的正文，同时返回一份摘要信息（摘要里绝不含密码和 key）。"""
    username = env("CHX_USER")
    password = env("CHX_PASS")
    missing = [name for name in ("CHX_USER", "CHX_PASS") if not env(name)]
    if missing:
        # 一行醒目提示：新手最常漏的就是没加 Secret
        print(
            f"【生成配置失败】缺少必要 Secret：{', '.join(missing)}。"
            f"请到 {SECRETS_URL_HINT} 里添加 {', '.join(missing)} 后重新运行。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # 课程：手动触发时填的 inputs.course_list 优先，留空才用 secret CHX_COURSE
    course_list = pick("INPUT_COURSE_LIST", "CHX_COURSE")
    course_source = "手动触发的课程列表" if env("INPUT_COURSE_LIST") else (
        "Secrets 里的默认课程" if env("CHX_COURSE") else "未指定（运行时列出全部课程）"
    )

    max_chapters = to_int(pick("INPUT_MAX_CHAPTERS", "CHX_MAX_CHAPTERS"), 0)
    solve_only = to_bool(pick("INPUT_SOLVE_ONLY", "CHX_SOLVE_ONLY"), False)
    scan_jobs = to_bool(pick("INPUT_SCAN_JOBS", "CHX_SCAN_JOBS"), False)
    submit = to_bool(pick("INPUT_SUBMIT", "CHX_SUBMIT"), True)
    speed = pick("CHX_SPEED", default="2")
    # 每章之间的随机停顿：测验轮可以调大（比如 20~60）来降低提交密度
    delay_min = to_float(pick("CHX_DELAY_MIN"), 3.0)
    delay_max = to_float(pick("CHX_DELAY_MAX"), 9.0)
    if delay_min < 0 or delay_max < 0 or delay_min > delay_max:
        print(
            f"[警告] 章节间隔 CHX_DELAY_MIN={delay_min:g} / CHX_DELAY_MAX={delay_max:g} 不合理"
            "（要求 0 <= min <= max），按 3~9 秒处理"
        )
        delay_min, delay_max = 3.0, 9.0

    # 长休息（防风控）：连续刷 N 章后静默一段时间。0 = 关掉
    rest_every = to_int(pick("CHX_REST_EVERY"), 8)
    rest_min = to_float(pick("CHX_REST_MIN"), 120.0)
    rest_max = to_float(pick("CHX_REST_MAX"), 300.0)
    if rest_every < 0 or rest_min < 0 or rest_min > rest_max:
        print(
            f"[警告] 长休息 CHX_REST_EVERY={rest_every} / {rest_min:g}~{rest_max:g} 秒不合理，"
            "按 8 章 / 120~300 秒处理"
        )
        rest_every, rest_min, rest_max = 8, 120.0, 300.0

    llm_key = pick("CHX_LLM_KEY")
    llm_endpoint = pick("CHX_LLM_ENDPOINT", default=DEFAULT_ENDPOINT)
    llm_model = pick("CHX_LLM_MODEL", default=DEFAULT_MODEL)

    if not llm_key:
        # 不退出：没有 key 会退化成随机作答，配置本身仍然有效
        print(
            "[警告] 没有配置 CHX_LLM_KEY，题库会退化成随机作答、正确率没法保证。"
            f"建议去 {SECRETS_URL_HINT} 里加一个 CHX_LLM_KEY。"
        )

    lines = [
        "; 本文件由 .github/workflows/make_config.py 从环境变量生成，不要提交到仓库",
        "; 账号密码与大模型 key 只在 CI 的内存里存在，跑完即随 runner 销毁",
        "",
        "[common]",
        "; 手机号账号（Secrets: CHX_USER）",
        f"username = {ini_value(username)}",
        "; 登录密码（Secrets: CHX_PASS）",
        f"password = {ini_value(password)}",
        "; 要学习的课程 ID，英文逗号分隔；留空 = 运行时列出全部课程",
        f"course_list = {ini_value(course_list)}",
        "; 视频播放倍速（1 ~ 2，超过按 2 处理）",
        f"speed = {ini_value(speed)}",
        "; 只做章节测验与作业：true 时跳过视频 / 文档 / 阅读（对应 inputs.solve_only）",
        f"solve_only = {solve_only}",
        "; 每章之间的随机停顿（秒）：对应 Secrets CHX_DELAY_MIN / CHX_DELAY_MAX，测验轮建议 20~60",
        f"chapter_delay_min = {delay_min:g}",
        f"chapter_delay_max = {delay_max:g}",
        "; 每门课最多处理几个未完成章节；0 = 不限制（对应 inputs.max_chapters）",
        f"max_chapters = {max_chapters}",
        "; 任务点巡检：true 时只读不写，一个学习请求都不发（对应 inputs.scan_jobs）",
        f"scan_jobs = {scan_jobs}",
        "; 防超星风控的长休息：连刷 rest_every 章后静默 rest_min~rest_max 秒（0 章 = 关闭）",
        "; 对应 Secrets CHX_REST_EVERY / CHX_REST_MIN / CHX_REST_MAX",
        f"rest_every = {rest_every}",
        f"rest_min = {rest_min:g}",
        f"rest_max = {rest_max:g}",
        "; 遇到未开放章节的行为：retry / ask / continue（CI 里没人能回答，用 retry）",
        "notopen_action = retry",
        "",
        "[tiku]",
        "; 题库实现，必须是 TikuLLM（漏了这一行题库会被静默停用，全部题变成随机作答）",
        "provider = TikuLLM",
        "; OpenAI 兼容接口，DeepSeek / 硅基流动 / 中转站都行",
        f"endpoint = {ini_value(llm_endpoint)}",
        f"key = {ini_value(llm_key)}",
        f"model = {ini_value(llm_model)}",
        "; 每题独立采样次数（投票用），越大越准越慢",
        "votes = 3",
        "; 判定可信的最低票占比",
        "confidence = 0.67",
        "temperature = 0.6",
        "max_escalations = 2",
        "min_interval_seconds = 1",
        "cache_enabled = true",
        "; 答完是否直接提交（对应 inputs.submit）；false = 只保存，你回头自己提交",
        f"submit = {submit}",
        "; 覆盖率低于这个比例就不提交，防止乱答一通",
        "cover_rate = 0.8",
        "tokens = ",
        "true_list = 正确,对,√,是",
        "false_list = 错误,错,×,否,不对,不正确",
        "",
    ]

    summary = {
        "课程": course_list or "（未指定，运行时列出全部课程）",
        "课程来源": course_source,
        "章数上限": f"{max_chapters}（0 = 不限制）",
        "只做测验": solve_only,
        "章节间隔": f"{delay_min:g}~{delay_max:g} 秒",
        "长休息": (f"每 {rest_every} 章停 {rest_min / 60:.1f}~{rest_max / 60:.1f} 分钟"
                   if rest_every else "已关闭"),
        "只读巡检": scan_jobs,
        "答完提交": submit,
        "大模型": f"{llm_model} @ {llm_endpoint}",
        "大模型 key": "已配置" if llm_key else "未配置（会退化成随机作答）",
        "倍速": speed,
    }
    return "\n".join(lines), summary


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_CONFIG_PATH)
    config_text, summary = build_config()

    out_path.write_text(config_text, encoding="utf-8", newline="\n")
    print(f"config.ini 已生成：{out_path.resolve()}（账号来自环境变量，未落仓库）")
    print("==== 本次配置摘要 ====")
    for key, value in summary.items():
        print(f"  {key}：{value}")
    print("======================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
