# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 扩充会话唤起语：本机有没有…能力、你会…吗、帮我找找、哪个 skill 最适配、整理已装 skills 等口语
- 长意图在打开发现页前先压缩，避免 `?q=` 塞进整段对话
- 变现与握手的已锁定设计决定（`docs/monetization-handshake.md`）
- `version.py` 作为版本号唯一真相源，并加一致性测试
- 打分引擎数值一致性测试：把 `dashboard.py` 里的 JS 打分片段切出来喂 node，
  与 `matching.py` 对排名和分数。此前只有正则查源码，证明得了「两边都写了这行」，
  证明不了「两边算出同一个数」

### Fixed
- 看板与会话内对同一查询给出不同答案：看板喂 JS 的描述截 220 字、关键词截 300，
  而 CLI 索引全文（146/280 描述超 220、210/280 关键词超 300，共丢 4136 个 token）；
  且双语拼接只在看板侧存在。21 个查询里 18 个 top-4 不同，现已 21/21 一致
- 双语索引收归 `matching.py`（`is_zh` / `load_translations` / `bilingual` 单一实现），
  CLI 与 meta-skill 因此也能用中文意图打中只写英文描述的 skill（反之亦然）
- G4 黄金用例「设计」回归：`lark-apps` 靠一段罗列了大量触发词的描述压过 figma 全家。
  三处成因各修一处——整串命中与描述分重复计分（`desc_substr` 加双计守卫）、
  FigJam 被当成设计工具的近义词（白板不是设计工具，从 `rules.json` 移除）、
  只在描述里沾到分类却拿全额置顶加分（新增 `cat_pin_desc_only`，此种情形折半）
- 并列条目的先后此前取决于文件系统扫描顺序，同一份 catalog 换台机器能给出不同 top-N；
  两套引擎均改为按 `dir_name` 兜底排序
- 交叉命中门槛（3 处）此前写死 0.08，改 `rules.json` 管不到它；收归 `field_hit_floor`
- MCP `SERVER_INFO` 谎报 `1.0.0`（CHANGELOG 与 release 都停在 0.2.1，从无此版本），
  宿主握手时拿到的版本号对不上任何一次真实发布
- README / `dashboard.py` 的「无外部 API / 无外部资源」措辞改准确：发现 tab 在本机
  未同步时会加载公开 embed，找技能 / 理技能仍全程不发网络请求

## [0.2.1] - 2026-07-27

### Fixed
- 会话唤起看板时自动填入意图：URL `?q=` + `pending_intent` 落盘 + serve 无参时 302 预填
- 用户不再需要在看板里把对话意图重新手输一遍

## [0.2.0] - 2026-07-27

### Added
- 公开 Live Demo（GitHub Pages，合成数据，可试「找技能 / 理技能」）
- `scripts/build_demo.py`：一键生成可传播的静态 demo
- MIT License、徽章、CONTRIBUTING、推广清单 `docs/LAUNCH.md`
- README 10 秒入口：一句话价值 + Demo 链接 + 一键安装

### Changed
- 仓库描述与 homepage 指向 Live Demo，方便转发
- 看板意图预填改用 `?q=`（不再依赖 `#q=`）：Cursor 打开时自动展示匹配结果
- 看板意图预填改用 `?q=`（不再依赖 `#q=`）：Cursor 打开时自动展示匹配结果，无需用户重输

## [0.1.0] - 2026-07

### Added
- 扫描本机 Claude / Cursor / Codex / OpenClaw / Gemini skill 目录
- 共享打分引擎（`matching.py` + `rules.json`）
- 本地单文件 dashboard（找技能 / 理技能）
- 四道门禁 G1–G4
- MCP server（`skill_match` / `skill_dashboard`）+ CLI 降级链
- meta-skill 安装到四宿主
- 中英双语界面与描述
