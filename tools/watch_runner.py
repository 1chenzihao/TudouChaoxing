# -*- coding: utf-8 -*-
"""通宵看护：把 main.py 跑起来，异常退出就自动重启，直到正常完成或用完重试次数。

用法：
    python tools/watch_runner.py -c config.ini            （参数原样透传给 main.py）
    CHX_MAX_RESTARTS=50 python tools/watch_runner.py -c config.ini   （改重启上限）

退出码约定（来自 main.py）：
    0 = 所有课程正常跑完，看护脚本也退出
    非 0 = 异常（登录失败 / 未捕获错误），等 60 秒重启
"""
import os
import subprocess
import sys
import time
from pathlib import Path

MAX_RESTARTS = int(os.environ.get("CHX_MAX_RESTARTS", "30"))
RESTART_WAIT = int(os.environ.get("CHX_RESTART_WAIT", "60"))
ROOT = Path(__file__).resolve().parent.parent


def stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    args = sys.argv[1:]
    cmd = [sys.executable, "-u", str(ROOT / "main.py")] + args
    print(f"[看护] 启动命令: {' '.join(cmd)}", flush=True)
    print(f"[看护] 重启上限 {MAX_RESTARTS} 次, 间隔 {RESTART_WAIT} 秒", flush=True)

    attempt = 0
    while True:
        attempt += 1
        print(f"\n[看护] ===== 第 {attempt} 次启动 {stamp()} =====", flush=True)
        started = time.time()
        code = subprocess.call(cmd, cwd=str(ROOT))
        elapsed = time.time() - started
        print(
            f"[看护] ===== 第 {attempt} 次退出 {stamp()}: code={code}, "
            f"运行 {elapsed / 60:.1f} 分钟 =====",
            flush=True,
        )

        if code == 0:
            print("[看护] 程序正常跑完（code=0），不再重启。", flush=True)
            return 0
        if attempt >= MAX_RESTARTS:
            print(f"[看护] 已重启 {attempt - 1} 次，达到上限，放弃。", flush=True)
            return code

        print(f"[看护] 异常退出，{RESTART_WAIT} 秒后重启…", flush=True)
        time.sleep(RESTART_WAIT)


if __name__ == "__main__":
    raise SystemExit(main())
