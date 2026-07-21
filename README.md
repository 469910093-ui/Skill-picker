# skill-picker

本机 agent skills 的扫描、查重、场景聚类与路由工具。

装的 skills 越来越多之后，两个痛点会越来越明显：想不起 skill 的名字、不知道怎么唤醒；
功能重叠的 skills 不知道该用哪个。skill-picker 解决这两个问题——
自动扫描本机所有 skill 目录，按场景聚类生成一份 catalog，标出重复和漂移，
并以 meta-skill 的形式装进各个 agent 宿主：当你说"用哪个 skill 做 X"这类模糊意图时，
agent 用**共享打分引擎**检索候选、给出推荐、**弹出选项让你自己选**。

设计哲学来自 [tab-out](https://github.com/zarazhangrui/tab-out)：
不管理数据，只读取已存在的事实；自动聚类；暴露冗余但让用户决策；
零服务器、零账号、零外部 API、零第三方依赖（仅 Python 标准库，3.10+）。

## 快速开始

```bash
python skillpick.py install
```

一条命令完成：扫描 → 生成 catalog + dashboard → meta-skill 装进四宿主
（Cursor / Claude Code / Codex / OpenClaw）→ 工具自拷贝到 `~/.skill-picker/`
（此后所有路径与克隆目录解耦，跨机器可用）。

之后在任意会话里说「帮我选个 skill 做周报」「有没有 skill 能画图表」，
skill-picker 就会被唤醒并弹出候选让你选择。
也可以直接双击打开 `~/.skill-picker/dashboard.html`（无需服务器）。

给 agent 装：把仓库链接丢给任意 coding agent 说 "install this"，见 [AGENTS.md](AGENTS.md)。

## 命令

| 命令 | 作用 |
|---|---|
| `python skillpick.py scan` | 扫描所有 skill 目录，生成/刷新 catalog + dashboard + 门禁 |
| `python skillpick.py check` | 同 scan（退出码 0=可信 / 2=门禁 FAIL） |
| `python skillpick.py match "意图" [--top N] [--json]` | 共享引擎检索候选（meta-skill 第一步必跑） |
| `python skillpick.py serve [--port N]` | 起本地看板服务（Cursor 中由 agent 侧边打开） |
| `python skillpick.py install` | scan + 四宿主装 meta-skill + 工具自拷贝 |
| `python skillpick.py report` | 打印当前 catalog |
| `python -m unittest discover -s tests` | 跑测试（30 用例，含黄金匹配回归） |

## 架构：单一真相源

```
rules.json ──── 近义词/权重/分类规则/停用词/黄金用例（唯一可调参处）
    │
    ├── matching.py ── 共享打分引擎（tokenize/IDF/多标签分类/合并副本/match）
    │       ├── mcp_server.py       ← 宿主插件形态（MCP stdio，降级链第①级）
    │       ├── skillpick.py match  ← CLI 路由（降级链第②级）
    │       ├── run_gates G4        ← 黄金用例门禁
    │       └── tests/              ← 机器无关 fixture 回归
    └── dashboard.py ── JS 为同构镜像，常量由 rules.json 注入（禁止手写）
```

**页面搜索和会话路由永远同一套结果**——这是 v2 重构的核心承诺。

## 三级降级链（宿主插件优先，确保体验）

`install` 会把本地 MCP server 注册进 Cursor（`~/.cursor/mcp.json`）、
Claude Code（`~/.claude/mcp.json`）、Codex（`~/.codex/config.toml`）——
写入前读原文件、只增改 `skill-picker` 一个键、幂等可重跑，不碰用户已有配置。

| 级别 | 入口 | 说明 |
|---|---|---|
| ① 插件 | MCP 工具 `skill_match` / `skill_dashboard` | agent 直接调用，免 shell、免路径/PATH/编码问题 |
| ② CLI + HTML | `skillpick.py match` / `serve` + 内置浏览器 | MCP 未注册或调用失败时降级 |
| ③ 浏览器兜底 | 系统默认浏览器打开 `dashboard.html`（file:// 可用） | serve 也起不来时的最终兜底 |

OpenClaw 暂无标准 MCP 注册入口，从第②级起步。

## 工作原理

1. **扫描**：遍历宿主目录（`~/.claude/skills|plugins`、`~/.cursor/skills|skills-cursor|plugins/cache`、
   `~/.agents/skills|plugins`、`~/.codex/skills|plugins/cache`、`~/.openclaw/skills|workspace/skills`、
   `~/.gemini/skills` + `config.json` 自定义目录）中的所有 `SKILL.md`，
   解析 frontmatter，并提炼正文关键词（标题/加粗/命令名）参与匹配。
2. **多标签分类**：名称强路由（`figma-*`→设计）+ 关键词规则表，主分类用于分组展示，
   全部标签参与匹配的场景置顶。
3. **查重**：同名多份比内容 hash 分「一致/漂移」；描述相似度仅在同分类内比较、
   同插件家族内部跳过（官方套件的正常分工不算重叠）。
4. **匹配**：查询侧禁中文单字、IDF 稀有词加权、中英近义词扩展（泛词折价）、
   名称/描述/正文三字段交叉验证、场景点名置顶。
5. **路由**：meta-skill 被唤醒后**必须先跑 `match` CLI**（禁止凭记忆翻 catalog），
   结合会话上下文标注推荐项，用宿主提问工具让用户点选，选定后才执行对应 SKILL.md。

## 强制门禁（每次 scan 自动执行）

| 门禁 | 校验什么 | 不过会怎样 |
|---|---|---|
| **G1 覆盖率（全）** | 全盘搜 `SKILL.md` 与扫描根比对，未覆盖即 FAIL | 退出码 2；页面标红；meta-skill 提醒"结果不完整" |
| **G2 解析质量（准）** | 缺有效描述的 skill 占比 ≤10% | WARN，列出问题 skill |
| **G3 漂移提醒** | 同名 skill 跨端内容是否一致 | WARN，指引「理技能」tab 人工取舍 |
| **G4 匹配黄金用例** | rules.json 里的典型意图必须命中期望 skill（未安装则跳过） | FAIL=匹配引擎回归，退出码 2 |

G1 未通过时把未覆盖目录写进 `~/.skill-picker/config.json`：

```json
{ "extra_roots": [ { "path": "D:/some/custom/skills", "host": "custom" } ] }
```

## 只读铁律

工具与 meta-skill 都**永不修改、移动、删除任何 skill 文件**，只写 `~/.skill-picker/`。
发现漂移/重叠只提示；清理是用户逐项确认后的独立操作。

## 调参

改 `rules.json`（近义词 `syn`、权重 `weights`、分类 `category_rules`、黄金用例 `golden`），
然后 `python skillpick.py check` —— G4 会告诉你有没有改坏。
