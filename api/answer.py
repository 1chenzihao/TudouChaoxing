import configparser
import json
import random
import re
import time
from pathlib import Path
from re import sub

import httpx
import requests
from openai import OpenAI
from urllib3 import disable_warnings, exceptions

from api.answer_check import *
from api.logger import logger

# 大模型解题能力由 api/llm.py 与 api/solver.py 提供, 接口已冻结
# 若这两个模块暂时缺失或依赖不全, 这里降级为"题库未启用", 原有搜题流程不受影响
try:
    from api.llm import LLMClient, LLMConfig, LLMError
    from api.solver import Question, QuestionSolver, SolveResult

    LLM_IMPORT_ERROR = None
except Exception as _llm_exc:  # ImportError 及模块内部依赖缺失都算
    LLMClient = None
    LLMConfig = None
    LLMError = None
    Question = None
    QuestionSolver = None
    SolveResult = None
    LLM_IMPORT_ERROR = _llm_exc

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy
    """
    DEFAULT_CACHE_FILE = "cache.json"

    def __init__(self, file: str = DEFAULT_CACHE_FILE):
        self.cache_file = Path(file)
        if not self.cache_file.is_file():
            self._write_cache({})

    def _read_cache(self) -> dict:
        try:
            with self.cache_file.open("r", encoding="utf8") as fp:
                return json.load(fp)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_cache(self, data: dict) -> None:
        try:
            with self.cache_file.open("w", encoding="utf8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=4)
        except IOError as e:
            logger.error(f"Failed to write cache: {e}")

    def get_cache(self, question: str):
        data = self._read_cache()
        return data.get(question)

    def add_cache(self, question: str, answer: str) -> None:
        data = self._read_cache()
        data[question] = answer
        self._write_cache(data)


class Tiku:
    CONFIG_PATH = "config.ini"  # 默认配置文件路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率
    true_list = []
    false_list = []
    def __init__(self) -> None:
        self._name = None
        self._api = None
        self._conf = None

    @property
    def name(self):
        return self._name
    
    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api
    
    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self,value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())
        if not self.DISABLE:
            # 设置提交模式
            # 注意: 配置项一律用 .get 取值, 缺键时用默认值, 避免 KeyError 直接把整章答题崩掉
            conf = self._conf or {}
            submit = str(conf.get('submit', 'false')).strip().lower()
            self.SUBMIT = submit in ('true', '1', 'yes', 'on')
            try:
                self.COVER_RATE = float(conf.get('cover_rate', 0.8))
            except (TypeError, ValueError):
                logger.warning(f"配置项 cover_rate 非法 -> {conf.get('cover_rate')!r}, 已回退默认值 0.8")
                self.COVER_RATE = 0.8
            self.true_list = self._split_conf_list(
                conf.get('true_list', '正确,对,√,是')
            )
            self.false_list = self._split_conf_list(
                conf.get('false_list', '错误,错,×,否,不对,不正确')
            )
            # 调用自定义题库初始化
            self._init_tiku()

    @staticmethod
    def _split_conf_list(value) -> list:
        """
        把配置里的逗号分隔列表切成 list, 并去掉空白项
        兼容传入空值 / 空字符串的情况, 此时返回空列表
        """
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(',') if item.strip()]
        
    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self,config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置, 如果未能查到, 停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            return config['tiku']
        except (KeyError, FileNotFoundError):
            logger.info("未找到tiku配置, 已忽略题库功能")
            self.DISABLE = True
            return None
    def query(self,q_info:dict):
        if self.DISABLE:
            return None

        # 预处理, 去除【单选题】这样与标题无关的字段
        logger.debug(f"原始标题：{q_info['title']}")
        q_info['title'] = sub(r'^\d+', '', q_info['title'])
        q_info['title'] = sub(r'（\d+\.\d+分）$', '', q_info['title'])
        logger.debug(f"处理后标题：{q_info['title']}")

        # 先过缓存
        cache_dao = CacheDAO()
        answer = cache_dao.get_cache(q_info['title'])
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info)
            if answer:
                answer = answer.strip()
                cache_dao.add_cache(q_info['title'], answer)
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
                if check_answer(answer, q_info['type'], self):
                    return answer
                else:
                    logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
                    return None
                    
            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None
    
    def _query(self,q_info:dict):
        """
        查询接口, 交由自定义题库实现
        """
        pass

    def solve_structured(self, q_info: dict):
        """结构化解题旁路。子类可返回 SolveResult, 基类返回 None 表示走旧 query 流程"""
        return None

    def get_available_providers(self) -> list:
        """
        列出当前已实现的题库 provider, 用于配置写错时给出可用列表
        """
        return [
            name
            for name, obj in globals().items()
            if isinstance(obj, type) and issubclass(obj, Tiku) and obj is not Tiku
        ]

    def get_tiku_from_config(self):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        if not self._conf:
            # 尝试从默认配置文件加载
            self.config_set(self._get_conf())
        if self.DISABLE:
            return self
        available = ", ".join(sorted(self.get_available_providers()))
        try:
            cls_name = self._conf['provider']
        except (KeyError, TypeError):
            cls_name = ""
        if not cls_name:
            self.DISABLE = True
            logger.error(f"未找到题库配置(provider 为空), 已忽略题库功能; 可用 provider: {available}")
            return self
        new_cls_type = globals().get(cls_name)
        if not isinstance(new_cls_type, type) or not issubclass(new_cls_type, Tiku):
            # provider 写错时不能静默崩掉, 把可用列表打出来方便改配置
            self.DISABLE = True
            logger.error(f"题库 provider 不存在 -> {cls_name}; 可用 provider: {available}")
            return self
        new_cls = new_cls_type()
        new_cls.config_set(self._conf)
        return new_cls

    def judgement_select(self, answer: str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        # 对响应的答案作处理
        answer = answer.strip()
        if answer in self.true_list:
            return True
        elif answer in self.false_list:
            return False
        else:
            # 无法判断, 随机选择
            logger.error(f'无法判断答案 -> {answer} 对应的是正确还是错误, 请自行判断并加入配置文件重启脚本, 本次将会随机选择选项')
            return random.choice([True,False])
    
    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

# 按照以下模板实现更多题库

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0   # token队列计数器
        self._times = 100   # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self,q_info:dict):
        res = requests.get(
            self.api,
            params={
                'question':q_info['title'],
                'token': self._token,
                # 'type':q_info['type'], #修复478题目类型与答案类型不符（不想写后处理了）
                # 没用，就算有type和options，言溪题库还是可能返回类型不符，问了客服，type仅用于收集
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times",f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times",self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None
    
    def load_token(self): 
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()

class TikuLike(Tiku):
    # Like知识库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'Like知识库'
        self.ver = '1.0.8' #对应官网API版本
        self.query_api = 'https://api.datam.site/search'
        self.balance_api = 'https://api.datam.site/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._token = None
        self._times = -1
        self._search = False
        self._count = 0

    def _query(self,q_info:dict):
        q_info_map = {"single":"【单选题】","multiple":"【多选题】","completion":"【填空题】","judgement":"【判断题】"}
        api_params_map = {0:"others",1:"choose",2:"fills",3:"judge"}
        q_info_prefix = q_info_map.get(q_info['type'],"【其他类型题目】")
        option_map = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5, "G": 6, "H": 7, 'a': 0, "b": 1, "c": 2, "d": 3,
                      "e": 4, "f": 5, "g": 6, "h": 7}
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n{options}"
        ret = ""
        ans = ""
        res = requests.post(
            self.query_api,
            json={
                'query': question,
                'token': self._token,
                'model': self._model if self._model else '',
                'search': self._search
            },
            verify=False
        )

        if res.status_code == 200:
            res_json = res.json()
            q_type = res_json['data'].get('type', 0)
            params = api_params_map.get(q_type, "")
            tans = res_json['data'].get(params, "")
            ans = ""
            match q_type:
                case 1:
                    for i in tans:
                        ans = ans + q_info['options'][option_map[i]] + '\n'
                case 2:
                    for i in tans:
                        ans = ans + i + '\n'
                case 3:
                    ans = "正确" if tans == 1 else "错误"
                case 0:
                    ans = tans
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
            return None

        ret += str(ans)

        self._times -= 1

        #10次查询后更新实际次数
        self._count = (self._count+1) % 10

        if self._count == 0:
            self.update_times()
        
        return ret
    
    def update_times(self):
        res = requests.post(
            self.balance_api,
            json={
                'token': self._token,
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            self._times = res_json["data"].get("balance",self._times)
            logger.info(f"当前LIKE知识库Token剩余查询次数为: {self._times}")
        else:
            logger.error('TOKEN出现错误，请检查后再试')

    def load_token(self): 
        token = self._conf['tokens'].split(',')[-1] if ',' in self._conf['tokens'] else self._conf['tokens']
        self._token = token

    def load_config(self):
        self._search = self._conf['likeapi_search']
        self._model = self._conf['likeapi_model']
        var_params = {"likeapi_search": self._search, "likeapi_model": self._model}
        config_params = {"likeapi_search": False, "likeapi_model": None}

        for k,v in config_params.items():
            if k in self._conf:
                var_params[k] = self._conf[k]
            else:
                var_params[k] = v

    def _init_tiku(self):
        self.load_token()
        self.load_config()
        self.update_times()

class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self) -> None:
        super().__init__()
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = requests.post(
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['bestAnswer']):
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            return sep.join(res_json['answer']['bestAnswer']).strip()
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']

class AI(Tiku):
    # AI大模型答题实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'AI大模型答题'
        self.last_request_time = None

    def _query(self, q_info: dict):
        def remove_md_json_wrapper(md_str):
            # 使用正则表达式匹配Markdown代码块并提取内容
            pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
            match = re.search(pattern, md_str, re.DOTALL)
            return match.group(1).strip() if match else md_str.strip()
        
        if self.http_proxy:
            proxy = self.http_proxy
            httpx_client = httpx.Client(proxy=proxy)
            client = OpenAI(http_client=httpx_client, base_url = self.endpoint,api_key = self.key)
        else:
            client = OpenAI(base_url = self.endpoint,api_key = self.key)
        # 去除选项字母，防止大模型直接输出字母而非内容
        options_list = q_info['options'].split('\n')
        cleaned_options = [re.sub(r"^[A-Z]\s*", "", option) for option in options_list]
        options = "\n".join(cleaned_options)
        # 判断题目类型
        if q_info['type'] == "single":
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为单选题，你只能选择一个选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}\n选项：{options}"
                    }
                ]
            )
        elif q_info['type'] == 'multiple':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为多选题，你必须选择两个或以上选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案1\",\n\"答案2\",\n\"答案3\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}\n选项：{options}"
                    }
                ]
            )
        elif q_info['type'] == 'completion':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为填空题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}"
                    }
                ]
            )
        elif q_info['type'] == 'judgement':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为判断题，你只能回答正确或者错误，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}"
                    }
                ]
            )
        else:
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为简答题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"这是我的答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}"
                    }
                ]
            )

        try:
            if self.last_request_time:
                interval_time = time.time() - self.last_request_time
                if interval_time < self.min_interval_seconds:
                    sleep_time = self.min_interval_seconds - interval_time
                    logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
                    time.sleep(sleep_time)
            self.last_request_time = time.time()
            response = json.loads(remove_md_json_wrapper(completion.choices[0].message.content))
            sep = "\n"
            return sep.join(response['Answer']).strip()
        except Exception:
            logger.error("无法解析大模型输出内容")
            return None

    def _init_tiku(self):
        self.endpoint = self._conf['endpoint']
        self.key = self._conf['key']
        self.model = self._conf['model']
        self.http_proxy = self._conf['http_proxy']
        self.min_interval_seconds = int(self._conf['min_interval_seconds'])
class SiliconFlow(Tiku):
    """硅基流动大模型答题实现"""
    def __init__(self):
        super().__init__()
        self.name = '硅基流动大模型'
        self.last_request_time = None

    def _query(self, q_info: dict):
        def remove_md_json_wrapper(md_str):
            # 解析可能存在的JSON包装
            pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
            match = re.search(pattern, md_str, re.DOTALL)
            return match.group(1).strip() if match else md_str.strip()

        # 构造请求头
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # 构造系统提示词
        system_prompt = ""
        if q_info['type'] == "single":
            system_prompt = "本题为单选题，请根据题目和选项选择唯一正确答案，输出的是选项的具体内容，而不是内容前的ABCD，并以JSON格式输出：示例回答：{\"Answer\": [\"正确选项内容\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'multiple':
            system_prompt = "本题为多选题，请选择所有正确选项，输出的是选项的具体内容，而不是内容前的ABCD，以JSON格式输出：示例回答：{\"Answer\": [\"选项1\",\"选项2\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'completion':
            system_prompt = "本题为填空题，请直接给出填空内容，以JSON格式输出：示例回答：{\"Answer\": [\"答案文本\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'judgement':
            system_prompt = "本题为判断题，请回答'正确'或'错误'，以JSON格式输出：示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"

        # 构造请求体
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": f"题目：{q_info['title']}\n选项：{q_info['options']}"
                }
            ],
            "stream": False,

            "max_tokens": 4096,

            "temperature": 0.7,
            "top_p": 0.7,
            "response_format": {"type": "text"}
        }

        # 处理请求间隔
        if self.last_request_time:
            interval = time.time() - self.last_request_time
            if interval < self.min_interval:
                time.sleep(self.min_interval - interval)

        try:
            response = requests.post(
                self.api_endpoint,
                headers=headers,
                json=payload,
                timeout=30
            )
            self.last_request_time = time.time()
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                parsed = json.loads(remove_md_json_wrapper(content))
                return "\n".join(parsed['Answer']).strip()
            else:
                logger.error(f"API请求失败：{response.status_code} {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"硅基流动API异常：{e}")
            return None

    def _init_tiku(self):
        # 从配置文件读取参数
        self.api_endpoint = self._conf.get('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
        self.api_key = self._conf['siliconflow_key']

        self.model_name = self._conf.get('siliconflow_model', 'deepseek-ai/DeepSeek-V3')


        self.min_interval = int(self._conf.get('min_interval_seconds', 3))

class TikuLLM(Tiku):
    """
    大模型解题题库实现

    依赖 api/llm.py (OpenAI 兼容客户端) 与 api/solver.py (投票 + 置信度判定)。
    与搜题类题库不同, 它不依赖题库命中, 覆盖面更广; 正确率靠多次采样投票与置信度阈值守住。
    配置项(均在 [tiku] 段): endpoint / key / model / votes / temperature / confidence /
    max_escalations / min_interval_seconds / cache_enabled
    """

    def __init__(self) -> None:
        super().__init__()
        self.name = '大模型解题'
        self.client = None
        self.solver = None
        self.votes = 3
        self.temperature = 0.6
        self.confidence = 0.67
        self.max_escalations = 2
        self.cache_enabled = True
        self._cache_dao = None
        # 统计计数, 供 report() 输出
        self._question_count = 0
        self._confident_count = 0
        self._low_confidence_count = 0
        self._failed_count = 0
        self._cache_hit_count = 0

    def _init_tiku(self):
        if LLMClient is None or QuestionSolver is None:
            # 依赖模块缺失时把题库停用, 主流程会自动退回"随机作答 + 不提交"
            self.DISABLE = True
            logger.error(f"大模型解题模块不可用, 已停用该题库: {LLM_IMPORT_ERROR}")
            return

        conf = self._conf or {}
        self.votes = self._conf_int(conf, 'votes', 3)
        self.temperature = self._conf_float(conf, 'temperature', 0.6)
        self.confidence = self._conf_float(conf, 'confidence', 0.67)
        self.max_escalations = self._conf_int(conf, 'max_escalations', 2)
        self.cache_enabled = str(conf.get('cache_enabled', 'true')).strip().lower() in (
            'true', '1', 'yes', 'on', 'y', 't',
        )
        self._cache_dao = CacheDAO() if self.cache_enabled else None
        try:
            llm_config = LLMConfig.from_mapping(dict(conf))
            self.client = LLMClient(llm_config)
            self.solver = QuestionSolver(
                self.client,
                votes=self.votes,
                temperature=self.temperature,
                confidence=self.confidence,
                max_escalations=self.max_escalations,
                cache=self._cache_dao,
            )
        except Exception as e:
            self.DISABLE = True
            logger.error(f"大模型解题初始化失败, 已停用该题库: {type(e).__name__}: {e}")
            return
        logger.info(
            f"大模型解题已就绪: endpoint={conf.get('endpoint', '')} model={conf.get('model', '')} "
            f"votes={self.votes} temperature={self.temperature} confidence={self.confidence} "
            f"max_escalations={self.max_escalations} 缓存={'开启' if self._cache_dao else '关闭'}"
        )

    @staticmethod
    def _conf_int(conf, key: str, default: int) -> int:
        try:
            return int(str(conf.get(key, default)).strip())
        except (TypeError, ValueError):
            logger.warning(f"配置项 {key} 非法 -> {conf.get(key)!r}, 已回退默认值 {default}")
            return default

    @staticmethod
    def _conf_float(conf, key: str, default: float) -> float:
        try:
            return float(str(conf.get(key, default)).strip())
        except (TypeError, ValueError):
            logger.warning(f"配置项 {key} 非法 -> {conf.get(key)!r}, 已回退默认值 {default}")
            return default

    @staticmethod
    def _build_question_dict(q_info: dict) -> dict:
        """
        把主流程的题目字典转成交给 solver 的字典
        标题清洗与旧 query 保持一致, 去掉题号与分值, 提高模型命中率
        """
        q = dict(q_info)
        title = str(q.get('title', ''))
        title = sub(r'^\d+', '', title)
        title = sub(r'（\d+\.\d+分）$', '', title)
        q['title'] = title.strip()
        options = q.get('options', '')
        if isinstance(options, (list, tuple)):
            options = "\n".join(str(o) for o in options)
        q['options'] = str(options or '')
        if not q.get('type'):
            q['type'] = 'unknown'
        return q

    def solve_structured(self, q_info: dict):
        """
        结构化解题旁路: 返回 SolveResult, 不可用或异常时返回 None 让主流程回退旧搜题
        单题异常一律吞掉, 不能让一道题把整章答题崩掉
        """
        if self.DISABLE or self.solver is None:
            return None
        self._question_count += 1
        title_preview = str(q_info.get('title', ''))[:60]
        try:
            question = Question.from_dict(self._build_question_dict(q_info))
            result = self.solver.solve(question)
        except Exception as e:
            self._failed_count += 1
            logger.warning(
                f"大模型解题异常, 本题回退旧搜题流程 -> {title_preview}: {type(e).__name__}: {e}"
            )
            return None
        answer = getattr(result, 'answer', None)
        if not isinstance(answer, str) or not answer.strip():
            self._failed_count += 1
            logger.warning(f"大模型未给出可用答案, 本题回退旧搜题流程 -> {title_preview}")
            return None
        if getattr(result, 'source', '') == 'cache':
            self._cache_hit_count += 1
        if getattr(result, 'confident', False):
            self._confident_count += 1
        else:
            self._low_confidence_count += 1
        logger.info(
            f"大模型解题 -> {answer} (confident={getattr(result, 'confident', False)}, "
            f"votes={getattr(result, 'votes', None)}, source={getattr(result, 'source', None)})"
        )
        return result

    @staticmethod
    def _letters_to_option_text(answer: str, options) -> list:
        """
        把 "ABD" 这种选项字母还原成选项原文, 供旧格式(_query)使用
        answer 不是纯字母时返回空列表, 交给调用方原样返回
        """
        if not re.fullmatch(r'[A-Ha-h]+', answer.strip()):
            return []
        if isinstance(options, (list, tuple)):
            texts = [str(o).strip() for o in options if str(o).strip()]
        else:
            texts = [line.strip() for line in str(options or '').split('\n') if line.strip()]
        if not texts:
            return []
        lines = []
        for ch in answer.strip():
            index = ord(ch.upper()) - ord('A')
            if 0 <= index < len(texts):
                lines.append(texts[index])
        return lines

    def _query(self, q_info: dict):
        """
        旧接口兼容: 调 solve_structured 并把结果转成旧格式
        单选/多选用选项原文换行拼接, 判断题返回 正确/错误, 其它返回答案文本
        """
        result = self.solve_structured(q_info)
        if result is None:
            return None
        answer = str(getattr(result, 'answer', '') or '').strip()
        if not answer:
            return None
        q_type = q_info.get('type', '')
        if q_type in ('single', 'multiple'):
            option_lines = self._letters_to_option_text(answer, q_info.get('options', ''))
            return "\n".join(option_lines) if option_lines else answer
        if q_type == 'judgement':
            low = answer.lower()
            if low in ('true', '正确', '对', '√', 't', '1', 'yes', '是'):
                return '正确'
            if low in ('false', '错误', '错', '×', 'f', '0', 'no', '否', '不对', '不正确'):
                return '错误'
            return answer
        return answer

    def report(self):
        """
        打印一条大模型解题统计, 供主流程结束前调用
        """
        stats = getattr(self.client, 'stats', None)
        calls = getattr(stats, 'calls', 0) if stats is not None else 0
        failures = getattr(stats, 'failures', 0) if stats is not None else 0
        prompt_tokens = getattr(stats, 'prompt_tokens', 0) if stats is not None else 0
        completion_tokens = getattr(stats, 'completion_tokens', 0) if stats is not None else 0
        retries = getattr(stats, 'retries', 0) if stats is not None else 0
        # 求解器没有自带命中计数时, 用 source=="cache" 的返回数兜底统计
        cache_hits = getattr(self.solver, 'cache_hits', None) if self.solver is not None else None
        cache_hit_text = cache_hits if cache_hits is not None else self._cache_hit_count
        logger.info(
            f"{self.name}统计: 题目 {self._question_count} 道, 高可信 {self._confident_count} 道, "
            f"低可信 {self._low_confidence_count} 道, 失败 {self._failed_count} 道, "
            f"API 调用 {calls} 次, 失败 {failures} 次, 重试 {retries} 次, "
            f"prompt_tokens {prompt_tokens}, completion_tokens {completion_tokens}, "
            f"缓存命中 {cache_hit_text}"
        )
