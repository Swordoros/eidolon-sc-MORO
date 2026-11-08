# 从 Milimil fork 移植三个功能：兼容性评估与方案

目标仓库：https://github.com/Galaxy-CZZ/eidolon-sc-Milimil
我们想要的功能：**群聊系统、酒吧客人配音、好感度改四档 + 取消每日上限**。

## 一、先厘清两件事

### 1. 血缘关系

Milimil 是 **Sallyn0225/eidolon-sc** 的直接 fork（GitHub `parent = Sallyn0225/eidolon-sc`，默认分支 `main`）。
我们（Moro = Hibi + 本地改动）也是 **Sallyn0225/eidolon-sc** 的后代（Hibi 基于上游 `d1f6bfd4`）。
所以两边**共享同一套核心模块**：`affinity.py`、`bar.py`、`characters.py`、`memory.py`、`greetings.py`、
`readings.py`、`assembly.py`、`app.py`。

Milimil 还周期性 merge 上游（commit `4249fd115a`「上游 85 个提交」），所以它不是死 fork，而是跟着上游走的。

结论：**概念上三个功能都能搬，不需要重写；但要手工移植，不是 `git cherry-pick`**——因为我们（Hibi）已经在上游基础上
加了大量功能（行程/日记/来信/纪念品/约会/好感度五档场景/周期/酒吧），同名文件已经分叉。

### 2. 「好感度四档」是个误读

Milimil 那句 commit 是「好感度**四档反馈**」，指**反馈文案分四档**（很不开心 / 有点不开心 / 有点开心 / 很开心），
不是把好感度从五档改成四档。它的 `affinity.py` 现在仍然是五档：

```python
Tier = Literal["cold", "distant", "trust", "close", "intimate"]
TIERS = (("cold", 0), ("distant", 20), ("trust", 40), ("close", 65), ("intimate", 85))
```

它真正改的是：
- `GAINS_PER_DAY` 0.6 → **10.0**、`CONSIDERATE_GAINS_PER_DAY` 0.6 → 10.0（**实际等于取消每日上限**）；
- 负向 `LEVEL_STEPS` 从 `(-1.5, -0.5)` 放宽到 `(-0.4, -0.2)`（可选项，属数值调校）；
- 前端显示**具体数值**（0–100）和每档区间；
- 反馈文案按判断模型的 0–4 级分成**四档**（`veryUnhappier/unhappier/happier/veryHappier`）。

所以用户想要的「取消每日上限」在，但「四档」实际是「数值 + 四档反馈」，不是重做档位系统。

### 3. ⚠️ 内容红线

Milimil 是个 **18+ fork**（亲密档解锁成人向对话/照片/通话语音）。这些 NSFW 改动**和我们要的功能搅在同一个文件里**
（`affinity.py` 里有 `NSFW_TIER`、`nsfw_open`、`_BEYOND` 等）。**只搬三个干净功能，NSFW 一律不碰。**
另外它还有「三周日程」「酒吧涨幅加倍」「官方简体译名」等，本次不在范围内（要的话单列）。

---

## 二、三个功能的兼容难度

| 功能 | 难度 | 依赖是否已就绪 | 主要工作量 |
|---|---|---|---|
| 酒吧配音 | **低** | 全部就绪 | 一个后端方法 + 一个 TTS 端点 + 前端一个 hook |
| 好感度（取消上限 + 数值 + 四档反馈） | **低–中** | 常量同名、结构基本一致 | 改常量 + 给 `Status`/`Change` 加字段 + 前端展示 |
| 群聊 | **中–高** | 模块依赖基本就绪，但体量大 | 后端 1300 行 + 前端一整页 + 记忆写入适配 + 前端重样式 |

### 1. 酒吧配音（`/api/bar/tts` + `useBarVoice`）—— 低

Milimil 的做法：她的每一句日语在酒馆里也读出来。`useBarVoice.ts`（80 行）提前把队列里的 `ja` 交给
`POST /api/bar/tts` 合成 mp3，轮到时用酒馆的 AudioContext 播放，换句才停；打字速度从 30 字/秒降到 18 遮住合成延迟。

后端端点用到的所有东西**我们都有，且签名一致**：
- `eidolon.greetings.FishAudioSettings`、`_voice(client, fish, text, voice_id, dictionary)` ✓
- `eidolon.readings.load_readings / readings_path / pronunciation_dictionary / used_in` ✓
- `bar.py` 里 `self._characters` ✓（加一个 `character()` 返回 `self._characters.get(id)` 即可）

前端：`useBarSound` 加 `say`/`stopSay`，`BarSubpage` 接 `useBarVoice`，`barText.ts` 的 `CHARS_PER_SECOND` 30→18。
我们的 `useBarSound`/`BarSubpage`/`barScene` 都在，`bar_reply.Speaker = {"her","producer"}`、`ja` 字段也对得上。

**这是三个里最省事的，可以先做、独立上线。**

### 2. 好感度（取消上限 + 数值 + 四档反馈）—— 低–中

后端 `affinity.py` 我们和 Milimil 的**常量名完全相同**（`GAINS_PER_DAY`、`CONSIDERATE_GAINS_PER_DAY`、
`LEVEL_STEPS`、`HER_DAYS_LEVEL_STEPS`、`Status`、`Change`），直接照搬数值改动即可：

- `GAINS_PER_DAY = 0.6 → 10.0`，`CONSIDERATE_GAINS_PER_DAY = 0.6 → 10.0`（取消每日上限）；
- （可选）`LEVEL_STEPS`/`HER_DAYS_LEVEL_STEPS` 放宽；
- `Status` 加 `value: float`、`shown()` 加 `value`；`Change` 加 `level: int = 2`；
- 前端 `AffinityBadge` 显示数值和每档区间、`affinity.ts` 加四档反馈、`zh.ts` 加文案。

**唯一的真实差异**：我们的 `Status` 已经有第三个字段 `opened`（重要的一幕场景用），Milimil 没有。
所以 `value` 要作为**第四个字段**加，并同步改那几处 `Status(...)` 构造点——小改，不影响场景逻辑。

注意：取消每日上限是**玩法平衡决策**，不是 bug 修复。我们的 FORK.md 里「聊天每天 +6 上限」是刻意设计；
礼物本来就绕过上限。这里改成 10.0 等于让聊天也能无限涨，需要用户确认要这个效果。

### 3. 群聊（`groups.py` + 前端一整页）—— 中–高

架构（Milimil 的 `groups.py`，1316 行，设计得挺完整）：
- 一个用户可开多个群，每个群有名字、成员、话题、历史；成员是偶像 + 用户自己（用户说话等同普通成员）。
- 调度器 `keep_going()` 每分钟 tick，约每 2–6 小时给开着的群抛一个新话题（`TOPIC_GAP`），夜间 1–7 点不抛。
- 每句由后台逐句生成，句间 10–300 秒；成员**按「与话题相关度」决定说不说**（相关度由 LLM judge 打分，
  `_relevance` → `_relevance_prompt` → `_parse_scores`，不是向量检索），阈值随轮次抬升 + 说过的成员相关度递减，
  所以每段对话会自己收敛结束（`MAX_TURNS=30` 兜底）。
- 结束把「用户在群里透露的事」写进各成员记忆（`memories` 表，`conversation_id=NULL`）。

**依赖全部已就绪**：`Affinities.tier_prompt` ✓、`Character`（含 `name_zh`/`intro`/`voice_id`/`sheet`）✓、
`Memories.kept` ✓、`config.lease`（`live_config.py`）✓、`config.on_change` ✓。`assembly.py`/`app.py` 的结构也同源。

**需要适配的点（关键）**：
1. **函数名**：Milimil 用 `chinese_names()`，我们叫 `chinese_name_map()`——改个 import 就行（2 处调用）。
2. **记忆写入**：Milimil 的 `_write_memories` 直接裸 SQL `INSERT INTO memories (user_id, source_character_id,
   conversation_id, text, created_at)`，**不写 `emotion/strength/maintain_until/kind`**。我们阶段一加了这四列
   （`strength NOT NULL DEFAULT 1`、`kind NOT NULL DEFAULT 'short'`），裸插会得到「强度永远 1、无感情、维持期 NULL
   立即衰减」的记忆，和正常记忆不一致。**要改成走 `Memories._add`（或补上 emotion/strength/maintain_until）**，
   让群聊记忆和私聊记忆一样有挡位/衰减/感情。顺带：它复用了我们的 `_INSTRUCTIONS`（现在会输出 emotion/tier），
   但它的 `_parse_memories` 只读 text——需要决定群聊记忆要不要也带感情标签（建议带，保持一致）。
3. **前端重样式**：我们前端做了 shadcn redesign（base-ui + base-nova），Milimil 的 JSX 不能直接贴，
   群聊页/列表入口/i18n 都要照我们的 `DESIGN.md` 重排。这是群聊最花时间的一块。
4. **导航入口**：Milimil 把「拉群」挂在对话列表 + `play/group` 玩法页。要按我们的导航外壳决定放哪。
5. **成本**：群聊是**部署者出 LLM 额度**（走 `config.lease`），每句都是 judge + 生成多次调用，还有后台调度器。
   要做开关、保持它的「夜间安静 + 收敛」设计，避免服务器空烧钱。

---

## 三、方案（分阶段，可独立上线）

按**风险从低到高、可独立交付**排序：

### 阶段 1 —— 酒吧配音（先做，验证「移植」这条路通不通）
1. `bar.py` 加 `character(character_id)` 方法。
2. `bar_api.py` 加 `POST /api/bar/tts`（照 Milimil，import 我们已有的 `greetings`/`readings`）。
3. 前端：新增 `useBarVoice.ts`；`useBarSound` 加 `say`/`stopSay`；`BarSubpage` 接上；`barText.ts` 打字 30→18。
4. 后端测试 + 前端测试 + 部署验收（酒馆里她的台词有声音）。

### 阶段 2 —— 好感度（取消上限 + 数值 + 四档反馈）
1. `affinity.py`：`GAINS_PER_DAY`/`CONSIDERATE_GAINS_PER_DAY` → 10.0；`Status` 加 `value`、`Change` 加 `level`。
2. 前端：`AffinityBadge` 显示数值 + 每档区间；`affinity.ts` 加四档反馈 + `value`；`zh.ts` 加文案。
3. 确认「取消每日上限」是想要的平衡（先跟用户敲定）。
4. 测试 + 部署。

### 阶段 3 —— 群聊（最大，单独一轮）
1. 后端：移植 `groups.py`/`groups_api.py`；`chinese_names` → `chinese_name_map`；记忆写入改走 `_add`（带感情/挡位）；
   `assembly.py` 挂 `Groups` + `_talking` 任务，`app.py` 注册路由。
2. 前端：群聊页 + 组件 + `group.ts` + 列表「拉群」入口 + i18n，按 `DESIGN.md` 重样式。
3. 加部署开关（默认关或明确成本），调调度器参数。
4. 后端测试（含记忆写入、收敛、调度）+ 前端测试 + 部署验收。

---

## 四、风险与提醒

- **不碰 NSFW**：Milimil 是 18+ fork，只取三个干净功能，`affinity.py` 里 NSFW 部分、成人对话/照片/语音一律不带。
- **前端分叉最费工**：三处前端都因 redesign 要重排，群聊尤甚。
- **群聊烧钱**：后台调度器 + 每句多次 LLM 调用，走部署者额度；务必保留开关和夜间安静逻辑。
- **记忆一致性**：群聊记忆必须走新记忆模型（强度/感情/衰减），否则和阶段一的记忆系统对不上。
- **先 commit 基线**：按项目规矩，动手前先 commit 当前干净状态留基线。

## 五、待确认

1. 「好感度取消每日上限」确认要 10.0（聊天也能无限涨），还是只想去掉上限但留个更宽松的顶？
2. 群聊入口放哪（对话列表的「拉群」按钮？玩法页加「群聊」卡？）。
3. 群聊默认开关、以及要不要把「酒吧涨幅加倍」「三周日程」也纳入（本次没排）。
