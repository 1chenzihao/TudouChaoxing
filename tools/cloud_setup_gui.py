# -*- coding: utf-8 -*-
"""土豆刷学习通 · 云端一键部署（独立小工具）

这个文件是给「完全不想装 Python」的人用的：打包成单个 exe 之后，
双击就能填账号、登录 GitHub、把 Secrets 和定时推到自己的仓库。

它只做云端那一件事：
    ① 学习通账号  →  CHX_USER / CHX_PASS / CHX_COURSE
    ② 大模型      →  CHX_LLM_KEY
    ③ GitHub 登录 + 一键推送 Secrets、workflow、触发一次运行
    ④ 每天几点跑（改 cron）

本地刷课（main.py）不在这里，那个本来就要 Python。
"""
import json
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# ---------------------------------------------------------------- 路径
def app_dir():
    """exe 所在目录（打包后）/ 源码所在目录（开发时）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def res_path(name):
    """打包进 exe 的资源路径"""
    base = getattr(sys, "_MEIPASS", app_dir())
    p = os.path.join(base, name)
    return p if os.path.exists(p) else os.path.join(app_dir(), name)


ROOT = app_dir()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cloud_deploy  # noqa: E402

PROFILE = os.path.join(ROOT, "cloud_setup.json")
WORKFLOW_RESC = res_path("main.yml")

PAPER, INK, SUB, ACCENT, CARD = "#F7EFDF", "#33261A", "#6B5842", "#C87F2E", "#FFFFFF"
FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_T = ("Microsoft YaHei UI", 15, "bold")


def load_profile():
    try:
        with open(PROFILE, "r", encoding="utf-8") as fp:
            d = json.load(fp)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profile(d):
    try:
        with open(PROFILE, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=1)
    except OSError:
        pass


def read_workflow():
    try:
        with open(WORKFLOW_RESC, "r", encoding="utf-8", errors="ignore") as fp:
            return fp.read()
    except OSError:
        return ""


def set_schedule(text, hour_bj, minute_bj, gap_hours=6):
    """把 schedule 段换成两条 cron：视频轮 + 测验轮（北京时间）"""
    utc_h = (int(hour_bj) - 8) % 24
    cron1 = "%d %d * * *" % (int(minute_bj), utc_h)
    quiz_h = (int(hour_bj) + int(gap_hours)) % 24
    cron2 = "%d %d * * *" % (int(minute_bj), (quiz_h - 8) % 24)
    hhmm = "%02d:%02d" % (int(hour_bj), int(minute_bj))
    quiz_hhmm = "%02d:%02d" % (quiz_h, int(minute_bj))
    block = ("\n  schedule:\n"
             "    - cron: '" + cron1 + "'   # 北京 " + hhmm + " 视频轮（只刷视频）\n"
             "    - cron: '" + cron2 + "'   # 北京 " + quiz_hhmm + " 测验轮（只做测验）\n")
    pat = re.compile(r"\n[ \t]*(?:#[ \t]*)?schedule:[^\n]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n)*")
    if pat.search(text):
        return pat.sub(lambda m: block, text, count=1)
    return text.replace("\nworkflow_dispatch:", block + "  workflow_dispatch:", 1)


class App:
    def __init__(self, master):
        self.master = master
        master.title("土豆刷学习通 · 云端一键部署")
        master.geometry("700x900")
        master.resizable(False, False)
        self._style(master)
        self._login_stop = False
        self._busy = False

        prof = load_profile()

        head = tk.Frame(master, bg=PAPER)
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="土豆刷学习通", font=FONT_T, bg=PAPER, fg=INK).pack(anchor="w")
        tk.Label(head, text="云端一键部署 · 填账号 → 登录 GitHub → 推到仓库",
                 font=FONT, bg=PAPER, fg=SUB).pack(anchor="w")

        self.var_user = tk.StringVar(value=prof.get("chx_user", ""))
        self.var_pass = tk.StringVar(value=prof.get("chx_pass", ""))
        self.var_course = tk.StringVar(value=prof.get("chx_course", ""))
        self.var_key = tk.StringVar(value=prof.get("llm_key", ""))
        self.var_endpoint = tk.StringVar(value=prof.get("llm_endpoint", "https://api.deepseek.com/v1"))
        self.var_model = tk.StringVar(value=prof.get("llm_model", "deepseek-chat"))
        self.var_gh_user = tk.StringVar(value=prof.get("gh_user", ""))
        self.var_gh_repo = tk.StringVar(value=prof.get("gh_repo", "TudouChaoxing"))
        self.var_gh_token = tk.StringVar(value=prof.get("gh_token", ""))
        self.var_enable = tk.BooleanVar(value=bool(prof.get("schedule_on", True)))
        self.var_hour = tk.StringVar(value=prof.get("hour", "01"))
        self.var_minute = tk.StringVar(value=prof.get("minute", "00"))

        body = tk.Frame(master, bg=PAPER)
        body.pack(fill="x", padx=18, pady=(6, 0))   # 不给 expand，否则它会把日志区挤没

        self._section(body, "① 学习通账号")
        self._row(body, "手机号", self.var_user, "11 位，别带 +86")
        self._row(body, "密码", self.var_pass, "只传给 GitHub Secrets", show="*")
        self._row(body, "课程 ID", self.var_course, "留空 = 刷全部课程")

        self._section(body, "② 大模型（答题用）")
        self._row(body, "接口地址", self.var_endpoint, "DeepSeek 照抄")
        self._row(body, "API Key", self.var_key, "sk- 开头", show="*")
        self._row(body, "模型名", self.var_model, "deepseek-chat")

        self._section(body, "③ GitHub")
        row = tk.Frame(body, bg=PAPER)
        row.pack(fill="x", pady=2)
        tk.Label(row, text="账号", width=10, anchor="w", bg=PAPER, fg=INK, font=FONT).pack(side="left")
        ttk.Entry(row, textvariable=self.var_gh_user, width=22).pack(side="left")
        ttk.Button(row, text="GitHub 一键登录", command=self.login).pack(side="left", padx=8)
        self._row(body, "仓库名", self.var_gh_repo, "fork 出来的仓库")
        self._row(body, "Token", self.var_gh_token, "登录后自动填", show="*")

        self._section(body, "④ 每天定时跑")
        row2 = tk.Frame(body, bg=PAPER)
        row2.pack(fill="x", pady=2)
        ttk.Checkbutton(row2, text="启用定时", variable=self.var_enable).pack(side="left")
        tk.Label(row2, text="  北京时间", bg=PAPER, fg=INK, font=FONT).pack(side="left")
        ttk.Spinbox(row2, from_=0, to=23, width=4, textvariable=self.var_hour, format="%02.0f").pack(side="left", padx=(8, 2))
        tk.Label(row2, text="时", bg=PAPER, fg=INK, font=FONT).pack(side="left")
        ttk.Spinbox(row2, from_=0, to=59, width=4, textvariable=self.var_minute, format="%02.0f").pack(side="left", padx=(6, 2))
        tk.Label(row2, text="分", bg=PAPER, fg=INK, font=FONT).pack(side="left")
        tk.Label(body, text="这个时间跑视频轮；测验轮自动排在 6 小时后，不用单独设",
                 bg=PAPER, fg="#8A7A64", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 0))

        btns = tk.Frame(master, bg=PAPER)
        btns.pack(fill="x", padx=18, pady=(10, 4))
        ttk.Button(btns, text="检测仓库", command=self.check).pack(side="left")
        ttk.Button(btns, text="一键部署并开跑", style="Accent.TButton", command=self.deploy).pack(side="left", padx=8)
        ttk.Button(btns, text="打开 Actions", command=self.open_actions).pack(side="left")

        self.log = tk.Text(master, height=6, wrap="word", font=("Consolas", 9),
                           bg="#241B12", fg="#F6ECD8", insertbackground="#F6ECD8",
                           relief="flat", bd=0, padx=8, pady=6)
        self.log.pack(fill="both", expand=True, padx=18, pady=(4, 12))
        self.say("先填 ①②，再点「GitHub 一键登录」，最后「一键部署并开跑」。")
        self.say("这个工具只配云端；本地刷课另说（那个要 Python）。")

    # ---------------- 样式 ----------------
    def _style(self, root):
        st = ttk.Style(root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", font=FONT, background=PAPER, foreground=INK)
        st.configure("TFrame", background=PAPER)
        st.configure("TEntry", fieldbackground=CARD, padding=6)
        st.configure("TButton", padding=(12, 6), background="#EDE1C8", borderwidth=0)
        st.configure("Accent.TButton", padding=(16, 7), background=ACCENT, foreground="#FFFFFF")
        st.map("Accent.TButton", background=[("active", "#A96A24")])
        st.configure("TCheckbutton", background=PAPER)
        st.configure("TSpinbox", fieldbackground=CARD)
        root.configure(bg=PAPER)

    def _section(self, parent, text):
        tk.Label(parent, text=text, font=FONT_B, bg=PAPER, fg=ACCENT).pack(anchor="w", pady=(8, 2))

    def _row(self, parent, label, var, hint="", show=None):
        row = tk.Frame(parent, bg=PAPER)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, width=10, anchor="w", bg=PAPER, fg=INK, font=FONT).pack(side="left")
        ttk.Entry(row, textvariable=var, width=26, show=show).pack(side="left")
        if hint:
            tk.Label(row, text=hint, bg=PAPER, fg="#8A7A64", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))

    def say(self, text):
        try:
            self.log.insert("end", str(text) + "\n")
            self.log.see("end")
        except tk.TclError:
            pass

    def _save(self):
        save_profile({
            "chx_user": self.var_user.get().strip(),
            "chx_pass": self.var_pass.get().strip(),
            "chx_course": self.var_course.get().strip(),
            "llm_key": self.var_key.get().strip(),
            "llm_endpoint": self.var_endpoint.get().strip(),
            "llm_model": self.var_model.get().strip(),
            "gh_user": self.var_gh_user.get().strip(),
            "gh_repo": self.var_gh_repo.get().strip(),
            "gh_token": self.var_gh_token.get().strip(),
            "schedule_on": bool(self.var_enable.get()),
            "hour": self.var_hour.get(),
            "minute": self.var_minute.get(),
        })

    # ---------------- 登录 ----------------
    def login(self, on_success=None):
        self._login_stop = False
        win = tk.Toplevel(self.master)
        win.title("用 GitHub 账号登录")
        win.geometry("540x320")
        win.configure(bg=PAPER)
        win.transient(self.master)
        try:
            win.grab_set()
        except tk.TclError:
            pass

        tk.Label(win, text="第 1 步：复制下面这个码", font=FONT_B, bg=PAPER, fg=INK).pack(anchor="w", padx=20, pady=(18, 4))
        code_var = tk.StringVar(value="正在向 GitHub 申请…")
        tk.Label(win, textvariable=code_var, font=("Consolas", 24, "bold"), bg=CARD, fg=INK,
                 padx=14, pady=10).pack(fill="x", padx=20, pady=(2, 6))
        tk.Label(win, text="第 2 步：浏览器里粘贴这个码，点 Authorize", bg=PAPER, fg=SUB, font=FONT).pack(anchor="w", padx=20)
        state = tk.StringVar(value="等待申请…")
        tk.Label(win, textvariable=state, bg=PAPER, fg=SUB, font=FONT, wraplength=480,
                 justify="left").pack(anchor="w", padx=20, pady=(10, 6))

        rowb = tk.Frame(win, bg=PAPER)
        rowb.pack(fill="x", padx=20)
        btn = ttk.Button(rowb, text="复制并打开浏览器", style="Accent.TButton", state="disabled")
        btn.pack(side="left")
        ttk.Button(rowb, text="取消", command=lambda: (setattr(self, "_login_stop", True), win.destroy())).pack(side="left", padx=8)
        info = {}

        def ui(fn, *a):
            self.master.after(0, lambda: fn(*a))

        def safe(t):
            try:
                if win.winfo_exists():
                    state.set(t)
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
            safe("已复制「%s」，浏览器打开了授权页。粘进去、点 Authorize。" % code)

        btn.config(command=do_open)

        def worker():
            try:
                d = cloud_deploy.device_start()
                info.update(d)
                ui(code_var.set, d["user_code"])
                ui(btn.config, {"state": "normal"})
                ui(do_open)
                token = cloud_deploy.device_poll(
                    d["device_code"], interval=d["interval"], expires_in=d["expires_in"],
                    should_stop=lambda: self._login_stop, on_tick=lambda s: ui(safe, s))
                ui(lambda: self._login_ok(win, token, on_success))
            except Exception as exc:  # noqa: BLE001
                ui(safe, "失败：" + str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _login_ok(self, win, token, on_success):
        try:
            if win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass
        self.var_gh_token.set(token)
        name = ""
        try:
            name = cloud_deploy.GitHubDeploy("", "", token).whoami().get("login", "")
        except Exception:  # noqa: BLE001
            name = ""
        if name:
            self.var_gh_user.set(name)
        self._save()
        self.say("[OK] 登录成功：%s" % (name or "(已获取 Token)"))
        if on_success:
            on_success()

    # ---------------- 客户端 ----------------
    def _client(self):
        owner = self.var_gh_user.get().strip()
        repo = self.var_gh_repo.get().strip()
        token = self.var_gh_token.get().strip()
        if not owner or not repo:
            messagebox.showwarning("缺信息", "GitHub 账号和仓库名都要填。")
            return None
        if not token:
            messagebox.showwarning("还没登录", "先点「GitHub 一键登录」。")
            return None
        self._save()
        return cloud_deploy.GitHubDeploy(owner, repo, token,
                                         log=lambda s="": self.master.after(0, lambda t=s: self.say(t)))

    def check(self):
        c = self._client()
        if not c:
            return
        self.say("正在检测…")

        def work():
            try:
                me = c.whoami()
                self.master.after(0, lambda: self.say("[OK] Token 属于：%s" % me.get("login")))
                info = c.repo_info()
                self.master.after(0, lambda: self.say("     仓库：%s（%s）" % (
                    info.get("full_name"), "fork" if info.get("fork") else "自己的")))
                names = c.list_secrets()
                self.master.after(0, lambda: self.say("     已有 Secrets：%s" % (", ".join(names) or "无")))
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self.say("[失败] " + e))
        threading.Thread(target=work, daemon=True).start()

    def deploy(self):
        if not self.var_gh_token.get().strip():
            if messagebox.askyesno("还没登录", "还没连 GitHub，现在登录一次？"):
                self.login(on_success=self.deploy)
            return
        c = self._client()
        if not c:
            return
        user = self.var_user.get().strip()
        pwd = self.var_pass.get().strip()
        if not user or not pwd:
            messagebox.showwarning("缺信息", "学习通手机号和密码都要填（这两个要传给 GitHub Secrets）。")
            return
        if not messagebox.askyesno(
            "确认部署",
            "会把账号密码和大模型 Key 写进 GitHub 仓库的 Secrets，并推上定时设置，然后跑一次。\n\n"
            "Secrets 是加密存的，写完连你自己也看不到明文。继续吗？"):
            return

        secrets = {
            "CHX_USER": user,
            "CHX_PASS": pwd,
            "CHX_COURSE": self.var_course.get().strip(),
            "CHX_LLM_KEY": self.var_key.get().strip(),
        }
        wf = read_workflow()
        if wf and self.var_enable.get():
            try:
                wf = set_schedule(wf, int(self.var_hour.get() or 1), int(self.var_minute.get() or 0))
            except ValueError:
                messagebox.showwarning("时间填错了", "定时的小时/分钟要是数字。")
                return
        self._save()
        self.say("开始部署…")

        def work():
            try:
                c.deploy(secrets=secrets, workflow_text=wf or None,
                         inputs={"run_mode": "video", "max_chapters": "1"}, run_now=True)
                self.master.after(0, lambda: self.say("\n=== 部署完成，去 Actions 页面看第一次运行 ==="))
                self.master.after(0, lambda: messagebox.showinfo(
                    "部署完成", "已经推到 GitHub，并触发了第一次运行（只跑 1 章试水）。\n\n去 Actions 页面看进度。"))
            except Exception as exc:  # noqa: BLE001
                self.master.after(0, lambda e=str(exc): self.say("[失败] " + e))
        threading.Thread(target=work, daemon=True).start()

    def open_actions(self):
        o, r = self.var_gh_user.get().strip(), self.var_gh_repo.get().strip()
        if not o or not r:
            messagebox.showwarning("缺信息", "先填 GitHub 账号和仓库名。")
            return
        import webbrowser
        webbrowser.open("https://github.com/%s/%s/actions" % (o, r))


def main():
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    ico = res_path("mascot.ico")
    try:
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        try:
            with open(os.path.join(ROOT, "cloud_gui_error.log"), "w", encoding="utf-8") as fp:
                fp.write(traceback.format_exc())
        except OSError:
            pass
        raise
