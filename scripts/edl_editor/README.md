# EDL 剪辑器（edl_editor）

用几份 JSON 描述一条片子：镜头顺序、入出点、转场、标题卡、字幕、引线标注、角标。脚本把它们剪成一条 H.264 成片，并附带一份时间线报告。JSON 可以 diff、可以评审、可以交给 Claude 修改，改完重新渲染就行，同一套文件还能出横屏、竖屏、方屏三个版本。

适用于任何实体产品的说明片、广告片、发布会短片。素材可以是 AI 视频模型生成的镜头、实拍、录屏或 3D 渲染，编辑器只认视频文件。

## 目录

1. [快速开始](#1-快速开始)
2. [项目目录约定](#2-项目目录约定)
3. [命令行参数](#3-命令行参数)
4. [EDL：edl.json](#4-edledljson)
5. [字幕与卡片：captions.json](#5-字幕与卡片captionsjson)
6. [引线标注：callouts.json](#6-引线标注calloutsjson)
7. [横屏、竖屏、方屏](#7-横屏竖屏方屏)
8. [字体](#8-字体)
9. [输出、检查与缓存](#9-输出检查与缓存)
10. [局限](#10-局限)
11. [工具备注（2026-10）](#11-工具备注2026-10)
12. [测试](#12-测试)

文件：

| 文件 | 作用 |
|---|---|
| `edit.py` | 剪辑器主程序：读 EDL，渲染成片和 `.timeline.json` |
| `typeset.py` | 排版模块：字幕、卡片、角标、引线标注都用 Pillow 画成 PNG；单独运行时显示本机选中的字体 |
| `make_demo.py` | 生成一个一次性的演示项目（合成测试画面，不含任何真实产品） |
| `test_edit.py` | 离线测试：画幅、竖版检查、安全框、字幕角色、卡片文案检查（只做 `--dry`，不渲染） |
| `../../templates/*.example.json` | 新项目的起步模板：edl、captions、callouts、approved_drafts、approved_bridges |

---

## 1. 快速开始

需要 [uv](https://docs.astral.sh/uv/)。脚本开头有 PEP 723 依赖声明（pillow、numpy、imageio-ffmpeg），`uv run` 会自动装好，ffmpeg 也由 imageio-ffmpeg 自带，不用另装。

```bash
# 1. 生成演示项目（目录须为空，或加 --force）
uv run scripts/edl_editor/make_demo.py demo

# 2. 只排时间线、不渲染：看每个镜头、转场、字幕落在第几秒
uv run scripts/edl_editor/edit.py --project demo --dry

# 3. 渲染 854x480 预览 -> demo/renders/edl_854x480.mp4
uv run scripts/edl_editor/edit.py --project demo

# 4. 同一套文件出竖屏 -> demo/renders/edl_1080x1920.mp4
uv run scripts/edl_editor/edit.py --project demo --size 1080x1920
```

演示项目为了简单，把几份 JSON 直接放在项目根目录（所以命令里不用写 `--edl`）；正式项目按下一节的约定放进 `edit/`。

演示片 18.9 秒，每个功能用一次：黑场淡入、标题卡、溶解、带频道字（drop cap）的中文字幕、闪白（flare）、拉丁字母字幕（preset latin）、未批准的过渡镜头自动换成甩切（whip）、通过批准台账找到的镜头、定格加三条引线标注、竖屏下改用模糊铺底（blur）、结尾卡和小字说明、淡出。

自己的项目：把 `templates/` 里五个 `*.example.json` 复制进项目的 `edit/`，去掉 `.example`（`edl.json`、`captions.json`、`callouts.json`、`approved_drafts.json`、`approved_bridges.json`），在 `edl.json` 的 `size` 里写成片画幅（竖版 `[1080, 1920]`），改镜头和文字，先 `--dry` 再渲染：

```bash
uv run scripts/edl_editor/edit.py --project <project> --edl edit/edl.json --dry        # at the EDL "size"
uv run scripts/edl_editor/edit.py --project <project> --edl edit/edl.json --res 480    # small preview, same aspect
uv run scripts/edl_editor/edit.py --project <project> --edl edit/edl.json              # delivery size
uv run scripts/edl_editor/edit.py --project <project> --edl edit/edl.json --size 1920x1080   # another aspect
```

**镜头还没生成时不用做占位视频**：EDL 里没有 `src`、账本里也还没有批准版本的镜头，渲染成同样长度、同样画幅、标着镜头 id 的黑底提示画面（slate），时间线、字幕位置和安全框照样能审。`make_demo.py` 只生成固定的演示项目，不用来给真实项目做占位。

## 2. 项目目录约定

所有相对路径（命令行参数和 JSON 里的路径）都相对于 `--project`（默认当前目录）。目录按 skill 的项目文件夹约定（`SKILL.md`），和剪辑器有关的部分：

```
<project>/
  edit/edl.json               剪辑表（必需，命令里用 --edl edit/edl.json 指定）
  edit/captions.json          字幕、卡片文字、排版样式（EDL 用 captions_file 指向）
  edit/callouts.json          引线标注（EDL 用 callouts_file 指向）
  edit/approved_drafts.json   已批准镜头台账（EDL 用 approved_drafts_file 指向）
  edit/approved_bridges.json  已批准过渡镜头台账（EDL 用 approved_bridges_file 指向）
  footage/                    实拍、录屏等不是生成的素材（可选）
  gen/<镜头id>/<任务id>.mov    视频模型生成的镜头（台账默认按这个规律找文件）
  fonts/                      项目自带字体（最先被搜索）
  renders/                    剪辑器输出的无声画面和时间线（默认位置）
  .cache/edl_editor/          分段缓存（可随时删除）
```

EDL 里的 `captions_file` 等路径也相对项目根目录，所以写成 `edit/captions.json`。建议把 `renders/` 和 `.cache/` 写进 `.gitignore`。混音后的成片放进 `deliver/`（见 `../audio/README.md`）。

## 3. 命令行参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--project DIR` | 当前目录 | 项目根目录 |
| `--edl FILE` | `edl.json` | 剪辑表；正式项目写 `edit/edl.json` |
| `--captions FILE` | EDL 的 `captions_file` | 字幕文件；两处都没有就不加字幕 |
| `--callouts FILE` | EDL 的 `callouts_file` | 引线标注文件 |
| `--drafts FILE` | EDL 的 `approved_drafts_file` | 已批准镜头台账 |
| `--bridges FILE` | EDL 的 `approved_bridges_file` | 已批准过渡镜头台账 |
| `--res 480\|720\|1080` | 见下 | 画面短边，比例取 EDL 的 `size` / `aspect`（没写时 16:9）：16:9 下 480 = 854x480，9:16 下 480 = 480x854 |
| `--size WxH` | 无 | 任意画幅，如 `1080x1920`、`1080x1080`，优先于 `--res` 和 EDL；两边须为偶数 |

画幅的取法：`--size` → `--res`（按 EDL 比例）→ EDL 的 `size` → 854x480。开头一行打印实际画幅和来源，例如 `1080x1920 (portrait, EDL size)`。大部分素材的方向和输出不同（竖版素材按横版出片）时给警告；用 `--size` 故意另出别的画幅时只打印提示。
| `--out FILE` | `renders/<edl名>_<W>x<H>.mp4` | 输出文件 |
| `--dry` | 关 | 只打印时间线和警告，不渲染 |
| `--fresh` | 关 | 忽略缓存，重新渲染所有分段 |
| `--jobs N` | 3 | 并行渲染分段数 |
| `--crf N` | 480 及以下 16，以上 18 | 成片 H.264 质量（越小越好越大） |
| `--offset-basis trimmed\|source` | 按文件 | 强制字幕 offset 的计时方式（见 5.1） |

## 4. EDL：edl.json

### 4.1 顶层字段

| 字段 | 说明 |
|---|---|
| `fps` | 帧率，默认 24 |
| `size` | 成片画幅 `[宽, 高]`，例如 `[1080, 1920]`；不带 `--size` / `--res` 时按它出片，`--res` 按它的比例取短边 |
| `aspect` | 只写比例（`"9:16"`）不写尺寸时用；有 `size` 时不用 |
| `label` | 角标文字，每帧都画在角落；空字符串 = 不画。默认留空：AI 标识放在开头的一条短字幕和片尾卡的 `note` 里，只有平台要求常驻标识时才用它（`references/06-edit-type.md` 第 9 节） |
| `captions_file` / `callouts_file` | 字幕、引线标注文件 |
| `approved_drafts_file` / `approved_bridges_file` | 两本台账（见 4.3） |
| `ledger_pattern` | 台账里只写任务 id 时怎么拼路径，默认 `gen/{id}/{task}`，没有扩展名时依次试 .mov .mp4 .m4v .mkv .webm |
| `captions` | 也可以把字幕直接写在 EDL 里（同一镜头两边都有时，captions.json 优先） |
| `offset_basis` | EDL 内字幕的计时方式 |
| `lint` | 文案检查设置（见 5.5） |
| `items` | 剪辑项列表，按顺序排 |

以 `_` 开头的键都是注释，会被忽略。任何剪辑项、字幕、标注都可以带 `"skip": true` 临时拿掉。

### 4.2 剪辑项（items）

**clip 镜头**

| 字段 | 说明 |
|---|---|
| `id` | 镜头编号，字幕和标注靠它定位 |
| `src` | 视频文件。不写时去已批准镜头台账里找；找不到或文件不存在，就渲染成同样长度的黑底提示画面（slate），方便在镜头没齐时先审节奏 |
| `in` / `out` | 入点、出点（秒，素材时间）。`out` 省略或写 `"end"` = 素材结尾 |
| `speed` | 速度，<1 为慢放 |
| `interp` | 慢放补帧：`blend`（默认，帧混合）、`mci`（运动补偿，慢但顺）、`dup`（重复帧） |
| `hold_start` / `hold_end` | 片头、片尾定格秒数。`hold_end` 定格的是 `out` 那一帧 |
| `fit` | 素材比例和画幅不同时：`cover`（默认，铺满后裁切）、`contain`（完整画面加黑边）、`blur`（完整画面叠在自身模糊放大的底上） |
| `focus` | `cover` 裁切窗口的中心，`[x, y]`，0–1，默认 `[0.5, 0.5]`。横片改竖片时用它保住主体，例如 `[0.3, 0.5]` |
| `callouts` | 引线标注组：`"组id"`，或 `{"group", "at", "end", "stagger", "fade_out", "items": [条目id]}` |
| `annotations` | 单条标注：`[{"ref": "组id:条目id", "start": 秒, ...覆盖字段}]` |

**transition 转场**：连接前后两段；两段之间什么都不写 = 硬切。

| kind | 效果 |
|---|---|
| `cut` | 硬切 |
| `dissolve` | 溶解，时长 `dur`（默认 0.6） |
| `dip_black` / `dip_white` | 经黑 / 经白，`dur` 默认 0.8 |
| `flare` | 以剪切点为中心的光晕加亮度峰，峰值下藏一个 `mix` 秒（默认 0.125）的短溶解；`gain` 调强度 |
| `whip` | 甩切：缓动横移加跟随速度的水平动态模糊；`dir` 为 `left` / `right`，`blur` 调模糊 |

放在第一段之前 = 从黑（`dip_white` 为白）淡入；放在最后一段之后 = 淡出。重叠时长超过素材允许时自动缩短并警告。

**bridge 过渡镜头**：两个镜头之间由视频模型生成的衔接镜头（例如从产品全景推到局部）。在已批准过渡镜头台账里找到就当普通镜头用，并自动检查它的首帧是否接得上前一镜头的末帧、尾帧是否落在后一镜头的首帧（给出建议入出点）；没批准就用它的 `fallback` 转场（默认溶解 0.6 秒），并忽略紧挨着它的其他转场项。这样 EDL 可以先写好还没生成的过渡镜头。

**card 卡片**：整屏文字卡。

| 字段 | 说明 |
|---|---|
| `style` | `title`（标题卡）、`chapter`（章节卡）、`end`（结尾卡，带一条强调细线） |
| `mark` | 大号拉丁字母标识（产品名或品牌字） |
| `zh` / `en` | 主行 / 小号英文行 |
| `note` | 底部小字（AI 生成说明、音乐署名等），`\n` 换行 |
| `dur` / `fade` | 时长；`fade: [淡入, 淡出]`，默认 0.6/0.6，从黑 / 到黑 |
| `preset` | 排版预设，例如 `latin` |
| `mark_px`、`zh_px`、`zh_tracking_em`、`zh_font` 等 | 单张卡的排版覆盖 |

卡片上没写的 `zh/en/mark/style/note` 会从 captions.json 的 `cards` 里同 id 的条目补上，所以文字可以集中放在 captions.json。

卡片是**纯色底**（`style.cards.bg`）加文字，不能放产品图或视频。以产品画面收尾时，用最后一个产品镜头加字幕，AI 说明小字用一条 `"preset": "note"` 的字幕（见 5.4）。

### 4.3 台账（approved drafts / bridges）

视频模型一个镜头往往生成好几版，台账记录“哪一版被批准了”，EDL 就不用改路径：

```json
{"_doc": "镜头id -> 批准的版本",
 "S02": "task_0412",
 "S05": "footage/S05_retake.mp4",
 "S07": {"task": "task_0419", "src": "gen/S07/task_0419.mov"}}
```

值可以是任务 id（按 `ledger_pattern` 拼路径）、媒体路径，或 `{"task", "src"}`。以 `_` 开头的键是注释，剪辑器会忽略。

和视频客户端合用（`references/05-video-gen.md` 第 13 节）：

- `../video_api/seedance.py upgrade --from-json edit/approved_drafts.json` 读同一份台账，但它只认"镜头 id → 草稿任务号"的纯字符串写法，不认 `_` 注释键、媒体路径和 `{"task", "src"}`。要交给它升级的台账就只写这一种（模板 `templates/approved_drafts.example.json` 就是这样）。
- 升级到 1080p 后，把镜头的值改成 `{"task": "<草稿任务号>", "src": "gen/<镜头id>/<升级任务号>.mov"}`：剪辑器优先用 `src`。改完以后这份台账不再交给 `upgrade --from-json`。
- 过渡镜头提交时用它的 id 当镜头号（`--shot B_S02_S03`），文件就落在 `gen/B_S02_S03/`，`approved_bridges.json` 里只写任务号即可。

## 5. 字幕与卡片：captions.json

```json
{"offset_basis": "trimmed",
 "style": { ...排版样式，全部可选... },
 "cards": [ {"id": "title", "style": "title", "mark": "...", "zh": "...", "en": "..."} ],
 "captions": [ {...}, {...} ]}
```

也可以直接是一个字幕列表。

### 5.1 字幕字段

| 字段 | 说明 |
|---|---|
| `id` | 字幕编号（报告里用） |
| `anchor` | 挂在哪个镜头（clip / bridge / card 的 id）上 |
| `offset` | 相对该镜头第一帧的秒数。默认按“剪后时间”（trimmed）计：0 = 镜头入点那一帧；`offset_basis: "source"` 时按素材时间计，自动减去入点。变速镜头会按速度换算 |
| `occurrence` | 同一镜头用了多次时，挂第几次（默认 1） |
| `at` | 直接写成片绝对时间，代替 anchor + offset |
| `dur` | 显示时长，含淡入淡出 |
| `zh` | 主行（任何语言都放这里）。`静｜一句话`：竖线前的字是频道字（drop cap），竖线画成细线 |
| `en` | 副行：小号、全大写、宽字距 |
| `emph` | 主行里要放大加粗的词，列表；也可写 `{"text": "轻、静、快", "level": "glyph"}` 指定层级 |
| `en_emph` | 副行里要加粗的词（产品名） |
| `zh_break` / `en_break` | 在这个词之后换行（字符串或列表），用于构图紧时把字幕块变窄 |
| `layout` | `drop`（默认，频道字跨两行）或 `inline`（频道字和主行同一基线） |
| `pos` | 位置（见 5.3） |
| `ink` | `light`（默认，浅色字加柔和投影）或 `dark`（深色字，用于亮底） |
| `style` | `chapter` = 章节大字 |
| `preset` | 排版预设，`latin` 用于拉丁字母主行 |
| `fade` | 淡入淡出秒数，数字或 `[入, 出]` |
| `role` | `label`（开头的 AI 标识）或 `note`（说明小字）：不是阅读字幕，不参与最小间隔、行长和"字幕与标注重叠"的检查，风险词照查。也可以直接写 `"reading": false` |
| `portrait` / `landscape` / `square` | 某种画幅下覆盖的字段，如 `"portrait": {"pos": "upper_center"}` |

自动规则：

- 浅色字下的画面太亮（平均亮度超过 `scrim_params.threshold`，默认 0.24）时，加一层羽化的暗底（scrim），看不出边框。
- 字幕与转场最多重叠 0.2 秒，否则自动提前结束（至少保留 2 秒）；章节字幕除外。
- 字幕块宽于画面减安全边距时整体缩小并提示；任何字幕块都被限制在安全边距内。
- `emph`、换行词在原文里找不到时警告。
- 两条阅读字幕之间的最小间隔：`style.min_gap_s`（模板 0.6 秒）。起点和时长各自取整到帧，所以比较时容许 1 帧误差：按 0.6 秒排的两条不会因为显示成 0.58 秒而报警。`role` 为 `label` / `note` 的字幕不参与。
- 写了 `safe_box`（或用了预设 `feed_safe`）时，每个字幕块、标注的文字和圆点、卡片小字都检查是否在框内，`--dry` 也查。

### 5.2 样式（style）

所有像素值以 `ref_resolution`（默认 1920x1080）为基准，按输出画面**短边**等比缩放，所以 1080x1920 竖屏和 1920x1080 横屏字号一样大。常用键：

| 键 | 内容 |
|---|---|
| `caption.levels` | 五个层级：`glyph` 频道字 104、`emph` 重点词 64、`rest` 其余 48、`en` 副行 20、`en_emph` 副行加粗 20（px、字距 em、字体、颜色键） |
| `caption.layout`、`hairline`、`line_gap_px`、`en_fit` | 频道字布局、细线、行距、副行字距自动收紧 |
| `caption.punct_trail` | 中文标点挤压（，、。占半宽） |
| `chapter` | 章节字号、字距、淡入淡出 |
| `positions` | 位置表（见 5.3） |
| `ink.light` / `ink.dark` | 两套字色和投影 |
| `scrim_params` | 暗底阈值、范围、不透明度、羽化 |
| `fade_in` / `fade_out` / `rise_px` | 字幕淡入淡出和上浮距离 |
| `min_px` | 小尺寸预览的最小字号（输出像素） |
| `safe_px` | 安全边距 |
| `safe_box` | `[x0, y0, x1, y1]`，短边 1080 的画面上的像素（随画面缩放），通常写在 `portrait` 里；出框的字幕、标注、卡片小字报警告。预设 `feed_safe` 已经带上竖版保守框 `[65, 288, 888, 1248]` |
| `label` | 角标：字体、字号、颜色、`right` + `bottom`（或 `top`） |
| `cards` | 卡片：底色、`mark`/`zh`/`en`/`note` 字体字号、`channel_chars`（主行里用强调色的字）、`rule` 细线、`side_px` |
| `callout` | 引线标注（见第 6 节） |
| `font_zh` / `font_glyph` / `font_en` | 默认字体（见第 8 节） |
| `preset` / `presets` | 整片预设（一个名字，或列表如 `["feed_safe", "latin"]`）；自定义预设 |
| `portrait` / `landscape` / `square` | 某种画幅下覆盖的样式 |
| `transition_trim` | `{"max_s": 0.2, "min_on_s": 2.0}` |

完整默认值见 `typeset.py` 里的 `DEFAULT_STYLE`。其中的颜色（深底、暖米色字、一个强调色）和字体只是**占位**，来自案例项目的样式；新产品在 `style` 里换成自己的品牌色和字体。能沿用的是层级和字号比例：频道字 104 ≈ 正文 48 的 2.2 倍，重点词 64 ≈ 1.33 倍，副行 20 ≈ 0.4 倍。

### 5.3 位置（pos）

内置：`lower_left`、`lower_center`、`lower_right`（让开角标）、`upper_left`、`upper_center`、`upper_right`、`mid_left`、`mid_right`、`center`。

也可以写字典（基准像素）：横向 `x`（左边距）/ `cx`（中心）/ `right`（右边距）三选一，纵向 `top` / `bottom` / `cy` 三选一，`align` 可强制对齐方式。`x`、`right`、`top`、`bottom` 是边距，按短边缩放；`cx`、`cy` 是基准画面上的坐标，按比例映射到输出画面。

选位置的原则：放在画面的安静区域，不压产品、不压人物、不压正在演示的效果，通常在主体的对侧。

### 5.4 预设（preset）

- `latin`：主行用拉丁字体（Segoe UI / Helvetica Neue / Noto Sans 系），44 px，不做中文标点处理，频道字改为小号字距大写。可整片用（`style.preset`），也可单条字幕或单张卡用（`"preset": "latin"`），中英双版可以共用一套样式。
- `feed_safe`：整片用，只改竖屏：所有位置、角标、片尾卡小字都放进竖版保守安全框（左 72、右 200、上 340、下 700 px，角标在框内右上角），并设好 `safe_box` 让 `--dry` 检查。竖版信息流投放时用它（第 7 节）。
- `note`：单条字幕用，小号 Light 字（26 px 主行、15 px 副行，副行不转大写），用于压在产品镜头上的 AI 说明小字；配 `"role": "note"`。模板 `templates/captions.example.json` 里的 `end_note` 就是这样写的。

自定义预设写在 `style.presets` 里。

### 5.5 文案检查（lint）

每条字幕、标注、卡片（标记、主行、副行、小字）和角标都会检查，只给警告，不阻止渲染：

- 风险词：医疗功效、绝对化、保证类说法（治疗、治愈、最佳、顶级、100%、guarantee、best 等）。`lint.banned` 替换整份词表，`lint.banned_extra` 追加。
- 带 % 或温度单位的数字：这类说法需要出处。
- 长度：阅读字幕的主行默认不超过 12 个中文字或 42 个拉丁字符，标注名称 14 / 28；卡片和 `role` 为 `label` / `note` 的字幕不查长度。

它只是提醒，不是法务审核，宣传用语是否合规以所在地法规和平台规则为准。

## 6. 引线标注：callouts.json

用于爆炸图、剖视、部件名称：一个圆点落在部件上，细线引到旁边，旁边是名称 / 功能 / 英文三行。

```json
{"_style": { ...覆盖 style.callout... },
 "S03": {"at": 4.1, "stagger": 0.35, "fade_out": 0.5,
         "items": [{"id": "lid", "order": 1, "anchor_xy": [0.56, 0.29], "label_xy": [0.68, 0.22],
                    "zh": "顶盖", "fn": "一体成型", "en": "Top cover"}]}}
```

| 字段 | 说明 |
|---|---|
| 组 id（如 `S03`） | 一组标注；镜头用 `"callouts": "S03"` 引用 |
| `at` | 第一条出现的时间，**镜头时钟**：素材秒数（和 in/out 同一时钟），过了 `out` 继续数进定格，`out + 1` = 定格第 1 秒；省略 = 定格开始时 |
| `stagger` | 每条之间的间隔（按 `order` 排序） |
| `end` / `fade_out` | 结束时间（镜头时钟）/ 淡出秒数。默认在下一个转场开始前消失，因为标注是叠加层，画面换了它也得走 |
| `anchor_xy` | 部件上的点，0–1，相对**素材画面**（自动跟随 fit 和裁切） |
| `label_xy` | 引线终点；文字在它外侧，在锚点右边就左对齐，在左边就右对齐 |
| `zh` / `fn` / `en` | 名称 / 功能一句 / 英文 |
| `ink` | `auto`（默认，按背景亮度整组统一选浅色或深色）、`light`、`dark` |
| `route` / `waypoints` | `auto`（水平时直线，否则斜线加一小段水平）、`straight`、`elbow`；或手动折点 |
| `todo` | 位置是估计值（镜头还没生成）：照画，但每次渲染都警告，直到去掉这个键 |

动画：圆点弹出并扩散一圈，细线约 0.3 秒画到文字，文字边淡入边向外滑出；整组一起淡出。版面自动检查：引线互不交叉、文字块互不重叠、引线不穿过别人的文字、不出安全边距、不压角标，问题以警告列出。完整状态停留少于 1.5 秒也会警告（看不完）。

坐标量法：在批准版本的定格帧上量（截图后按像素除以宽高）。横屏和竖屏共用一套坐标时，竖屏的镜头最好用 `fit: "blur"` 或 `contain`，否则裁切会把标签切出画面；也可以在组或条目里写 `"portrait": {...}` 给竖屏单独的坐标。

## 7. 横屏、竖屏、方屏

- `--size 1080x1920` 出竖屏，`--size 1080x1080` 出方屏，所有字号按短边缩放，不用另调。
- **竖版项目先在 EDL 写 `"size": [1080, 1920]`**：不带参数的 `--dry` 和渲染就按竖版排；不写时按 854x480 横版排，素材大多是竖的会警告。
- 默认样式自带 `portrait` 段：竖屏时字幕离底边 420 px、离右边 168 px、上方位置离顶边 260 px，角标移到右上，卡片底部小字上移，让开短视频平台叠加的按钮和文案区。这组默认值比 `references/02-formats.md` 第 5 节的保守安全框松（框是上 288、下 672、左 65、右 192 px，片尾小字会落在 y≈1520）。**投信息流广告时在 captions.json 的 `style` 里写 `"preset": "feed_safe"`**：位置和小字都进框，`--dry` 打印 `SAFE BOX ... N outside` 并逐条警告出框的字幕块、标注和小字；投放前再用平台模板核对。
- 任何 JSON 里的 `portrait` / `landscape` / `square` 子字典只在对应画幅生效，例如：

```json
{"type": "clip", "id": "S05", "src": "footage/S05.mp4", "portrait": {"fit": "blur"}}
{"anchor": "S05", "offset": 1.0, "zh": "...", "pos": "lower_left", "portrait": {"pos": "upper_center"}}
```

- 横片改竖片三种方式：`cover` + `focus`（裁切，保住主体）、`blur`（完整画面 + 模糊铺底，适合产品全景和标注镜头）、`contain`（加黑边）。英雄镜头（钩子、产品亮相、片尾产品镜头）默认直接按 9:16 生成或拍摄；`cover` 裁切只用于次要镜头，而且生成横版时主体已在中间竖条里，或源素材是 4K（`references/02-formats.md` 第 8.3 节）。

## 8. 字体

字体规格可以写：`@角色名`、文件名（在字体目录里找，不分大小写）、相对项目的路径、绝对路径。集合字体（.ttc）用 `文件#序号` 或 `文件#字体名` 选其中一款，例如 `NotoSansCJK-Regular.ttc#Noto Sans CJK SC`（Noto CJK 集合的第一款是日文字形，必须指定 SC）。

搜索顺序：环境变量 `EDL_EDITOR_FONT_DIRS`（多个用系统路径分隔符）→ `<项目>/fonts` → 脚本旁的 `fonts/` → 系统字体目录（Windows、macOS 含 /System/Library/AssetsV2 里的苹方、Linux 的 XDG 目录）。

| 角色 | 用途 | Windows | macOS | Linux |
|---|---|---|---|---|
| `zh_light` | 字幕正文、卡片、角标 | 微软雅黑 Light、等线 Light | 苹方 Light、冬青黑体 W3、华文黑体 Light | Noto Sans CJK SC Light / Regular、思源黑体、文泉驿 |
| `zh_regular` | 频道字、重点词、标注名称 | 微软雅黑、等线、黑体 | 苹方 Regular、冬青黑体 W6、华文黑体 Medium | Noto Sans CJK SC Regular、思源黑体、文泉驿 |
| `en` | 大写宽字距英文、卡片标识 | Century Gothic、Segoe UI Light | Avenir Next、Avenir、Helvetica Neue Light | Noto Sans Light、DejaVu Sans |
| `en_bold` | 副行加粗 | Century Gothic Bold、Segoe UI Bold | Avenir Next Demi Bold、Helvetica Neue Bold | Noto Sans Bold、DejaVu Sans Bold |
| `latin_regular` / `latin_light` | latin 预设主行 | Segoe UI / Light | Helvetica Neue / Light | Noto Sans / Light、DejaVu Sans |

每个角色按列表顺序取第一个找得到的，渲染开始时打印 `FONT @zh_light -> 路径 (字体名)`。样式里指定的字体找不到时，回落到该层级的默认角色并打印 `[not found here: ...]`。

检查本机会选哪些字体，并出一张样张：

```bash
uv run scripts/edl_editor/typeset.py --project my-video --size 1080x1920 --specimen check.png
```

品牌字体：放进 `<项目>/fonts/`，在样式里按文件名引用，例如 `"caption": {"levels": {"rest": {"font": "MyBrand-Light.otf"}}}`。注意字体授权是否允许用于视频。

## 9. 输出、检查与缓存

- 成片：H.264 High，yuv420p，tv range，BT.709，`+faststart`，**无声**，默认写到 `renders/<EDL 名>_<W>x<H>.mp4`。音乐、音效、旁白在单独的混音步骤加，混好的成片放进 `deliver/`。
- `<成片>.timeline.json`：每个镜头、转场、字幕、标注在成片上的起止时间，字幕位置和是否加了暗底，过渡镜头回落情况和衔接检查，安全框检查（`safe_box`：框、查了几个、哪些出框），全部警告，以及实测帧数（`verified_frames`）。渲染结束时打印 `frames N OK`，帧数对不上会显示 `MISMATCH`。
- 警告（`WARN`）不会中断渲染；提示（`NOTE`）是自动做了的调整。交付前把警告清零或逐条确认。
- 缓存：每个镜头 / 卡片先单独规整成固定帧数、固定画幅的分段，存到 `.cache/edl_editor/<W>x<H>/`，键包含素材大小和修改时间，改了素材或参数会自动重做；`--fresh` 强制全部重做。改字幕只重做最后一遍合成，很快。
- 改 EDL 后先 `--dry` 看时间线，再出 480 预览，最后出交付尺寸。

## 10. 局限

- 只处理画面，不处理声音。
- 文字逐字绘制以实现字距，因此没有字偶距调整和连字；中文、全大写英文效果好，阿拉伯文、天城文等需要字形整形的文字不支持。
- 暗底很克制，在纯色高亮背景（例如测试图的黄色条）上小号英文仍然吃力，这时改用 `ink: "dark"` 或换位置。
- 引线标注坐标基于素材画面；裁切幅度大的竖屏需要 `blur` / `contain` 或单独坐标。
- 过渡镜头衔接检查假定过渡镜头和前后镜头的素材比例相同。
- 转场只有表中六种；没有调色、稳定、关键帧动画、画中画。
- 只接视频文件：静帧、真实界面截图要先在别的工具里做成视频（循环成 N 秒；轻推、界面动画也在那一步做），再作为镜头的 `src`。
- macOS 和 Linux 的字体回落列表按常见安装位置编写，本次只在 Windows 上实测；如果打印的字体不对，用 `EDL_EDITOR_FONT_DIRS` 或项目 `fonts/` 指定。
- 文案检查只做关键词匹配，不能代替法务审核。

## 11. 工具备注（2026-10）

使用前核对最新文档。

- 本版在 Windows 11、uv 0.11、Python 3.12、Pillow 13、numpy 2.5、imageio-ffmpeg 0.6.0（自带 ffmpeg 7.1）上测试，PEP 723 声明最低 Python 3.10、Pillow 10.1。
- 用到的 ffmpeg 滤镜：xfade（含 custom 表达式）、gblur、blend、sendcmd、colorchannelmixer、tpad、minterpolate、framerate、overlay、pad、eq。ffmpeg 7 及以上用 `-/filter_complex` 从文件读滤镜图，更早的版本自动改用 `-filter_complex_script`。
- 演示素材来自 ffmpeg 内置测试源 testsrc2、smptebars、gradients。

## 12. 测试

```bash
uv run scripts/edl_editor/test_edit.py
```

离线、只做 `--dry`：用 ffmpeg 测试图生成几段竖版小片段，检查画幅取法（EDL `size` / `aspect`、`--res`、`--size`）、竖版素材按横版出片时的警告、默认竖屏位置出框而 `feed_safe` 不出框、`role` 为 `label` / `note` 的字幕不参与间隔检查、按 0.6 秒排的字幕不因取整报警而真正的 0.5 秒间隔照报、卡片文字的风险词检查。完整渲染用演示项目（第 1 节）。
