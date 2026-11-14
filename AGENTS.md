# Recommended Agents for KareShafaa

When using an ECC-compatible harness (Claude Code, Cursor with ECC rules, etc.), prefer these agents:

| Agent | When to use |
|-------|-------------|
| ecc:planner | Multi-step feature, pathway change, or large refactor |
| ecc:tdd-guide | Clinical rules, state machine, SLA, or ownership logic |
| ecc:code-reviewer | After any non-trivial change |
| ecc:security-reviewer | Auth, patient data, APIs, or audit changes |
| ecc:architect | Structural or database schema decisions |
| ecc:silent-failure-hunter | Background jobs, sweeps, or error paths |

Always start complex work with a plan, write the test first, implement the minimal change, then review.

See also `CLAUDE.md` and `skills/screening-to-care/SKILL.md`.
