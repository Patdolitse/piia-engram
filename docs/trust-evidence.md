# Trust evidence

This page shows how you can check the claims in piia-engram's trust and
privacy docs yourself, using public files, deterministic scripts, and local
commands. It complements [Trust model](trust.md), [Privacy](../PRIVACY.md), and
[Security](../SECURITY.md). It does not add new promises.

## How to read this page

Each row below has three parts:

- **Claim** — the public statement a reader may want to verify.
- **Evidence** — the file, test, or guard that keeps the claim grounded.
- **Check it yourself** — a command or local inspection path you can run.

The boundary column matters. A narrow claim that can be checked is more useful
than a broad claim that sounds impressive.

## Claim to evidence

| Claim | Evidence | Check it yourself | Boundary |
|---|---|---|---|
| Identity and knowledge tools use local files and make zero network calls by default | [PRIVACY.md](../PRIVACY.md), [SECURITY.md](../SECURITY.md), `src/piia_engram/telemetry.py` opt-in defaults | `python scripts/check_public_trust_claims.py` | The optional `read_web_content` tool can fetch a URL only when explicitly invoked. Remote telemetry and feedback are separate opt-ins. |
| Telemetry is off by default and count-only when enabled | [docs/telemetry-privacy.md](telemetry-privacy.md), `src/piia_engram/telemetry.py`, `src/piia_engram/telemetry_validation.py` | `engram telemetry preview` and `pytest tests/test_telemetry*.py -q` | This is a client/worker contract, not a third-party privacy audit. |
| Local data is user-owned plain JSON/Markdown by default | [PRIVACY.md](../PRIVACY.md), [docs/trust.md](trust.md), storage layout in [docs/architecture.md](architecture.md) | Open files under your selected Engram data folder such as `~/.engram/knowledge/` | Local processes with filesystem access can read plaintext files. Optional encryption is field-level only. |
| Optional encryption is field-level, not whole-store encryption | [SECURITY.md](../SECURITY.md), [PRIVACY.md](../PRIVACY.md), `src/piia_engram/crypto.py` | Install `piia-engram[secure]`, set `ENGRAM_SECRET`, and inspect encrypted supported profile fields | It is off unless configured and does not replace disk encryption or a secrets manager. |
| Public numbers should not drift silently | [docs/public-facts.json](public-facts.json), `scripts/check_public_fact_sync.py`, `scripts/check_public_claim_drift.py` | `python scripts/check_public_fact_sync.py` and `python scripts/check_public_claim_drift.py` | Historical files such as CHANGELOG and `release-evidence/` keep old release facts on purpose. |
| Security/privacy wording should stay consistent | `scripts/check_public_trust_claims.py` | `python scripts/check_public_trust_claims.py` | This catches contradictory public prose. It is not a security audit. |
| Memory retrieval quality has a deterministic regression floor | [docs/benchmarks/memory-eval-suite-v1.md](benchmarks/memory-eval-suite-v1.md), `scripts/run_memory_evals.py` | `python scripts/run_memory_evals.py --json` | This is a synthetic regression suite, not a live-agent benchmark and not a competitor comparison. |
| Releases cannot skip evidence | [release evidence index](../release-evidence/README.md), `scripts/check_release_gate.py` | `python scripts/check_release_gate.py` | Evidence files are factual summaries of public-safe release checks, not private review logs. |
| Release artifacts carry build provenance attestations | [docs/supply-chain.md](supply-chain.md), `.github/workflows/publish.yml` | `gh attestation verify piia_engram-<version>-py3-none-any.whl --repo Patdolitse/piia-engram` | This proves the artifact came from this repository's release workflow for that build. It does not prove vulnerability-free code, vulnerability-free dependencies, a third-party audit, or reproducible builds. |
| Release artifacts carry SBOM attestations | [docs/supply-chain.md](supply-chain.md), `.github/workflows/publish.yml`, `scripts/check_sbom_hygiene.py` | `gh attestation verify piia_engram-<version>-py3-none-any.whl --repo Patdolitse/piia-engram --predicate-type https://cyclonedx.org/bom` | This proves an SBOM predicate is attached to the artifact. The workflow artifact copy is for inspection and is not a permanent release asset. |

## Run the checks yourself

From the repository root:

```bash
python scripts/check_public_fact_sync.py
python scripts/check_public_claim_drift.py
python scripts/check_public_trust_claims.py
python scripts/run_memory_evals.py --json
python scripts/check_release_gate.py
```

On Windows, if `python` is the Microsoft Store placeholder, use your project
virtualenv or the full path to the Python runtime that installed piia-engram.

## What this evidence does not prove

- It is **not a security audit** or penetration test.
- It is **not a live-agent benchmark**. The memory eval suite checks the local
  memory layer with synthetic public-safe fixtures.
- It does not prove downstream models will always follow recalled context.
- It does not prove piia-engram is safer than Mem0, Letta, Zep, or another
  memory system.
- It does not make plaintext local files secret from other local processes.
- It does not make optional field-level encryption into whole-store encryption.

## Reporting an inaccuracy

If a public claim, command, or link on this page drifts, please open an issue or
follow the contact path in [SECURITY.md](../SECURITY.md) for sensitive reports.
The intent is that public trust claims are easy to challenge and easy to fix.
