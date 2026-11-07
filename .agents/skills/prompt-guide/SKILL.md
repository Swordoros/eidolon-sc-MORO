---
name: prompt-guide
description: "各类提示词的作用、拼装结构和修改位置：角色卡、此刻、回复格式、搭话、朋友圈、写信、行程、日记、纪念品、判定、记忆、配图等，各自改哪个文件。当用户问「某段提示词在哪改/是干什么的」「改 XX 的提示词」时用。"
---

# 提示词修改指南

Eidolon 的提示词不是一段长文本，而是**分文件、分层拼装**出来的。改之前先定位：你要改的那句话属于哪一层、在哪个文件、改完跑哪些测试、怎么上线。

## 一条提示词长什么样（ADR-0020 布局）

发给角色聊天模型的 prompt 按固定顺序拼成若干条 system/user 消息：

1. **`always_known`（固定可缓存段）** —— 角色卡 + 生日 + 世界设定。全天下所有用户都一样，标 `cache_breakpoint=True` 供服务端缓存。见 `today.py::always_known`；内容来自角色 `sheet`（`characters.py`）和 `world.py::World.prompt`。
2. **制作人段** —— 他是谁、性别、生日、口吻规则。`producer.py::producer_prompt`。
3. **历史对话**（回复/搭话等有上下文的场景）—— 最近几轮。
4. **此刻 + 素材 + 规则段** —— 时间/天气/行程/周期/好感 + 该场合的素材 + 该场合的规则。此刻由 `her_now.py::HerNow.lines` 拼，规则是各场合自己的 `*_RULES`。
5. **用户的话 / 触发语** —— `WANTS_TO_*` 或用户原文。

朋友圈（`moment_prompt.py::post_prompt`）、回评论（`reply_prompt`）、写信（`letter_prompt.py`）都照这个布局，只把「回复格式」换成各自的规则。

## 各层改哪里

### 角色 / 世界 / 今天
- 角色配置：`characters/characters.toml`（id、`name`/`name_zh`、`birthday`、`sheet`、`facts` 路径等；字段定义在 `characters.py::Character`）。
- 角色卡正文：`characters/sheets/<id>.md`。
- 角色事实（facts，朋友圈素材/人物关系等抽取）：`characters/facts/<id>.md`，解析在 `characters.py::parse_facts`。
- 世界设定（live、组合等）：`world.py::World.prompt`（数据来自配置，如 `characters/world.json`）。
- 今天的日期/节日/生日行：`today.py::today_lines`；`always_known` 也在 `today.py`。

### 制作人
- `producer.py::producer_prompt` —— 制作人性别、生日、口吻规则。
- `producer.py::now_prompt` —— 打字/通话/搭话用的「# 此刻」行（时间按制作人时区）。
- `producer.py::posting_now_prompt` —— 朋友圈/回评论用的「# 此刻」行（时间按东京，含时段词 `_SLOT_NAMES`）。

### 此刻（各场合都有的「# 此刻」段）
`her_now.py` 用 `CARRIES` 表决定每个场合带哪些信息（time/today/schedule/cycle/affinity/moments/weather），各子段落：
- 行程：`schedule.py::schedule_lines`
- 周期：`cycles.py::cycle_lines`
- 天气 / 制作人在哪：`her_weather.py::weather_lines`
- 好感（她对他什么态度、能不能亲昵）：`affinity.py::affinity_prompt` / `tier_prompt`
- 今天 / 节日：`today.py::today_lines`

### 回复格式与说话方式
`reply_format.py` —— `<ja>…</ja><zh>…</zh>` 的输出格式（`_FORMAT`）、说话长度、语气提示（`_CUES` / `_DIRECTED_CUES`，经 `format_instructions()` 选），以及流式解析器 `ReplyParser`、中文名替换 `NameReplacer`。改「她怎么输出、怎么分段、怎么念」看这里。

### 各场合的规则（`engine.py` 集中了一大批）
- 先开口（搭话）：`engine.py::REACH_OUT_RULES`（`_REACH_OUT_RULES` 模板；带朋友圈时 `REACH_OUT_RULES_WITH_MOMENTS`）。
- 回复里发照片的格式：`engine.py::PHOTO_RULES` / `photo_rules(...)`（`<photo>` 标签写法）。
- 外景照片：`engine.py` 的 `TRIP_PHOTO_*` 常量 + `trip_photos.py`。
- 写日记：`engine.py::DIARY_RULES`。
- 送纪念品：`engine.py::KEEPSAKE_RULES` / `keepsake_rules(...)`。
- 写行程：`engine.py::SCHEDULE_RULES`（单人）、`BATCH_SCHEDULE_RULES`（同组合批量）；行程的「变数/出外景」在 `schedules.py`（`TWISTS`、`TRIP_TWIST`、`TRIP_WITH_TWIST`）。
- 触发语：`engine.py::WANTS_TO_*`（`WANTS_TO_REACH_OUT`、`WANTS_TO_SEND_TRIP_PHOTO`、`WANTS_TO_PLAN`、`WANTS_TO_WRITE_DIARY`、`WANTS_TO_GIVE`）。
- 事件叙述（送礼/挂断/搭话等插进上下文的那句）：`engine.py::narrations` / `_narration` / `_reached_out`。

### 朋友圈
- 发动态规则：`moment_prompt.py::POST_RULES`（正文/配图/地点/点赞评论的格式和约束）。
- 回评论规则：`moment_prompt.py::REPLY_RULES`。
- 素材抽取（日常素材、地点、圈子、时段过滤）：`moment_draws.py`。素材按当前时段过滤的关键词在 `_SEED_SLOT_WORDS`（各时段词）和 `_SEED_NEUTRAL_WORDS`（习惯/过去的词，视为中性不挡）；时段划分在 `moment_slot()`、`MOMENT_SLOT_NAMES`，一条素材归哪个时段由 `seed_slot()` 判。
- 拼装：`moment_prompt.py::post_prompt` / `reply_prompt`。

### 写信 / 酒吧
- 写信：`letter_prompt.py::_RULES`（写信规则）、`letter_prompt()`。
- 酒吧（另立的视觉世界，`/play/bar`）：`bar_prompt.py::visit_prompt` / `shared_prompt`。

### 判定类（给「判断模型」看，不是给角色看）
- 不该说的话 / 操纵 / 态度：`judge.py`。
- 好感度增减：`affinity_judge.py`。
- 通话里每句的情绪：`emotion.py`。
- 通话环境音：`ambience.py`。

### 记忆
- 从对话里抽记忆：`memory.py::_extraction_prompt` / `_INSTRUCTIONS`。
- 检索剧情：`retrieval.py::recall_prompt`。

### 配图 / 生图提示词
- 照片 tag 的解析和拼装（NovelAI 格式）：`photo_prompt.py`（`read_tag`、`group_prompt`、`read_picture`）。
- GPT Image 的文生图提示词：`gpt_image_prompt.py::gpt_picture_prompt`。

## 改提示词的流程

1. 照上面定位到那个文件/常量/函数。
2. 改完跑对应测试：提示词相关集中在 `backend/tests/test_*prompt*`、`test_moments*`、`test_moment_*`，命令 `cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/<文件>.py`。
3. 上线：改的是后端 `.py`，走 overlay 镜像秒上线；改的是 `characters/` 数据（sheet/facts/toml）也是后端镜像的一部分。见 [[deploy-server]]。

## 注意

- 角色卡 `name` 是内部 key，提示词里显示名用 `name_zh`（中文官方名）；相关映射在 `characters.py::chinese_name_map`、`EXTRA_NAME_TRANSLATIONS`。
- `always_known` 段是缓存友好的，别把会随回合变化的东西塞进去；会变的放「此刻」段。
- 判定类提示词的输出不是给用户看的，改它们要连带看解析方（`judge.py` 的问题解析、`affinity_judge.py` 的分级）。
