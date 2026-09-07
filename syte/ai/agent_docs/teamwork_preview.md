# Teamwork Multi-Agent System Protocol & Standards

When handling large, multi-faceted projects or when the user invokes `/teamwork`, follow this structured two-phase workflow:
**(1)** Craft a well-structured task prompt with clear requirements and objective acceptance criteria.
**(2)** Delegate execution to specialized subagent workflows or drive multi-step autonomous execution.

## Core Principles

| # | Principle | Rule |
|---|-----------|------|
| 1 | **Specify What, Not How** | Define requirements and acceptance criteria. Avoid prescribing implementation details (file names, architecture, algorithms, libraries) unless the user explicitly requests them. |
| 2 | **Objective Verification** | Every requirement needs a verification mechanism independent of the implementing agent's self-assessment. Programmatic verification (tests, lint, build checks) is preferred over subjective self-judgments. |
| 3 | **Acceptance Criteria = Guardrails** | Set the bar based on the user's actual needs. Purpose: prevent self-certification of incomplete or flawed work. |
| 4 | **Minimal Requirements** | Only specify what the user cares about. Infer the rest logically. More requirements = more constraints = less room for creative engineering. |

## 9-Step Project Elicitation Workflow

### Step 1: Elicit the Core Idea
- Ask: What do you want to build? What is the purpose (production, demo, internal tool)? Who is the audience?
- Capture in 1-2 clear opening sentences.

### Step 2: Identify Ambiguity
- Probe key dimensions: scope, technology constraints, infrastructure needs (network, database, storage), quality bar.
- Identify decisions that affect scope or verification.

### Step 3: Determine Integrity Mode
- `development` (default): Standard full-featured development with standard libraries.
- `demo`: Showcase capability with polished UI and interactive mock data.
- `benchmark`: Strict algorithmic testing and verification.

### Step 4: Draft Requirements Blocks (R1, R2, ...)
- Write 2-5 requirement blocks.
- Each requirement: 1-3 sentences stating WHAT is needed, not HOW.

### Step 5: Design Objective Verification
- Programmatic verification: Automated build commands (`npm run build`, `pytest`), AST syntax verification, lint passes.
- Verification must be runnable and objectively checkable without human intervention.

### Step 6: Set Acceptance Criteria
- Convert verification into concrete checkable criteria with checkboxes (`- [ ]`).

### Step 7: Infrastructure Constraints
- Controlled file access, API tokens, port bindings, database credentials.

### Step 8: Working Directory
- Set project working directory root.

### Step 9: Assemble `plan.md`
- Assemble the complete project prompt and plan directly into `plan.md` in the project root.
- Await user approval or auto-start.
