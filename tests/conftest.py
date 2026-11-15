# -*- coding: utf-8 -*-
"""pytest 公共夹具。

本目录下的测试全部离线运行：不联网、不读 cookies、不写缓存文件。
任何需要真实网络或题库接口的路径都用假对象（stub / monkeypatch）替换。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# 保证 `import api` 能解析到仓库根下的包，而不是依赖调用者的工作目录
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _patch_loguru_file_sink():
    """阻止 api.logger 在仓库根目录写出 chaoxing.log。

    api/logger.py 在 import 时就会执行 logger.add("chaoxing.log", ...)，
    测试不想在仓库里留日志文件，所以先把这个 add 换成空操作，
    之后再挂一个只输出 WARNING 以上级别的 stderr sink。
    """
    import loguru

    add = loguru.logger.add

    def _skip_file_sink(*args, **kwargs):
        return 0

    loguru.logger.add = _skip_file_sink   # type: ignore[assignment]
    return add


_ORIGINAL_LOGURU_ADD = _patch_loguru_file_sink()

import api.logger  # noqa: E402  在替换 sink 之后再导入，避免落盘

_ORIGINAL_LOGURU_ADD(sys.stderr, level="WARNING")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def questions_html() -> str:
    return (FIXTURES_DIR / "questions_page.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def questions_html_encrypted() -> str:
    return (FIXTURES_DIR / "questions_page_encrypted.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def form_html() -> str:
    return (FIXTURES_DIR / "form_skeleton.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def decoded_questions(questions_html):
    """对 4 题样本页只解析一次，避免每个用例重复建 BeautifulSoup 树。"""
    from api.decode import decode_questions_info

    return decode_questions_info(questions_html)


class StubTiku:
    """题库替身：只实现 check_answer / judgement_select 需要的字段和方法。"""

    DISABLE = False
    SUBMIT = True
    COVER_RATE = 0.8
    true_list = ["正确", "对", "√", "是", "T", "true"]
    false_list = ["错误", "错", "×", "否", "不对", "F", "false"]

    def __init__(self):
        self.queries = []

    def query(self, q_info):
        self.queries.append(q_info)
        return None

    def get_submit_params(self):
        return ""

    def judgement_select(self, answer: str) -> bool:
        from api.answer_check import check_judgement

        result = check_judgement(answer, self.true_list, self.false_list)
        return result == 1


@pytest.fixture
def stub_tiku():
    return StubTiku()
