---
name: grill-me
description: Stress-test a plan, design, decision, or idea through a rigorous, dependency-aware interview. Use only when explicitly invoked with $grill-me.
---

# Grill Me

Interview the user until both sides share a precise understanding of the proposed plan, design, decision, or idea.

Model the discussion as a design tree. Each unresolved decision is a node, and dependent decisions branch from it. Do not silently choose an answer for the user.

## Interview workflow

1. Establish the subject and intended outcome from the user's request and available context.
2. Inspect the codebase, files, documentation, or tools for facts that can be discovered directly. Ask the user for decisions and preferences, not facts you can determine yourself.
3. Identify the current frontier: unresolved decisions whose prerequisites are already settled.
4. Ask the frontier in a single round. Number every question, explain why it matters when that is not obvious, and include your recommended answer with the key tradeoff.
5. Wait for the user's answers. Do not ask questions whose answers depend on an unresolved question in the same round.
6. Rebuild the design tree from the answers and repeat with the newly unblocked frontier.
7. When no unresolved branches remain, summarize the agreed design, assumptions, rejected alternatives, risks, and open follow-ups.
8. Ask the user to confirm that the summary reflects the shared understanding. Do not implement the plan until they confirm or explicitly ask you to proceed.

Keep each round focused enough to answer comfortably. Prefer concrete choices over vague prompts, but always allow a free-form answer.

Use this shape for each question:

```markdown
❓ **Q1: <short title>**

<Question and concise choices or constraints>

➡️ **Recommendation:** <recommended answer and principal tradeoff>
```

Separate questions with a horizontal rule. Make the recommendation substantive rather than treating it as the user's decision.

## Exploration

If repository or environment facts are missing, investigate them while continuing with unrelated questions whose prerequisites are settled. When delegation is available and useful, assign independent fact-finding to a sub-agent. Never delegate the user's product or design decisions.

## Completion condition

The interview is complete only when every material branch has been visited and no important assumption remains implicit. Completion produces a confirmed shared design, not an implementation.
