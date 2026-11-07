# 记忆三分类（私密 / 群组 / 共享）

## 目标

给记忆加一个「可见范围」维度，分三类，前端每条记忆打一个颜色标签：

- **私密（private）**：仅来源偶像可知（其他偶像、同组合伙伴都不知）。
- **群组（unit）**：来源偶像 + 她同组合的伙伴可知。
- **共享（shared）**：所有偶像可知（现状）。

分类由提炼记忆的 LLM 初判，用户在前端可以改。旧记忆默认 `shared`。

## 现状

- `memories` 表有 `source_character_id` 标记来源，但没有可见范围列。
- 记忆是所有角色共享的（GLOSSARY「记忆」）；普通回合检索不分来源（所有记忆都是候选），搭话只取来源＝自己的（`_holding(memories, character_id)`）。
- 组合归属在 `world.json` 的 `units`，`World.unit_mates(name)` 已能查同组合成员。
- 前端记忆页 `MemoriesSettings.tsx` 只能列 + 删，不能改，没有标签。

## 设计决策（已和用户确认）

- `scope` 值：`private` / `unit` / `shared`；默认 `shared`。
- 分类方式：LLM 提炼时输出 `scope`（缺省 `shared`），用户在前端可改。
- 检索过滤：某角色能想起一条记忆，当且仅当：
  - `shared`：任何人；
  - `unit`：来源角色本人，或与来源角色同组合；
  - `private`：仅来源角色本人。
- 数据包：导出/导入带上 `scope`（格式升版）。

## 后端改动

1. `memories` 表加 `scope TEXT NOT NULL DEFAULT 'shared'`（`_open` 里 `_add_missing_columns`）。
2. `Memory`、`CarriedMemory`、`PackagedMemory`（data_package.py）加 `scope`。
3. 提炼提示词 `_INSTRUCTIONS` + `_extraction_prompt`：记忆输出格式加 `"scope": "shared|unit|private"`，说明三类怎么判；解析时缺省 `shared`。
4. 检索：把 `_holding` 的「来源 or 全部」改成三类过滤（需要拿到「检索角色」和来源的组合关系；`Memories` 已持有 `characters`，可再注入 `World` 或一个 `unit_of` 回调）。
5. API（app.py 的 `/api/memories`）：列表返回 `scope`；新增改分类路由 `PUT /api/memories/{id}/scope`（只能改自己的）。
6. 数据包：导出/导入带 `scope`。

## 前端改动

1. `lib/api.ts`：`Memory` 类型加 `scope`；加 `setMemoryScope(id, scope)`。
2. `MemoriesSettings.tsx`：每条记忆加颜色标签（私密/群组/共享），点击可改分类（循环或下拉）。
3. i18n `zh.ts`：三类标签文案（+ 可能的三类颜色，走 DESIGN.md 的具名主题变量或中性 Badge variant）。

## 测试接缝

- 检索过滤：单测过滤函数——构造不同 source + 组合的记忆，断言某角色对每条可见/不可见。
- 提炼解析：LLM 输出含 / 不含 `scope`，断言默认 `shared` 且正常解析三类。
- API：`PUT /api/memories/{id}/scope` 改分类；非本人记忆 404。
- 前端：标签显示 + 改分类调用的单测（`lib` 层）。

## 票（按顺序）

1. 记忆 scope 模型：表列 + `Memory`/`CarriedMemory`/`PackagedMemory` + 数据包迁移。
2. 提炼 LLM 输出 `scope` + 解析（含默认 shared）。
3. 检索按 scope + 组合过滤（`_holding` 改三类，注入组合关系）。
4. 记忆 API 暴露 scope + `PUT .../scope`。
5. 前端标签 + 改分类 + i18n。
