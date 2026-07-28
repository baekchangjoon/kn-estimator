# kn-estimator

> 🌐 [한국어](README.md) · **English** (current)

<p align="center">
  <a href="https://github.com/baekchangjoon/kn-estimator/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/baekchangjoon/kn-estimator/actions/workflows/tests.yml/badge.svg"></a>
  <a href="https://github.com/baekchangjoon/kn-estimator/releases"><img alt="release" src="https://img.shields.io/github/v/release/baekchangjoon/kn-estimator?display_name=release&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-blue">
  <img alt="Dependencies" src="https://img.shields.io/badge/dependencies-stdlib%20only-informational">
  <img alt="LLM calls" src="https://img.shields.io/badge/LLM%20calls-0-success">
</p>

**Before you start generating black-box API tests for a Spring backend with an
LLM**, kn-estimator statically scans the target project (no LLM calls, a few
seconds) and computes the **expected cost/time and the cost-optimal generation
batches (chunk plan)** — how many endpoints per batch, and which endpoints
belong together, so that a single session's quadratic (N²) cost becomes linear.

```
single session:  cost(N) = a + b·N + c·N²        ← quadratic term from context accumulation (δ·τ)
chunked runs:    cost(N) ≈ N × g(K),  g(K) = a/K + b + c·K   ← linear once K is kept inside the walls
                 K*_cost = √(a/c)     K*_wall = (W_soft − S0 − δ_env) / δ_ep
```

## Install

Python is not required — the recommended path is [uv](https://docs.astral.sh/uv/)
(uv downloads and manages the CPython it needs):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # one-time uv install
uvx --from git+https://github.com/baekchangjoon/kn-estimator kn-estimate <spring-project> --groups
```

**Homebrew** (macOS/Linux):

```bash
brew install baekchangjoon/tap/kn-estimator
```

**Docker** (GHCR — mount the target project as a volume):

```bash
docker run --rm -v "$PWD:/w" ghcr.io/baekchangjoon/kn-estimator /w --groups
```

> `/w` in the output is the in-container mount path — artifacts land in `$PWD/.kn` on the host.

**pip** (if you already have Python 3.9+):

```bash
pip install git+https://github.com/baekchangjoon/kn-estimator
```

Standard library only — no extra dependencies. For development:

```bash
git clone https://github.com/baekchangjoon/kn-estimator && cd kn-estimator
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
```

## Quick start

```bash
kn-estimate <spring-project-root> --groups
```

Example (actual output; the group lines are in Korean):

```text
N=18 chunks=3 k_avg=6.0 est=$21.18

[spring-petclinic] cost-optimal generation batches (template×sonnet):
  group1(POST /api/reservations, GET /api/reservations/{id}, …) — $6.93, peak 295,105
  group2(GET /api/pets/types, GET /api/pets/{petId}, …) — $7.08, peak 301,767
  group3(GET /api/owners, GET /api/owners/{ownerId}, …) — $7.17, peak 305,431
Run each of the 3 groups in a fresh independent session — continuing one session
brings the quadratic cost back. Expected total $21.18, interval $15~$28.
ℹ No project-specific calibration — estimated with the bundled (tainted-spring-auth-user) coefficients …
```

Run each group as one fresh session and the spend stays linear in the number of
endpoints N. Artifacts written alongside:

| Artifact | Contents |
|---|---|
| `<root>/.kn/kn-report.md` | Human report — N and w distribution, recommended plan, prediction interval, cost curve (a,b,c), K\*, cell (label×model) matrix, per-controller table, stated limits |
| `<root>/.kn/kn-plan.json` | Machine plan — per-chunk endpoints, estimated cost, peak context, `cost_curve`, `controllers` |

## Idea

Processing endpoints sequentially in a single LLM session grows two things at
once — the number of turns (∝N) and the context each turn re-reads (accumulated
residue, ∝N). Total read cost is their product, hence ∝N². Splitting the
endpoints into independent sessions of K keeps every session on the still-cheap
head of the quadratic curve, so the total becomes linear in N; the slope g(K)
is U-shaped, so an optimal K exists.

Before generation starts, kn-estimator:

1. **Statically scans** the project for N (JSON endpoints) and per-endpoint
   workload w_i (handler span + dependency slice + MyBatis XML / JPA entities),
2. **Simulates chunks turn by turn** with measured calibration coefficients
   (S0/τ/δ/out per cell = label×model),
3. Produces a **controller-affine bin-packing chunk plan** bounded by whichever
   binds first: the cost optimum or the context wall.

```
cost = P_cache_read·Σ(τ_i·C) + P_cache_write·(S0 + Σδ_i) + P_out·Σout_i
```

w_i enters only as a multiplicative covariate (ŵ^α) on δ/out/τ — never as
absolute tokens — so the absolute cost level comes entirely from the
calibration coefficients (scaling all w by a constant changes nothing).
Derivation and measurements: [docs/cost-model-explained.md](docs/cost-model-explained.md) (Korean).

## CLI options

```bash
kn-estimate <project_root> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--label <name>` | template | Task label — the first half of the cell name (free string; bundled labels: `template`\|`flat`) |
| `--model opus\|sonnet\|haiku` | sonnet | Target model (uncalibrated cells are reported as `insufficient_calibration`) |
| `--groups` | off | Print the cost-optimal batches as runnable "groupN(EP, …)" instructions |
| `--calibration <path\|name>` | bundled (auth-user) | Calibration file path or a bundled name (`petclinic`\|`community`\|`auth-user`) |
| `--w-soft <n>` | 330000 | Quality-policy wall (penalty + warning above it; capped at the effective W_hard) |
| `--w-hard <n>` | 900000 | Model ceiling wall (auto-capped at 0.9× the model window — 180K for haiku) |
| `--conservative` | off | W_soft=250K preset |
| `--parallel` | off | Assume parallel chunk execution (wall-clock = max, 5% cache-write surcharge) |
| `--out-dir <path>` | `.kn` | Output directory — relative to the project root, or absolute |

## Calibration

The bundled calibration (17 measured runs on tainted-spring-auth-user, 3 cells —
opus not measured) works out of the box, and petclinic/tainted-spring-community
calibrations ship alongside, selectable by name (`--calibration petclinic`). It is still a single-project
measurement, so **absolute USD is not guaranteed** — across 54 runs on three other projects, bundled coefficients
were off by −23~−34%, and a pilot recalibration brought the error within ±10%.
When run without `--calibration`, the CLI states this and prints the pilot
procedure:

```bash
# 1) Measure >=2 groups of DIFFERENT sizes in the same label×model cell
#    (e.g. a 1-endpoint group + the smallest planned group)
# 2) Recompute project-specific coefficients from the measured ledger
kn-calibrate --ledger run_ledger.jsonl --runs runs/ --out my-cal.json
# 3) Re-derive --w-soft from the context distribution of gate-passing sessions, rerun
kn-estimate <root> --calibration my-cal.json --w-soft <re-derived>
```

Who produces the ledger/transcripts and how (agent prompt template, adapters
for other agents, recording snippet): **[docs/CALIBRATION.md](docs/CALIBRATION.md)** (Korean).
Ledger schema summary: [docs/GUIDE.md](docs/GUIDE.md) §4.4 (Korean).

## Layout

```
src/kn_estimator/
  endpoints.py           # endpoint inventory (N) — @RestController/@ResponseBody scan
  scan.py                # per-endpoint workload slices (w_i) — DI-graph BFS, MyBatis/JPA joins
  model.py               # chunk cost simulation (turn-level; cache read/write/output split)
  plan.py                # controller-affine FFD partition, two-tier walls, K*, cost curve
  calibrate.py           # measured ledger → per-cell coefficients (kn-calibrate)
  cli.py                 # kn-estimate — report, plan, matrix, groups output
  data/calibration*.json # bundled calibrations (campaign — default auth-user + petclinic/community)
docs/                    # guide, derivations, design, measurement campaign, research notes
results/                 # calibration ledger + transcripts (raw data for reproduction)
research/                # verification scripts (w covariate, per-unit coefficient tests)
tests/                   # pytest — 93 tests without the SUT, +13 with it
```

## Tests

```bash
.venv/bin/python -m pytest tests/
```

Tests that depend on the SUT (a petclinic fork) or an external sample are
skipped when absent (paths configurable via `KN_SUT`, `KN_EXTERNAL_SAMPLE`,
`KN_LEDGER`/`KN_RUNS`). The remaining 93 are environment-independent and run in
CI (GitHub Actions) on every push and PR.

## Documentation (Korean)

| Document | Contents |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | How it works, pipeline, CLI, pilot-calibration workflow |
| [docs/CALIBRATION.md](docs/CALIBRATION.md) | Calibration in practice — who produces the ledger/transcripts, agent prompt template, adapters for other agents |
| [docs/cost-model-explained.md](docs/cost-model-explained.md) | Why quadratic, why chunking makes it linear, why K matters |
| [docs/2026-07-16-kn-estimator-overview.md](docs/2026-07-16-kn-estimator-overview.md) | Background, model, status, improvements |
| [docs/2026-07-20-multi-project-calibration-campaign.md](docs/2026-07-20-multi-project-calibration-campaign.md) | 54-run measurement campaign on 3 projects — coefficient transfer, mode inversion, pilot-loop validation |
| [docs/2026-07-26-cost-curve-and-unit-coefficients.md](docs/2026-07-26-cost-curve-and-unit-coefficients.md) | Deriving a,b,c and testing per-unit coefficient differentiation |

## Limits

- **Absolute USD is not guaranteed** — the primary use is relative comparison
  across labels/models/chunkings, and the chunk plan itself.
- Prediction intervals combine α sensitivity (narrow band) with between-run
  variance (measured ±30~46%) — read the interval, not the point estimate.
- Cells with fewer than 2 calibration runs report
  `insufficient_calibration (reason)` instead of a number.
- Token counts are a bytes/4 approximation. Static slicing can underestimate
  reflection, dynamic routing, and configuration-driven beans.
- Workload w reflects code size only — branch counts and complexity are not
  modelled (background: [overview §4](docs/2026-07-16-kn-estimator-overview.md)).

## License

[MIT](LICENSE)
