# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 扩充会话唤起语：本机有没有…能力、你会…吗、帮我找找、哪个 skill 最适配、整理已装 skills 等口语

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
