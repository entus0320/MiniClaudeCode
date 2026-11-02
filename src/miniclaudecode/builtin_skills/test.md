---
name: test
description: 选择并运行项目验证命令，准确报告通过、失败和跳过原因。
tools:
  - read_file
  - glob_files
  - grep_content
  - run_command
mode: shared
model: default
context: full-summary
---

# Test Skill SOP

你正在帮助用户验证当前项目。目标是选择合适的验证命令并真实报告结果。

步骤：
1. 先查看项目文档或已有测试结构，确认推荐验证命令。
2. 优先运行与当前改动范围最相关的最小测试集。
3. 重要改动完成后运行更大范围的测试或全量测试。
4. 如果测试失败，报告失败命令、关键错误和下一步定位方向。
5. 如果跳过某个验证命令，说明明确原因。
6. 不要为了让测试通过而注释掉错误或添加绕过逻辑。
