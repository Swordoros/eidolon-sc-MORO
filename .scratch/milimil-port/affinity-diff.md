# 好感度系统：Milimil fork vs Moro（本 fork）差异对比

两个 `affinity.py` 同源（都从 Sallyn0225/eidolon-sc 分叉），但已分叉很多，逐项对比如下。

## 一、完全相同（不用动）

| 项 | 两边一致 |
|---|---|
| 档位模型 | **5 档**：`cold / distant / trust / close / intimate`，阈值 `0 / 20 / 40 / 65 / 85`，值域 0–100 |
| 心形 | 冷淡 HeartOff / 疏远 HeartCrack / 信任空心 / 亲近半心 / 亲密实心 |
| 冷却 | `LOWEST=0` 进冷却、`cooldown_until`、恢复路径一致 |
| 判断模型 | `Judged(level 0–4)`、`step()`、`considerate_step()`、`feelings()` 一致 |
| 送礼 | `GIFT_STEPS`（0.3/0.5/0.7）、`COOLDOWN_GIFT_STEP=3.0`、`LIKED_GIFT_STEP` 一致 |
| 挂断/结束 | `FED_UP_TURNS=8`、`FED_UP_AT=12`、`WARNS_AT=6`、`hurt()` 一致 |
| 周期（她的日子） | `INCONSIDERATE_STEP=-0.5`、`CONSIDERATE_STEP=0.3` 一致（`HER_DAYS_*` 的**数值**不同，见下） |

**所以「改成四档」是误读——两边都还是五档。** Milimil 那句 commit 是「好感度**四档反馈**」，指反馈文案分四档，不是改档位。

## 二、Milimil 改了什么（这是我们想要的那部分，干净可搬）

| 项 | Moro（现在） | Milimil | 说明 |
|---|---|---|---|
| 每日上限 | `GAINS_PER_DAY = 0.6`、`CONSIDERATE_GAINS_PER_DAY = 0.6` | **`10.0` / `10.0`** | 0.6 ≈ 一天 +6 点；10.0 实际上等于取消每日上限 |
| 负向步进 | `LEVEL_STEPS = (-1.5, -0.5, 0, 0.1, 0.2)` | **`(-0.4, -0.2, 0, 0.1, 0.2)`** | 说错话掉分放宽（可选，属平衡调校） |
| 她日子负向 | `HER_DAYS_LEVEL_STEPS = (-3.0, -1.0, …)` | **`(-0.8, -0.4, …)`** | 同上 |
| 显示数值 | `Status` 只有 `tier + cooldown_until + opened`，`shown()` 不返回数值 | **`Status` 加 `value: float`，`shown()` 返回 `value`** | 页首能看到具体数值（0–100） |
| 反馈档位 | `Change` 无 `level`，文案只有 2 档（开心/不开心） | **`Change` 加 `level: int = 2`**，文案 4 档 | 前端按判断模型 0–4 级分成「很不开心/有点不开心/有点开心/很开心」 |

前端对应改动（Milimil 做的）：
- `lib/affinity.ts`：加 `TIER_RANGES`（每档区间）、`AffinityStatus.value?`、`AffinityChange.level?`、`cueText` 四档。
- `AffinityBadge.tsx`：显示数值 + 每档区间。
- `i18n/zh.ts`：`valueLabel`、`veryHappier`、`veryUnhappier`。

## 三、Moro 独有（必须保留，不能碰）

- **重要的一幕**（场景）：`Scene` 类、`SCENE_TIERS=("close","intimate")`、`SCENE_TURNS=6`、`scene_prompt`、`Affinities.scene / scene_done / read_scene`、`on_scene_done`。`Status.opened` 就是为它服务的。Milimil 没有这套。
- **约定**（promises）：`Affinities.promise_settled / _promise_settled`。Milimil 没有。
- `Affinities.__init__` 多一个 `features: Switches` 参数（玩法的「重要的一幕」开关）。Milimil 是 `(db, clock, is_on)` 三个参数。
- `tier_prompt(status, heart, female_producer)` 多两个可选参数（我们的签名）。Milimil 是 `tier_prompt(status)`。

## 四、Milimil 独有（18+，一律不搬）

- `NSFW_TIER = "intimate"`、`nsfw_open()`、`nsfw_line()`、`_BEYOND`、`INTIMATE_NSFW`、`NSFW_REALISM / NSFW_PACE / NSFW_STANDS / NSFW_VOICE / NSFW_OFF_LINE` 等。
- `tier_prompt(status)` 里亲密档会注入 NSFW 提示。我们没有任何这部分。

## 五、顺带的相关项（酒吧→好感度，可选）

Milimil 的三功能 commit 里 `bar.py` 也改了一处：酒吧的 `visit_done` 好感度从「每天第一次光顾才加」改成「**每次光顾都加**」（抽奖次数仍每天一次）。这算「酒吧涨幅加倍」的一部分，和好感度相关但属酒吧结算，要不要一起搬另说。

## 六、结论（如果要搬「取消上限 + 数值 + 四档反馈」）

要改的就三处后端常量/字段 + 对应前端，都不碰 NSFW、不碰场景/约定：

1. `affinity.py`：`GAINS_PER_DAY`/`CONSIDERATE_GAINS_PER_DAY` 0.6→10.0；`Status` 加 `value`（注意我们已有 `opened` 第三个字段，`value` 加成第四个）；`Change` 加 `level: int = 2`；`shown()` 带 `value`。
2. （可选）`LEVEL_STEPS`/`HER_DAYS_LEVEL_STEPS` 放宽。
3. 前端：`affinity.ts` 加 `TIER_RANGES`/`value`/`level` + 四档文案；`AffinityBadge.tsx` 显示数值和区间；`zh.ts` 加文案。
