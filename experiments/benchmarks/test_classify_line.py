"""分流函数 _classify_line 准确率测试。

目标：30 条混合内容，分流准确率 >= 80%。
"""

from __future__ import annotations

import sys
sys.path.insert(0, "src")

from engram_core.setup_wizard import _classify_line, _scan_rule_files

# (input_line, scope, expected_category)
TEST_CASES: list[tuple[str, str, str]] = [
    # === 用户身份类 ===
    ("所有沟通使用中文。", "global", "user"),
    ("All communication in Chinese only.", "global", "user"),
    ("我是全栈开发者，主要做后端。", "global", "user"),
    ("I am a data scientist focused on NLP.", "global", "user"),
    ("偏好简洁的代码风格，不要过度注释。", "global", "user"),
    ("Never add unnecessary abstractions.", "global", "user"),
    ("禁止在代码中使用 emoji。", "global", "user"),
    ("必须所有回复用中文。", "global", "user"),
    ("沟通风格要简洁直接。", "global", "user"),
    ("工作方式：先看代码再改。", "global", "user"),

    # === 项目规则类 ===
    ("这个 repo 使用 Tailwind CSS。", "project", "project"),
    ("所有测试必须通过才能合并。", "project", "project"),
    ("Build command: npm run build", "project", "project"),
    ("Deploy 到 AWS Lambda。", "project", "project"),
    ("CI/CD 用 GitHub Actions。", "project", "project"),
    ("pre-commit hook 会检查 lint。", "project", "project"),
    ("数据库用 PostgreSQL 14。", "project", "project"),
    ("API endpoint 都在 /api/v2 下。", "project", "project"),
    ("路由定义在 src/routes/ 目录下。", "project", "project"),
    ("migration 文件不要手动编辑。", "project", "project"),

    # === 应该跳过的 ===
    ("", "global", "skip"),
    ("# 标题", "global", "skip"),
    ("---", "project", "skip"),
    ("```python", "project", "skip"),
    ("短", "global", "skip"),
    ("<!-- comment -->", "project", "skip"),

    # === 歧义/边界 case ===
    # 全局文件中的偏好 → user
    ("我偏好所有测试都写 integration test。", "global", "user"),
    # 项目文件中的偏好 → 看内容，含 test → project
    ("我偏好所有测试都写 integration test。", "project", "project"),
    # 全局文件中的通用规则 → user
    ("代码提交前必须 review。", "global", "user"),
    # 项目文件中的通用规则 → project (default for project scope)
    ("代码提交前必须 review。", "project", "project"),
]


def run_test() -> None:
    correct = 0
    total = len(TEST_CASES)
    failures: list[str] = []

    for i, (line, scope, expected) in enumerate(TEST_CASES):
        actual = _classify_line(line, scope)
        if actual == expected:
            correct += 1
        else:
            failures.append(
                f"  [{i+1}] scope={scope} expected={expected} got={actual}\n"
                f"       line: {line!r}"
            )

    accuracy = correct / total * 100
    print(f"\n分流准确率: {correct}/{total} ({accuracy:.1f}%)")
    print(f"门槛: >= 80% ({'PASS' if accuracy >= 80 else 'FAIL'})\n")

    if failures:
        print("失败 case：")
        for f in failures:
            print(f)
        print()


if __name__ == "__main__":
    run_test()
