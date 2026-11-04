# KareShafaa handover package – Challenge 05

Everything needed to understand, run, evaluate and pilot KareShafaa. All data is synthetic.
Printable version: `KareShafaa_Handover_Package.pdf` (also `.docx`).

| # | Part | File |
|---|---|---|
| 1 | Problem brief | [problem_brief.md](problem_brief.md) |
| 2 | Runnable project (setup, env vars, one-command example) | [runnable_project.md](runnable_project.md), `scripts/handover_example.sh` |
| 3 | How to run the task (input / output agreement) | [how_to_run_the_task.md](how_to_run_the_task.md), [examples/](examples/) |
| 4 | Test results | [test_results.md](test_results.md) |
| 5 | Pilot plan | [pilot_plan.md](pilot_plan.md) (condensed from `docs/pilot_plan.md`) |
| 6 | Handover session | [handover_session.md](handover_session.md) |

## Six-part checklist

- [x] **1. Problem brief** – users, task, scope in/out, intended outcome → `problem_brief.md`
- [x] **2. Runnable project** – clean-clone setup (SQLite and Docker/Postgres), all env vars with secrets named
      only, repeatable one-command example → `runnable_project.md`, `scripts/handover_example.sh`,
      `scripts/handover_example.py`
- [x] **3. How to run the task** – inputs (HL7 ORU, FHIR) with fields; outputs (alerts, tiers, SLA timers, FHIR,
      audit); sample inputs and the example run record → `how_to_run_the_task.md`,
      `examples/input_oru_r01_fit_positive.hl7`, `examples/input_fhir_bundle_hba1c.json`,
      `examples/example_run_record.md`, `examples/example_run_record.json`, `examples/example_output_fhir_bundle_case.json`
- [x] **4. Test results** – test cases and counts, baseline comparisons, errors found, known limits → `test_results.md`
- [x] **5. Pilot plan** – participants, success criteria, review lead role, review dates → `pilot_plan.md`
- [x] **6. Handover session** – 60-minute agenda, results review, walkthrough, Q&A, decisions, next steps,
      sign-off table → `handover_session.md`
