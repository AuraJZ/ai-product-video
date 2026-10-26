# ai-product-video：用 AI 给实体产品做视频

一个 [Claude Code](https://docs.claude.com/en/docs/claude-code) skill。它把"3D 预演定位 → 图像模型画关键帧 → 视频模型生成镜头 → 代码剪辑、字幕、配乐、混音 → 多版本交付"这套流程，整理成 Claude 可以照着执行的方法、模板和脚本。

适用于**实体产品**的视频：产品功能演示片、品牌广告（15/30/60 秒）、抖音、小红书、视频号等竖版短视频、电商主图视频、发布会和路演短片。纯剧情短剧、MV、动画等没有实体产品的视频不适用。

> English summary: a Claude Code skill for making AI-generated videos of physical products (explainers, ads, vertical social videos, e-commerce clips, launch films). It encodes a hybrid pipeline: 3D previs (optional) locks layout and scale; an image model paints photoreal keyframes on top; a video model animates first/last frames from per-second scripts; a code-driven EDL editor adds transitions, captions, leader-line callouts and cards; music and SFX are mixed by measurement. Documentation is in Simplified Chinese; scripts are cross-platform Python run with `uv`.

## 它解决什么问题

AI 视频做产品片，常见三种失败：

- 产品被画走样：接口方向、字样、比例都不对；
- 画面一股 CG 味或塑料感；
- 剪出来很生硬，字幕和声音都要手工反复改。

这个 skill 用三条原则来解决：

1. **产品事实和 3D（或产品图）是标准答案。** AI 只负责"画得真"，不负责"摆在哪"。代码对照标准答案检查每张图和每段视频，检查不过就不花钱。
2. **写实关键帧直接驱动视频。** 先由图像模型在定位底图上画出照片质感的首帧和尾帧，再交给视频模型，按逐秒脚本生成。
3. **剪辑、字幕和声音都是数据。** EDL、字幕和标注都是 JSON，声音按时间线生成；改一个字或调一次顺序，几分钟就能重出一版。

## 目录结构

```
ai-product-video/
├── SKILL.md                 入口：先问什么、项目文件夹约定、流水线、最重要的 12 条、协作与安全
├── references/              方法库：按阶段分 10 篇，另有规则手册 139 条和常见坑速查表
├── templates/               立项单、产品事实确认单、质量标杆、预算、宣称清单、授权记录、镜头表、关键帧清单、反馈 brief、决策清单、音效表、出图包与手动视频包、提示词模板、EDL/字幕/标注/批准账本示例
├── scripts/
│   ├── edl_editor/          EDL 剪辑器和排版引擎（16:9、9:16、1:1），make_demo.py 一键生成演示项目
│   ├── video_api/           Seedance（火山方舟）客户端：草稿、高清升级、可续跑查询、费用账本，可照着它写别的模型的适配
│   ├── audio/               按时间窗平衡音乐和音效、实测、响度标准化
│   └── qc/                  逐帧拼图、运动和亮度曲线、硬切与定格检测、联系表、关键帧统一尺寸、提示词禁用词扫描
└── examples/case-study.md   一个真实项目的匿名复盘
```

## 安装

1. 安装 [Claude Code](https://docs.claude.com/en/docs/claude-code) 和 [uv](https://docs.astral.sh/uv/)。
2. 把整个 `ai-product-video` 文件夹放进 Claude Code 的 skills 目录，用户级就是 `~/.claude/skills/ai-product-video/`。
   - macOS / Linux：`git clone https://github.com/AuraJZ/ai-product-video.git ~/.claude/skills/ai-product-video`
   - Windows：`git clone https://github.com/AuraJZ/ai-product-video.git %USERPROFILE%\.claude\skills\ai-product-video`
3. 重启 Claude Code。之后说「帮我给这款产品做一支 15 秒的竖版广告」「用 AI 做一支产品演示片」这类话，skill 会被自动调用。

可选准备：

- **3D 软件**：比如 Blender，用来做预演；没有也能走"无 3D 路线"。
- **图像模型**：订阅（用户手动画）或 API 都可以。
- **视频模型账号**：脚本内置火山方舟 Seedance 的适配，其他模型可以照着写。

## 先跑一个演示

不需要任何账号，也不花钱：

```bash
cd ai-product-video/scripts/edl_editor
uv run make_demo.py demo
uv run edit.py --project demo --res 480
uv run edit.py --project demo --size 1080x1920
```

演示项目建在当前目录下的 `demo/`（Windows、macOS、Linux 都一样），用完直接删掉。

演示会用测试图案生成几个片段，剪成一支带片头卡、叠化、光晕、甩镜、中英文字幕、引线标注和片尾卡的短片，横版、竖版各出一个。演示项目为了简单，把 JSON 直接放在根目录；正式项目按 `SKILL.md` 的文件夹约定放进 `edit/`，成片画幅写在 `edit/edl.json` 的 `size` 里（竖版 `[1080, 1920]`），命令是 `uv run scripts/edl_editor/edit.py --project <project> --edl edit/edl.json`（预览加 `--res 480`，另出别的画幅加 `--size WxH`）。

## 测试

```bash
uv run scripts/video_api/test_seedance.py   # 离线，用假密钥和假服务器
uv run scripts/edl_editor/test_edit.py      # 只做 --dry：画幅、竖版安全框、字幕角色、卡片文案检查
uv run scripts/audio/test_mix.py            # 合成音频和视频
uv run scripts/qc/test_qc.py                # 带硬切和定格的合成片段、关键帧统一、提示词扫描
```

## 重要提醒

- **模型变化很快。** 文档里点名的模型（Seedance、GPT Image、Kling 等），其能力、价格和限制都写在带日期的"模型备注"里，使用前请核对官方文档。
- **合规不是法律意见。** `references/09-compliance.md` 整理了《广告法》、AI 生成内容标识和素材授权的要点，正式投放前请按目标市场再核对一次。
- **密钥只放在项目的 `.env` 里**，不要提交到版本库。
- **字体**：脚本只引用系统里已经安装的字体，不附带任何字体文件。商用前请确认字体授权。

## 作者与许可

- 作者：Jingyi Zhang
- 单位：寰感科技（Shanghai Huangan Technology Co., Ltd.）
- 许可：MIT，见 `LICENSE`。使用、修改和再发布时请保留版权声明。
