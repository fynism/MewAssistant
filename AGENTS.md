## Working Style
- Think from first principles: confirm the real goal, constraints, and success criteria first.
- Log pitfalls in HANDOFF.md and read it before each new task.
- For production work, include performance cost in design and acceptance.

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