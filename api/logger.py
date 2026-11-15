# -*- coding: utf-8 -*-
"""日志单例：全项目统一用 `from api.logger import logger` 取同一个 logger。

项目名叫「土豆刷学习通」，所以每一行日志都带这个前缀，方便在 CI 那一大坨输出里
一眼认出是谁在说话。日志文件名仍然是 chaoxing.log —— 这是历史产物，Actions 的
artifact、.gitignore 规则、测试说明都认这个名字，改文件名会连带一堆东西，别动。
"""
from loguru import logger

# 项目名：日志前缀、通知前缀一律用它，不要再手写字符串
PROJECT_NAME = "土豆刷学习通"

# 日志文件名：保持 chaoxing.log 不变（历史产物 + CI artifact 名）
LOG_FILE = "chaoxing.log"

# 每行格式：时间 | 项目名 | 级别 | 正文
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    + PROJECT_NAME
    + " | <level>{level: <8}</level> | <level>{message}</level>"
)


def add_file_sink(level: str = "TRACE") -> None:
    """挂文件 sink：10MB 轮转，UTF-8 写盘（Windows 默认 GBK 会把中文写成乱码）。

    --verbose / --debug 会先 logger.remove() 再调这个函数重挂一遍。
    """
    logger.add(
        LOG_FILE,
        rotation="10 MB",
        level=level,
        format=LOG_FORMAT,
        encoding="utf-8",
    )


add_file_sink()
