# Paper (LaTeX)

The final paper. Read `docs/PAPER_PLAN.md` first: thesis, research questions, every fact and number we state,
who writes what, and wording rules.

- `main.tex` pulls in one file per section from `sections/`. Each section file starts with comments naming its
  writer and the facts it may state. Replace the red `\todo{...}` markers with text.
- `refs.bib` holds the proposal's references. Add new entries only from the source itself.
- Tables in sections 4 and 7 are already filled from the logs. GPT-6.1 Sol rows are added after Mahek's merge.

## Writing in OpenAI Prism

1. Download this folder (or `paper.zip` from Ibrahim) and import it in Prism (All Projects -> import a .zip or
   folder).
2. The project owner adds the other two as collaborators (each needs a Prism account).
3. When a section is done, export the project and copy the changed `sections/*.tex` back here in a PR, so the
   repo keeps the final text.

| Writer | Files |
| --- | --- |
| Mahek | 00_abstract, 01_introduction, 02_related_work, 10_conclusion |
| Ammar | 03_phase1_method, 04_phase1_results, 09_limitations, Phase 1 part of 11_appendix |
| Ibrahim | 08_discussion; checks 05-07 (drafted from docs/PHASE2_REPORT.md) |
