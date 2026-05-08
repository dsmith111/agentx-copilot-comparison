# em_base — Evaluation Base Template

This directory is the **starting point** from which all agent runs in this evaluation were set up.

It contains the task contract, constraints, prompts, and scoring rubric that were handed to each agent.
No implementation was provided — agents built from scratch using only these inputs.

## Contents

| File | Purpose |
|------|---------|
| `Design.md` | Product contract — full specification for the ADLS Gen2 Lite Emulator |
| `Agents.md` | Agent instruction rules (do not use real Azure, do not weaken tests, etc.) |
| `Initial_Info.md` | MVP scope overview and structural guidance |
| `Prompts.md` | Prompts used for Copilot/Claude and AgentX runs |
| `Scoring_Rubric.md` | Scoring rubric used to evaluate each implementation |
| `scripts/evaluate.sh` | Acceptance gate script (pytest + Docker + SDK smoke test) |
| `scripts/smoketest.py` | Standalone Python SDK smoke test script |

## How it was used

Each evaluation folder (`em_copilot`, `em_agentx`, `em_agentx2`) started as a copy of this
base directory. The agent then built the full emulator implementation on top of it.

The files in this directory were **read-only inputs** — agents were not supposed to remove or
weaken `Design.md`, `Agents.md`, or the acceptance tests.

## Evaluation runs

| Folder | Agent | Operating model |
|--------|-------|-----------------|
| `em_copilot` | Copilot / Claude 4.7 1M | Standard coding-agent workflow |
| `em_agentx` | AgentX Auto | Used like a normal coding agent |
| `em_agentx2` | AgentX (role-driven) | Forced full role workflow (PM → Research → Architect → Engineer → Tester → Reviewer → DevOps → Certification → Learning) |

See the [main README](../README.md) for the full evaluation methodology, results, and conclusions.
