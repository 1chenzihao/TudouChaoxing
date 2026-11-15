# -*- coding: utf-8 -*-
import os

# cookies.txt 固定在项目根目录。以前是相对路径，从别的目录启动程序就会
# 在 cwd 里另建一份 cookie，看起来像"登录态莫名失效"。
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GlobalConst:
    AESKey = "u2oh6Vu^HWe4_AES"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
    }
    COOKIES_PATH = os.path.join(_BASE_DIR, "cookies.txt")
    VIDEO_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2023-1110-1610",
        "Host": "mooc1.chaoxing.com",
    }
    AUDIO_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2023-0428-1705",
        "Host": "mooc1.chaoxing.com",
    }
    THRESHOLD = 3
