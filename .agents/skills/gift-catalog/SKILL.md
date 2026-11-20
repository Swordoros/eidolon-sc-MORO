---
name: gift-catalog
description: "新增、修改或下架礼物，调整礼物分类、价格、喜欢角色、插画，或排查礼物目录问题时使用；覆盖 gifts.toml、GIFT_FILTERS、512×512 WebP、facts、测试和部署。"
---

# 礼物目录

礼物是配置，不是数据库数据。目录在 `characters/gifts.toml`；角色喜欢谁在 `characters/characters.toml`；插画是静态文件。

## 先看这几处

| 内容 | 位置 |
|---|---|
| 礼物条目、稀有度、下架 | `characters/gifts.toml` |
| 礼物插画 | `frontend/public/gifts/<id>.webp` |
| 角色喜欢礼物 | `characters/characters.toml` 的 `liked_gifts` |
| 喜欢关系的出处 | `characters/facts/<角色>.sources.md` 的 `## 喜欢的礼物` |
| 前端分类、筛选 | `frontend/src/lib/gifts.ts`、`components/gifts/MyGifts.tsx` |
| 分类图标、文案 | `frontend/src/theme/icons.ts`、`frontend/src/i18n/zh.ts` |

当前分类：

```text
food / drink / flower / trinket / fashion / care
```

`Category` 在后端 `gift_catalog.py` 和前端 `gifts.ts` 各有一份，新增分类时两边一起改。

## 加一件礼物

### 1. 定字段

```toml
[[gifts]]
id = "lowercase-with-hyphens"
name = "中文名"
category = "fashion"
rarity = "common"   # common / uncommon / rare
warm = false        # 可选；只给经期更受欢迎的礼物
retired = false     # true = 不入抽奖、不可购买
```

`id` 同时是插画文件名，一旦有背包或送礼记录就不要改。

当前按稀有度定价：

```text
             买入  卖出
common       10     5
uncommon     30    15
rare         50    25
```

单件自定义价格目前不支持；要 9999 这类价格，先扩展 `Gift` 和 `described()` / `gifts._buy`。

### 2. 做插画

文件必须是：

```text
frontend/public/gifts/<id>.webp
512×512
≤ 64 KB
透明背景
```

没有插画或尺寸不对，`test_gift_catalog.py` 会失败；界面没有图标后备。

现有风格骨架：

```text
A single <物件和细节>, centered, three-quarter view.
Style: soft flat vector illustration, gentle colours,
subtle soft shading with no harsh outlines, simple rounded shapes,
like a modern mobile game item icon.
Transparent background, no text, no lettering,
object fills about 75% of the frame.
```

生成后裁掉透明边、按物件约 75% 居中、缩到 512×512，再转 WebP 质量 82。新图要和已有图并排比较画风。

### 3. 加喜欢关系

`liked_gifts` 的上限是 5，且：

```text
至少 1 件
不能重复
必须都在目录里
不能全是稀有
```

每加一条喜欢关系，都要在对应的 `facts/<角色>.sources.md` 的 `## 喜欢的礼物` 里加一行，格式必须让测试能解析出 id：

```markdown
- 礼物名（gift-id）：出处和对应理由。
```

出处优先写具体卡面、剧情、台词；只能推断时明确写“对应得比较松”。没有出处就写“具体剧情出处待补”，不要伪装成已核实。

测试要求除热水袋外每件礼物至少被一个角色喜欢；新礼物如果暂时没人喜欢，需要明确决定是补喜欢关系、改测试规则，还是先不加入目录。

### 4. 新分类

新增 category 时同时改：

```text
backend/src/eidolon/gift_catalog.py 的 Category
frontend/src/lib/gifts.ts 的 Category / GIFT_FILTERS
frontend/src/components/gifts/MyGifts.tsx 的 FILTER_ICONS
frontend/src/theme/icons.ts
frontend/src/i18n/zh.ts 的 copy.gifts.filters
```

筛选是同一页的 ToggleGroup，不是独立路由页面。

## 下架与占位

```toml
retired = true
```

效果：

```text
不进入随机抽取池
不出现在商店「购买」
仍会出现在 /api/gifts/catalog
已持有的仍可在背包看到、可出售、可赠送
```

它不等于完全隐藏。要连目录接口和背包都隐藏，需要新增 `hidden` 语义并过滤读取；仅靠 `retired` 做不到。

## 测试

后端：

```powershell
cd backend
$env:PYTHONPATH="src"
.venv/Scripts/python.exe -m pytest tests/test_gift_catalog.py -q
```

前端：

```powershell
cd frontend
$env:Path = "C:\Users\17680\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin;$env:Path"
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vitest/vitest.mjs run src/lib/gifts.test.ts
```

`test_gift_catalog.py` 会检查：

```text
目录顺序和字段
每档稀有度至少一件未下架
每件礼物都有 512×512、≤64 KB 的 WebP
角色 liked_gifts 的规则
每件礼物（热水袋除外）至少有一个角色喜欢
facts 里的喜欢关系顺序与 characters.toml 一致
```

## 部署

前端有改动时必须重新构建前端；只改后端 Python 才能走 overlay。完整步骤见 `deploy-server` 技能。

礼物流程涉及：

```text
characters/gifts.toml
characters/characters.toml
characters/facts/*.sources.md
frontend/public/gifts/*.webp
frontend 源码
backend gift_catalog.py
```

所以通常需要：

```text
1. 提交并 push
2. 后端 overlay gift_catalog.py
3. 前端构建并上传 .next + public
4. 重建容器
5. 验证礼物目录接口、图片、分类筛选
```
