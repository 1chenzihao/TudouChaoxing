try:
    from requests.exceptions import JSONDecodeError
except:  # noqa: E722
    from json import JSONDecodeError


class LoginError(Exception):
    def __init__(self, *args: object):
        super().__init__(*args)


class InputFormatError(Exception):
    def __init__(self, *args: object):
        super().__init__(*args)


class MaxRollBackExceeded(Exception):
    def __init__(self, *args: object):
        super().__init__(*args)


class MaxRetryExceeded(Exception):
    def __init__(self, *args: object):
        super().__init__(*args)


class FontDecodeError(Exception):
    def __init__(self, *args: object):
        super().__init__(*args)

class AntiSpiderError(Exception):
    """超星风控拦截：返回 antispiderShowVerify.ac 要求图形验证码，或 HTTP 202。

    这玩意儿一出现，卡片页会变空页、视频上报会 403。继续硬撞只会让风控更严，
    正确做法是停下来让用户去浏览器输一次验证码，或者等待冷却。
    """

    def __init__(self, *args: object):
        super().__init__(*args)
