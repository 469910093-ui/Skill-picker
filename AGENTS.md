# AGENTS.md — skill-picker：安装与引导指南（给 coding agent）

你正在替用户安装 **skill-picker**。你的任务不只是装好，还要让用户明白它解决什么问题。

---

## Step 0 — 介绍产品

安装前先告诉用户他们会得到什么：

> **skill-picker** 把你本机所有 agent skills（Claude Code / Cursor / Codex / OpenClaw / 插件）
> 扫进一份带门禁的 catalog：
>
> - **找技能**：说"用哪个 skill 做周报"，会话内弹出 2-4 个候选 + AI 推荐，你自己点选
> - **理技能**：同名漂移、功能重叠自动圈出，红色感叹号提醒（只提示，不代删）
> - **本地 dashboard**：单文件 HTML，三 tab（找技能 / 理技能 / **去 GitHub 发现**）
> - **本机无解时**：切到「去 GitHub 发现」（skill-feed lite 子页，无关注/发布/个人后台）→ 打开 GitHub 自行安装 → 再 `scan`
> - **四道门禁**：覆盖率、解析质量、漂移、匹配黄金用例，每次扫描强制自检
> - **找/理技能 100% 本地**：无服务器、无账号；发现子页数据来自本机 skill-feed 或公开 embed
>
> 安装大约 1 分钟。

## Step 1 — 克隆并安装

```bash
git clone <repo-url> skill-picker
cd skill-picker
python skillpick.py install
```

`install` 会：扫描全部 skill 目录 → 生成 `~/.skill-picker/catalog.md` + `dashboard.html`
→ 把 meta-skill 装进四宿主（`~/.cursor/skills`、`~/.claude/skills`、`~/.agents/skills`、
`~/.openclaw/skills`）→ 工具自拷贝到 `~/.skill-picker/`（此后一切命令用 `~` 路径，与克隆目录解耦）。

## Step 2 — 检查门禁输出

安装输出末尾有四道门禁。逐条向用户解释：

- **G1 覆盖率 FAIL**：本机有 skill 目录没被扫到。把输出里列出的目录加进
  `~/.skill-picker/config.json`：

  ```json
  { "extra_roots": [ { "path": "D:/some/custom/skills", "host": "custom" } ] }
  ```

  然后重跑 `python ~/.skill-picker/skillpick.py check` 直到 PASS。
- **G3 漂移 WARN**：同名 skill 多端内容不一致，引导用户打开 dashboard「理技能」tab 查看，
  **不要替用户合并**。
- **G4 FAIL**：匹配引擎回归，请到仓库报 issue。

## Step 3 — 带用户试一圈

> 装好了！查本机 skills / 选 skill 时，agent **必须先弹出看板**，再在对话里给候选点选。
>
> 1. **会话内**：说「帮我选个 skill 做 X」「帮我看看本机有没有 X 能力」
>    「你会 X 吗」「我要干 X 了哪个 skill 最适配」「整理一下我当前安装的所有 skills」→
>    先开 `dashboard`（带 `?q=` 意图，页面自动展示匹配结果），再给候选和推荐，由你点选。
> 2. **看板**：也可双击 `~/.skill-picker/dashboard.html`；「理技能」看重复体检；
>    **本机匹配为空时**点「去 GitHub 发现」（或 reco 空态按钮）。
>
> 若 agent 只在聊天里列清单、看板没出来，就是流程违规——让它补开看板。
> 若本机无匹配，引导用户进「去 GitHub 发现」，**不要硬凑本机 skill**。

## Key Facts

- 纯本地（找/理）：数据只写 `~/.skill-picker/`，不修改任何已有 skill 文件
- 发现子页：`discover.py` 同步 `~/.skill-picker/discover.html`（来自 `~/.skill-feed/feed.lite.html`）；
  未同步时可回退公开 `embed.html`
- 更新：`git pull` 后重跑 `python skillpick.py install`
- 刷新索引（装了新 skill 后）：`python ~/.skill-picker/skillpick.py scan`
- 会话内候选检索（meta-skill 用，与页面同一引擎）：
  `python ~/.skill-picker/skillpick.py match "意图" --top 4 --json`
- 测试：`python -m unittest discover -s tests`
