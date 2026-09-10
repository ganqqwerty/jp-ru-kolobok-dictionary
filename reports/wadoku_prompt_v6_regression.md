# WR6 — Unedited Luna regression

WR6-1 — Run 11 completed: four articles, 33 units, translation prompt v6. The main thread read all 33 source/target pairs. No target in this run was manually edited. This comparison checks general prompt changes; it does not prove quality across the full 193-entry scope.

WR6-2 — Positive evaluation now survives: jmds. Leistung würdigen became давать высокую оценку достижениям. 外れる now uses отклоняться от нормы / отступать от правил rather than bare transitive verbs. The yen amount remains correct from the Japanese source. Glossary arrays no longer contain embedded semicolon lists. The drum definition no longer repeats the separate explanation's instrument.

WR6-3 — Conflicting evaluations in 素晴らしい now produce low confidence and a specific warning instead of unsupported high confidence. This is correct routing, not resolution of the source ambiguity. The review pipeline must retain this issue until an evidence-backed linguistic decision is applied.

WR6-4 — Natural Russian is not fully solved: случайно обрести немного удачи remains awkward, and маленький барабан should use the domain term малый барабан. These show two general review targets: literal collocations in examples and technical terminology in explanations. Do not hand-edit these and count the run as a prompt success. Use them in the next contextual review/repair stage alongside a fresh sample, without requiring exact wording matches.

WR6-5 — Script changes: window-report exposes source, target and warning; analyze-window records a hash-bound orchestrator decision. Continuing requires explicit coverage of the uncertain translations, not a human signature. Eleven focused tests pass against the isolated PostgreSQL database, including rejection of ignored uncertainty and stale reports. The live run started before window integration; it does not prove that the CLI boundary works in a live dispatch yet.
