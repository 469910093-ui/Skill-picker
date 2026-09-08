"""机器无关的合成 skills fixture：覆盖黄金用例涉及的正样本与干扰项。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matching  # noqa: E402

RULES = matching.load_rules()


def _skill(name, desc, kw="", host="claude-code", dir_name=None):
    primary, labels = matching.categorize(name, desc, RULES)
    return {
        "name": name,
        "dir_name": dir_name or name,
        "description": desc,
        "keywords": kw,
        "category": primary,
        "categories": labels,
        "host": host,
        "path": f"C:/fake/{host}/{dir_name or name}/SKILL.md",
        "sha256": name[:8].ljust(8, "0"),
    }


FIXTURE_SKILLS = [
    # --- 正样本 ---
    _skill("figma-generate-design",
           "Use this skill alongside figma-use when the task involves translating an "
           "application page, view, or multi-section layout into Figma. Triggers: write to "
           "Figma, create in Figma from code, build a landing page in Figma, mockup.",
           kw="figma design system components variables import assemble"),
    _skill("figma-design-to-code",
           "MANDATORY prerequisite before get_design_context. Trigger whenever the user "
           "wants to implement, build, port, or code up a Figma design as code.",
           kw="figma get_design_context screenshot code connect tokens"),
    _skill("figma-use",
           "MANDATORY prerequisite before every use_figma tool call. Trigger for write "
           "actions in the Figma file context: create edit delete nodes, variables, "
           "components and variants, auto-layout, fills.",
           kw="figma plugin api variables components variants"),
    _skill("video-use",
           "Edit any video by conversation. Transcribe, cut, color grade, generate overlay "
           "animations, burn subtitles - for talking heads, montages, tutorials, travel, "
           "interviews. No presets, no menus.",
           kw="video edit cut transcribe subtitles ffmpeg color grade"),
    _skill("work-report",
           "扫描本地工作目录，自动发现项目并汇总近期工作记录，生成结构化的中文工作汇报。"
           "当用户需要总结近期工作、生成工作汇报、准备周报/日报/月报时使用此 skill。",
           kw="工作汇报 周报 日报 月报 扫描 项目"),
    _skill("ibu-html-weekly-overview",
           "通用周报 HTML 总览生成能力：读取任意周报文档，自动提炼每个项目的进展卡点风险"
           "待办四象限，产出单文件交互式 HTML。当用户提到周报 HTML、一屏看完周报时使用。",
           kw="周报 HTML 四象限 卡片 图表"),
    _skill("lark-doc",
           "飞书云文档（Docx / Wiki 文档）：读取和编辑飞书文档内容。当用户给出文档 URL 或 "
           "token，或需要查看、创建、编辑文档、插入或下载文档图片附件时使用。",
           kw="飞书 文档 docx wiki token 编辑"),
    _skill("html-ppt",
           "HTML PPT Studio - author professional static HTML presentations in many styles. "
           "Use when the user asks for a presentation, PPT, slides, keynote, deck, "
           "slideshow, 幻灯片, 演讲稿, 做一份 PPT.",
           kw="ppt slides presentation deck 幻灯片 模板"),
    _skill("guizang-ppt-skill",
           "生成横向翻页网页 PPT（单 HTML 文件），含 WebGL 背景、章节幕封、数据大字报、"
           "图片网格等模板。当用户需要制作分享 / 演讲 / 发布会风格的网页 PPT 时使用。",
           kw="PPT 翻页 WebGL 模板 杂志风 瑞士风"),
    _skill("chart-visualization",
           "将数据可视化为图表。当用户需要生成柱状图、折线图、饼图、散点图、雷达图、桑基图、"
           "思维导图、流程图等图表时调用此技能，通过 curl 工具调用 AntV API 生成图表图片。",
           kw="图表 柱状图 折线图 饼图 AntV 可视化"),
    _skill("gochina-weekly-review",
           "GOCHINA 五产线周度经营复盘（飞书 DocxXML）：看板对齐、数据驱动 callout、"
           "AntV 结论图、QA 门禁与一键编排。Use when the user mentions GOCHINA 复盘、周复盘。",
           kw="复盘 周报 GOCHINA 飞书 QA"),
    # --- 干扰项（历史 bug 的元凶们）---
    _skill("mckinsey-consultant",
           "McKinsey顾问式问题解决系统。从商业问题出发,通过假设驱动的结构化分析方法,"
           "生成McKinsey风格研究报告和PPT。融合MECE原则、Issue Tree拆解、Dummy Page设计。",
           kw="McKinsey PPT 分析 报告 设计"),
    _skill("baidu-ai-map",
           "百度地图 Agent Plan，无需成为百度地图开发者，立即接入百度地图为 Agent 场景"
           "原生设计的地图能力，例如 AI 地点检索、AI 路线规划、地理编码等。",
           kw="百度地图 API 路线规划 地理编码"),
    _skill("ai-short-drama",
           "精细化 AI 短剧 IP 创作技能。三阶段架构：剧本+ref图、分镜图（视频首帧）、出片。"
           "用户提到短剧、微短剧、竖屏剧、AI 短剧、分镜、出片时触发。",
           kw="短剧 分镜 视频 出片 剧本 设计"),
    _skill("mimeng-writing",
           "咪蒙爆款文章写作技巧。适用于需要创作10万+阅读量爆款文章、情感共鸣类内容、"
           "故事叙事或社会议题评论时使用。掌握标题制造、开篇设计、情绪调动等核心技巧。",
           kw="爆款 文章 标题 开篇设计 金句"),
    _skill("lark-calendar",
           "飞书日历：管理日历日程和会议室。查看/搜索日程、创建/更新日程、查询忙闲、"
           "预定会议室。不负责查询过去的视频会议记录。",
           kw="飞书 日历 日程 会议室"),
    # 大杂烩描述：把一堆触发词罗列进 description 的 skill。它靠描述字段刷高分，
    # 且「设计」整串出现在描述里，一度还能白拿一次 desc_substr（同一条命中记两次）。
    _skill("lark-apps",
           "妙搭应用开发与托管：应用创建、本地全栈开发、云端生成迭代、创意设计"
           "（UI mockup / 可交互原型 / 线框图 / 落地页 / 仪表盘 / 幻灯片 deck / 视觉探索）、"
           "飞书平台能力集成、日志与监控查询。当用户要开发一个系统、工具、平台、应用，"
           "或要设计 / design / mockup / prototype / wireframe / 做 PPT / deck / 视觉探索时使用。",
           # 关键词刻意不沾设计类词——真实那条正是这个形状：名称与正文都不提设计，
           # 全靠描述里罗列的触发词刷分。
           kw="妙搭 应用 部署 飞书 环境变量 日志 协作者"),
    # figma 同族里的白板工具。FigJam 是白板产品而非设计工具，「设计」不该把它
    # 抬到出图类 skill 前面。
    _skill("figma-use-figjam",
           "This skill helps agents use Figma's use_figma MCP tool in the FigJam context. "
           "Can be used alongside figma-use which has foundational context.",
           kw="figma figjam whiteboard sticky notes sections connectors"),
]
