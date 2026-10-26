# qc：逐帧质检脚本

AI 生成的视频常见的毛病，单看一遍播放很容易漏掉：片段中间突然硬切到另一个画面、画面定住不动、亮度一跳、两个画面内部叠化出重影、产品轮廓或字样在几秒里慢慢变形。前两个脚本把视频摊成图，让人和 Claude 都能逐帧看清；后两个在花钱之前检查送进视频模型的关键帧和提示词。

| 脚本 | 用途 |
|---|---|
| `strip.py` | 一条视频 → 每 0.25 秒一帧的联系表 + 运动量和亮度曲线 + 逐帧数据 JSON，并自动标出可疑的硬切、亮度跳变、定格 |
| `contact_sheet.py` | 多张图（或视频里的某几帧）→ 一张带标签的对比图：关键帧审查、几次生成结果对比、剪辑点两侧对比 |
| `normalise_keyframes.py` | 关键帧 `keyframes/raw` → `keyframes/norm`：镜头帧裁成输出画幅并缩放，母版和参考图只缩不裁，放大、硬裁切、重复文件都警告 |
| `lint_prompts.py` | 扫 `shots/prompts/` 下所有视频提示词里的禁用词族（改任务类型、造机位、造光源、让常亮的灯熄灭、空泛词、写时长）和本产品的机理禁用词 |

这些都不是 Gate K / Gate V 的判定本身：闸门按项目写在 `work/`（`references/04-keyframes.md` 第 7 节、`05-video-gen.md` 第 10 节），可以调用这里的脚本。

## 什么时候用

- 每条草稿交给用户看之前，先跑 `strip.py`，自己先把明显的废片筛掉。
- 进剪辑之前，对选定的片段再跑一遍，确定可用的入点、出点（避开定格和内部叠化）。
- 两个镜头接在一起之前，比较前一镜结尾和后一镜开头的运动量，差太多会出现"停一下再走"。
- 提交视频生成之前，把首帧、尾帧、参考图排进一张对比图，确认它们是对的那几张、方向和比例一致。

## strip.py

```bash
uv run strip.py gen/S01/take1.mov                   # 输出到 qc/take1_strip.png、_curves.png、_strip.json
uv run strip.py gen/S01 gen/S02 --every 0.5         # 文件夹里的每条视频
uv run strip.py "gen/*/*.mov" --cols 6              # 通配符（加引号）
uv run strip.py renders/edl_1920x1080.mp4 --start 40 --end 52   # 长片只看一段，时间仍按全片计
```

不在项目根目录运行时加 `--project 路径`；输出文件夹用 `--out-dir`（默认 `qc`，相对项目根目录）。

**输出**

- `<名字>_strip.png`：按 `--every` 秒（默认 0.25）取帧排成网格，每格标注时间、帧号和运动量 `m`，最后一帧总会放进去。可疑事件附近的格子加彩色边框：红 = 硬切，橙 = 亮度跳变，蓝 = 定格。
- `<名字>_curves.png`：每一帧的运动量和亮度曲线，事件用同样颜色标出，底部灰色短刻度是联系表里那些帧的位置。
- `<名字>_strip.json`：全部数字，`per_frame` 是逐帧的 `t`（秒）、`luma`（平均亮度，0–255）、`motion`（运动量）、`hist`（亮度直方图变化，0–2）；`flags` 是标出的事件及证据；`samples` 是联系表里的帧。

**运动量**是相邻两帧亮度的平均绝对差（0–255 刻度，在缩小后的画面上算，对噪点不敏感）。它衡量"画面变了多少"，不区分是镜头在动还是物体在动。不同视频之间只比相对值：同一组参数下，镜头 A 结尾和镜头 B 开头的数值接近，接起来才顺。

**自动标记（只用来提示看哪里，结论靠眼睛）**

| 标记 | 判据（默认值） | 常见原因 |
|---|---|---|
| `cut` 硬切 | 某一帧运动量 ≥ 6，且是前后各 12 帧中位数的 4 倍以上；或直方图变化 ≥ 0.3 且是邻近帧的 4 倍以上 | 生成视频中途跳到参考图的构图、跳到另一个场景 |
| `luma_jump` 亮度跳变 | 相邻两帧平均亮度差 ≥ 8，且是邻近帧的 3 倍以上 | 闪光、曝光突变、相似构图之间的切换 |
| `freeze` 定格 | 运动量 < 0.3 持续 ≥ 0.3 秒 | 画面卡住；也可能是本来就静止的固定机位镜头 |

阈值都能改：`--cut-min`、`--cut-ratio`、`--cut-hist`、`--luma-jump`、`--freeze-motion`、`--freeze-min`。加 `--fail-on-flags` 时只要有标记就以退出码 2 结束，方便批处理脚本判断。

**它看不出来的**：内部叠化（一个画面慢慢溶进另一个）、重影、部件多出一个、产品轮廓和字样的缓慢漂移，这些变化是渐进的，曲线上没有尖峰。看联系表时专门找它们：同一个部件在相邻几格里是否变了形状、数量、方向，文字是否还能读。

案例：某产品片的一条草稿，在 2.25–3.4 秒内部叠化，出现重复的部件以及袖子、头发的重影；曲线上很平，是在 0.25 秒间隔的联系表里看出来的。剪辑时把出点放在叠化之前。

## contact_sheet.py

```bash
# 关键帧审查：一个文件夹或通配符里的所有图
uv run contact_sheet.py qc/keyframes.png "keyframes/norm/*.png" --cols 4

# 剪辑点两侧：前一镜的最后一帧和后一镜的第一帧并排
uv run contact_sheet.py qc/junction_S01_S02.png gen/S01/take2.mov@last gen/S02/take1.mov@first --width 800

# 几次生成结果对比，自定标签和标题
uv run contact_sheet.py qc/S03_takes.png gen/S03/t1.mov@2.5 gen/S03/t2.mov@2.5 \
    --label "第 1 次" --label "第 2 次" --title "S03 2.5 秒处对比"
```

输入可以混用：图片文件、文件夹（取其中所有图片，按文件名排序）、通配符（加引号，Windows 的命令行不会替你展开）、视频帧 `视频@秒数`、`视频@first`、`视频@mid`、`视频@last`。直接给视频文件等于 `@first`。

参数：`--cols` 列数（默认 4）；`--width` 每格宽度（默认 480 像素）；`--label` 按输入顺序逐个指定标签（可重复）；`--no-labels`；`--title`；`--font` 字体文件；`--font-size`。输出扩展名为 `.jpg` 时按质量 92 保存，否则 PNG。

## normalise_keyframes.py

```bash
uv run normalise_keyframes.py --project <project> --size 1080x1920 --dry   # 只报告
uv run normalise_keyframes.py --project <project> --size 1080x1920         # 写进 keyframes/norm/
uv run normalise_keyframes.py --project <project> --size 1920x1080 --only S05-K00 --only S05-K05
```

- **镜头帧**（`keyframes/keyframes.json` 里 role 是 first / mid / last，或不在清单里的）：居中裁成输出比例，再缩放到 `--size`。同一镜头的首尾帧因此尺寸完全一致。
- **母版和参考图**（role 是 master / ref；没有清单时，`M1`、`R2` 这种 M 或 R 开头加数字的 id）：不裁，只按自己的方向缩小到放得进输出画幅（横图 1920×1080、竖图 1080×1920），比它小就原样保留。横版的产品参考表不会被裁成竖条。
- 读 .png .jpg .jpeg .webp，写 `<id>.png`；EXIF 旋转会被摆正，透明背景垫白。
- 警告：`UPSCALED`（源图比输出小，放大后发软，能重画大一点就重画）、`CROPPED`（比例和输出差 5% 以上，裁掉了一大块，多半是方向画错了）、`DUPLICATE`（同一个 id 有两种格式，两张都不用，删掉一张再跑）。`--fail-on-warn` 时有警告就以退出码 2 结束。

## lint_prompts.py

```bash
uv run lint_prompts.py --project <project>                                   # shots/prompts/ 下的所有 .txt
uv run lint_prompts.py --project <project> --families work/lint_words.json   # 加上本产品的词
uv run lint_prompts.py --project <project> --strict                          # 有命中就退出码 2
```

`--families` 的 JSON：`{"not this product": ["喷雾", "蒸汽"], "<别的词族>": ["词", ...]}`，值可以是词的列表或一个正则；同名词族会被扩充，`_` 开头的键是注释。每个命中打印 `文件:行: 词族: 词 | 那一行`，最后打印扫了几个文件。**一个文件都没扫到时以退出码 1 结束**（多半是 `--project` 或 `--prompts` 写错了），不会看起来像"全部干净"。命中只是提醒：边界否定句（"本身不发光"）、有意关灯（`references/05-video-gen.md` 第 3 节）确认后保留。

## 工具备注（2026-10）

- 解码用 `imageio-ffmpeg` 自带的 ffmpeg，无需另装；想用系统的 ffmpeg，设 `IMAGEIO_FFMPEG_EXE`。
- 标签字体：自动找系统里能显示中文的字体（Windows 微软雅黑 / 黑体，macOS 苹方 / 冬青黑体，Linux Noto CJK / 文泉驿），都找不到时退回 Pillow 内置字体（中文会显示成方块），这时用 `--font` 指定一个字体文件。

## 测试

```bash
uv run test_qc.py
```

用合成的 6 秒视频测试：前 2.5 秒平移一种纹理，2.5 秒硬切到另一种纹理，4.0–5.0 秒定格，之后继续平移。预期正好标出 2.5 秒的硬切和 4.0 秒起约 1 秒的定格，没有误报；另有一条平稳平移的视频，预期没有任何标记；`--start/--end` 的时间仍按全片计；对比图支持文件夹、通配符和 `视频@时间`。关键帧统一：竖版镜头帧裁切缩放、横版参考表不被裁、.jpg 照样处理、放大和重复文件报警、竖帧进横版片报 CROPPED。提示词扫描：内置词族和 `--families` 词表都能命中并给出行号，路径写错（扫到 0 个文件）时以退出码 1 结束。
