# Engram v3.11 全功能验证报告

- 生成时间：2026-05-22T02:35:32
- 仓库：`E:\Personal Intelligence Identity Asset\engram`
- 任务包：codex-task-verify-v3.11-full.md
- Python：(local runtime)

## 总结
- 总 case 数：32
- 通过：32
- 失败：0
- 通过率：100.0%
- T1-T5 功能/回归：28/28
- T6 LLM 评估：4/4（100.0%）

## 各组结果
| 组 | 名称 | Case 数 | 通过 | 失败 |
|---|---|---:|---:|---:|
| T1 | 记忆对账 | 6 | 6 | 0 |
| T2 | AI 配置扫描 | 4 | 4 | 0 |
| T3 | 审查页面 | 5 | 5 | 0 |
| T4 | Tier 系统 | 8 | 8 | 0 |
| T5 | Bug 修复回归 | 5 | 5 | 0 |
| T6 | LLM 智能评估 | 4 | 4 | 0 |

## 失败详情
- 无

## LLM 评估详情（T6）
- `T6.1` 高价值知识识别：通过
  - accuracy：1.00
  - 原始响应摘要：{"selected_ids":["K1","K2"],"reason":"K1 和 K2 具有长期价值：K1 的本地优先架构决策可泛化到所有用户数据隐私场景，独特且影响未来所有 AI 工具的设计；K2 的身份定位原则可泛化到产品战略和团队协作，独特且持续指导决策。K3、K4、K5 是短期操作细节，缺乏泛化性和长期影响。"}
- `T6.2` 暂存区内容筛选：通过
  - accuracy：1.00
  - 原始响应摘要：{"promote_ids":["S1","S2"],"reason":"S1 描述了可复用的工作流原则，S2 是影响文档和 UI 的持久质量规则，两者均具有长期参考价值；S3、S4、S5 为一次性调试信息，无晋升必要。"}
- `T6.3` 重复检测：通过
  - accuracy：1.00
  - 原始响应摘要：{"pairs":[{"id":"P1","duplicate":true},{"id":"P2","duplicate":false},{"id":"P3","duplicate":false}]}
- `T6.4` tier 决策合理性：通过
  - median reasonable_ratio：0.73
  - 原始响应摘要：{"reasonable_ids":["R1","R2","R3","R4","R6","R7","R9","R10","R11","R13","R15"],"unreasonable_ids":["R5","R8","R12","R14"],"reasonable_ratio":0.7333,"notes":"R5、R8、R12、R14 为一次性或临时性细节，缺乏通用价值或可复用性，不应评为 rare 或 staging 以上；R8 和 R14 的 staging 评级合理但 rarity 不应为 staging（应为 common 或 trash）。其余条目符合其 rarity 定义：legendary 对应架构级或跨域决策，epic 对应核心身份或保护策略，rare 对应已验证的通用实践。"}

## 额外验证命令

## 结论
T1-T5 全部通过，T6 达到 60% 门槛；按任务包门槛，可以发布 v3.11.0。
