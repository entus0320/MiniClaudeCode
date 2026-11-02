---
name: commit
description: 生成提交前检查与 commit message 建议，不自动提交或推送。
tools:
  - read_file
  - glob_files
  - grep_content
  - run_command
mode: shared
model: default
context: full-summary
---

# Commit Skill SOP

你正在帮助用户准备一次本地代码提交。必须先了解变更，再给出可审计的提交建议。

步骤：
1. 查看工作区状态，区分已跟踪修改和未跟踪文件。
2. 阅读与本次变更直接相关的文件，不要凭文件名猜测内容。
3. 如果需要验证，优先选择项目已有测试命令。
4. 汇报变更摘要、验证结果和风险。
5. 可以建议 commit message，但不要自动执行 `git commit`，除非用户明确要求。
6. 不要执行 `git push`、`git rebase`、`git reset --hard` 或强制推送。
