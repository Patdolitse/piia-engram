# Honest comparison: piia-engram vs mem0 / Basic Memory / ByteRover

> Every claim about a competitor below is dated and footnoted to **their own** public docs (see
> [Sources](#sources-primary-accessed-2026-06-08)). Competitor features change — verify against their
> latest documentation before citing. For the broader category map, see [comparison.md](comparison.md).
>
> Chinese version: [honest-comparison.zh-CN.md](honest-comparison.zh-CN.md)

---

## Why these three

mem0 (incl. its OpenMemory component), Basic Memory, and ByteRover are the projects users most often
put in the same sentence as piia-engram, because all four say some version of "your AI keeps
forgetting you — we remember." That shared one-liner is exactly why "memory" is a red ocean and why
we do **not** lead with it. This piece is about the one axis where we are actually different.

> Note (2026-06-08): mem0's standalone **OpenMemory** project is being sunset — its repo README now
> directs users to the mem0 self-hosted server for local, dashboarded memory. We keep mem0 as the
> primary comparison and treat OpenMemory only as historical context.

We are **not** claiming to beat them at what they are built for. mem0 is a far stronger agent-memory
engine; Basic Memory is a lovely Zettelkasten brain; ByteRover has real traction as a portable
coding-memory layer. The honest question is not "who remembers more" — it's **"who lets you govern
what gets remembered."**

---

## The one wedge: governance you can actually see and reverse

piia-engram's structural difference is a **governance model baked into the data layer**, not a
feature bolted on top:

1. **Risk-gated staging → verified.** AI-proposed knowledge passes through a risk gate. Low/medium-risk
   items are auto-verified *with an audit entry*; high-risk items (credentials, shell commands, MCP
   config, permission rules) — plus all unsupervised background writeback and LLM-extracted
   suggestions — are held in `staging` for your approval and cannot self-label as `verified`.
2. **Identity + decision chains.** Decisions can be superseded by newer decisions while the full
   history is preserved (you can see *why* you changed your mind, not just the current answer).
3. **Field-level encryption at rest.** Sensitive fields (e.g. email, phone) are AES-256-GCM
   encrypted on disk.
4. **Everything is visible, editable, reversible, and auditable** — and a planned opt-in strict mode
   (`ENGRAM_APPROVAL=strict`, roadmap) can route *all* writes through review for users who want the
   maximum-control posture by default.

> **Honesty note (load-bearing):** our default is **not** "nothing is stored until you approve it."
> Low/medium-risk memories are auto-verified. The honest claim is *capability* — you can see, edit,
> override, and roll back anything; high-risk and unsupervised writes are gated; nothing is a black
> box. The blanket "only what you approve" claim is an overclaim we have deliberately removed from
> all public copy.

Among the three competitors surveyed, **none documents this full set** in their public docs (as of
2026-06-08) — risk-tiered approval **and** identity/decision chains **and** field-level encryption in
the local/open tier. (ByteRover documents AES-256 at rest, but only for its enterprise/cloud tier and
without an approval/audit model; see the head-to-head below.) That gap is the whole pitch.

---

## Head-to-head

### vs mem0 / OpenMemory

| | mem0 / OpenMemory | piia-engram |
|---|---|---|
| Primary job | Agent memory: store & recall what the agent did | Identity layer: store who *you* are, across tools |
| Storage | Vector DB. Library default is local Qdrant (`/tmp/qdrant`); self-hosted server default is Postgres + pgvector; the docs quickstart steers new users to the managed cloud (account sign-up) ¹ | Local JSON files you own |
| Capture | Strong automatic capture | AI proposes; risk-gated into staging/verified |
| Governance | No documented risk-tiered staging→verified review of AI-proposed memories, as of 2026-06-08 ² | Risk-gated staging→verified + audit log |
| Encryption at rest | No documented at-rest encryption, as of 2026-06-08 ³ | Field-level AES-256-GCM |
| Best when | One (or many) agents need rich recall over lots of history | You want your identity/standards to follow you across Claude Code / Codex / Cursor / Windsurf |

**Where mem0 wins:** scale, semantic recall, ecosystem, benchmark recall numbers. If you need a big
document/conversation corpus with strong retrieval, mem0 is the better tool — pair it with us rather
than replace it.

**Our honest edge:** the governed, user-owned identity layer above the tools, not the agent's
working memory inside one tool.

### vs Basic Memory

| | Basic Memory | piia-engram |
|---|---|---|
| Primary job | Markdown + knowledge-graph "second brain" (Zettelkasten) | Cross-tool personal identity for AI coding tools |
| Storage | Local Markdown + SQLite-backed KG (cloud tier: Neon Postgres + Tigris S3) ⁴ | Local JSON (structured: profile/lessons/decisions/playbooks) |
| Governance | No documented risk-tiered approval / staging / audit model for writes, as of 2026-06-08; tools carry read-only/destructive hints for the agent, and "audit logs" appear only as a Teams-plan line item without documented implementation ⁵ | Risk-gated staging→verified + audit log |
| Encryption at rest | No documented at-rest encryption, as of 2026-06-08; local storage is described as "plain text on your disk" ⁶ | Field-level AES-256-GCM |
| Shape | Note-taking knowledge base | Identity store tuned for AI cold-start |
| Best when | You want a durable personal wiki/notes graph | You want AI tools to start from the same approved you |

**Where Basic Memory wins:** it's a genuinely nice human-first notes system with graph linking. If
your goal is a personal wiki, it's a better fit than us.

**Our honest edge:** we're built for the *AI-cold-start* job (the AI reads a curated identity at
session start and writes back under governance), not human note browsing.

### vs ByteRover

| | ByteRover | piia-engram |
|---|---|---|
| Primary job | Local-first portable context layer for coding agents ⁷ | Governed personal identity layer for coding tools |
| Narrative | "Local-first AI context engineering for coding agents" ⁷ | "Memory you can govern, portable across tools" |
| Retrieval | Hierarchical file-search (claims 92.2% accuracy, non-vector) ⁷ | Deterministic n-gram + alias (offline, CJK-friendly) |
| Governance | No documented risk-tiered approval / staging / audit model, as of 2026-06-08 ⁸ | Risk-gated staging→verified + decision chains |
| Encryption at rest | Enterprise/Cloud tier documents AES-256 at rest + SOC 2 Type II; for the standard local tier only credential storage is documented as encrypted (`~/.local/share/brv/`), with no documented at-rest encryption of the memory data itself, as of 2026-06-08 ⁹ | Field-level AES-256-GCM in the local/open tier |
| Best when | You want low-friction portable coding memory | You want portability *plus* control/auditability |

**Where ByteRover wins:** traction and a clean local-first portable-context story; lower setup
friction — install is a single `curl … install.sh | sh` or `npm install -g byterover-cli`, then a
3-step quickstart ¹⁰.

**Our honest edge:** portability is table stakes for both of us, and ByteRover is also local-first
and non-vector — so we do **not** claim portability or "local-first" as our wedge against it. Our
difference is the governance layer (risk-gated approval, audit trail, decision history) plus
field-level encryption *in the local/open tier* — ByteRover documents at-rest encryption only for
its enterprise/cloud tier, not the standard local tier (as of 2026-06-08).

---

## Where we are weaker (say it plainly)

Mirrors [comparison.md](comparison.md) §"Where competitors are stronger" — repeated here so this
piece never reads as a hit job:

- **Install friction:** `pip install` + MCP config (two one-time steps) vs competitors' one-liners
  (ByteRover `curl … | sh` or `npm install -g`; Basic Memory `uv tool install` / brew).
- **Semantic recall:** we use deterministic character n-gram + alias tokenization (offline,
  CJK-friendly), not vector embeddings — mem0 (vector-DB) and ByteRover (which claims 92.2%
  file-search retrieval) both publish stronger recall numbers than we target.
- **Ecosystem scale:** we're a small, focused project; mem0's ecosystem dwarfs ours.
- **No GUI dashboard:** CLI + generated HTML review page only.
- **Benchmark narrative:** we optimize governance metrics (approval precision, conflict rate,
  stale-decay accuracy), not LongMemEval-style recall scores.

---

## When to use which (the honest decision guide)

- **One tool, rich recall over lots of history** → mem0.
- **Personal Markdown wiki / second brain** → Basic Memory.
- **Low-friction, local-first portable coding memory, control not a priority** → ByteRover.
- **2+ AI coding tools, and you want your identity/standards to follow you *under governance you can
  see and reverse*** → piia-engram. And you can run us *alongside* any of the above.

> **Disclaimer:** competitor features change. The claims above are sourced from each project's own
> public docs as of 2026-06-08 (see Sources). Verify against their latest documentation before citing.

---

## Sources (primary, accessed 2026-06-08)

All competitor claims above are footnoted to the project's **own** docs/repo. Re-verify before any
public use — competitor docs change.

1. mem0 storage defaults — docs.mem0.ai/components/vectordbs/config (library default: local Qdrant,
   `/tmp/qdrant`); docs.mem0.ai/open-source/overview (self-hosted: Postgres + pgvector);
   docs.mem0.ai/platform/quickstart (managed cloud, account sign-up).
2. mem0 governance — no documented risk-tiered staging/approval review found at docs.mem0.ai and
   github.com/mem0ai/mem0 (2026-06-08).
3. mem0 encryption — no documented at-rest encryption found at docs.mem0.ai (docs.mem0.ai/security
   returned 404) (2026-06-08).
   *OpenMemory sunset:* github.com/mem0ai/mem0/tree/main/openmemory README (2026-06-08).
4. Basic Memory storage — github.com/basicmachines-co/basic-memory README (local Markdown +
   SQLite-backed KG; cloud: Neon Postgres + Tigris S3) (2026-06-08).
5. Basic Memory governance — no documented risk-tiered approval/staging/audit for writes found at
   github.com/basicmachines-co/basic-memory and basicmemory.com; "audit logs" appear as a Teams-plan
   line item only (2026-06-08).
6. Basic Memory encryption — no documented at-rest encryption found; local storage described as
   "plain text on your disk" (github.com/basicmachines-co/basic-memory, docs.basicmemory.com)
   (2026-06-08).
7. ByteRover positioning + retrieval — byterover.dev, docs.byterover.dev ("Local-first AI context
   engineering for coding agents"; hierarchical file-search retrieval, 92.2% accuracy claim)
   (2026-06-08).
8. ByteRover governance — no documented approval/staging/audit model found at docs.byterover.dev and
   byterover.dev (2026-06-08).
9. ByteRover encryption — byterover.dev (enterprise/cloud: AES-256 at rest + TLS 1.2+ + SOC 2 Type
   II); docs.byterover.dev/quickstart (standard tier: only credential storage at `~/.local/share/brv/`
   documented as encrypted; no documented at-rest encryption of memory data) (2026-06-08).
10. ByteRover install — docs.byterover.dev quickstart (`curl -fsSL https://byterover.dev/install.sh |
    sh` or `npm install -g byterover-cli`; 3-step setup) (2026-06-08).
