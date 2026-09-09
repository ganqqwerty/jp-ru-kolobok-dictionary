# WPQ — Pipeline revision after the complete pilot review

WPQ-1 — This document is the required implementation and orchestration contract after `reports/wadoku_pilot_manual_problems.md`. It covers WPM-P01 through WPM-P30. It takes precedence over older conflicting rebuild-plan details. It is a specification, not a claim that the current commands implement these gates.

WPQ-2 — Execution is on hold by user instruction. Do not dispatch Luna, prepare or overwrite manifests, migrate a database, export a dictionary, or resume a run as part of this update. A later explicit execution request is required. Existing ZIPs, source XML, accepted translations and prompt versions remain unchanged.

## WPQ-ORDER — Required stage order

WPQ-ORDER-1 — A: inventory the lossless source and build typed metadata plus forward and reverse references. B: decide lexical grouping and lookup aliases. C: select linked examples and reference dependencies. D: build versioned sense translation units and their complete context. E: translate with Luna CLI. F: validate contracts and review meaning. G: assemble and validate both archives. H: test actual Yomitan behavior and obtain pilot approval. No later stage may silently supply missing decisions from an earlier stage.

WPQ-ORDER-2 — Before A runs on production data, implement and test the contracts below with small fixtures. Reconcile database schema, manifest schema, response validation, acceptance, manual replacement, reuse, exporter and reporting together. Bump affected versions and freeze a new run identity. Do not point the old scalar pipeline at a newer prompt and call that an upgrade.

## WPQ-A — Typed source inventory and rendering policy

WPQ-A-1 — Keep raw XML trees immutable. Every derived value has entry ID, XML path, scope, original value and transform version. Inventory node text AND attributes. Require a disposition for every observed node/attribute: native metadata, glossary content, structural relation, retained source-only evidence, or unresolved. Unknown learner-facing data blocks preparation; it must not silently disappear.

WPQ-A-2 — Separate entry-level accents from sense-level accents. Parse pitch positions and pronunciation details against the exact reading. Deduplicate identical metadata only within the same scope. Keep sense restrictions in the canonical model even if the client cannot express them natively. Confirm the pinned Yomitan capabilities before choosing a display fallback; unsupported scope becomes an explicit reviewed limitation, never naked digits in definitions. Regression set: 結果, おろし, きつけ. Covers WPM-P01/P02.

WPQ-A-7 — Group pronunciations by their applicable source senses, not by pitch count. If two pitches apply to the same set of senses, keep one article with both pronunciation variants. If their sense sets differ, create separate article groups with distinct sequences and only the applicable senses. Keep all spelling aliases within each group. Preserve original entry IDs and sense paths. Do not merge senses merely because their translations match.

WPQ-A-8 — Use explicit source scope to establish pitch-to-sense membership. A sense-specific accent overrides the entry accent for that sense; an unmarked sense inherits the entry accent. Missing or conflicting evidence blocks automatic grouping and requires a recorded classification and review. Luna may assess ambiguous source context in a separate classification step; the translation worker must not invent pitch assignments.

WPQ-A-9 — Native pitch metadata may not distinguish articles with the same expression and reading. Distinct sequence IDs alone do not prove correct graph filtering. Test this in the pinned Yomitan client. The current exporter adds an explicit article-pitch label for split groups; this is a provisional fallback, not an approved solution. Do not release until the popup review accepts the display or a verified alternative replaces it.

WPQ-A-3 — Decode hatsuon separately, including `[Dev]`, with verified mora alignment and native schema fields. Do not copy raw notation into learner text and do not discard it silently. Record unsupported constructs with source paths; resolve or explicitly review them before release. Test all 17 affected pilot entries. Covers WPM-P26.

WPQ-A-4 — Resolve usage text and attributes such as `reg=lit`, `coll`, `hofdamenspr.` through reviewed label tables. Reject empty badges. Put entry-wide labels before senses and sense labels inside their own sense. Preserve source scope and `sense@related` hierarchy; do not let rendering invent a new sense or merge distinct senses. Covers WPM-P03/P04/P30.

WPQ-A-5 — Preserve orthographic preference and rarity separately from lookup strings. Interpret source signs such as △/× only through documented rules; attach resulting tags to the relevant spelling, not every spelling in the article. Never put editorial signs in search keys. Covers WPM-P25.

WPQ-A-6 — Render protected fragments by their XML type. Inline references stay inline; standalone references get a separate labelled block. Japanese transcriptions, foreign etymons, scientific names and media identifiers are different types. Restore Japanese material as Japanese text/readings, preserve legitimate foreign material, and keep media captions and index numbers out of definitions. Covers WPM-P15/P16.

## WPQ-B — Lexical identity, search and references

WPQ-B-1 — Keep article classification separate from translation. Explicit unambiguous source evidence permits a deterministic decision; mixed/unmarked lexical cases require the dedicated Luna classifier and review on uncertainty. An inflected form, lexicalized noun, phrase and example may have different article and lookup policies. Include ほだし and たまらない alongside 知る／知らない／知らない人. Covers WPM-P29.

WPQ-B-2 — Extend reviewed morphology handling to `meishi@suru`, phrase-final verbs and source irregular classes. Validate the exact exported rule against the pinned Yomitan deinflector. Required cases include 評価した, 連絡して, 心変わりした, ファインプレーをした, 行った and 行って. Empty or generic rules are not automatically failures, but an untested class cannot pass the search gate. Never derive lexical grouping from a successful deinflection alone. Covers WPM-P05/P06/P07.

WPQ-B-3 — Alternative spellings share sequence and assembled glossary but retain separate search keys. Test 金縛り and 金縛 with the intended client grouping mode and primary dictionary recorded. Do not delete an alias to hide duplicate display. Covers WPM-P08.

WPQ-B-4 — Resolve references through the same accepted alias table as lookup rows. Keep target entry ID, target sense when supplied, spelling and reading as separate values. No raw IDs or unresolved template characters in search links. Verify the pinned client's exact-reading/entry addressing capabilities. If exact addressing is unavailable, mark the link as a general search and test that limitation; never claim an exact jump. Covers WPM-P10/P11.

WPQ-B-5 — Inventory missing link dependencies before freezing a pilot. Choose a bounded dependency set for testing; record whether each target is included, deliberately external to the pilot, or unresolved. Test links in an isolated profile and with intended additional dictionaries. The previous pilot had 58 of 63 links targeting expressions absent from its literal term keys; this is a baseline, not an acceptable silent failure rate. Covers WPM-P09.

WPQ-B-6 — Template expansion must retain fixed fragments, slot roles, aligned readings and evidence. A suffix-only alias is a limited lookup aid, not a complete expanded phrase. Apply this rule to reference targets too. Translate the slot's grammatical role consistently; do not drop the dependent object from the Russian explanation. Covers WPM-P11/P23.

## WPQ-C — Examples as a separate relation and translation stage

WPQ-C-1 — Build reverse `main/VwBsp` edges over the full XML. Do not assume every edge represents a complete sentence or every VwBsp entry is only an example: the same source type includes inflections and independent expressions. Keep `example_attachment` decisions separate from `article_policy` and `lookup_policy`. An independently searchable phrase may also appear as an example under its parent.

WPQ-C-2 — Store parent ID, child ID, source relation path, Japanese text and reading, source translation paths, optional parent sense ID, selection reason and review state. Attach to a specific sense only with explicit evidence or an accepted semantic decision. Otherwise use an article-level examples section; never guess the first sense. Preserve all source edges even when only some examples are displayed.

WPQ-C-3 — Start with at most three representative examples per parent article in the pilot; allow a reviewed override up to five for distinct grammar functions. Prefer complete attested phrases, coverage of different functions and no near-duplicates. Use deterministic selection only where evidence suffices; send ambiguous sense attachment to a bounded Luna classification task. Report candidate, selected, rejected and unresolved counts. The measured baseline is 958 source edges to 95 pilot parents, not a promise to display 958 examples.

WPQ-C-4 — Pair the immutable Japanese example with a translated learner-facing German meaning using an `example_translation` unit. Reuse an existing child translation only when its full role, sense and context contract matches; otherwise translate the example separately. No invented Japanese examples. Preserve independent child lookup where its grouping decision requires it.

WPQ-C-5 — Export pairs in verified structured-content example containers, with Japanese and Russian language roles separate from the glossary. Do not invent unsupported top-level Yomitan fields. Require pair completeness, provenance, no duplicate attachment, and no silent example loss. Count selected example units separately in budgets and coverage. Covers WPM-P12/P13/P14/P23.

## WPQ-FORMAL — Formal example-export gate for the orchestrator

WPQ-FORMAL-1 — The orchestrator must run this gate before it reports a pilot export as complete, publishes a review site, or asks for pilot approval. A valid Yomitan schema is not enough. The gate reads the frozen pilot selection, the complete reverse `main/VwBsp` candidate inventory, reviewed example decisions, translation manifests, accepted example translations and the built ZIP.

WPQ-FORMAL-2 — Require one reviewed decision for every example candidate linked to a selected parent article. The allowed decisions are `accept` and `reject`. Each decision must keep parent ID, child ID, relation path, source hash, reviewer and reason. Require `candidate_count = accepted_decision_count + rejected_decision_count` and zero unresolved candidates. A reviewed exclusion is a rejection with a reason, not a missing decision.

WPQ-FORMAL-3 — Require at most 5 accepted examples per parent article. Allow 6 or 7 only with a stored review override and reason. Reject templates, duplicate Japanese text and duplicate parent-child-relation identities unless a reviewed rule explicitly makes them distinct.

WPQ-FORMAL-4 — Require every accepted example to have immutable Japanese text, reading, source relation, source translation path and one `example_translation` unit. Require one accepted Russian result with matching unit ID, source hash and context hash. Empty, missing, scalar-contract-mismatched or reused-without-exact-context translations fail the gate.

WPQ-FORMAL-5 — Scan the built Yomitan ZIP, not an intermediate render. Require exactly one structured example container for every accepted example and no container without an accepted decision. Each container must appear under the intended parent and reviewed sense or article scope. It must contain separate `ja` and `ru` content, preserve the Japanese text and reading, and match the accepted Russian translation.

WPQ-FORMAL-6 — Require `accepted_example_count = translated_example_unit_count = exported_example_container_count`. Require zero missing pairs, duplicates, wrong-parent attachments, wrong-sense attachments, unapproved containers and silent losses. Store the exact identities and counts in a machine-readable report tied to the selection hash, manifest hashes and ZIP SHA-256.

WPQ-FORMAL-7 — The export command must stop with a nonzero result when any formal example check fails. It must not label the artifact complete and must not publish or replace the review site. The orchestrator reports `FAIL — examples stage missing` when candidate parents exist but the decision, translation or archive stages are absent.

WPQ-FORMAL-8 — After this gate passes, test example rendering in the pinned Yomitan client as required by WPQ-GATES-3. The HTML review page and schema validation cannot replace either the formal ZIP check or the Yomitan popup check.

WPQ-FORMAL-9 — The completed legacy Wadoku SQLite runs contain zero `example` units, zero `example_translation` units and no stored `main/VwBsp` relations in article JSON. The standalone rich pilots also did not write example candidates, decisions or translations to a database. Treat this as the measured baseline. Do not claim that examples already exist in the authoritative run database.

WPQ-FORMAL-10 — Build the initial example candidate inventory from the immutable official Wadoku XML, not from legacy translation rows. Store every imported relation in the authoritative database of the new run before example selection starts. Keep parent ID, child ID, relation path, source hash, Japanese text, reading and source translation paths.

WPQ-FORMAL-11 — Store reviewed accept and reject decisions, `example_translation` units, accepted Russian targets and export identities in the same authoritative run database. Do not use an HTML page, standalone pilot directory or old SQLite translation as the source of truth for this stage. A later database migration must preserve exact counts and hashes.

WPQ-FORMAL-12 — The orchestrator preflight must query the selected authoritative database and report counts for imported candidates, decisions, unresolved candidates, example translation units and accepted example translations. If selected parent articles have XML candidates but any required database stage is empty or incomplete, stop before translation or export and report `FAIL — examples database stage missing`.

## WPQ-D — Translation contract and context budget

WPQ-D-1 — Preserve the lossless source-block list separately from the versioned translation projection. Group unrestricted alternatives within ONE source sense into a `glossary_set`; return an ordered array of distinct Russian equivalents. Restricted/protected blocks and explanations retain their own IDs and scalar targets. Keep an exact member-path map so every source block is accounted for. Do not merge senses, even when German or Russian strings coincide.

WPQ-D-2 — Supply the whole current sense, relevant entry grammar, scope-aware labels, source restrictions, typed protected fragments, accepted forms/aliases, linked example evidence and adjacent senses with explicit boundaries. The worker must read a meaning and its explanation together so Russian case/agreement is coherent. It must not move an object or qualifier from a neighbouring sense. Review repeated or dangling explanations instead of permitting missing output units. Covers WPM-P17/P22.

WPQ-D-3 — For the next run, freeze a new prompt version based on v4 only after its contract is implemented. Add explicit requirements for grammatical function, construction restrictions, concise equivalents, idiom interpretation, noun/verb role, register/history uncertainty, Russian-oriented explanations and source-error flags. Preserve v2/v3/v4 provenance. Updating this specification does not activate a prompt in the current production config. Covers WPM-P14/P18/P19/P21/P22/P24/P27/P28.

WPQ-D-4 — Include projection, example selection, label mapping, morphology, source-correction overlay, prompt and schema versions in run identity and manifest hashes. Include all meaning-relevant context in the reuse key, not only identical German text. Old scalar V1 reuse is allowed only where it meets the new exact contract; it cannot fill part of a glossary set by heuristically matching one synonym.

WPQ-D-5 — Start the next pilot at concurrency 5 with at most 18 articles, 80 units and 24,576 serialized bytes per batch; byte/unit caps take precedence over article count. Count system instructions, examples, protected trees, schema and expected output against the worker's context budget. Record actual per-call usage. A sense must fit whole; overlarge input stops for reviewed splitting, not truncation. Re-estimate production concurrency and limits after the new pilot; prior scalar-run token figures are not a new contract budget.

## WPQ-E — Semantic review and source corrections

WPQ-E-1 — Contract-valid is not meaning-approved. Record structural validation and semantic review separately. Review all articles in the new pilot in the main orchestrator thread, plus every flagged low/medium-confidence unit and source conflict. For the full run use an explicit risk queue and a separate unseen holdout; no mandatory second LLM pass over the entire dictionary. High confidence does not exempt a known failure class.

WPQ-E-2 — Never auto-correct suspected source errors. A reviewed correction stores entry/path, source hash, original value, replacement, reason, evidence, reviewer and version. Apply it as a derived overlay, never a source edit. Keep XML relation type and effective relation type separately. Confirmed pilot relations 捨てる→拾う and やっと→辛くも are regression cases; do not swap all syn/anto codes. Covers WPM-P20.

WPQ-E-3 — The German edition keeps original German learner text. A shared structural correction may affect both editions only with preserved original evidence and a disclosed correction note. A Russian wording correction does not rewrite German. Structural parity compares the shared resolved model; the source-fidelity audit compares against untouched XML and the explicit correction ledger.

WPQ-E-4 — Flag missing historical/construction context and questionable specialist meanings for review. Do not delete 輝く's historical meaning or invent a period label for 楽しい without evidence. Resolve name-transcription policy and doubtful source readings separately. Regression queue: WPM-P19–P22 and WPM-P27/P28. Unresolved meaning-changing defects block release; a nonblocking limitation requires a named review decision, not an automatic waiver.

## WPQ-GATES — Regression and release requirements

WPQ-GATES-1 — Structural fixtures must prove: no accent nodes in glossary text; no duplicate pitch within one scope; no empty badge; correct entry/sense label placement; preserved related-sense hierarchy; inline references; typed foreign/Japanese fragments; variant-specific orthographic labels; no lost supported pronunciation data. Search and rendering fallback limitations are listed, not hidden by schema success.

WPQ-GATES-2 — Expand the frozen 150-entry regression set with an explicitly versioned supplement, without altering its old selection file. Include missing grammar functions (especially genitive の and interrogative か), exact-reading link targets, する/irregular forms, lexicalized forms and selected example dependencies. Record core, supplement and dependency counts separately. Read the complete combined pilot, and also test an unseen sample that excludes the old 22 manually corrected units.

WPQ-GATES-3 — Record Yomitan/browser versions, archive hashes, primary dictionary, grouping mode and enabled dictionaries. Test full phrases from their first character, suffix lookup, spelling variants, inflections, exact-reading/general-search links, example rendering, pitch scope and devoicing. Manual popup evidence is required; a HTML preview or ZIP schema check cannot stand in for it.

WPQ-GATES-4 — Give each WPM-P01–P30 a status: open, fixed-with-evidence, or reviewed-limitation. Store responsible stage, regression case and evidence path. None starts as fixed merely because it appears in this plan. All meaning-changing P1 defects and unresolved source/lookup decisions block release. User approval of the new pilot remains mandatory before full-run preparation or dispatch.

WPQ-GATES-5 — A renderer-only repair reuses accepted translations only if their projection/context contract is unchanged. Contract, example or prompt changes require a new run and measured reuse. Re-export only after a later execution request. Keep old pilot archives and reports for comparison; do not edit ZIP contents in place.

## WPQ-MAP — Coverage of the manual problem list

WPQ-MAP-1 — P01/P02 → A-2, GATES-1. P03/P04/P30 → A-4, GATES-1. P25 → A-5. P26 → A-3. P15/P16 → A-6. All require parser/renderer fixtures, not a translation-only fix.

WPQ-MAP-2 — P05/P06/P07 → B-2. P08 → B-3. P09 → B-5. P10 → B-4. P11 → B-4/B-6. P29 → B-1. All require real lookup tests as well as source decisions.

WPQ-MAP-3 — P12 → C-1 through C-5. P13 → GATES-2. P14 → C-5/D-3. P23 → B-6/C-5. Example selection and lexical grouping are distinct decisions.

WPQ-MAP-4 — P17/P22 → D-2/E-4. P18/P19/P24 → D-3/E-1. P20 → E-2/E-3. P21/P27/P28 → E-4. Semantic review may find an error in the source rather than the translator.

## WPQ-IMPLEMENT — Implementation handoff, not executed commands

WPQ-IMPLEMENT-1 — Parser/renderer and typed projections: `src/jitendex_ru/wadoku_xml.py`. Orchestration, reference/example dependency preparation, issue reports and audited replacement: `scripts/wadoku_xml_dictionary.py`. Batch schema/context: `src/jitendex_ru/batch.py`. Response and acceptance gates: `src/jitendex_ru/validate_response.py`, `src/jitendex_ru/jpdb_scope.py`, `src/jitendex_ru/cli.py`. Add versioned database support and fixtures only after checking existing schema and call sites with CodeGraph.

WPQ-IMPLEMENT-2 — Do not use `scripts/run_wadoku_pilot_standalone.py all` as evidence that production grouping, examples or PostgreSQL acceptance are implemented. Before enabling the next config, check the full prepare→dispatch→accept→replace→export contract. Old command examples in the rebuild plan are historical until reconciled with this revision. No new command name in this document implies an existing CLI implementation.
