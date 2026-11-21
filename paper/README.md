# Paper (LaTeX)

The final paper. Read `docs/PAPER_PLAN.md` first: thesis, research questions, every fact and number we state,
who writes what, and wording rules.

- `main.tex` pulls in one file per section from `sections/`. Each section file starts with comments naming its
  writer and the facts it may state. Replace the red `\todo{...}` markers with text.
- `refs.bib` holds the proposal's references. Add new entries only from the source itself.
- Tables in sections 4 and 7 are already filled from the logs (all 480 Phase 1 runs, Sol included).

## Writing in OpenAI Prism

1. The Prism project `mlr570-paper` is already set up with these files; Ammar and Mahek are invited with edit
   access on their university emails (sign in to Prism with that email to see it).
2. Prism shuts down on 2027-01-01: export the project before then.
3. When a section is done, export the project and copy the changed `sections/*.tex` back here in a PR, so the
   repo keeps the final text.

| Writer | Files |
| --- | --- |
| Mahek | 00_abstract, 01_introduction, 02_related_work, 10_conclusion |
| Ammar | 03_phase1_method, 04_phase1_results, 09_limitations, Phase 1 part of 11_appendix |
| Ibrahim | 05_phase2_method, 06_phase2_setup, 07_phase2_results, 08_discussion, Phase 2 part of 11_appendix |
