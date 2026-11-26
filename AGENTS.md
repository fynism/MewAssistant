## Working Style
- Think from first principles: confirm the real goal, constraints, and success criteria first.
- Log pitfalls in HANDOFF.md and read it before each new task.
- For production work, include performance cost in design and acceptance.

## Branches and Commits
- Create a dedicated `codex/` branch before starting each new feature. Use a different prefix if the user requests one.
- Commit after completing and verifying each coherent stage. Keep each commit focused on one change that can be reviewed on its own.
- Aim for fewer than 1,000 changed lines per commit, counting additions and deletions together. Split larger changes by logical stage when practical. If a change needs to stay together and exceeds the target, explain why in the commit message.

## Simplicity First
- Build only what was asked: no unrequested features, options or configurability, no abstractions for single-use code, no error handling for impossible scenarios.
- If 50 lines would do what you wrote in 200, rewrite it.
- If you change approach midstream, remove the abandoned path before finishing.

## Fix Root Causes
- Fix bugs at their root cause, reasoning from first principles and established best practice. Never patch over symptoms.
- If a confusing design caused the bug, fix the design, and leave the code you touch clearer than you found it.
- This takes precedence over any other guidance that favors a smaller change.

## Comments Explain Why
- Write comments as if the current code is the only version a reader will see. Never mention this session, prior attempts, or the editing process.
- Explain why (non-obvious constraints, business rules), not what. If you'd only write a "what" comment, delete it and improve the names instead.
- Write comments in English.

## UI/UX
- Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining.
- Every element must serve a clear user purpose; no slogans, decoration, nested cards, or repeated info.
- Show explanations only for abnormal states or required actions; put secondary details behind progressive disclosure.
- Stay consistent with existing screens; prefer removing or simplifying over adding, and don't redesign the whole page to change one part.

## 项目对话中的 Agent 调度

每个为本项目新开的对话都由该对话的主 Agent 担任任务经理。主 Agent 是用户在该对话中唯一直接沟通的 Agent，负责理解目标、分析方案、拆分任务、协调依赖、审查子代理的产出，并整合结论向用户汇报。

简单问题可以直接回答。涉及实际执行时，默认把调研、实现和测试交给子代理。独立任务可以并行，有依赖的任务按顺序安排。发现遗漏或验证失败时，交回子代理补齐，再由主 Agent 验证。

常规决策由主 Agent 处理。只有需要用户授权、会明显改变任务范围，或缺少关键信息时，才向用户确认。如果当前对话无法调用子代理，先告知用户。
