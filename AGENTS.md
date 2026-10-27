# context-mode — MANDATORY routing rules

context-mode MCP tools available. Rules protect context window from flooding. One unrouted command dumps 56 KB into context. Codex CLI hooks provide runtime enforcement when `[features].hooks = true`; these instructions remain mandatory model-side enforcement. Follow strictly.

## Think in Code — MANDATORY

Analyze/count/filter/compare/search/parse/transform data: **write code** via `ctx_execute(language, code)`, `console.log()` only the answer. Do NOT read raw data into context. PROGRAM the analysis, not COMPUTE it. Pure JavaScript — Node.js built-ins only (`fs`, `path`, `child_process`). `try/catch`, handle `null`/`undefined`. One script replaces ten tool calls.

## BLOCKED — do NOT use

### curl / wget — FORBIDDEN
Do NOT use `curl`/`wget` in shell. Dumps raw HTTP into context.
Use: `ctx_fetch_and_index(url, source)` or `ctx_execute(language: "javascript", code: "const r = await fetch(...)")`

### Inline HTTP — FORBIDDEN
No `node -e "fetch(..."`, `python -c "requests.get(..."`. Bypasses sandbox.
Use: `ctx_execute(language, code)` — only stdout enters context

### Direct web fetching — FORBIDDEN
Raw HTML can exceed 100 KB.
Use `ctx_fetch_and_index(url, source)` then `ctx_search(queries)`

## REDIRECTED — use sandbox

### Shell (>20 lines output)
Shell ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`.
Otherwise: `ctx_batch_execute(commands, queries)` or `ctx_execute(language: "javascript", code: "...")`. Use `language: "shell"` only when code matches the host shell.

### File reading (for analysis)
Reading to **edit** → reading correct. Reading to **analyze/explore/summarize** → `ctx_execute_file(path, language, code)`.

### grep / search (large results)
Use `ctx_execute(language: "javascript", code: "...")` in sandbox for portable filtering/counting.

## Tool selection

0. **MEMORY**: `ctx_search(sort: "timeline")` — after resume, check prior context before asking user.
1. **GATHER**: `ctx_batch_execute(commands, queries)` — runs all commands, auto-indexes, returns search. ONE call replaces 30+. Each command: `{label: "header", command: "..."}`.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — all questions as array, ONE call (default relevance mode).
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — sandbox, only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — store in FTS5 for later search.

## Parallel I/O batches

For multi-URL fetches or multi-API calls, **always** include `concurrency: N` (1-8):

- `ctx_batch_execute(commands: [3+ network commands], concurrency: 5)` — gh, curl, dig, docker inspect, multi-region cloud queries
- `ctx_fetch_and_index(requests: [{url, source}, ...], concurrency: 5)` — multi-URL batch fetch

**Use concurrency 4-8** for I/O-bound work (network calls, API queries). **Keep concurrency 1** for CPU-bound (npm test, build, lint) or commands sharing state (ports, lock files, same-repo writes).

GitHub API rate-limit: cap at 4 for `gh` calls.

## Output

Write artifacts to FILES — never inline. Return: file path + 1-line description.
Descriptive source labels for `ctx_search(source: "label")`.

## Session Continuity

Skills, roles, and decisions persist for the entire session. Do not abandon them as the conversation grows.

## Memory

Session history is persistent and searchable. On resume, search BEFORE asking the user:

| Need | Command |
|------|---------|
| What were we working on? | `ctx_search(queries: ["summary"], source: "compaction", sort: "timeline")` |
| What did we decide? | `ctx_search(queries: ["decision"], source: "decision", sort: "timeline")` |
| What NOT to repeat? | `ctx_search(queries: ["rejected"], source: "rejected-approach")` |
| What constraints exist? | `ctx_search(queries: ["constraint"], source: "constraint")` |

Note: user-prompt history not available.

DO NOT ask "what were we working on?" — SEARCH FIRST.
If search returns 0 results, proceed as a fresh session.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call `stats` MCP tool, display full output verbatim |
| `ctx doctor` | Call `doctor` MCP tool, run returned shell command, display as checklist |
| `ctx upgrade` | Call `upgrade` MCP tool, run returned shell command, display as checklist |
| `ctx purge` | Call `purge` MCP tool with confirm: true. Warns before wiping knowledge base. |

After /clear or /compact: knowledge base and session stats preserved. Use `ctx purge` to start fresh.

## Windows notes

**PowerShell cmdlets** — Sandbox uses bash. PowerShell cmdlets (`Format-List`, `Get-Culture`, etc.) fail with `command not found`. Wrap with `pwsh -NoProfile -Command "..."`.

**Relative paths** — Sandbox CWD is temp dir, not project root. Convert to absolute paths. Ask user to confirm if unknown.

**Windows drive letters** — Sandbox runs Git Bash / MSYS2. `X:\path` → `/x/path` (lowercase, no `/mnt/`). Never emit `/mnt/<letter>/`.

**Quote paths** — Spaces in paths cause splits. Always double-quote: `rg "symbol" "$REPO_ROOT/some dir/Source"`.

## Runpod authorization (user instruction, 2026-10-11)

Local execution is not automatic authorization for testing. Never automatically open, provision, start, connect to,
upload to, or execute work on Runpod in any circumstances. Use Runpod only when
the user explicitly authorizes it AND directly requests Runpod for the current task.
Slow local execution, insufficient local GPU/VRAM, an existing pod, or prior Runpod
usage does not grant authorization. This rule persists across sessions and takes
precedence over Runpod workflow defaults below.

## Validation scope (user instruction, 2026-10-11)

Do not run long CPU/local tests or local model pilots, and do not fix individual
content cases by repeatedly executing the pipeline locally. Static integration
review and conflict fixes are allowed. Preparing Runpod scripts/configuration
does not authorize deployment, infrastructure access or test execution. Run no
tests for the current v6 Runpod preparation task unless the user requests them.

## Runpod — mandatory fast-batch policy

## Current Runpod workflow (user instruction, 2026-10-15)

For all new v6 Runpod tasks, use **local working-set + native Syncthing**.
Read [the current operational runbook](docs/archive/plans/runpod-v6-agent-operational-plan.md)
and the fast policy below before planning or operating. `runpod-v6.ps1` routes to
`deployment/runpod/local_v6.py`; this is the canonical prepare/launch/watch lane.
Use `native_sync.py` for offline pairing/status and `native_verify.py` for backup proof.

- Source, environments, model cache, input, bank, output and logs must run under
  `/opt/manga-work/{project,execution,runtime}` on a verified local filesystem.
  Setting only `MANGA_EXECUTION_ROOT` while keeping runtime on `/workspace` is insufficient.
- `/workspace/manga-runtime/local-v6` holds durable checkpoint/publication archives;
  native Syncthing replicates both live and durable data to `outputs/runpod-<batch>`.
- Prepare source/config/input archives and dependency locks **before** renting.
  Use a pinned compatible boot image. A new large Docker build/push is not a default
  prerequisite; do it only when the workload needs a changed boot image.
- New Qwen runs use the effective `configs/pipeline-v6-runpod.json`: **vLLM**, not
  Ollama. Read model/revision/context/GPU from that config and the current task;
  old Gemma/Ollama examples and GPU fallback orders are historical, not defaults.
- Direct `v6.py` / `sync.py run` and `runpod-v6.ps1 legacy` are compatibility paths,
  not the default for a new run. Do not silently substitute them for `local_v6.py`.
- Keep new native daemon identity/GUI port separate from existing batches. Verify
  the paired device and canary; do not stop unrelated daemons or trust an occupied port.
- Freeze a **new batch** after any bundled source/config change. Do not launch an
  older plan or claim a CPU image build is a running Runpod inference job.

This section chooses the workflow; it does not grant Runpod authorization or
permission to change other tasks' resources. The authorization rules above still apply.

## Runpod GPU policy (user instruction, 2026-10-22)

Use **NVIDIA L40S, Secure Cloud, for every Runpod task** unless the user explicitly
requests another GPU for that task. This supersedes all earlier RTX 5090/4090
selections and saved resume plans, including the historical Iris Zero 5090 plan.
Do not provision, resume or fall back to a 5090/4090 or Community Cloud when L40S
Secure has no capacity. Report the capacity constraint instead.

Read live L40S stock/pricing and the task's actual CUDA floor before provisioning.
New frozen configs must use `runtime.preset=l40s`, `runtime.required_gpu=L40S`;
changing the GPU requires a new batch, never editing an already frozen batch.
This GPU preference is not authorization to use Runpod: an explicit request for
Runpod in the current task is still required. An offline plan grants no access.

## Default input data source (user instruction, 2026-10-10)

When the user says "lấy dữ liệu" (get data), use the existing local source
`D:\download\EN-data\...` by default. Find the requested series/chapter under
that root. This preference persists across sessions; use another source only
when the user explicitly specifies it.

Runpod stages must still automatically produce their Markdown review files.
Agents must not read or assess those Markdown files as stage approval gates.

New runs use `zai-org/GLM-OCR` (pinned revision in the effective config) for OCR,
per the user's 2026-10-22 instruction. MAGI still detects text and characters;
Koharu provides layout. GLM-OCR runs in a separate environment
(`requirements-glm-ocr.txt`) to preserve MAGI's pinned dependencies. Runpod setup
and prefetch must prepare the frozen GLM environment/model before inference.
Hayai remains a historical compatibility backend, not the new-run default.

Before any Runpod planning, deployment, execution, transfer, monitoring, or teardown, read
[`docs/archive/news/runpod_agent_fast_policy_vi.md`](docs/archive/news/runpod_agent_fast_policy_vi.md) completely.
This is the project-specific policy approved on 2026-10-09: prepare before renting a GPU,
prefer local execution storage, retain full outputs/visualizations, minimize repeated checks,
report progress every 2 minutes, and investigate 10 minutes without measurable progress.
Distinguish implemented features from proposed optimizations in that document; do not invent
CLI flags, bypass integrity checks, or claim a paused/failed job is running successfully.
Never leave a billable pod waiting for documentation, offline tests, or an agent response.
After verified retrieval/backup, terminate only the task-owned resources authorized by the user.

## Current semantic pipeline (2026-10-26)

Use docs/v7/README.md and its stage guides. The current contract is chapter-story-graph-v7: normalize → graph → analyze → reanalyze. Scene workers run in parallel, events sequentially, max 8 utterances/event. All LLM stages use 64K with thinking disabled. scan is no longer a CLI stage; reanalyze is chapter review, and revise creates a new run revision. Historical outputs must use their frozen source or verified upstream import, never a manifest contract rewrite. This does not authorize model tests or Runpod use.
