# 礼物插画的来源

这个目录里的 `*.webp` 是礼物目录（`characters/gifts.toml`）里每件礼物的插画，文件名就是礼物的 `id`。全部由图像生成模型生成（gpt-image-2.5，透明底），没有使用他人的作品；生成后缩到 512×512、转成 WebP（质量 82），每张控制在几十 KB，测试会检查尺寸和大小。

- 画风是「柔和平涂」（礼物规格 `.scratch/gifts/spec.md`「插画」一条）：柔和的粉彩、轻微的柔和阴影、没有硬描边、圆润的形状，像现代手游里的小图标；透明底，物件居中，不带文字。
- `pudding.webp`、`cocoa.webp`、`bouquet.webp`、`plush.webp` 四张是原型分支 `prototype/gifts-page` 上的「柔和平涂」样图（`frontend/public/gifts/proto/flat/`），原样取来。
- 其余 15 张（2026-10-13 生成）用同一个提示词骨架，只换 `<物件和细节>`：

  > A single <物件和细节>, centered, three-quarter view. Style: soft flat vector illustration, gentle pastel colours, subtle soft shading with no harsh outlines, simple rounded shapes, like a modern mobile game item icon. Transparent background, no text, object fills about 75% of the frame.

  例如蛋糕是「slice of strawberry shortcake with white cream and a red strawberry on top, on a tiny plate」。书和音乐 CD 在骨架末尾另加了「no lettering on the cover」。

- `hot-water-bag.webp`（2026-10-25，gpt-image-2）用同一个骨架，`<物件和细节>` 是「hot water bottle in a soft knitted cover (warm pastel pink with a cream cable-knit pattern), round rubber cap on top」；裁掉透明边、物件占画面约八成后缩到 512×512。
- 2026-11-21 新增 8 张（`manga-volume`、`coin-purse`、`hair-clip`、`brooch`、`scarf`、`scented-candle`、`hand-cream`、`tea-gift-box`，gpt-image-2.6）用同一骨架，只有风格句改成 `gentle colours`；模型返回透明底，裁掉透明边、按物件约 75% 缩放并居中后转成 512×512 WebP。
- 同日再新增 5 张（`perfume`、`bath-salts`、`succulent`、`flower-arrangement`、`crystal-ornament`，gpt-image-2.6）沿用同一骨架；`crystal-ornament` 画的是淡紫色晶簇，文件名仍按礼物 `id`。
- `mp3-player.webp`（2026-11-21，gpt-image-2.6）也是同一骨架，画的是奶油色机身、灰蓝按键的 MP3 播放器和有线耳机。

新加一件礼物时照这个骨架生成插画，和已有的放在一起比一比画风再提交；没有插画的礼物不上线（界面上没有图标后备）。
