# AI-Assisted Development with ECC

This repository has been improvised with patterns and guidance from [ECC](https://github.com/priyamsinghjhs/ECC) (the agent harness performance optimization system) so that AI coding agents work more effectively **without changing any existing functionality**.

## What was added (additive only)

- `CLAUDE.md` — primary guidance for Claude Code and similar agents. Encodes ECC principles (plan → test → implement → review → remember) plus KareShafaa-specific invariants.
- `AGENTS.md` — recommended ECC agents for different kinds of work.
- `.cursor/rules/kareshafaa-health.md` — always-on Cursor rule that protects clinical, SLA, ownership and privacy invariants.
- `.cursor/rules/python-coding-style.md` — ECC-derived Python style rule.
- `skills/screening-to-care/SKILL.md` — custom skill describing the screening-to-care loop, its invariants, and the recommended ECC-style workflow.

## How to use

1. Install ECC in your harness (Claude Code plugin, Cursor rules, etc.) from the official source.
2. Open this repository. The `CLAUDE.md`, Cursor rules and skills will be picked up automatically by compatible tools.
3. For any change that touches clinical rules, state machine, SLA, ownership or patient data:
   - Start with the `ecc:planner` or `ecc:tdd-guide` agent.
   - Write the failing test first.
   - Implement the minimal change.
   - Review with `ecc:code-reviewer` and `ecc:security-reviewer`.

## Safety guarantee

No application code, templates, database schema, or clinical rules were modified. All additions are documentation and agent configuration only. Existing tests and behaviour remain unchanged.

See also the original ECC healthcare skills (`healthcare-cdss-patterns`, `healthcare-phi-compliance`) which can be referenced or copied from the ECC repository for deeper patterns.
