# 好感度判断方案（暂留）

日期：2026-11-14

## 当前判断模型

- `JUDGE_PROVIDER=bailian`
- `BAILIAN_JUDGE_MODEL` 未填写，使用 `decision-model-preview`
- 区域：`beijing`
- 当前 family 阈值：`out_of_line=0.3`、`manipulation=0.4`
- 后台没有 `config_overrides`

## 观察到的问题

截至 2026-11-14，11 月以来的 473 次判定：

```text
level 4：407 次（86.0%）
level 1： 39 次（8.2%）
level 0： 19 次（4.0%）
level 3：  8 次（1.7%）
level 2：  0 次（0%）
```

判断集中在两端，中间档没有出现；且 `turn_state()` 原本把她的回复也放进 judge 上下文，使 judge 受她自己的语气影响。

## 已确认的修改决定

1. **retrieval 和 affinity 分开请求**
   - retrieval judge 使用完整上下文（用户发言 + 她的回复）；
   - affinity judge 使用只含用户发言的上下文；
   - 两次请求并行发出，retrieval 失败时不影响原来的检索兜底；
   - affinity 失败时，好感保持不变，retrieval 仍继续。

2. **接受普通对话以 level 2 为主**
   - level 2 明确为普通对话的默认值；
   - 普通问候、短确认、事务问答、正常请求和拒绝属于 level 2；
   - 普通而真诚的关心（“辛苦了”“早点休息”“今天怎么样”“累不累”）属于 level 3，不是 level 2，也不是 level 4。

3. **判断模型暂时不换**
   - 继续使用 `decision-model-preview`；
   - 暂不切换 Jev / Cloudflare。

4. **level 4 仍然保留**
   - 只有“记得她说过的重要事情”“让她明显感到被在意”等明确证据才判 4；
   - 不因为普通关心或普通礼貌判 4。

## 暂不执行

- 不新增置信度门槛；
- 不改 `out_of_line` 阈值和 `-10.0` 代价；
- 不加负向每日上限；
- 不记录原始概率到数据库；
- 不切换判断模型。

这些留到本次改动后的真实分布观察再决定。

## 验证重点

- retrieval 请求包含角色回复，affinity 请求不包含；
- 两次请求的内容确实分开：retrieval 问 retrieval 的题，affinity 只问好感的题；
- level 2 的默认说明和普通关心示例确实进入 prompt；
- 普通问候 / 短确认 / 事务问答不会被当作 1；
- 普通关心是 3；
- 只有明确的记得细节、情感意义才是 4；
- 检索 judge 仍能正常工作；
- judge 失败时好感不变。

## 回滚点

本次只改：

- `backend/src/eidolon/judge.py`
- `backend/src/eidolon/affinity_judge.py`
- `backend/src/eidolon/retrieval.py`

不涉及数据库 schema，不需要数据迁移。
