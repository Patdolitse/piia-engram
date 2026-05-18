"""
Engram 跨工具实证 Demo
====================

演示效果：同一个 Engram 让 Claude Code 和 Codex 都能
1. 知道用户是谁（身份、角色、语言）
2. 遵守用户的偏好（中文、GUI优先、快速迭代）
3. 应用用户的质量标准（编译通过+括号平衡+功能可运行）
4. 利用用户的经验教训（避免已知的坑）

使用方法：
    python cross_tool_demo.py

前提：engram MCP server 可用（已在 Claude Code 和 Codex 配置）
"""

import json
import sys
from pathlib import Path

# Add tools directory to path
TOOLS_DIR = Path(__file__).resolve().parent.parent / "src" / "engram_core"
sys.path.insert(0, str(TOOLS_DIR.parent))

from engram_core.core import Engram


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def demo_without_engram():
    """模拟没有 Engram 时 AI 的默认行为。"""
    print_section("场景 A：没有 Engram（AI 的默认行为）")

    print("用户: 帮我写一个函数检查文件是否存在")
    print()
    print("AI（默认回复，英文，带冗长注释）:")
    print('  def check_file_exists(filepath: str) -> bool:')
    print('      """')
    print('      Check if a file exists at the given filepath.')
    print('      ')
    print('      Args:')
    print('          filepath: The path to the file to check.')
    print('      ')
    print('      Returns:')
    print('          True if the file exists, False otherwise.')
    print('      """')
    print('      import os')
    print('      return os.path.exists(filepath)')
    print()
    print("问题：")
    print("  [X] 用英文回复（用户要求中文）")
    print("  [X] 加了冗长docstring（用户偏好实用为主）")
    print("  [X] 不知道用户的验收标准")
    print("  [X] 不知道用户是非技术背景")
    print("  [X] 不知道 Windows 上 python 的已知问题")


def demo_with_engram():
    """展示有 Engram 时 AI 如何自动适配。"""
    print_section("场景 B：接入 Engram（AI 自动了解用户）")

    engram = Engram()
    context = engram.generate_context(
        project_folder=str(Path.home())
    )

    print("[ Engram 冷启动上下文（AI 自动获取）]\n")
    print(context)
    print()
    print("-" * 40)
    print()
    print("用户: 帮我写一个函数检查文件是否存在")
    print()
    print("AI（Engram 增强回复，自动适配）:")
    print('  from pathlib import Path')
    print()
    print('  def check_file(filepath):')
    print('      return Path(filepath).exists()')
    print()
    print("优势：")
    print("  [OK] 用中文回复（Engram记录了语言偏好）")
    print("  [OK] 简洁实用（Engram记录了代码风格偏好）")
    print("  [OK] 用 Path 而不是 os（Engram记录了已知问题：Windows python stub）")
    print("  [OK] 知道用户是非技术背景，不加过度抽象")
    print("  [OK] 验收时检查：编译通过+括号平衡+功能可运行")


def demo_cross_tool_knowledge():
    """展示跨工具知识共享。"""
    print_section("场景 C：跨工具知识共享")

    engram = Engram()
    stats = engram.get_stats()
    lessons = engram.get_lessons(limit=5)
    decisions = engram.get_decisions(limit=3)

    print(f"Engram 知识资产统计：")
    print(f"  - 经验教训: {stats['total_lessons']} 条")
    print(f"  - 关键决策: {stats['total_decisions']} 条")
    print(f"  - 涉及领域: {stats['domain_count']} 个")
    print(f"  - 来源追踪: {json.dumps(stats.get('lessons_by_source_tool', {}), ensure_ascii=False)}")
    print(f"  - Schema版本: {stats['schema_version']}")
    print()

    print("最近5条经验教训（任何AI工具都能获取）：")
    for l in lessons:
        tool = l.get("source_tool", "未标记")
        print(f"  [{tool}] {l.get('summary', '')[:60]}")
    print()

    print("最近3条决策（任何AI工具都能获取）：")
    for d in decisions:
        tool = d.get("source_tool", "未标记")
        print(f"  [{tool}] {d.get('question', '')[:40]} → {d.get('choice', '')[:40]}")
    print()

    print("关键点：")
    print("  [OK] Claude Code 存的教训，Codex 下次对话就能用")
    print("  [OK] Codex 做的决策，Claude Code 下次对话就知道")
    print("  [OK] source_tool 字段追踪知识来源，防止重复")
    print("  [OK] 同一个 MCP 接口，两端配置完全一致")


def demo_identity_card():
    """展示可携带身份卡。"""
    print_section("场景 D：可携带身份卡（粘贴到任何AI）")

    engram = Engram()
    card = engram.export_identity_card()

    # 只显示前30行
    lines = card.split("\n")[:30]
    for line in lines:
        print(f"  {line}")
    if len(card.split("\n")) > 30:
        print(f"  ... (共 {len(card.split(chr(10)))} 行)")
    print()
    print("用法：复制这段文字，粘贴到 ChatGPT/Kimi/任何AI 的对话开头")
    print("效果：AI 立刻了解你是谁、如何工作、质量标准是什么")


def demo_schema_v2():
    """展示 v2.0 新功能。"""
    print_section("Engram Schema v2.0 新功能")

    engram = Engram()

    print("1. 工具偏好（preferences.json）：")
    prefs = engram.get_preferences()
    tool_prefs = prefs.get("tool_preferences", {})
    for k, v in tool_prefs.items():
        print(f"   {k}: {v}")

    print()
    print("2. 信任边界（trust_boundaries.json）：")
    tb = engram.get_trust_boundaries()
    print(f"   默认共享级别: {tb.get('default_sharing', 'N/A')}")
    print(f"   按工具限制: {tb.get('tool_access', {}) or '无（全部开放）'}")
    print(f"   隐私字段: {tb.get('private_fields', []) or '无'}")

    print()
    print("3. 来源追踪（source_tool字段）：")
    print("   每条 lesson/decision 记录创建它的工具")
    print("   → 防止两端重复存储相同知识")
    print("   → 知道哪些知识来自哪个工作上下文")


def main():
    print("\n" + "=" * 60)
    print("   Engram 跨工具实证 Demo")
    print("   让每个AI工具都认识你")
    print("=" * 60)

    demo_without_engram()
    demo_with_engram()
    demo_cross_tool_knowledge()
    demo_identity_card()
    demo_schema_v2()

    print_section("总结")
    print("Engram 的价值：")
    print("  1. 不用每次对话都重新解释你是谁")
    print("  2. 换工具不丢失积累的知识和偏好")
    print("  3. 经验教训跨工具共享，避免重复踩坑")
    print("  4. 身份卡让任何AI（包括不支持MCP的）都能了解你")
    print()
    print("一句话：")
    print("  现在AI很聪明，但它们不认识你。")
    print("  Engram让每个AI工具都认识你——而且记忆属于你。")


if __name__ == "__main__":
    main()
