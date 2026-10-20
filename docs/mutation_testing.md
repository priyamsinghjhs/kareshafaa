# Mutation testing (mutmut 3.8)

Config: `setup.cfg`. Modules mutated: `wasl/clinical_rules.py` and `wasl/statemachine.py`. Tests used for each mutant:
golden clinical cases (with a full-output snapshot), differential test, Hypothesis properties, and both state-machine
test files. Database-backed tests are excluded so the run stays fast. Run: `.venv/bin/mutmut run --max-children 6`
(about 10 minutes of mutation time on this box), then `.venv/bin/mutmut results`.

## Results

| Run (21 Oct 2026, IST) | Mutants | Killed | Survived | No covering test | Kill rate |
|---|---|---|---|---|---|
| 1: `clinical_rules.py`, Iteration 6 tests | 1,440 | 1,028 | 412 | 0 | 71.4 % |
| 2: `clinical_rules.py` + `statemachine.py`, after strengthening (reason codes, explanations, snapshot, differential) | 1,560 | 1,419 | 136 | 5 | **91.0 %** (91.3 % of covered mutants) |
| &nbsp;&nbsp; of which `clinical_rules.py` | 1,494 | 1,377 | 117 | 0 | 92.2 % |
| &nbsp;&nbsp; of which `statemachine.py` | 66 | 42 | 19 | 5 | 63.6 % |

## The 141 mutants not killed in run 2, classified by hand

* **87 change only a display string**: next-step text, error messages, the `GEN-ERR`/`DM-ERR` rule labels on error
  paths. The golden snapshot pins the wording of every golden decision. Error-message wording is not asserted.
* **About 42 are equivalent, or keep the same disposition and tier.** Examples:
  * `_parse_cervical` returning `ok=True` on a contradiction still ends in the `CX-ERR` review;
  * `hpv is None or cyto == "NILM"` is unreachable for any other HPV value;
  * BI-RADS numeric 6 or 7 becomes an urgent review either way;
  * `10**6` sentinels;
  * `ELIGIBLE_AGE.get` defaults that are unreachable for unknown programmes;
  * unit normalisation fallbacks that turn into a more cautious "unknown unit" review.
* **12 were real test gaps.** None was a safety-critical bug in the code; all are now covered by tests added after this run:
  * plausibility-range edges (HbA1c 3.0 %, 20.0 %, 9 and 195 mmol/mol; FPG 10, 1,500 mg/dL and 0.6, 83 mmol/L; FIT 10,000 µg/g);
  * a boolean value read as 1;
  * the 10-character minimum for a closure note (9 vs 10 characters);
  * `allowed_targets()` agreeing with the transition guard (5 mutants that had no covering test).

  These tests were added after run 2, so they are not reflected in the 91.0 % figure.

The earlier deliberate fault-injection run (`docs/fault_injection_results.md`) detected 33 of 33 faults.
