# Red team of docs/V4_PLAN.md (draft 1), 2026-11-18

Reviewer: harness-red-team agent (read-only; no ho* jobs opened). Verdict: build with guards, after three fixes.

- Strongest objection: in v2.8 the judge rarely ships (1/12 dv8, 2/12 dv13; 2 of 3 right). Wrong ships are mostly
  older versions. v2.8's cost is handing over too often (21/24 runs) and takeovers ($9.52 of ~$12 in dv8); judge
  $1.66. A1 only cuts cost if green checks let the judge ship more. Visible checks often do not exist (dv13 builders
  never ran a test; answer tasks have nothing to run), so A1 = builder-written tests, which can share the misread spec.
- Held-out as written is impossible: scripts/draw_heldout.py finds 0 eligible never-run tasks (27 with --no-jobs).
- Backend move off Droid is the biggest confound: rerun v2.8, Opus alone, GLM alone on dev on the new backend first.
- Noise: dv8 vs dv13 swapped tasks both ways; +-2 dev solves is noise. v4 vs v2.8 cannot be resolved on held-out.
- A2 test first (v3.1 handoff 2/7 vs 4/7; 61% of pre-edit commands read files that must be read anyway).
- A3 test first (crash; in-loop revise rescued 1/19; extra judge call each fire).
- A4 drop (lowest prior, highest cost). B2 drop (exists: max_observation_chars 10000 head+tail).
- B3 test first (tunes Opus not who works). B1, B4, B6 build. B5 build as a check (may be a no-op).
- D: replay-only now, live chat after held-out.
- Latent bug: senior_review returns "ship" when out of turns (phase2/review.py:210); should be "fix" and logged.
- Missing: a small local router (course question) or say plainly the cascade is the router; per-run cost budget
  (v2.8 $0.92/run dev vs Opus $0.87/run held-out); Opus-alone dev arm on new backend.
- Keep rule for A1: takeovers down >=25% at equal solves (+-1) on two dev passes vs new-backend v2.8 baseline.
