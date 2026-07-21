# skill-picker

本机 agent skills 的扫描、查重、场景聚类与路由工具。

装的 skills 越来越多之后，两个痛点会越来越明显：想不起 skill 的名字、不知道怎么唤醒；
功能重叠的 skills 不知道该用哪个。skill-picker 解决这两个问题——
自动扫描本机所有 skill 目录，按场景聚类生成一份 catalog，标出重复和漂移，
并以 meta-skill 的形式装进各个 agent 宿主：当你说"用哪个 skill 做 X"这类模糊意图时，
agent 会读 catalog、给出 2-4 个候选、**弹出选项让你自己选**。

设计哲学来自 [tab-out](https://github.com/zarazhangrui/tab-out)：
不管理数据，只读取已存在的事实；自动聚类；暴露冗余但让用户决策；
零服务器、零账号、零外部 API、零第三方依赖（仅 Python 标准库）。

## 快速开始

```bash
python skillpick.py install
```

一条命令完成：扫描 → 生成 catalog → 把 skill-picker meta-skill 装进 Cursor 和 Claude Code。

之后在任意会话里说「帮我选个 skill 做周报」「有没有 skill 能画图表」「skill 太多了不知道用哪个」，
skill-picker 就会被唤醒并弹出候选让你选择。

## 命令

| 命令 | 作用 |
|---|---|
| `python skillpick.py scan` | 扫描所有 skill 目录，生成/刷新 catalog |
| `python skillpick.py install` | scan + 安装 meta-skill 到各宿主 |
| `python skillpick.py report` | 打印当前 catalog |

## 工作原理

1. **扫描**：遍历以下目录中的所有 `SKILL.md`（存在才扫）：
   - `~/.claude/skills`（Claude Code）
   - `~/.cursor/skills` 与 `~/.cursor/skills-cursor`（Cursor）
   - `~/.agents/skills`（Codex）
   - `~/.openclaw/skills`（OpenClaw）

   解析 frontmatter 中的 `name` / `description`（无 frontmatter 时回退到首个标题+首段）。

2. **场景聚类**：按 `CATEGORY_RULES` 中的关键词规则把 skills 归入场景
   （周报/复盘、PPT/演示、图表/可视化、飞书办公……），规则可自行增改。

3. **查重**：
   - **同名多份**：同一 skill 目录名出现在多个宿主时，比对内容 hash，
     区分「内容一致」和「内容已漂移」；
   - **功能重叠**：description 做字符 bigram Jaccard 相似度，≥ 0.5 的组合标记为可能重叠。

4. **产出**：写入 `~/.skill-picker/catalog.md`（给 agent 读）和 `catalog.json`（完整字段）。

5. **路由**：meta-skill（`SKILL.md`）的 description 覆盖"不知道用哪个 skill"类模糊意图。
   被唤醒后 agent 读 catalog，找出 2-4 个候选，用宿主的提问工具
   （Cursor: AskQuestion / Claude Code: AskUserQuestion）让用户点选，
   选定后再读取并执行该 skill 的 SKILL.md。候选间若有漂移或重叠会明确提示。

## 说明

- 装了新 skill 后重新跑一次 `scan` 即可刷新 catalog（meta-skill 也会在 catalog
  超过 7 天未更新时提示 agent 自动刷新）。
- Codex / OpenClaw 宿主的 meta-skill 入口在 `INSTALL_TARGETS` 中追加一行即可。
- 要求 Python 3.9+，无任何第三方依赖。
