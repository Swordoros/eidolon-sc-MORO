# 记忆系统改造：强度 + 衰减 + 感情标签（全流程方案）

## 目标

把记忆从「纯相关性检索」改成「带强度与衰减的记忆模型」：

- 记忆生成时带**感情标签**和**强/中/弱强度挡位**；
- 强度有**维持期**（12–48 小时）和之后的**每小时衰减**；
- 召回时**按强度排序**给 3–5 条；
- 每天 3 点把相关短期记忆**整合成中/长期**（维持 4–7 天）。

分两个阶段落地，先做阶段一（可独立运行），再做阶段二（整合）。

---

## 数据模型（`memories` 表加列）

| 列 | 类型 | 含义 |
|---|---|---|
| `emotion` | TEXT | 感情标签（开心/难过/生气/怀念/平静…），阶段一只存不用 |
| `strength` | REAL NOT NULL DEFAULT 1 | 当前强度，0–1 |
| `maintain_until` | TEXT | 维持期截止时间（ISO）；早于它不衰减，过了才开始衰减 |
| `kind` | TEXT NOT NULL DEFAULT 'short' | short / mid / long，阶段一都是 short，阶段二整合后写 mid/long |

现有行迁移：`strength=1`、`kind='short'`、`emotion`/`maintain_until` 为 NULL（NULL 视为「维持期已过，可衰减」）。

---

## 强度挡位（写记忆时由后台模型给）

| 挡位 | 初始强度 | 维持期 |
|---|---|---|
| 强 strong | 1.0 | 48 小时 |
| 中 medium | 0.7 | 24 小时 |
| 弱 weak | 0.5 | 12 小时 |

（数值可调；先取这套。）

## 衰减

- 每 **1 小时**跑一次后台任务，对 `maintain_until IS NULL OR maintain_until < now` 的记忆执行 `strength = strength × 0.9`。
- `strength < 0.1` 视为「遗忘」：不再召回（数据仍留着，可列表/删除）。

## 召回（见正文「召回系统设计」）

1. 向量相似度粗筛（候选 12 条，现状保留）。
2. reranker 阈值精筛（只过滤、不排序，现状保留）。
3. 按 `strength` 降序，跳过 `strength < 0.1` 的，取前 `MAX_FOUND=5`。

---

## 阶段一（本次实现 ✅）

- [x] `memories` 表加 4 列 + 迁移。
- [x] `Memory`/`NewMemory` 加 `emotion`、`strength`、`maintain_until`、`kind`（NewMemory 里挡位叫 `tier`）。
- [x] 后台记忆模型 prompt（`_INSTRUCTIONS`）让每条多输出 `emotion` 和 `tier`（强/中/弱）。
- [x] `parse_memories` 解析这两个新字段。
- [x] `_add` 写入时按挡位算初始 `strength` 和 `maintain_until`，存 `emotion`。
- [x] `_relevant` 改成「相关性过滤 + 强度排序」，`MAX_FOUND` 3→5。
- [x] `Memories.decay()` + `keep_decaying()`（每小时），`assembly.start()` 里挂后台任务。
- [x] 测试：`test_memory_strength.py`（解析、挡位、衰减）+ `test_memories.py` 加写入门流测试；90 个相关测试通过。

## 阶段二（暂不实现）

- 每天 3 点后台任务：把「相关」的短期记忆（过了维持期、还没忘）按向量相似度聚类，用后台 LLM 合并成一条 `kind=mid/long` 的新记忆，维持 4–7 天，原记忆标记已整合（复用 `superseded_by` 或新标记）。
- 待定：相关性阈值、一次合几条、LLM 合并失败怎么降级、中/长期与短期衰减的关系。
