# -*- coding: utf-8 -*-
"""土豆刷学习通 · 图形配置工具（Tkinter，零依赖）

双击「一键配置.bat」或者直接运行本文件即可。
它做四件事：改 config.ini、测大模型能不能用、改云端定时、一键拉最新代码。
"""
import configparser
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config.ini")
TEMPLATE = os.path.join(ROOT, "config_template.ini")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "main.yml")
MASCOT = os.path.join(ROOT, "assets", "mascot.png")
ICON = os.path.join(ROOT, "assets", "mascot.ico")

# 环境检测/装依赖的逻辑都在 env_setup.py（纯标准库）。这里容错导入：
# 就算这个文件丢了，配置工具本身还能用，只是「环境」页会提示仓库不完整。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    import env_setup
except Exception:  # noqa: BLE001
    env_setup = None
try:
    import cloud_deploy
except Exception:  # noqa: BLE001
    cloud_deploy = None

# 记住上次填的 GitHub 用户名/仓库/Token（本机明文，已在 .gitignore）
CLOUD_PROFILE = os.path.join(ROOT, ".cloud.json")

PAPER = "#F7EFDF"
INK = "#33261A"
SUB = "#6B5842"
ACCENT = "#C87F2E"
CARD = "#FFFFFF"
FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_T = ("Microsoft YaHei UI", 13, "bold")


def setup_style(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", font=FONT, background=PAPER, foreground=INK)
    style.configure("TFrame", background=PAPER)
    style.configure("TLabel", background=PAPER, foreground=INK, font=FONT)
    style.configure("Hint.TLabel", background=PAPER, foreground="#8A7A64", font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", background=PAPER, foreground=INK, font=FONT_T)
    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(6, 8, 6, 0))
    style.configure("TNotebook.Tab", padding=(20, 9), font=FONT_B,
                    background="#EDE1C8", foreground=SUB, borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", CARD)],
              foreground=[("selected", INK)])
    style.configure("TEntry", fieldbackground=CARD, padding=7, borderwidth=1, relief="flat")
    style.configure("TButton", padding=(14, 7), font=FONT, background="#EDE1C8", borderwidth=0)
    style.map("TButton", background=[("active", "#E3D4B4")])
    style.configure("Accent.TButton", padding=(20, 9), font=FONT_B,
                    background=ACCENT, foreground="#FFFFFF", borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#A96A24")])
    style.configure("TCheckbutton", background=PAPER, font=FONT)
    style.map("TCheckbutton", background=[("active", PAPER)])
    style.configure("TRadiobutton", background=PAPER, font=FONT)
    style.map("TRadiobutton", background=[("active", PAPER)])
    style.configure("TSpinbox", fieldbackground=CARD, padding=4)
    root.configure(bg=PAPER)


def read_ini(path):
    """返回 {section: {key: value}}，读不到就返回空"""
    cfg = configparser.ConfigParser(interpolation=None)
    if os.path.exists(path):
        try:
            cfg.read(path, encoding="utf-8")
        except Exception:
            pass
    return cfg


def ini_get(cfg, section, key, default=""):
    try:
        return cfg.get(section, key)
    except Exception:
        return default


def write_ini_values(path, section, values):
    """就地改写 ini，保留原有注释和其它行；缺的键补到对应 section 末尾。"""
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as fp:
            lines = fp.read().splitlines()
    elif os.path.exists(TEMPLATE):
        with open(TEMPLATE, "r", encoding="utf-8", errors="ignore") as fp:
            lines = fp.read().splitlines()

    out = []
    cur = None
    pending = dict(values)
    for line in lines:
        stripped = line.strip()
        m = re.match(r"^\[([^\]]+)\]", stripped)
        if m:
            if cur is not None and pending:
                for k, v in pending.items():
                    out.append(f"{k} = {v}")
                pending.clear()
            cur = m.group(1).strip()
            out.append(line)
            continue
        if cur == section and stripped and not stripped.startswith((";", "#")) and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in pending:
                out.append(f"{k} = {pending.pop(k)}")
                continue
        out.append(line)
    if pending and section:
        if not any(l.strip() == f"[{section}]" for l in out):
            out.append("")
            out.append(f"[{section}]")
        for k, v in pending.items():
            out.append(f"{k} = {v}")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out).rstrip() + "\n")


def parse_workflow():
    """从 main.yml 里读出 cron 和 timeout-minutes"""
    data = {"cron": "", "minutes": 150, "enabled": False}
    if not os.path.exists(WORKFLOW):
        return data
    text = open(WORKFLOW, "r", encoding="utf-8", errors="ignore").read()
    crons = re.findall(r"^\s*-\s*cron:\s*['\"]([^'\"]+)", text, re.M)
    data["crons"] = crons
    if crons:
        data["cron"] = crons[0]
        data["enabled"] = True
    # 第二条 cron 是测验轮，算出它比视频轮晚几个小时
    data["gap"] = 6
    if len(crons) >= 2:
        def _hh(c):
            parts = c.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
            return None
        a, b = _hh(crons[0]), _hh(crons[1])
        if a is not None and b is not None:
            data["gap"] = (b - a) % 24 or 6
    m = re.search(r"timeout-minutes:\s*(\d+)", text)
    if m:
        data["minutes"] = int(m.group(1))
    return data


def write_workflow(hour_bj, minute_bj, minutes, enabled, gap_hours=6):
    """改 main.yml 的 cron 与 timeout-minutes；enabled=False 时把 schedule 注释掉"""
    if not os.path.exists(WORKFLOW):
        return False, "没找到 .github/workflows/main.yml"
    text = open(WORKFLOW, "r", encoding="utf-8", errors="ignore").read()
    utc_hour = (int(hour_bj) - 8) % 24
    cron = f"{int(minute_bj)} {utc_hour} * * *"
    minutes = max(10, min(360, int(minutes)))

    # 1) timeout
    if re.search(r"timeout-minutes:\s*\d+", text):
        text = re.sub(r"timeout-minutes:\s*\d+", f"timeout-minutes: {minutes}", text)
    else:
        text = re.sub(r"^(\s*)runs-on:", lambda m: m.group(1) + f"timeout-minutes: {minutes}" + "\n" + m.group(1) + "runs-on:", text, count=1, flags=re.M)

    # 2) schedule 段
    # 整段替换：从 schedule:（含被注释的）一直到下一个顶层键，避免残留第二条 cron
    block_re = re.compile(r"\n[ \t]*(?:#[ \t]*)?schedule:[^\n]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n)*")
    gap = max(0, min(23, int(gap_hours)))
    quiz_hour = (int(hour_bj) + gap) % 24
    quiz_cron = f"{int(minute_bj)} {(quiz_hour - 8) % 24} * * *"
    hhmm = f"{int(hour_bj):02d}:{int(minute_bj):02d}"
    quiz_hhmm = f"{quiz_hour:02d}:{int(minute_bj):02d}"
    if gap:
        new_block = ("\n  schedule:\n"
                     "    - cron: '" + cron + "'   # 北京 " + hhmm + " 视频轮（只刷视频）\n"
                     "    - cron: '" + quiz_cron + "'   # 北京 " + quiz_hhmm + " 测验轮（只做测验）\n")
    else:
        new_block = ("\n  schedule:\n"
                     "    - cron: '" + cron + "'   # 北京 " + hhmm + " 视频+测验一起刷\n")
    if not enabled:
        if gap:
            new_block = ("\n  # schedule: 已关闭（要用就在配置工具里打开）\n"
                         "  #   - cron: '" + cron + "'\n  #   - cron: '" + quiz_cron + "'\n")
        else:
            new_block = "\n  # schedule: 已关闭（要用就在配置工具里打开）\n  #   - cron: '" + cron + "'\n"
    if block_re.search(text):
        text = block_re.sub(lambda m: new_block, text, count=1)
    else:
        text = re.sub(r"(\n\s*workflow_dispatch:)", lambda m: "\n" + new_block + m.group(1), text, count=1)

    open(WORKFLOW, "w", encoding="utf-8").write(text)
    if gap:
        return True, (f"定时：北京 {hhmm} 视频轮 / {quiz_hhmm} 测验轮，单次最多 {minutes} 分钟"
                      f"（cron: {cron} + {quiz_cron}）")
    return True, f"定时：北京 {hhmm} 开始（一起刷），单次最多 {minutes} 分钟（cron: {cron}）"


class App:
    def __init__(self, master):
        self.master = master
        master.title("土豆刷学习通 · 配置工具")
        master.geometry("940x800")
        master.resizable(False, False)
        setup_style(master)
        self._mascot = None
        try:
            if os.path.exists(ICON):
                master.iconbitmap(ICON)
        except Exception:
            pass
        head = tk.Frame(master, bg=PAPER)
        head.pack(fill="x", padx=16, pady=(10, 2))
        if os.path.exists(MASCOT):
            try:
                img = tk.PhotoImage(file=MASCOT)
                self._mascot = img.subsample(2, 2)
                tk.Label(head, image=self._mascot, bd=0, bg=PAPER).pack(side="left", padx=(0, 14))
            except Exception:
                self._mascot = None
        titles = tk.Frame(head, bg=PAPER)
        titles.pack(side="left", anchor="w")
        tk.Label(titles, text="土豆刷学习通", font=("Microsoft YaHei UI", 19, "bold"),
                 bg=PAPER, fg=INK).pack(anchor="w")
        tk.Label(titles, text="图形配置工具 · 装环境 / 账号 / Key / 定时",
                 font=("Microsoft YaHei UI", 10), bg=PAPER, fg=SUB).pack(anchor="w", pady=(2, 0))
        self._head_btns = tk.Frame(head, bg=PAPER)
        self._head_btns.pack(side="right", anchor="e", padx=(0, 4))

        cfg = read_ini(CONFIG if os.path.exists(CONFIG) else TEMPLATE)
        wf = parse_workflow()

        nb = ttk.Notebook(master)
        # 先不 pack 笔记本，等底栏和状态栏放好再放它，避免按钮被挤出窗口
        self.nb = nb

        # ---------- 账号 ----------
        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="① 账号")
        self.var_user = tk.StringVar(value=ini_get(cfg, "common", "username"))
        self.var_pass = tk.StringVar(value=ini_get(cfg, "common", "password"))
        self.add_row(tab1, 0, "学习通手机号", self.var_user, "11 位手机号")
        self.add_row(tab1, 1, "学习通密码", self.var_pass, "存在本机，不上传", show="*")
        self.var_course = tk.StringVar(value=ini_get(cfg, "common", "course_list"))
        self.add_row(tab1, 2, "课程 ID（逗号分隔）", self.var_course, "留空=启动后选")
        self.var_speed = tk.StringVar(value=ini_get(cfg, "common", "speed", "2"))
        self.add_row(tab1, 3, "视频倍速", self.var_speed, "1 或 2")

        # ---------- 大模型 ----------
        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="② 大模型")
        self.var_provider = tk.StringVar(value=ini_get(cfg, "tiku", "provider", "TikuLLM"))
        self.add_row(tab2, 0, "答题引擎", self.var_provider, "填 TikuLLM，别删", readonly=True)
        self.var_endpoint = tk.StringVar(value=ini_get(cfg, "tiku", "endpoint", "https://api.deepseek.com/v1"))
        self.add_row(tab2, 1, "接口地址", self.var_endpoint, "DeepSeek 照抄左边")
        self.var_key = tk.StringVar(value=ini_get(cfg, "tiku", "key"))
        self.add_row(tab2, 2, "API Key", self.var_key, "sk- 开头", show="*")
        self.var_model = tk.StringVar(value=ini_get(cfg, "tiku", "model", "deepseek-chat"))
        self.add_row(tab2, 3, "模型名", self.var_model, "deepseek-chat")
        self.var_votes = tk.StringVar(value=ini_get(cfg, "tiku", "votes", "3"))
        self.add_row(tab2, 4, "每题投票次数", self.var_votes, "3 够用")
        btn_test = ttk.Button(tab2, text="测试这把 Key 能不能用", command=self.test_key)
        btn_test.grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=14)
        self.lbl_test = ttk.Label(tab2, text="", style="Hint.TLabel", wraplength=560, justify="left")
        self.lbl_test.grid(row=7, column=0, columnspan=2, sticky="w", padx=10)

        # ---------- 运行 ----------
        tab3 = ttk.Frame(nb)
        nb.add(tab3, text="③ 怎么跑")
        self.var_submit = tk.BooleanVar(value=ini_get(cfg, "tiku", "submit", "false").lower() in ("true", "1", "yes", "on"))
        # 运行模式三选一：对应 config.ini 里 solve_only / video_only 两个开关的合法组合
        _solve_on = ini_get(cfg, "common", "solve_only", "false").lower() in ("true", "1", "yes", "on")
        _video_on = ini_get(cfg, "common", "video_only", "false").lower() in ("true", "1", "yes", "on")
        if _video_on:
            _mode = "video"          # 互斥：只刷视频优先
        elif _solve_on:
            _mode = "quiz"
        else:
            _mode = "all"
        self.var_run_mode = tk.StringVar(value=_mode)
        ttk.Label(tab3, text="运行模式（三选一）").grid(row=0, column=0, sticky="w", padx=10, pady=(12, 0))
        for _i, (_val, _text) in enumerate((
            ("all", "视频 + 测验一起刷（默认）"),
            ("video", "只刷视频，测验留到第二轮"),
            ("quiz", "只做测验，跳过视频"),
        )):
            ttk.Radiobutton(tab3, text=_text, value=_val, variable=self.var_run_mode).grid(
                row=1 + _i, column=0, columnspan=2, sticky="w", padx=28, pady=2)
        ttk.Checkbutton(tab3, text="答完直接提交（不勾就只保存）", variable=self.var_submit).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        self.var_maxch = tk.StringVar(value=ini_get(cfg, "common", "max_chapters", "0"))
        self.add_row(tab3, 5, "每门课最多跑几章", self.var_maxch, "0=不限，试水填1")

        row_delay = ttk.Frame(tab3)
        row_delay.grid(row=6, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))
        ttk.Label(row_delay, text="章节间隔").pack(side="left")
        self.var_delay_min = tk.StringVar(value=ini_get(cfg, "common", "chapter_delay_min", "3"))
        ttk.Spinbox(row_delay, from_=0, to=300, width=5, textvariable=self.var_delay_min, format="%3.0f").pack(side="left", padx=(10, 4))
        ttk.Label(row_delay, text="~").pack(side="left")
        self.var_delay_max = tk.StringVar(value=ini_get(cfg, "common", "chapter_delay_max", "9"))
        ttk.Spinbox(row_delay, from_=0, to=300, width=5, textvariable=self.var_delay_max, format="%3.0f").pack(side="left", padx=(4, 4))
        ttk.Label(row_delay, text="秒").pack(side="left")
        ttk.Label(tab3, text="测验轮建议 20~60 秒；想更稳就分两轮：先「只刷视频」跑一次，再「只做测验」跑一次。",
                  style="Hint.TLabel", wraplength=720, justify="left").grid(row=7, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 0))

        # 长休息：超星风控看的是"持续活跃时长"，几秒间隔挡不住，必须掺真静默
        row_rest = ttk.Frame(tab3)
        row_rest.grid(row=9, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))
        ttk.Label(row_rest, text="每连刷").pack(side="left")
        self.var_rest_every = tk.StringVar(value=ini_get(cfg, "common", "rest_every", "8"))
        ttk.Spinbox(row_rest, from_=0, to=100, width=4, textvariable=self.var_rest_every, format="%3.0f").pack(side="left", padx=(8, 3))
        ttk.Label(row_rest, text="章长休息").pack(side="left")
        self.var_rest_min = tk.StringVar(value=ini_get(cfg, "common", "rest_min", "120"))
        ttk.Spinbox(row_rest, from_=0, to=3600, width=5, textvariable=self.var_rest_min, format="%4.0f").pack(side="left", padx=(8, 3))
        ttk.Label(row_rest, text="~").pack(side="left")
        self.var_rest_max = tk.StringVar(value=ini_get(cfg, "common", "rest_max", "300"))
        ttk.Spinbox(row_rest, from_=0, to=3600, width=5, textvariable=self.var_rest_max, format="%4.0f").pack(side="left", padx=(3, 3))
        ttk.Label(row_rest, text="秒").pack(side="left")
        ttk.Label(tab3, text="长休息填 0 章 = 关闭", style="Hint.TLabel").grid(
            row=10, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 0))

        # ---------- 云端定时 ----------
        tab4 = ttk.Frame(nb)
        nb.add(tab4, text="④ 云端定时")
        ttk.Label(tab4, text="这里改的是 GitHub Actions 的定时（云端跑），跟本机开不开机无关。",
                  style="Hint.TLabel", wraplength=620, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(12, 6))
        self.var_cron_on = tk.BooleanVar(value=wf["enabled"])
        ttk.Checkbutton(tab4, text="启用每日定时", variable=self.var_cron_on).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=6)
        hh, mm = "01", "00"
        if wf["cron"]:
            parts = wf["cron"].split()
            if len(parts) >= 2 and parts[1].isdigit():
                mm = parts[0].zfill(2)
                hh = str((int(parts[1]) + 8) % 24).zfill(2)
        self.var_hour = tk.StringVar(value=hh)
        self.var_minute = tk.StringVar(value=mm)
        row = ttk.Frame(tab4)
        row.grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        ttk.Label(row, text="每天几点开始（北京时间）").pack(side="left")
        ttk.Spinbox(row, from_=0, to=23, width=4, textvariable=self.var_hour, format="%02.0f").pack(side="left", padx=(10, 4))
        ttk.Label(row, text="时").pack(side="left")
        ttk.Spinbox(row, from_=0, to=59, width=4, textvariable=self.var_minute, format="%02.0f").pack(side="left", padx=(10, 4))
        ttk.Label(row, text="分").pack(side="left")
        self.var_gap = tk.StringVar(value=str(wf.get("gap") or 6))
        ttk.Label(tab4, text="测验轮晚几小时跑（0 = 只跑一轮，视频和测验一起刷）").grid(row=3, column=0, sticky="w", padx=10, pady=(10, 0))
        ttk.Spinbox(tab4, from_=0, to=23, width=6, textvariable=self.var_gap, format="%02.0f").grid(row=3, column=1, sticky="w", padx=10, pady=(10, 0))
        self.var_minutes = tk.StringVar(value=str(wf["minutes"]))
        self.add_row(tab4, 4, "每次最多跑多少分钟", self.var_minutes, "建议 120~180")
        ttk.Label(tab4, text="提示：跑不完不要紧，进度在超星服务器上，第二天接着刷。分两轮（视频轮 + 测验轮）更不容易被风控。",
                  style="Hint.TLabel", wraplength=620, justify="left").grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 0))

        # ---------- 环境 ----------
        tab5 = ttk.Frame(nb)
        nb.add(tab5, text="⑤ 环境")
        ttk.Label(tab5, text="装依赖不用连外网：自动挑最快的国内镜像，一个不通换下一个，官网打不开也能装上。",
                  style="Hint.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(12, 6))
        self.txt_env = tk.Text(tab5, height=9, wrap="none", font=("Consolas", 9),
                               bg=CARD, fg=INK, relief="flat", bd=0, padx=8, pady=6)
        self.txt_env.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=10)

        rowm = ttk.Frame(tab5)
        rowm.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))
        ttk.Label(rowm, text="用哪个镜像").pack(side="left")
        self.var_mirror = tk.StringVar(value="自动测速")
        ttk.Combobox(rowm, textvariable=self.var_mirror, width=11, state="readonly",
                     values=("自动测速", "华为云", "阿里云", "清华 TUNA", "腾讯云", "中科大", "官方 PyPI")).pack(side="left", padx=(8, 18))
        self.var_web = tk.BooleanVar(value=False)
        ttk.Checkbutton(rowm, text="连网页版依赖一起装", variable=self.var_web).pack(side="left")

        rowb = ttk.Frame(tab5)
        rowb.grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))
        ttk.Button(rowb, text="检查环境", command=self.env_check).pack(side="left")
        ttk.Button(rowb, text="一键安装依赖", style="Accent.TButton",
                   command=self.env_install).pack(side="left", padx=8)
        ttk.Button(rowb, text="下载并安装 Python", command=self.env_python).pack(side="left")
        ttk.Button(rowb, text="建桌面图标", command=self.env_shortcut).pack(side="left", padx=(8, 0))

        self.txt_env_log = tk.Text(tab5, height=5, wrap="word", font=("Consolas", 9),
                                   bg="#241B12", fg="#F6ECD8", insertbackground="#F6ECD8",
                                   relief="flat", bd=0, padx=8, pady=6)
        self.txt_env_log.grid(row=4, column=0, columnspan=3, sticky="nsew", padx=10, pady=(10, 10))
        tab5.columnconfigure(0, weight=1)
        tab5.rowconfigure(4, weight=1, minsize=120)
        self._env_busy = False

        # ---------- 云端部署 ----------
        tab6 = ttk.Frame(nb)
        nb.add(tab6, text="⑥ 云端部署")
        ttk.Label(tab6, text="「GitHub 一键登录」→ 浏览器点一下授权 → 「一键部署并开跑」，完事。",
                  style="Hint.TLabel").grid(row=0, column=0, columnspan=3,
                                            sticky="w", padx=10, pady=(12, 2))

        _cp = cloud_deploy.load_profile(CLOUD_PROFILE) if cloud_deploy else {}
        self.var_gh_user = tk.StringVar(value=_cp.get("owner", ""))
        self.var_gh_repo = tk.StringVar(value=_cp.get("repo", "TudouChaoxing"))
        self.var_gh_token = tk.StringVar(value=_cp.get("token", ""))
        self.add_row(tab6, 1, "GitHub 用户名", self.var_gh_user, "登录名")
        self.add_row(tab6, 2, "仓库名", self.var_gh_repo, "fork 来的同名仓库")
        self.add_row(tab6, 3, "Token", self.var_gh_token, "点下方登录", show="*")

        ttk.Label(tab6, text="写入 4 个 Secrets（账号 / 密码 / Key / 课程 ID），值取自 ①② 页",
                  style="Hint.TLabel").grid(row=4, column=0, columnspan=3,
                                            sticky="w", padx=10, pady=(8, 0))

        rowc = ttk.Frame(tab6)
        rowc.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))
        ttk.Button(rowc, text="GitHub 一键登录", command=self.cloud_login).pack(side="left")
        ttk.Button(rowc, text="检测仓库", command=self.cloud_check).pack(side="left", padx=(8, 0))
        ttk.Button(rowc, text="一键部署并开跑", style="Accent.TButton",
                   command=self.cloud_deploy).pack(side="left", padx=8)
        ttk.Button(rowc, text="打开 Actions 页面", command=self.cloud_open).pack(side="left")

        ttk.Label(tab6, text="Token 只存本机 .cloud.json（已 gitignore），不上传；不用了去 GitHub 删掉即可。",
                  style="Hint.TLabel").grid(row=6, column=0, columnspan=3,
                                            sticky="w", padx=10, pady=(6, 0))

        self.txt_cloud_log = tk.Text(tab6, height=6, wrap="word", font=("Consolas", 9),
                                     bg="#241B12", fg="#F6ECD8", insertbackground="#F6ECD8",
                                     relief="flat", bd=0, padx=8, pady=6)
        self.txt_cloud_log.grid(row=7, column=0, columnspan=3, sticky="nsew", padx=10, pady=(10, 10))
        tab6.columnconfigure(0, weight=1)
        tab6.rowconfigure(7, weight=1, minsize=90)

        # ---------- 底部按钮 ----------
        bar = ttk.Frame(master)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        ttk.Button(self._head_btns, text="保存全部", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(self._head_btns, text="一键更新代码", command=self.run_update).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="保存全部", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(bar, text="打开配置文件", command=self.open_ini).pack(side="left", padx=8)
        ttk.Button(bar, text="退出", command=master.destroy).pack(side="right")
        self.status = ttk.Label(master, text="改完记得点「保存全部」", style="Hint.TLabel")
        self.status.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        nb.pack(fill="both", expand=True, padx=12, pady=(4, 2))

        # 开界面就把环境查一遍，缺东西直接摆在脸上，不用用户自己想到去点
        master.after(300, self.env_check)

    # ---------------- 环境页 ----------------

    def _env_say(self, text):
        """往深色日志框追加一行；窗口被关掉时静默忽略"""
        try:
            if self.txt_env_log.winfo_exists():
                self.txt_env_log.insert("end", str(text) + "\n")
                self.txt_env_log.see("end")
        except tk.TclError:
            pass

    def _env_report(self, text):
        try:
            if self.txt_env.winfo_exists():
                self.txt_env.delete("1.0", "end")
                self.txt_env.insert("1.0", text)
        except tk.TclError:
            pass

    def _env_mirror(self):
        choice = (self.var_mirror.get() or "自动测速").strip()
        return "auto" if choice.startswith("自动") else choice

    def env_check(self):
        """检查 Python / pip / 依赖，结果写在上面的白框里"""
        if env_setup is None:
            self._env_report("仓库里没找到 env_setup.py，从 GitHub 重新下载一份完整代码。")
            return
        if self._env_busy:
            return
        self._env_busy = True
        self.status.config(text="正在检查运行环境…", style="Hint.TLabel", foreground=SUB)

        def worker():
            lines = []
            try:
                info = env_setup.check_all(include_web=True)
                env_setup.report(info, log=lines.append, compact=True)
                tip = ("缺 %d 个必需依赖，点「一键安装依赖」" % len(info["missing_required"])
                       if info["missing_required"] else "依赖齐全，可以开跑")
            except Exception as exc:  # noqa: BLE001 - 检查失败不能把界面搞崩
                lines.append("检查失败: %s: %s" % (type(exc).__name__, exc))
                tip = "环境检查失败"
            text = "\n".join(lines)
            self.master.after(0, lambda: self._env_check_done(text, tip))

        threading.Thread(target=worker, daemon=True).start()

    def _env_check_done(self, text, tip):
        self._env_busy = False
        self._env_report(text)
        self.status.config(text=tip, style="Hint.TLabel", foreground=SUB)

    def env_install(self):
        """一键装依赖：自动挑镜像，失败换下一个"""
        if env_setup is None:
            messagebox.showwarning("缺文件", "仓库里没找到 env_setup.py，从 GitHub 重新下载一份完整代码。")
            return
        if self._env_busy:
            messagebox.showinfo("稍等", "上一个任务还在跑，等它跑完。")
            return
        web = bool(self.var_web.get())
        mirror = self._env_mirror()
        if not messagebox.askyesno(
            "安装依赖",
            "会用国内镜像（%s）装一遍依赖。\n\n%s\n\n装的时候别关窗口，大概 1~3 分钟。"
            % (self.var_mirror.get(), "包含网页版依赖 flask/celery。" if web else "只装刷课必需的包。"),
        ):
            return
        self._env_busy = True
        self.txt_env_log.delete("1.0", "end")
        self._env_say("开始安装依赖（镜像: %s）…" % self.var_mirror.get())
        self.status.config(text="正在安装依赖…", style="Hint.TLabel", foreground=ACCENT)

        def worker():
            ok = False
            try:
                ok = env_setup.install_all(
                    log=lambda s="": self.master.after(0, lambda t=s: self._env_say(t)),
                    mirror=mirror, include_web=web,
                )
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self._env_say("[失败] " + e))
            self.master.after(0, lambda: self._env_install_done(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _env_install_done(self, ok):
        self._env_busy = False
        self._env_say("\n[完成] 依赖装好了，重新检查一遍…" if ok else "\n[失败] 看上面的红字，或换个镜像再试")
        self.env_check()
        if ok:
            messagebox.showinfo("装好了", "依赖都装上了。\n现在可以双击「一键配置.bat」填账号，或者直接跑 main.py。")

    def env_shortcut(self):
        """建桌面快捷方式（等价于双击「初始化快捷方式.bat」，只是不用去找文件）"""
        script = os.path.join(ROOT, "tools", "make_shortcut.ps1")
        if not os.path.exists(script):
            messagebox.showwarning("缺文件", "没找到 tools/make_shortcut.ps1，从 GitHub 重新下一份完整代码。")
            return
        if self._env_busy:
            messagebox.showinfo("稍等", "上一个任务还在跑，等它跑完。")
            return
        self._env_busy = True
        self._env_say("正在创建桌面快捷方式…")
        self.status.config(text="正在创建桌面快捷方式…", style="Hint.TLabel", foreground=ACCENT)

        def worker():
            ok = False
            try:
                proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", script, "-NoPause"],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="ignore", bufsize=1,
                )
                for line in proc.stdout:
                    self.master.after(0, lambda l=line: self._env_say(l.rstrip()))
                ok = proc.wait() == 0
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self._env_say("[失败] " + e))
            self.master.after(0, lambda: self._shortcut_done(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _shortcut_done(self, ok):
        self._env_busy = False
        if ok:
            self.status.config(text="桌面图标已创建", style="Hint.TLabel", foreground="#1a7f37")
            messagebox.showinfo("好了", "桌面已经有「土豆刷学习通 配置工具」图标了，双击就能开。")
        else:
            self.status.config(text="桌面图标没建成，看日志", style="Hint.TLabel", foreground="#b5462f")

    def env_python(self):
        """本机没装 Python（或装的是应用商店占位版）时，从国内镜像下安装包"""
        if env_setup is None:
            messagebox.showwarning("缺文件", "仓库里没找到 env_setup.py。")
            return
        if self._env_busy:
            messagebox.showinfo("稍等", "上一个任务还在跑，等它跑完。")
            return
        if not messagebox.askyesno(
            "下载 Python",
            "会从国内镜像（华为云 / npmmirror）下载 Python %s 安装包，约 25 MB，然后静默安装。\n\n"
            "不需要管理员权限，装完要重开一次配置工具。继续吗？" % env_setup.PYTHON_VERSION,
        ):
            return
        self._env_busy = True
        self._env_say("开始下载 Python %s …" % env_setup.PYTHON_VERSION)
        self.status.config(text="正在下载 Python…", style="Hint.TLabel", foreground=ACCENT)

        def worker():
            ok = False
            try:
                ok = env_setup.download_python(
                    log=lambda s="": self.master.after(0, lambda t=s: self._env_say(t)))
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self._env_say("[失败] " + e))
            self.master.after(0, lambda: self._env_python_done(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _env_python_done(self, ok):
        self._env_busy = False
        if ok:
            self.status.config(text="Python 装好了，重开配置工具再点一键安装依赖",
                               style="Hint.TLabel", foreground="#1a7f37")
            messagebox.showinfo("装好了", "Python 装完了。\n关掉这个窗口重新打开，再点「一键安装依赖」。")
        else:
            self.status.config(text="Python 没装上，看下面日志", style="Hint.TLabel", foreground="#b5462f")

    # ---------------- 云端部署页 ----------------

    def _cloud_say(self, text):
        try:
            if self.txt_cloud_log.winfo_exists():
                self.txt_cloud_log.insert("end", str(text) + "\n")
                self.txt_cloud_log.see("end")
        except tk.TclError:
            pass

    def _cloud_client(self):
        if cloud_deploy is None:
            messagebox.showwarning("缺文件", "仓库里没找到 cloud_deploy.py，从 GitHub 重新下一份完整代码。")
            return None
        owner = self.var_gh_user.get().strip()
        repo = self.var_gh_repo.get().strip()
        token = self.var_gh_token.get().strip()
        if not owner or not repo:
            messagebox.showwarning("填错了", "GitHub 用户名和仓库名都要填。")
            return None
        if not token:
            messagebox.showwarning("填错了", "Token 是空的。\n去 github.com/settings/tokens → Generate new token (classic)\n勾 repo 和 workflow，生成后复制过来。")
            return None
        cloud_deploy.save_profile(CLOUD_PROFILE, {"owner": owner, "repo": repo, "token": token})
        return cloud_deploy.GitHubDeploy(owner, repo, token,
                                        log=lambda s="": self.master.after(0, lambda t=s: self._cloud_say(t)))

    # ---------------- 一键登录（Device Flow） ----------------

    def cloud_login(self, on_success=None):
        """点一下就登录：弹一个窗口显示 6 位码，浏览器授权完自动填 Token。

        比让新手去 Settings → Developer settings → Tokens 里点八下靠谱得多。
        """
        if cloud_deploy is None:
            messagebox.showwarning("缺文件", "仓库里没找到 cloud_deploy.py，从 GitHub 重新下一份完整代码。")
            return
        self._login_stop = False

        win = tk.Toplevel(self.master)
        win.title("用 GitHub 账号登录")
        win.geometry("560x330")
        win.resizable(False, False)
        win.configure(bg=PAPER)
        try:
            if os.path.exists(ICON):
                win.iconbitmap(ICON)
            win.transient(self.master)
            win.grab_set()
        except tk.TclError:
            pass

        ttk.Label(win, text="第 1 步：复制下面这个码", style="Title.TLabel").pack(anchor="w", padx=20, pady=(18, 2))
        code_var = tk.StringVar(value="正在向 GitHub 申请…")
        code_lbl = tk.Label(win, textvariable=code_var, font=("Consolas", 26, "bold"),
                            bg=CARD, fg=INK, padx=16, pady=10)
        code_lbl.pack(fill="x", padx=20, pady=(4, 6))

        ttk.Label(win, text="第 2 步：浏览器里粘贴这个码，点 Authorize（授权）",
                  style="Hint.TLabel").pack(anchor="w", padx=20)
        state_var = tk.StringVar(value="等二维码就绪…")
        ttk.Label(win, textvariable=state_var, style="Hint.TLabel",
                  wraplength=500, justify="left").pack(anchor="w", padx=20, pady=(10, 6))

        roww = ttk.Frame(win)
        roww.pack(fill="x", padx=20, pady=(4, 0))
        btn_open = ttk.Button(roww, text="复制并打开浏览器", style="Accent.TButton", state="disabled")
        btn_open.pack(side="left")
        ttk.Button(roww, text="取消", command=lambda: (setattr(self, "_login_stop", True), win.destroy())).pack(side="left", padx=8)

        info = {}

        def ui(fn, *a):
            self.master.after(0, lambda: fn(*a))

        def safe_status(text):
            try:
                if win.winfo_exists():
                    state_var.set(text)
            except tk.TclError:
                pass

        def do_open():
            code = info.get("user_code", "")
            try:
                win.clipboard_clear()
                win.clipboard_append(code)
            except tk.TclError:
                pass
            import webbrowser
            webbrowser.open(info.get("verification_uri", "https://github.com/login/device"))
            safe_status("已复制「%s」，浏览器打开了授权页。粘贴进去、点 Authorize 就行。" % code)

        btn_open.config(command=do_open)

        def worker():
            try:
                d = cloud_deploy.device_start()
                info.update(d)
                ui(code_var.set, d["user_code"])
                ui(btn_open.config, {"state": "normal"})
                ui(lambda: do_open())
                token = cloud_deploy.device_poll(
                    d["device_code"], interval=d["interval"], expires_in=d["expires_in"],
                    should_stop=lambda: self._login_stop,
                    on_tick=lambda s: ui(safe_status, s),
                )
                ui(lambda: self._login_success(win, token, on_success))
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                ui(safe_status, "失败：" + msg)
                ui(lambda: None)

        threading.Thread(target=worker, daemon=True).start()

    def _login_success(self, win, token, on_success):
        try:
            if win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass
        self.var_gh_token.set(token)
        name = ""
        try:
            me = cloud_deploy.GitHubDeploy("", "", token).whoami()
            name = me.get("login", "")
        except Exception:  # noqa: BLE001
            name = ""
        if name:
            self.var_gh_user.set(name)
        if not self.var_gh_repo.get().strip():
            self.var_gh_repo.set("TudouChaoxing")
        self._cloud_say("✅ 登录成功：%s（Token 已自动填好，存在本机 .cloud.json）" % (name or "已获取 Token"))
        self.status.config(text="GitHub 已登录，可以点「一键部署并开跑」",
                           style="Hint.TLabel", foreground="#1a7f37")
        if on_success:
            on_success()
        else:
            messagebox.showinfo("登录成功", "已经用 %s 登录了。\n\n用户名和 Token 都自动填好，现在点「一键部署并开跑」就行。" % (name or "你的账号"))

    def _cloud_secrets(self):
        """把 ①② 页的值组装成要上传的 Secrets —— 用户不用在两处重复填"""
        return {
            "CHX_USER": self.var_user.get().strip(),
            "CHX_PASS": self.var_pass.get().strip(),
            "CHX_LLM_KEY": self.var_key.get().strip(),
            "CHX_COURSE": self.var_course.get().strip(),
        }

    def cloud_check(self):
        client = self._cloud_client()
        if client is None:
            return
        self.txt_cloud_log.delete("1.0", "end")
        self._cloud_say("正在检测仓库…")
        self.status.config(text="正在检测 GitHub 仓库…", style="Hint.TLabel", foreground=SUB)

        def worker():
            try:
                me = client.whoami()
                self.master.after(0, lambda: self._cloud_say("· Token 属于：%s" % me.get("login", "?")))
                info = client.repo_info()
                self.master.after(0, lambda: self._cloud_say(
                    "· 仓库：%s（%s）" % (info.get("full_name"), "fork" if info.get("fork") else "自己的")))
                self.master.after(0, lambda: self._cloud_say(
                    "· 默认分支：%s" % info.get("default_branch", "main")))
                names = client.list_secrets()
                self.master.after(0, lambda: self._cloud_say(
                    "· 已有 Secrets：%s" % (", ".join(names) if names else "（一个都没有）")))
                wf = client.get_file(".github/workflows/main.yml")[0]
                self.master.after(0, lambda: self._cloud_say(
                    "· workflow 文件：%s" % ("在" if wf else "缺失，一键部署会补上")))
                run = client.latest_run()
                if run:
                    self.master.after(0, lambda: self._cloud_say(
                        "· 最近一次运行：%s / %s（%s）" % (
                            run.get("status"), run.get("conclusion"), run.get("event"))))
                self.master.after(0, lambda: self.status.config(
                    text="检测通过，可以点「一键部署并开跑」", style="Hint.TLabel", foreground="#1a7f37"))
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self._cloud_fail(e))

        threading.Thread(target=worker, daemon=True).start()

    def _cloud_fail(self, text):
        self._cloud_say("[失败] " + text)
        self.status.config(text="部署失败，看下面日志", style="Hint.TLabel", foreground="#b5462f")

    def cloud_deploy(self):
        # 没登录过就直接走登录，登录完自动回到这里继续部署 —— 真正的"一键"
        if not self.var_gh_token.get().strip():
            if messagebox.askyesno("还没登录", "还没连 GitHub。现在登录一次？\n\n"
                                              "点「是」会弹出 6 位验证码，浏览器里授权一下就完事。"):
                self.cloud_login(on_success=self.cloud_deploy)
            return
        client = self._cloud_client()
        if client is None:
            return
        mode = self.var_run_mode.get()
        if not messagebox.askyesno(
            "确认部署",
            "会把账号密码和大模型 Key 写进 GitHub 仓库的 Secrets，并把定时设置推上去，然后立刻跑一次。\n\n"
            "Secrets 是加密存的，写进去之后连你自己也看不到明文。\n\n继续吗？",
        ):
            return
        self.txt_cloud_log.delete("1.0", "end")
        self._cloud_say("开始部署…")
        self.status.config(text="正在部署到 GitHub…", style="Hint.TLabel", foreground=ACCENT)

        wf_text = None
        try:
            if os.path.exists(WORKFLOW):
                wf_text = open(WORKFLOW, "r", encoding="utf-8", errors="ignore").read()
        except OSError:
            wf_text = None
        secrets = self._cloud_secrets()

        def worker():
            try:
                client.deploy(secrets=secrets, workflow_text=wf_text,
                              inputs={"run_mode": mode, "max_chapters": "1"},
                              run_now=True)
                self.master.after(0, self._cloud_done)
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self._cloud_fail(e))

        threading.Thread(target=worker, daemon=True).start()

    def _cloud_done(self):
        self._cloud_say("")
        self._cloud_say("=== 部署完成 ===")
        self._cloud_say("第一次跑的是「跑 1 章」试水，去 Actions 页面看结果；")
        self._cloud_say("确认能跑通，再让它按定时自己跑（定时在 ④ 页设置）。")
        self.status.config(text="部署完成", style="Hint.TLabel", foreground="#1a7f37")
        messagebox.showinfo("部署完成",
                            "已经推到 GitHub 了，并且触发了第一次运行。\n\n"
                            "去 Actions 页面看进度：\n%s" % (
                                cloud_deploy.GitHubDeploy(
                                    self.var_gh_user.get().strip(),
                                    self.var_gh_repo.get().strip(), "").actions_url()))

    def cloud_open(self):
        owner = self.var_gh_user.get().strip()
        repo = self.var_gh_repo.get().strip()
        if not owner or not repo:
            messagebox.showwarning("提示", "先填 GitHub 用户名和仓库名。")
            return
        url = "https://github.com/%s/%s/actions" % (owner, repo)
        import webbrowser
        webbrowser.open(url)

    def add_row(self, parent, row, label, var, hint="", readonly=False, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=(10, 0))
        ent = ttk.Entry(parent, textvariable=var, width=32, show=show)
        if readonly:
            ent.state(["readonly"])
        ent.grid(row=row, column=1, sticky="w", padx=10, pady=(10, 0))
        if hint:
            ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=175, justify="left").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=(12, 0))

    def validate_numbers(self):
        """保存前把几个数字项校验一遍，避免写进 config.ini 让主程序启动就崩。"""
        try:
            sp = float(str(self.var_speed.get()).strip() or 2)
        except ValueError:
            return "视频倍速要填数字（1 或 2）"
        if not (1 <= sp <= 2):
            return "视频倍速只能填 1 或 2"
        try:
            mc = int(str(self.var_maxch.get()).strip() or 0)
        except ValueError:
            return "每门课最多跑几章要填整数（0 表示不限制）"
        if mc < 0:
            return "每门课最多跑几章不能是负数"
        try:
            votes = int(str(self.var_votes.get()).strip() or 3)
        except ValueError:
            return "每题投票次数要填整数（建议 3）"
        if not (1 <= votes <= 10):
            return "每题投票次数填 1~10 之间"
        try:
            d_min = float(str(self.var_delay_min.get()).strip() or 3)
            d_max = float(str(self.var_delay_max.get()).strip() or 9)
        except ValueError:
            return "章节间隔要填数字（单位秒，0~300）"
        if not (0 <= d_min <= 300) or not (0 <= d_max <= 300):
            return "章节间隔要在 0~300 秒之间"
        if d_min > d_max:
            return "章节间隔的最小数不能大于最大数"
        try:
            r_every = int(str(self.var_rest_every.get()).strip() or 0)
            r_min = float(str(self.var_rest_min.get()).strip() or 0)
            r_max = float(str(self.var_rest_max.get()).strip() or 0)
        except ValueError:
            return "长休息要填数字（章数 / 秒数）"
        if r_every < 0 or r_min < 0:
            return "长休息的章数和秒数不能是负数"
        if r_min > r_max:
            return "长休息的最短秒数不能大于最长秒数"
        return ""

    def collect(self):
        # 运行模式三选一 -> 互斥的两个开关，同一时刻只会有一个 true
        mode = self.var_run_mode.get() or "all"
        return {
            "common": {
                "username": self.var_user.get().strip(),
                "password": self.var_pass.get().strip(),
                "course_list": self.var_course.get().strip(),
                "speed": self.var_speed.get().strip() or "2",
                "solve_only": "true" if mode == "quiz" else "false",
                "video_only": "true" if mode == "video" else "false",
                "chapter_delay_min": self.var_delay_min.get().strip() or "3",
                "chapter_delay_max": self.var_delay_max.get().strip() or "9",
                "max_chapters": self.var_maxch.get().strip() or "0",
                "rest_every": self.var_rest_every.get().strip() or "0",
                "rest_min": self.var_rest_min.get().strip() or "0",
                "rest_max": self.var_rest_max.get().strip() or "0",
            },
            "tiku": {
                "provider": self.var_provider.get().strip() or "TikuLLM",
                "endpoint": self.var_endpoint.get().strip(),
                "key": self.var_key.get().strip(),
                "model": self.var_model.get().strip(),
                "votes": self.var_votes.get().strip() or "3",
                "submit": "true" if self.var_submit.get() else "false",
            },
        }

    def save(self):
        # 定时的三个数字先校验，别让 int() 抛出去把整个保存流程打断
        try:
            hh = int(str(self.var_hour.get()).strip() or 1)
            mm = int(str(self.var_minute.get()).strip() or 0)
            mins = int(str(self.var_minutes.get()).strip() or 150)
        except ValueError:
            messagebox.showwarning("填错了", "定时里的小时 / 分钟 / 时长必须是数字，先改一下再保存。")
            return
        if not (0 <= hh <= 23) or not (0 <= mm <= 59):
            messagebox.showwarning("填错了", "小时要在 0~23 之间，分钟要在 0~59 之间。")
            return
        if not (5 <= mins <= 360):
            messagebox.showwarning("填错了", "单次时长要在 5~360 分钟之间（GitHub 单任务上限 6 小时）。")
            return
        self.var_hour.set(str(hh)); self.var_minute.set(str(mm)); self.var_minutes.set(str(mins))

        bad = self.validate_numbers()
        if bad:
            messagebox.showwarning("填错了", bad)
            return

        data = self.collect()
        if not data["common"]["username"] or not data["common"]["password"]:
            if not messagebox.askyesno("确认", "账号或密码是空的，程序启动后会问你要。仍要保存吗？"):
                return
        if not data["tiku"]["key"]:
            messagebox.showwarning("提醒", "API Key 是空的。没有 key 的话所有题目都会变成随机答案。")
        if not os.path.exists(CONFIG):
            if os.path.exists(TEMPLATE):
                open(CONFIG, "w", encoding="utf-8").write(open(TEMPLATE, "r", encoding="utf-8", errors="ignore").read())
        for section, values in data.items():
            write_ini_values(CONFIG, section, values)
        msgs = ["config.ini 已保存"]
        if os.path.exists(WORKFLOW):
            ok, msg = write_workflow(int(hh), int(mm), mins, self.var_cron_on.get(), int(self.var_gap.get() or 0))
            msgs.append(msg if ok else ("定时没改成：" + msg))
        else:
            msgs.append("没找到 workflow 文件，云端定时没动（本地跑不用管）")
        self.status.config(text=" | ".join(msgs), style="Hint.TLabel", foreground="#1a7f37")
        messagebox.showinfo("完成", "\n".join(msgs))

    def test_key(self):
        key = self.var_key.get().strip()
        endpoint = self.var_endpoint.get().strip().rstrip("/")
        model = self.var_model.get().strip()
        if not key:
            messagebox.showwarning("提示", "先填 API Key")
            return
        self.lbl_test.config(text="正在测试…", foreground="#666")

        def run():
            try:
                import urllib.request
                body = json.dumps({"model": model, "messages": [{"role": "user", "content": "回复两个字：可以"}], "max_tokens": 8}).encode()
                req = urllib.request.Request(endpoint + "/chat/completions", data=body,
                                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
                with urllib.request.urlopen(req, timeout=40) as resp:
                    data = json.loads(resp.read().decode("utf-8", "ignore"))
                ans = data["choices"][0]["message"]["content"][:30]
                self.master.after(0, lambda: self.lbl_test.config(text=f"连通了，模型回了：{ans}", foreground="#1a7f37", style="Hint.TLabel"))
            except Exception as exc:
                msg = str(exc)[:160]
                self.master.after(0, lambda: self.lbl_test.config(text="失败：" + msg, foreground="#b5462f", style="Hint.TLabel"))
        threading.Thread(target=run, daemon=True).start()

    def run_update(self):
        """调 update.ps1 拉最新代码，config.ini 会被备份并还原。"""
        script = os.path.join(ROOT, "update.ps1")
        if not os.path.exists(script):
            messagebox.showwarning("找不到更新脚本", "项目里没有 update.ps1，无法更新。\n可以从 GitHub 重新下载一份。")
            return
        if not messagebox.askyesno(
            "确认更新",
            "会用最新代码覆盖本地文件。\n\n你的 config.ini、cookies.txt、cache.json 会先备份再还原，不会丢。\n\n继续吗？",
        ):
            return

        win = tk.Toplevel(self.master)
        win.title("正在更新…")
        win.geometry("760x420")
        win.configure(bg=PAPER)
        ttk.Label(win, text="更新日志（完成后会自动关掉重开一次配置工具）", style="Hint.TLabel").pack(anchor="w", padx=10, pady=(8, 4))
        box = tk.Text(win, wrap="word", font=("Consolas", 9), bg="#241B12", fg="#F6ECD8", insertbackground="#F6ECD8")
        box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        box.insert("end", "开始执行 update.ps1 …\n")

        def say(text):
            # 用户可能中途把窗口关了，这时别再往已销毁的控件里写
            try:
                if box.winfo_exists():
                    box.insert("end", text)
                    box.see("end")
            except tk.TclError:
                pass
        win.update()

        import subprocess

        def worker():
            try:
                proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    bufsize=1,
                )
                for line in proc.stdout:
                    self.master.after(0, lambda l=line: say(l))
                code = proc.wait()
            except Exception as exc:  # noqa: BLE001
                code = -1
                self.master.after(0, lambda e=str(exc): say("\n[失败] " + e + "\n"))
            self.master.after(0, lambda: on_done(code))

        def on_done(code):
            say(("\n[完成] 退出码 %s\n" % code) if code == 0 else ("\n[失败] 退出码 %s，看上面红字\n" % code))
            try:
                if win.winfo_exists():
                    ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 10))
            except tk.TclError:
                pass
            if code == 0:
                messagebox.showinfo("更新完成", "代码已经是新版了。\n关掉这个窗口、重新双击桌面图标即可使用新版本。")

        threading.Thread(target=worker, daemon=True).start()

    def open_ini(self):
        path = CONFIG if os.path.exists(CONFIG) else TEMPLATE
        if not os.path.exists(path):
            messagebox.showwarning("提示", "还没生成 config.ini，先点保存")
            return
        os.startfile(path) if os.name == "nt" else subprocess.Popen(["xdg-open", path])


def main():
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()