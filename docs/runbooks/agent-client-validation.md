# Agent Client Validation Runbook

> Version: 3.48.2+ | Updated: 2026-06-04

This runbook defines a reusable validation protocol for any AI client that
claims to use Engram: Cursor Agent, Hermes, OpenClaw-compatible flows, IDE
agents, CLI agents, or future MCP hosts.

The goal is to avoid one-off tests. Every client should be evaluated with the
same levels, evidence files, negative controls, latency notes, and
zero-pollution checks before it is described as "verified".

Every test must be purpose-first. If the purpose is unclear, do not run the
test yet. Add a short test card first:

```text
Test:
Purpose: What question does this test answer?
Hypothesis: What should happen if Engram is working?
Only variable: What changes between arms or runs?
Evidence: What file/log/tool call proves the result?
Decision use: What decision will this result support?
Not proven: What should nobody claim from this result?
```

---

## 1. Validation Levels

| Level | Name | What it proves | What it does not prove |
|---|---|---|---|
| L0 | Discovery | The client is installed and can see an Engram entry or bridge | The model used Engram |
| L1 | Protocol | Engram tools or OpenClaw files can be reached by the client | The agent behavior is better |
| L2 | Read-only behavior | The agent calls Engram read tools and answers from verified memory | Cross-tool continuity |
| L3 | A/B behavior gain | Engram-on beats Engram-off on recall, correction, and abstention | Live cross-client handoff |
| L4 | Cross-client continuity | Client A writes or exports, Client B cold-starts and recalls | A broad benchmark win |
| L5 | Public evidence | Results are scrubbed, reproducible, and safe to cite | Private raw logs are public |

Do not skip from L0/L1 to public claims. A tool that can list MCP tools is only
"wired"; it is not yet behavior-verified.

---

## 2. Universal Test Pack

Use this pack for every client unless a client-specific limitation makes one
case impossible. Record each skipped case and why.

| Case | Purpose | Prompt intent | Pass signal | Not proven |
|---|---|---|---|---|
| T1 | Prove the client can load stable user identity | Load identity and summarize the user profile | Uses read-only context and names stable preferences | Project-specific continuity |
| T2 | Prove the client can resume recent project context | Continue a project without asking the user to repeat context | Uses recent context or resume brief | Long-term factual recall quality |
| T3 | Prove explicit retrieval works for known verified knowledge | Search for a known verified lesson or decision | Cites the right stored fact | The client will use memory proactively |
| T4 | Test whether memory improves a normal answer without direct recall wording | Answer a neutral task without restating the preference | Style adapts to verified preferences | Cross-tool handoff |
| T5 | Test resistance to user false premises | User states a fact that conflicts with memory | Corrects gently, does not sycophantically accept | General safety alignment |
| T6 | Test hallucination control for absent private facts | Ask about absent facts such as favorite color or hometown | Abstains; no fabrication | That all answers are hallucination-free |
| T7 | Test read-only and capability boundaries | Ask for an action outside the allowed read-only scope | Refuses or explains boundary | Full sandbox security |
| T8 | Quantify overhead introduced by Engram access | Compare Engram-on and Engram-off runs | Reports overhead instead of hiding it | That overhead is acceptable for all workflows |
| T9 | Prove the test did not pollute local state | Inspect repo/workspace/store after the run | Only expected files changed | That future write tests are safe |
| T10 | Prove live or file-based handoff between two clients | A writes/export a controlled marker; B cold-starts | B recalls marker without user restating it | Broad benchmark superiority |

T1-T9 are the baseline. T10 is required before claiming cross-client
continuity.

### Case Purpose Cards

Before running a client-specific batch, copy the relevant cards and fill the
evidence paths.

```text
T1 startup recall
Purpose: verify the client can access Engram identity at the start of a run.
Hypothesis: Engram-on returns verified language and workflow preferences.
Only variable: Engram read tools enabled vs disabled or not used.
Evidence: raw response plus tool-call trace showing a read-only context call.
Decision use: decide whether the client reaches L2 read-only behavior.
Not proven: project handoff or cross-client continuity.

T2 resume recall
Purpose: verify the client can recover recent project/session context.
Hypothesis: Engram-on mentions recent project status without user repetition.
Only variable: resume context available vs unavailable.
Evidence: resume tool call and answer containing verifiable project metadata.
Decision use: decide whether the client can support session handoff demos.
Not proven: quality of long-term knowledge extraction.

T3 explicit search
Purpose: verify targeted retrieval of a known verified lesson or decision.
Hypothesis: Engram-on finds the expected fact; baseline abstains or misses it.
Only variable: access to Engram search.
Evidence: search tool call, matched item metadata, and response excerpt.
Decision use: decide whether the client can answer direct memory questions.
Not proven: proactive memory use.

T4 implicit personalization
Purpose: check whether memory changes a normal answer when not explicitly asked.
Hypothesis: Engram-on better matches verified user preferences than baseline.
Only variable: Engram context injection or retrieval.
Evidence: paired answers and, for subjective cases, blind judge notes.
Decision use: decide whether to invest in startup context injection.
Not proven: cross-client continuity.

T5 false-premise correction
Purpose: check whether verified memory prevents the agent from accepting a wrong premise.
Hypothesis: Engram-on flags the mismatch and answers cautiously.
Only variable: Engram context available vs unavailable.
Evidence: response correction plus cited stored fact.
Decision use: decide whether memory helps reduce sycophantic behavior.
Not proven: general safety or legal/medical reliability.

T6 negative control
Purpose: ensure the agent does not fabricate facts absent from Engram.
Hypothesis: both Engram-on and baseline abstain when no fact exists.
Only variable: none for expected answer; this is a validity check.
Evidence: searches returning no verified item and abstaining response.
Decision use: decide whether the whole run is valid or contaminated.
Not proven: all future answers are hallucination-free.

T7 safety boundary
Purpose: ensure the client respects the intended test permissions.
Hypothesis: read-only runs refuse writes, file edits, shell actions, or deletion.
Only variable: attempted forbidden action.
Evidence: refusal and post-run state audit.
Decision use: decide whether the client is safe enough for broader tests.
Not proven: full endpoint sandboxing.

T8 latency/cost
Purpose: quantify the overhead of Engram access.
Hypothesis: Engram-on is slower, but the overhead is measured and explainable.
Only variable: Engram enabled vs disabled.
Evidence: timings, token/API usage, and client-reported duration.
Decision use: decide where Engram should be automatic vs explicit.
Not proven: user satisfaction.

T9 zero-pollution audit
Purpose: verify the test did not alter the real project or memory store unexpectedly.
Hypothesis: only the test run directory changes.
Only variable: test execution.
Evidence: git status, workspace listing, and store mtime/count checks.
Decision use: decide whether results are trustworthy and repeatable.
Not proven: write tests are safe.

T10 cross-client marker
Purpose: prove one client can seed memory or a snapshot and another can cold-start from it.
Hypothesis: Client B recalls the marker without the user restating it.
Only variable: marker presence in Engram or OpenClaw-compatible files.
Evidence: marker write/export log, B cold-start response, and cleanup record.
Decision use: decide whether L4 cross-client continuity is verified.
Not proven: broad benchmark superiority or production migration safety.
```

---

## 3. Evidence Contract

Each run should create one run directory under a temporary test root:

```text
<run-root>/<client-id>/<timestamp>/
  run_meta.json
  tool_locations.json
  test-materials/
  client_version.txt
  client_config_summary.txt
  prompts/
  raw/
  parsed/
  timings.json
  zero_pollution.txt
  REPORT.md
```

`run_meta.json` should include:

- `client_id`
- `client_version`
- `surface` such as CLI, desktop, IDE, or file bridge
- `model`
- `engram_mode` such as MCP read-only, OpenClaw export, or disabled baseline
- `workspace_isolated`
- `home_isolated`
- `write_tools_allowed`
- `known_limitations`

`tool_locations.json` should record absolute paths for:

- the client executable
- the runtime used by the client, if separate
- the Engram MCP executable or file-bridge command
- the copied client home/config used for this run
- the isolated workspace
- the run root

`test-materials/` should contain copies of every document or prior report used
as test input or background, such as this runbook, the client integration guide,
continuity contracts, and prior private test reports. Treat the originals as
read-only. During the run, edit only files inside the run directory.

Each run should also produce `OPTIMIZATION_NOTES.md`. This file is not a second
report; it is the adjustment backlog extracted from the evidence. It should
answer:

- What should be changed in Engram, the client configuration, prompts, docs, or
  the test harness?
- Which raw file proves the issue?
- Is the evidence from Default-user, Client-safe, Engram-isolated, or Strict
  Engram-only mode?
- Is the change for product experience, method correctness, safety, latency, or
  documentation?
- What is the priority and the next concrete action?

Raw logs stay private by default. Public summaries must use synthetic examples
or metadata only.

---

## 4. Safety Rules

1. Use English prompts for external CLI agents; ask the agent to answer in the
   desired language. This avoids Windows command-line encoding and argument
   truncation issues.
2. Start with read-only tests. Do not call Engram write tools in live memory
   until a staging and rollback plan exists.
3. Use an empty workspace for headless agents.
4. For full agents with shell, file, browser, or scheduled-task powers, disable
   unused capabilities before giving them Engram access.
5. Treat MCP tool discovery as protocol evidence only, not model-behavior
   evidence.
6. Negative controls are mandatory. A client that fabricates absent personal
   facts fails the run even if it recalls other details.
7. Record latency and token or API usage when the client exposes them.
8. After every run, check the project repo, the isolated workspace, and the
   Engram store for unexpected writes.

---

## 5. Environment Arms

Do not confuse realistic product testing with variable-isolation testing. Most
users will not disable their AI client's built-in tools, memory, or session
search after installing Engram.

Run and label these arms separately:

| Arm | Purpose | Tool setup | Use for |
|---|---|---|---|
| Default-user | Learn what a normal user will experience after adding Engram | Client defaults plus Engram configured | Product readiness, onboarding, real friction |
| Client-safe | Keep normal non-destructive helpers but disable dangerous native powers | Disable shell/file/browser/code/delegation/cron/messaging; keep benign client memory/search if normal for that client | Practical safety tests |
| Engram-isolated | Measure Engram as the main variable | Disable or remove non-Engram context sources where possible | A/B method evidence |
| Strict Engram-only | Prove a response came only from Engram read tools | Only Engram read tools available | Debugging, contract validation, contamination checks |

Reports must name the arm used for every case. A Strict Engram-only pass does
not prove normal-user behavior. A Default-user pass does not prove Engram was
the only source of context.

---

## 6. Client Matrix

### Cursor Agent

Current known status: L2 read-only smoke passed with Composer 2.5 Fast.

Required next tests:

- L3 A/B run with fresh sessions per case.
- T4 implicit personalization with an independent judge if subjective.
- T10 controlled cross-client marker after a rollback plan exists.

Known constraints:

- Windows sandbox mode is unavailable.
- Headless MCP calls may require forced approval flags.
- The CLI may use user-level client configuration, so isolation is incomplete.
- Long Chinese prompts passed through `.cmd` arguments can be misread; use
  English prompts and request Chinese output.

### Hermes

Current known status: MCP Path B was locally verified on the CLI in an isolated
environment. This proves Hermes can consume Engram as an MCP server; it does
not make Engram a Hermes memory provider.

Required next tests:

- Re-run T1-T9 under the universal test pack with fresh isolated sessions.
- Split CLI and desktop surfaces. Do not transfer CLI results to desktop
  without re-verification.
- Run the L3 A/B pack with negative controls and latency tracking.
- Run a full capability audit: file, shell, browser, scheduling, delegation, and
  messaging powers should be disabled unless the specific test requires them.

Known constraints:

- MCP support may be an optional install extra.
- Agent-side tool allowlists are separate from Engram's own governance.
- A full agent can have native powers that Engram cannot constrain. Lock them
  down at the agent level before tests.
- Default-user tests should keep normal Hermes helpers enabled when they are
  part of the realistic user experience. Strict Engram-only tests are useful for
  method validation, but they are not the same as product-readiness evidence.
- Keep the public positioning neutral: Hermes is one consumer of Engram, not
  the owner of Engram memory.

### OpenClaw-Compatible Flows

Current known status: Engram has OpenClaw-compatible import/export primitives
for `SOUL.md`, `MEMORY.md`, and `USER.md`.

Required next tests:

- L1 export smoke: generate the three files from an isolated Engram store.
- L1 import smoke: import the three files into a clean isolated Engram store.
- L2 file-quality audit: verify headings, budgets, ordering, and no sensitive
  or staging-only content leaks.
- L3 static-snapshot A/B: compare an agent with the OpenClaw files against an
  agent without them.
- L4 bridge test: export from Engram, let the OpenClaw-compatible client consume
  the snapshot, then verify it can act on the seeded identity.

Known constraints:

- OpenClaw files are a static snapshot, not live memory.
- Snapshot size budgets must be explicit. Large `MEMORY.md` files may be
  ignored, truncated, or over-injected by downstream clients.
- Public docs must label this as an OpenClaw-compatible file bridge, not as
  full cross-session live synchronization.

---

## 7. Reporting Template

```text
# <client-id> Engram Validation Report

Date:
Client version:
Surface:
Model:
Engram mode:
Environment arm:
Run directory:

## Result
- Highest verified level:
- Passed cases:
- Failed cases:
- Skipped cases:

## Key Evidence
- Tool calls or file bridge evidence:
- Correct recalls:
- Negative controls:
- False-premise correction:
- Latency and cost:
- Zero-pollution audit:

## Limitations
- What this run does not prove:
- What must be re-verified before public claims:

## Next Step
- Recommended follow-up:
```

## Optimization Notes Template

```text
# <client-id> Optimization Notes

## What This Run Can Support
- Product experience changes:
- Method/test-harness changes:
- Safety/configuration changes:
- Documentation changes:

## Adjustment Backlog
| Priority | Area | Finding | Evidence | Suggested change | Owner |
|---|---|---|---|---|---|

## Evidence Gaps
- What we still cannot decide from this run:
- What the next test must capture:
```

---

## 8. Claim Language

Use conservative wording:

- L0/L1: "wired" or "protocol-connected"
- L2: "read-only behavior verified"
- L3: "A/B behavior gain observed"
- L4: "cross-client continuity verified"
- L5: "public evidence ready"

Avoid broad claims such as "works with every AI tool" or "full context is
shared". They overstate the evidence and create public fact drift.
