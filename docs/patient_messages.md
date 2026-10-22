# Patient messages: plain language, reading level, translations and sign-off (Iteration 7)

## What is checked (every language, every patient-facing text)

`kareshafaa/comms/readability.py` measures each of the 109 patient-facing texts (programme, direct-pathway and pathway
wording, message templates in both variants, teach-back texts, and the neutral fallback). `tests/test_patient_messages.py`
fails the build if any text breaks a check.

| Check | English | Arabic / Hindi / Urdu (proxy) |
|---|---|---|
| Reading level | Flesch-Kincaid grade <= 8 (<= 6 for "simplified" variants); Flesch Reading Ease >= 60 (texts of 12+ words) | no validated FK equivalent: sentence and word-length proxies below |
| Sentence length | average <= 20 words, none > 30 | same (sentence ends: `. ! ? ؟ ۔ ।`) |
| Long words | <= 20 % with 3+ syllables | ar <= 25 % with >= 9 letters; hi/ur <= 15 % with >= 8 characters |
| Tone | no blaming or commanding words ("failed to", "you must", "non-compliant"...); "cancer" only in a reassuring or negated sentence; no exclamation marks; no shouting in capitals (except NOT, HPV, FIT, HbA1c) | blame lexicon; "سرطان / कैंसर / کینسر" only in a negated or reassuring sentence |

Results (21 Oct 2026): **109/109 texts pass**. English: 28 texts, median FK grade 3.7 (max 6.3), lowest
reading ease 69.1. Average words per sentence: en 9.3, ar 7.8, hi 9.8, ur 10.9; longest sentence 24 words (ur).

| New pathway text (English) | FK grade | Reading ease |
|---|---|---|
| repeat_fit (spoilt FIT kit) | 4.0 | 83.3 |
| repeat_sample (unreadable cervical sample) | 2.8 | 89.4 |
| prediabetes_gp | 6.3 | 69.1 |
| surveillance_1y (HPV+, normal cells) | 5.3 | 76.5 |
| colposcopy_urgent (high-grade cytology) | 3.7 | 86.7 |
| neutral fallback ("your team will call you") | 3.8 | 88.5 |

Empathy principles in the wording: say what the result is *not* ("This is NOT a result about your health",
"does NOT mean you have cancer"); one clear next step with a timeframe; "we will help / we will remind you";
no blame for missed appointments; an offer to talk to a clinician.

## Translations (Arabic, Hindi, Urdu) - DRAFTS, need professional translator review

The five pathway texts added in Iteration 6 (repeat_fit, repeat_sample, prediabetes_gp, surveillance_1y,
colposcopy_urgent) now have Arabic, Hindi and Urdu drafts (`PATHWAY_TEXT` in `kareshafaa/comms/content.py`), marked
`TRANSLATION_STATUS = "draft - needs professional medical translator review"`. Numbers and test names (12 months,
2 weeks, HPV) are checked to survive translation. **They are not shown to patients** until signed off (below);
until then those patients receive the English wording plus an interpreter-supported navigator call - never an
unreviewed translation.

## Human sign-off (the content gate)

* Every text has a content id (`group:key:lang`) and a sha256 of its exact wording. A sign-off is bound to that
  hash, so any edit invalidates it automatically.
* **Clinical sign-off** (`content:approve`, clinical lead): refused if the reading-level / tone checks fail.
* **Translation sign-off** (`content:translation_review`, new `translator` role, demo user `translator`): required
  for every non-English text, in addition to the clinical sign-off, and by a **different person**
  (separation of duties). The English source must itself be signed off in its current wording.
* Sign-offs need a named human and a note of what was checked; each one is audited (`content.signoff.*`).
* Governed today: the pathway texts. If even the English pathway wording lacks a current sign-off, patients get the
  neutral message "Your screening result is ready. Your care team will call you..." (no guessing).
* The older prototype content (programme texts, templates, teach-back) is listed with its readability and sign-off
  status in Governance -> "Patient content sign-off" but is **not yet gated**. It must be signed off before a pilot.
* Demo seed: each programme's clinical lead signs off the English pathway wording (labelled SYNTHETIC DEMO).
  No translation is signed off.
* Data validator invariants 47-48: no unapproved translation was ever sent; no wording has two sign-offs by the
  same person.

## Round 16: the message as actually sent (21 Oct 2026)

The checks above measure each template with neutral placeholder samples (`meaning` is empty). Rendering every
template x programme x pathway x tier x sign-off state as the patient receives it (138,240 messages) found:

* **Mixed-language messages.** While a pathway text has no signed-off translation, the English wording was spliced
  into the Arabic/Hindi/Urdu template ("مرحباً Yousef. ... Your blood sugar test was a little higher than normal ...
  خطوتك التالية: <English next step>"). The patient portal promised "a navigator will call you with an
  interpreter", but no call task was created. In the existing synthetic data: 637 (demo), 3,177 (10k) and
  2,197 (20k) sent messages were mixed.
  **Fix:** if the result wording is not signed off in the patient's language, the WHOLE message is sent in the
  signed-off English wording (`Communication.language = en`, audit `requested_language`) and an
  interpreter-supported navigator call task is created (audit `calltask.created` reason `interpreter`; one open task
  per case; a `comm_phone` deny is honoured). Messages that do not carry result wording (no-show, decline,
  reassurance) stay in the patient's language.
* **Reminders too long once the next step is inserted.** "your next step is a camera test of the bowel (colonoscopy)
  at a hospital clinic, today" gave FK grade > 8 (en) and sentences > 30 words (hi/ur). Reminder templates in all
  four languages now split the next step and the timeframe ("Your next step: ... When: ..."), reusing phrases of the
  existing simplified variants. The non-English template edits are drafts and need translator sign-off like the rest.
* Now: all 138,240 rendered messages are in one language and pass the reading-level and tone checks (the secure link
  is measured as one token, "the link"). New validator invariant 53 `comms.sent_message_single_language`.

## Limits

* FK / reading ease use a heuristic syllable counter, and the proxies for Arabic, Hindi and Urdu are coarse
  (e.g. the Arabic long-word threshold rarely triggers). They flag text for review; they do not prove it is
  understood. Teach-back results by language (already captured) and patient testing are the real measure.
* The translations were drafted without a professional translator or native-speaker patient testing.
