# Round 19: fresh-cohort re-check of the language feature choice (round 18)

Generated from `docs/ml_feature_revalidation.json` (`scripts/ml_feature_revalidation.py`, offline; nothing promoted). Every cohort was seeded with a seed never used before (scratch DBs under `/workspace/fhdic/r19_fresh/`). Production gates, tolerances, minimum group sizes and the label are unchanged. Recalibration = production default `none`. Hold-out = most recent 25 % of labelled cases. Feature sets: `onehot` = one-hot language (before round 18), `none` = no language information, `barrier` = `language_unsupported` (production since round 18).

| Cohort | Algorithm | Set | n train / hold-out | AUC | ECE | Slope | CITL | Language recall gap | Gates failed |
|---|---|---|---|---|---|---|---|---|---|
| fresh_demo_seed20261919 | logreg | onehot | 1,380 / 461 | 0.6755 | 0.0262 | 1.168 | 0.0123 | 0.0103 | fairness_recall:nationality_group |
| fresh_demo_seed20261919 | logreg | none | 1,380 / 461 | 0.6951 | 0.0254 | 1.257 | 0.0126 | 0.0497 | none |
| fresh_demo_seed20261919 | logreg | barrier | 1,380 / 461 | 0.6807 | 0.0323 | 1.19 | 0.0128 | 0.0103 | fairness_recall:nationality_group |
| fresh_demo_seed20261919 | gbm | onehot | 1,380 / 461 | 0.6716 | 0.0315 | 1.029 | 0.013 | 0.1201 | none |
| fresh_demo_seed20261919 | gbm | none | 1,380 / 461 | 0.6823 | 0.0284 | 1.155 | 0.0108 | 0.1719 | none |
| fresh_demo_seed20261919 | gbm | barrier | 1,380 / 461 | 0.6648 | 0.0323 | 1.059 | 0.0101 | 0.029 | fairness_recall:nationality_group |
| fresh_10k_seed20261929 | logreg | onehot | 6,905 / 2,302 | 0.6961 | 0.0378 | 1.016 | 0.0314 | 0.4815 | fairness_recall:nationality_group |
| fresh_10k_seed20261929 | logreg | none | 6,905 / 2,302 | 0.6836 | 0.0393 | 1.01 | 0.0319 | 0.2087 | none |
| fresh_10k_seed20261929 | logreg | barrier | 6,905 / 2,302 | 0.6931 | 0.0377 | 1.008 | 0.0318 | 0.4815 | fairness_recall:nationality_group |
| fresh_10k_seed20261929 | gbm | onehot | 6,905 / 2,302 | 0.6911 | 0.0416 | 1.015 | 0.0322 | 0.3981 | none |
| fresh_10k_seed20261929 | gbm | none | 6,905 / 2,302 | 0.6789 | 0.038 | 0.986 | 0.0325 | 0.2087 | none |
| fresh_10k_seed20261929 | gbm | barrier | 6,905 / 2,302 | 0.6934 | 0.0457 | 0.998 | 0.0329 | 0.4537 | fairness_recall:nationality_group |
| fresh_20k_realistic_seed20261939 | logreg | onehot | 4,039 / 1,347 | 0.7027 | 0.0368 | 1.028 | 0.0234 | 0.4941 | none |
| fresh_20k_realistic_seed20261939 | logreg | none | 4,039 / 1,347 | 0.6975 | 0.0359 | 1.065 | 0.0254 | 0.2701 | none |
| fresh_20k_realistic_seed20261939 | logreg | barrier | 4,039 / 1,347 | 0.702 | 0.0373 | 1.033 | 0.0241 | 0.5237 | none |
| fresh_20k_realistic_seed20261939 | gbm | onehot | 4,039 / 1,347 | 0.7061 | 0.0332 | 1.027 | 0.0249 | 0.4941 | none |
| fresh_20k_realistic_seed20261939 | gbm | none | 4,039 / 1,347 | 0.701 | 0.04 | 1.033 | 0.0269 | 0.3005 | none |
| fresh_20k_realistic_seed20261939 | gbm | barrier | 4,039 / 1,347 | 0.6988 | 0.035 | 0.987 | 0.0257 | 0.5168 | none |
| fresh2_demo_seed20261949 | logreg | onehot | 1,380 / 461 | 0.6232 | 0.0663 | 0.632 | 0.0444 | 0.5192 | calibration_slope |
| fresh2_demo_seed20261949 | logreg | none | 1,380 / 461 | 0.6074 | 0.0669 | 0.635 | 0.0428 | 0.1868 | calibration_slope |
| fresh2_demo_seed20261949 | logreg | barrier | 1,380 / 461 | 0.6172 | 0.0619 | 0.642 | 0.0444 | 0.3525 | calibration_slope |
| fresh2_demo_seed20261949 | gbm | onehot | 1,380 / 461 | 0.6302 | 0.0734 | 0.613 | 0.0388 | 0.4423 | calibration_slope |
| fresh2_demo_seed20261949 | gbm | none | 1,380 / 461 | 0.6013 | 0.0753 | 0.56 | 0.0356 | 0.2637 | calibration_slope |
| fresh2_demo_seed20261949 | gbm | barrier | 1,380 / 461 | 0.6254 | 0.077 | 0.61 | 0.0392 | 0.359 | calibration_slope |
| fresh2_10k_seed20261959 | logreg | onehot | 6,906 / 2,302 | 0.682 | 0.0266 | 1.082 | 0.0183 | 0.5512 | fairness_recall:nationality_group |
| fresh2_10k_seed20261959 | logreg | none | 6,906 / 2,302 | 0.672 | 0.0234 | 1.104 | 0.0178 | 0.2451 | none |
| fresh2_10k_seed20261959 | logreg | barrier | 6,906 / 2,302 | 0.679 | 0.0246 | 1.089 | 0.0181 | 0.5954 | none |
| fresh2_10k_seed20261959 | gbm | onehot | 6,906 / 2,302 | 0.671 | 0.0258 | 1.05 | 0.0181 | 0.4461 | none |
| fresh2_10k_seed20261959 | gbm | none | 6,906 / 2,302 | 0.6632 | 0.0247 | 1.104 | 0.0186 | 0.2621 | none |
| fresh2_10k_seed20261959 | gbm | barrier | 6,906 / 2,302 | 0.6707 | 0.0291 | 1.073 | 0.0187 | 0.4608 | none |
| fresh2_20k_realistic_seed20261969 | logreg | onehot | 4,046 / 1,349 | 0.7099 | 0.0412 | 1.15 | 0.0369 | 0.4747 | none |
| fresh2_20k_realistic_seed20261969 | logreg | none | 4,046 / 1,349 | 0.7012 | 0.0441 | 1.173 | 0.039 | 0.2196 | none |
| fresh2_20k_realistic_seed20261969 | logreg | barrier | 4,046 / 1,349 | 0.712 | 0.0422 | 1.155 | 0.0367 | 0.5728 | none |
| fresh2_20k_realistic_seed20261969 | gbm | onehot | 4,046 / 1,349 | 0.7095 | 0.0485 | 1.13 | 0.0391 | 0.5898 | fairness_recall:nationality_group |
| fresh2_20k_realistic_seed20261969 | gbm | none | 4,046 / 1,349 | 0.7024 | 0.0485 | 1.126 | 0.0405 | 0.2246 | none |
| fresh2_20k_realistic_seed20261969 | gbm | barrier | 4,046 / 1,349 | 0.717 | 0.0452 | 1.141 | 0.0377 | 0.6471 | fairness_recall:nationality_group |

Paired-bootstrap AUC differences (1,000 resamples of the same hold-out; 95 % CI):

| Cohort | Comparison | dAUC | 95 % CI |
|---|---|---|---|
| fresh_demo_seed20261919 | logreg: barrier - onehot | 0.0051 | -0.0091 to 0.0182 |
| fresh_demo_seed20261919 | logreg: barrier - none | -0.0144 | -0.036 to 0.0066 |
| fresh_demo_seed20261919 | gbm: barrier - onehot | -0.0068 | -0.0404 to 0.024 |
| fresh_demo_seed20261919 | gbm: barrier - none | -0.0175 | -0.0463 to 0.0073 |
| fresh_10k_seed20261929 | logreg: barrier - onehot | -0.003 | -0.0062 to 0.0001 |
| fresh_10k_seed20261929 | logreg: barrier - none | 0.0095 | -0.0015 to 0.0223 |
| fresh_10k_seed20261929 | gbm: barrier - onehot | 0.0022 | -0.0037 to 0.0085 |
| fresh_10k_seed20261929 | gbm: barrier - none | 0.0145 | 0.0008 to 0.0297 |
| fresh_20k_realistic_seed20261939 | logreg: barrier - onehot | -0.0007 | -0.0069 to 0.0055 |
| fresh_20k_realistic_seed20261939 | logreg: barrier - none | 0.0045 | -0.0094 to 0.0192 |
| fresh_20k_realistic_seed20261939 | gbm: barrier - onehot | -0.0073 | -0.0172 to 0.0015 |
| fresh_20k_realistic_seed20261939 | gbm: barrier - none | -0.0022 | -0.018 to 0.0149 |
| fresh2_demo_seed20261949 | logreg: none - onehot | -0.0158 | -0.0478 to 0.0127 |
| fresh2_demo_seed20261949 | logreg: none - barrier | -0.0098 | -0.0296 to 0.0065 |
| fresh2_demo_seed20261949 | gbm: none - onehot | -0.0289 | -0.0682 to 0.0048 |
| fresh2_demo_seed20261949 | gbm: none - barrier | -0.0241 | -0.0533 to 0.0011 |
| fresh2_10k_seed20261959 | logreg: none - onehot | -0.01 | -0.0226 to 0.0038 |
| fresh2_10k_seed20261959 | logreg: none - barrier | -0.007 | -0.0188 to 0.0047 |
| fresh2_10k_seed20261959 | gbm: none - onehot | -0.0077 | -0.022 to 0.007 |
| fresh2_10k_seed20261959 | gbm: none - barrier | -0.0074 | -0.0192 to 0.0049 |
| fresh2_20k_realistic_seed20261969 | logreg: none - onehot | -0.0087 | -0.0284 to 0.0089 |
| fresh2_20k_realistic_seed20261969 | logreg: none - barrier | -0.0108 | -0.0294 to 0.0064 |
| fresh2_20k_realistic_seed20261969 | gbm: none - onehot | -0.007 | -0.0291 to 0.0136 |
| fresh2_20k_realistic_seed20261969 | gbm: none - barrier | -0.0146 | -0.0333 to 0.003 |

Pre-registered verdicts:

* fresh_demo_seed20261919: primary `barrier` -> {'barrier_passes_all_gates': False, 'barrier_auc_not_credibly_worse': True, 'confirmed': False}
* fresh_10k_seed20261929: primary `barrier` -> {'barrier_passes_all_gates': False, 'barrier_auc_not_credibly_worse': True, 'confirmed': False}
* fresh_20k_realistic_seed20261939: primary `barrier` -> {'barrier_passes_all_gates': True, 'barrier_auc_not_credibly_worse': True, 'confirmed': True}
* fresh2_demo_seed20261949: primary `none` -> {'none_passes_all_gates': False, 'none_auc_not_credibly_worse': True, 'confirmed': False}
* fresh2_10k_seed20261959: primary `none` -> {'none_passes_all_gates': True, 'none_auc_not_credibly_worse': True, 'confirmed': True}
* fresh2_20k_realistic_seed20261969: primary `none` -> {'none_passes_all_gates': True, 'none_auc_not_credibly_worse': True, 'confirmed': True}

## Reading

* **Check 1 (barrier, seeds 20261919/29/39): NOT CONFIRMED.** The barrier set failed `fairness_recall:nationality_group` for both algorithms on the fresh demo (group Other Arab, 1 of 11 delayed cases flagged) and the fresh 10k (group Western, 2 of 13), and passed on the fresh 20k.
* **Check 2 (none, seeds 20261949/59/69): NOT CONFIRMED by the pre-registered rule.** `none` passed every gate on the fresh 10k and 20k. On the fresh demo it failed only `calibration_slope` (0.56-0.64), and so did every other feature set. This is the known small-sample over-confidence (461 hold-out cases), not a language effect.
* **Across the six fresh cohorts x two algorithms (12 candidates per set):** a nationality recall failure occurred for `barrier` 5/12, `onehot` 4/12 and `none` 0/12. Candidates passing every gate: `barrier` 5/12, `onehot` 6/12, `none` 10/12. AUC of `none` minus `barrier` ranged from -0.024 to +0.018 (median about -0.008). The paired CI never showed `none` credibly worse by more than 0.01, although on the fresh 10k (gbm) it was credibly worse by a small margin (CI -0.030 to -0.001). The language recall gap was 0.19-0.27 with `none` against 0.35-0.65 with `barrier`.
* **The gate failures are not gate noise.** With group labels permuted at random (no real difference), the fairness gates failed in 2.5 % of 200 permutations (95 % CI 0.3-4.7 %) on the fresh demo (`scripts/fairness_gate_null_rate.py`). 5/12 is far above that rate.
* **Mechanism.** The barrier flag ranks Malayalam and Tagalog speakers (Indian and Filipino patients) high for outreach. On the fresh 10k their flag rates were 0.69/0.59, against observed delay rates of 0.27/0.19. This used up the top-30 % capacity, so recall fell for Western, Emirati and Other Arab patients (0.15-0.38). These patients already get an interpreter call task from the workflow (round 16), whatever their ML priority.
* **Decision (round 19).** No production input was changed: neither pre-registered rule was met, and changing inputs again after looking at hold-outs would repeat the round-18 problem. The gates block every failing candidate (no promotion, rule-order fallback), so patient safety is not affected. Recommendation for the clinical lead / model governance: consider `none` as the input set (fairness evidence above), or pre-register a check that handles the small-demo slope failure. This is a human decision.
* Synthetic data only; no clinical claim.
