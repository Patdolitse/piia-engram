# Contributing to Engram

感谢你考虑为 Engram 做贡献。

## 开发环境

```bash
git clone https://github.com/<your-username>/engram.git
cd engram
pip install -e ".[dev]"
```

## 运行测试

```bash
python -m pytest tests/ -v
```

## 代码规范

- Python 3.10+
- 类型注解推荐但不强制
- 中文注释和 docstring 均可
- 测试覆盖新功能

## 提交 PR

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 运行测试确认通过
4. 提交 PR，说明改了什么和为什么

## 报告问题

在 GitHub Issues 中提交，请包含：
- 操作系统和 Python 版本
- 复现步骤
- 预期行为 vs 实际行为

## 行为准则

友善、尊重、建设性。我们都是为了让 AI 更好地认识人类。
