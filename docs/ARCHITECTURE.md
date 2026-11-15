# 架构与扩展说明（ARCHITECTURE）

> 本文描述改造后的模块分层、数据流与正确率机制。**范围只有章节测验与作业**：考试任务点（`exam-` 前缀）一律跳过，不发任何请求。

## 1. 分层

```
main.py                调度层：argparse / config.ini → 课程循环 → 任务点分派
  └─ api/base.py       Chaoxing 客户端 + 任务执行（study_video / study_document /
                        study_work / strdy_read）
       ├─ api/decode.py    解析层：课程、章节、任务卡、题目 HTML → 结构化 dict
       │    └─ api/font_decoder.py + cxsecret_font.py  字体加密还原
       ├─ api/answer.py    题库适配层：Tiku 基类 + 各 provider（含 TikuLLM）
       │    └─ api/solver.py  解题引擎：题型归一化 → 提示词 → 投票 → 校验 → 升级
       │         └─ api/llm.py  LLM 客户端：配置、限速、重试、JSON 解析、用量统计
       ├─ api/cipher.py    AES（登录参数）
       ├─ api/cookies.py   会话持久化
       └─ api/notification.py  外部通知
```

## 2. 一次章节测验/作业的数据流

```
任务卡(job) ──decode_course_card──► {type:"workid", jobid:"work-xxx" | "exam-xxx", enc, otherinfo}
      │
      ├─ jobid 前缀 work-  → study_work()   取题 GET /mooc-ans/api/work
      └─ jobid 前缀 exam-  → 记一行日志后直接放过（一个请求都不发）
      │
      ▼
decode_questions_info(html) ──► form_data + questions[]
      │                                        （含字体解密，答案域键名 answer<qid>）
      ▼
_solve_questions(questions)
      │  每题的优先级：
      │  1. tiku.solve_structured(q) → SolveResult.answer（已映射成提交格式）
      │  2. tiku.query(q) → 旧的字符串匹配映射（兼容所有老 provider / 第三方题库）
      │  3. random_answer(q)（保底合法答案）
      │  每题记 answerSource<qid> ∈ {llm, llm_low, tiku, cover, random}
      ▼
组装提交体（answer<qid> / answertype<qid> / answerwqbid / pyFlag）
      │  pyFlag="" 提交；pyFlag="1" 保存
      │  未答出的题（random）在"保存"模式下留空，不污染卷面
      ▼
POST /mooc-ans/work/addStudentWorkNew（章节测验 / 作业）
```

## 3. 正确率机制（四条腿）

| 机制 | 在哪 | 作用 |
| --- | --- | --- |
| 题型归一化 | `solver.Question.from_dict` | 平台题型 code（0/1/2/3/4）与题库 type 字符串可能错位，统一成 single/multiple/completion/judgement/shortanswer，再决定投票策略 |
| 选项结构化 | `solver.Option` | 把 `A. xxx` 拆成 key+text，答案校验和提交都基于 key，不再靠"取字符串首字"猜 |
| 自一致性投票 | `solver.QuestionSolver.solve` | 同题独立采样 N 次，选项级计票。单选/判断取票王，多选对每项独立过半，填空按相似度聚类取最大簇 |
| 校验 + 升级 | `solver` + `base._solve_questions` | 非法字母（不在选项集合内）的样本作废；有效票不足则加大票数 / 降温重问；仍然无解才 fallback，且 fallback 答案一定合法（多选 ≥2 项） |

推理成本与正确率的旋钮：`votes`（每题采样次数）、`confidence`（判可信的票占比）、`max_escalations`（升级轮数）。默认 3 / 0.67 / 2，一般够用；题目质量差的卷子把 votes 提到 5。

## 4. 缓存

- `cache.json`（`answer.CacheDAO`）：键是题目文本，跨课程复用，命中直接返回，省 token 也保证同一道题答案一致。
- `solver` 的缓存层用 `md5(type + title + raw_options)`，避免"题干相同但选项顺序不同"被误命中。

## 5. 任务点分派表（main.process_job）

| job.type | 处理 |
| --- | --- |
| `video` | study_video（先按 Video，失败回退 Audio） |
| `document` | study_document |
| `read` | strdy_read |
| `workid` + jobid 前缀 `work-` | study_work（章节测验/作业） |
| `workid` + jobid 前缀 `exam-` | 记日志跳过，不发请求（考试不在支持范围） |

## 6. 扩展点

- **新增题库/模型服务**：继承 `answer.Tiku`，实现 `_query`；想在答案层面拿结构化的（带置信度、投票明细）就再实现 `solve_structured`。provider 名写进 `config.ini` 的 `[tiku] provider=` 就能被 `get_tiku_from_config` 加载。
- **新增题型**：在 `decode._get_question_type` 加分类型 code→名称，在 `solver` 里给出对应的提示词与答案映射；提交侧无需改动（仍是 answer/answertype 两个键）。
- **新增任务类型**：`decode._process_attachment_cards` 加分支产出 job dict，`main.process_job` 加分派。

## 7. 故障隔离约定

- 单题失败不影响整章：`solve_structured` / `query` 内部异常一律捕获并降级。
- LLM 连续失败只降级不中断：进入 fallback_random，整卷覆盖率照算，未达阈值就只保存不提交。
- 考试任务点**零请求**：`main.process_job` 与 `base.study_work` 两处都会拦掉 `exam-` 前缀，避免拿 exam 的 workId 去请求测验接口、拿到错误页后触发重试。
- 会话级：`cookies.txt` 持久化，登录失败不重试超过一次。

## 8. 正确率相关的"已修坑"清单

这些是实现期由独立审查 + 离线实验确认过的真实缺陷，全部已修并有用例锁住（`tests/test_solver.py`、`tools/selftest_solver.py`）。改动时别退回去：

| 坑 | 症状 | 现在的做法 |
| --- | --- | --- |
| 答案到选项的映射用"子串包含" | 选项 `A=对 / B=不对`，模型回"不对" → 映射到 **A**（正好反了） | 先比"与选项正文完全一致"，再认字母，最后才做子串匹配 |
| 子串匹配取第一个命中 | 选项 `A=12 / B=2 / C=24`，模型回"24" → 映射到 **B** | 子串匹配两侧长度都 ≥ 2、取最长；`24` 不会再被 `2` 抢走 |
| 一个字符串里多个字母只抽第一个 | 模型回"A、B" → 只认 A，多选题被系统性漏选 | 支持 `A、B` / `A,B` / `A和B` 的多字母串 |
| 判断题判定走子串扫描 | "不是""没错""未必正确" → 结论反转 | 先剥否定前缀（不/非/未/没）再查词表；真假词同时命中直接判为不可判 |
| 判断题选项文本判不出 | 整批判废 → 每题抛硬币 | 两个选项且文本不可判时，按学习通固定顺序（正确在前、错误在后）退化 |
| 多选入选用严格过半 | `{A:2,B:2}/4` 一项都不入选，正确答案被丢 | 改为"达到半数"（`>= ceil(valid/2)`） |
| 多选只给 1 项还标高可信 | 单字母必然判错，却带着 confident 去提交 | 入选 < 2 项一律低可信 |
| 升级轮丢弃上一轮票 + 降到 0 温 | 一轮 2:1 的正确多数，被二轮 0 温复读同一个错误答案翻成"6:0 高可信" | 各轮票数**累加**；升级轮**升温**增加多样性 |
| `confidence=0.67` 配 `votes=3` 用比例比较 | `2/3=0.6667 < 0.67`，只要不是全票就必然升级，投票形同虚设 | 改整数票数：`need = round(valid * confidence)`，2/3 直接采信 |
| 假置信答案写进缓存 | 一次错误共识被永久固化，后续直接命中 | 只缓存"每项票数都达标且样本 ≥ 2"的结论 |
| 文本聚类只看向似度 | "正确"与"不正确"、"24"与"2.4" 被并成一簇 | 并簇前加闸门：否定方向必须一致、纯数值只认全等 |
| 空响应被判成不可重试 | 中转站偶发空 content 直接失败，白丢一次采样 | 新增 `LLMEmptyContentError`，属于可重试 |
| 未知异常默认重试 | 自家 `TypeError` 也重试 4 次，单题白等 10 秒 | 兜底改为不重试，只信任显式白名单 |
| 截断的 JSON 直接作废 | `max_tokens` 打死时整条采样浪费 | `_repair_truncated_json` 补结尾括号后重试解析（只补括号不猜内容） |
| 随机兜底被记成 `llm_low` | 覆盖率虚高 → "覆盖率不足就不交卷"的闸门失效 | 随机兜底来源记 `random`，solved 里减掉 |
| 保存模式把低可信/随机答案写进卷面 | 用户以为答完了，实际是模型瞎猜 | 保存模式只写 `cover/llm/tiku`；提交模式才全写 |

回归命令（都在仓库根跑，无需网络与 API key）：

```bash
python -m pytest -q                 # 138 项
python tools/selftest_solver.py     # 18 项，含投票/升级/降级/限速
python tools/e2e_offline.py         # HTML -> 解析 -> 投票 -> 答案合法性 -> 缓存
```
