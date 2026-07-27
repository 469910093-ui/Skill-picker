# Contributing

感谢你愿意改进 skill-picker。

## 开发

```bash
git clone https://github.com/469910093-ui/Skill-picker.git
cd Skill-picker
python -m unittest discover -s tests
```

改匹配规则只动 `rules.json`，然后跑：

```bash
python skillpick.py check
python -m unittest discover -s tests
```

改完 dashboard 后重建公开 Demo：

```bash
python scripts/build_demo.py
```

## 原则

1. **零第三方依赖**：仅 Python 标准库（3.9+）。
2. **只读 skills**：永不修改、移动、删除用户已有 `SKILL.md`；只写 `~/.skill-picker/`。
3. **单一真相源**：打分常量在 `rules.json`；`matching.py` / CLI / MCP / dashboard JS 必须同源。
4. **门禁优先**：G1–G4 失败时不要「悄悄放过」。

## PR 建议

- 小步提交，说明「为什么」而不只是「改了什么」
- 匹配逻辑变更请附带黄金用例（`rules.json` → `golden`）或测试
- 文档 / Demo / 翻译类改动同样欢迎
