# 推广与发布清单（skill-picker）

> 目标：让别人 10 秒看懂、30 秒愿意试、愿意转发。  
> 原则：先真实用户，再公开冲；Demo 优先于空吹。

## 0. 仓库可传播（已落地）

- [x] README 一句话价值 + Live Demo + 一键安装
- [x] 在线 Demo（GitHub Pages / `docs/demo/`）
- [x] MIT License、徽章、CHANGELOG、CONTRIBUTING
- [x] 名称具体：Skill Picker — 本机 agent skills 的「找技能 / 理技能」
- [ ] Release `v0.2.0`（随本次推送创建）
- [ ] 短视频 1 条（录屏：说意图 → 弹出候选 → 点选）
- [ ] README 内嵌 GIF（可用录屏导出，或先用 Demo 截图）

## 1. 小范围试（第 1 周）

找 **10–20 个真实用户**（装了很多 skills 的 Cursor / Claude Code 用户）：

| 渠道 | 话术示例 |
|---|---|
| 同事 / 朋友 | 「你本机 skills 超过 30 个的话，装这个扫一下，告诉我漏扫了没」 |
| 即刻 / 微信群 | 「做了个本机 skill 路由，不上传、无账号，求狠测」 |
| Discord / Slack agent 群 | 贴 Demo + `install` 命令，收集 G1 FAIL 的自定义路径 |

收集的问题记进 Issues，标签建议：`feedback` / `scan-miss` / `match-wrong`。

**公开冲之前至少修完：** 安装一步失败、Demo 打不开、匹配明显跑偏。

## 2. 公开冲（同一天集中发）

选一个「发布日」，同一天发完，形成短时热度：

| 平台 | 标题方向（具体场景，忌空夸） |
|---|---|
| **Show HN** | Show HN: Skill Picker – find the right local agent skill when you forget its name |
| **Reddit** r/LocalLLaMA / r/ClaudeAI / r/cursor | I built a local catalog that routes “which skill for X?” and flags duplicate skills |
| **Product Hunt** | Skill Picker – local dashboard for messy agent skill folders |
| **掘金 / V2EX** | 本机 skills 装多了找不到？一个零依赖工具：意图匹配 + 查重看板 |
| **即刻 / 小红书 / X** | 15 秒录屏 + Demo 链接 |

标题模板（好）：

> 把「用哪个 skill 做周报」变成 2–4 个可点选候选（100% 本地）

标题模板（差，别用）：

> Best AI tool ever / 革命性 Agent 基础设施

## 3. 持续节奏（比只发一次更稳）

- 每周至少：修 issue + 发一小版（CHANGELOG 记一笔）
- 每月：投 2–3 个相关 awesome / 周刊（agent skills、Claude Code、Cursor、local-first）
- 别买 star、别进互赞群

## 4. 一键素材包

- Demo：https://469910093-ui.github.io/Skill-picker/demo/
- Repo：https://github.com/469910093-ui/Skill-picker
- 安装：

```bash
git clone https://github.com/469910093-ui/Skill-picker.git
cd Skill-picker
python skillpick.py install
```

- 给 agent 装：把 repo 丢给 coding agent，说 `install this`（见 `AGENTS.md`）

## 5. 短视频分镜（30–45 秒）

1. 0–5s：打开塞满 skills 的文件夹（痛点）
2. 5–15s：`python skillpick.py install` + 门禁绿勾
3. 15–30s：会话里说「帮我选个 skill 做周报」→ 弹出候选
4. 30–40s：打开 dashboard「理技能」红色感叹号
5. 结尾：Demo 链接 + GitHub

## 6. 别踩的坑

- 只丢代码没 Demo → 转化极差（已用 Pages 规避）
- 买 star / 互赞 → 平台打压 + 伤信誉
- 自夸空洞标题 → 用具体场景句
