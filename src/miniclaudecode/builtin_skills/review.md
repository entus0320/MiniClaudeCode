---
name: review
description: 按 correctness、tests、security、maintainability 维度审查代码变更。
tools:
  - read_file
  - glob_files
  - grep_content
  - run_command
mode: shared
model: default
context: full-summary
---

# Review Skill SOP

你正在执行代码审查。目标是发现真实问题，而不是泛泛而谈。

审查要求：
1. 先确定审查范围：用户指定文件、当前工作区变更，或相关模块。
2. 对每个发现的问题给出文件位置、影响、复现或推理依据。
3. 优先关注 correctness、测试缺口、安全边界、错误处理和可维护性。
4. 不把风格偏好包装成缺陷。
5. 如果没有发现问题，直接说明未发现可确认问题，并列出实际检查过的范围。
6. 输出按严重程度排序，避免冗长背景铺垫。
