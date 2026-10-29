# Squipi harness and experience-learning review — 25 October 2026

Historical engineering review. Current scope and limitations are summarized in [VERIFICATION.md](VERIFICATION.md). Personal conversation excerpts and local installation history are excluded from this public version.

## Changes implemented

- Host-authored conversation checkpoints preserve speaker attribution, recent answers and corrections, task IDs, source IDs and output paths through prompt compaction. A new conversation no longer receives the last completed file task's objective merely because that task exists. Completed-work discussion requires that the most recent answer belongs to that work.
- Local original-message search supports exact message IDs and explicit paging, including messages longer than one tool response. Historical assistant statements remain assistant statements rather than becoming user instructions.
- Explicit online source checks route to web capabilities. Fresh/current external questions cannot silently read an old local source, and completion requires a successful web retrieval. Prompts distinguish a secondary reproduction from primary-source verification and request actual citations, dates, units and checked arithmetic. These are not full semantic or mathematical validation of every claim.
- Genuine empty final model replies trigger one checkpoint-preserving recovery, then a labelled partial failure. They are not accepted as task completion.
- Explicit run budgets are parsed from the user's instructions and enforced around model decisions and document-reading calls. Reading reserves time for synthesis and preserves incomplete-coverage labels. Startup/import overhead is outside that model budget; this is not a strict wall-clock SLA.
- Experience review runs locally while idle, stops for foreground work, and has a 90-second watchdog. Even Nebius task experience is reviewed locally rather than creating additional cloud egress. Ollama review uses a JSON schema; unconstrained review has one bounded retry. Invalid proposals save nothing.
- The worker validates proposed lessons against actual journal receipts and tools. Reusable procedures become trial hints; inferred findings/preferences remain pending. Automatic review cannot overwrite active user-approved procedures or resurrect archived ones. Changes have recoverable versions, evidence links and held-out technical outcomes. User-owned instruction files are not rewritten.
- Memory settings expose learning, trial evidence, activation, archiving, archived procedures and rollback. This changes persistent procedural memory, not model weights.

## Comparison with public implementations

[Hermes memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory), [skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator), and the public [background review implementation](https://github.com/NousResearch/hermes-agent/blob/main/agent/background_review.py) support separating bounded memory, reusable procedures and idle review. Squipi adopts local, interruptible review and recoverable procedure curation while retaining its own host approval and research-tool execution boundaries. Hermes was not substituted as the production harness.

[OpenAI's public memory/compaction example](https://developers.openai.com/cookbook/examples/agents_sdk/building_reliable_agents_memory_compaction) distinguishes active-task context from reusable workflow memory and reviewed evidence. Squipi now separates these records. [Agent evaluation guidance](https://developers.openai.com/api/docs/guides/agent-evals) supports evaluating traces and explicit outcomes rather than treating a completed status as a research-quality score. This comparison uses public documentation, not proprietary Codex internals.

[Open Deep Research](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/deep_researcher.py) separates the research brief, bounded evidence gathering/compression and report production. [STORM's article-generation code](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/storm_wiki/modules/article_generation.py) maps evidence and citations into writing. Squipi should continue strengthening evidence-linked deliverable checks instead of adding more harness options. No head-to-head performance benchmark against these systems was performed.

## Verification and scope

The structural inventory covers 204 first-party code files, approximately 32,000 lines, including the app, worker, web, distribution, experiments and tests. Python files parse. Execution, source selection, continuation, provider handling, approvals, caching, learning and report boundaries received focused manual review. This is not a claim that every line was manually audited or that the application is security-certified.

- Worker suite: **364 passed, 1 skipped, 22 subtests passed**.
- Source/policy checks: **282 assertions**; continuation: **136**; checkpoint: **20**.
- Budget tests cover parsing, the synthesis reserve and cancellation of a slow model call. Approval/path durability checks pass.
- Native harness fixtures cover Ollama and MLX across discussion recovery, a new external topic, an external-source follow-up, and lesson retrieval on/off. These use controlled model peers and real native/worker code; they do not qualify live model reasoning.
- Live local Qwen review with a real synthetic rate-tool receipt initially produced malformed JSON despite a normal stop. Schema-constrained review subsequently returned valid JSON and saved a trial procedure in **11.26 seconds**.
- A deterministic held-out transfer test uses a different schema/population: the training rate is 3/10 = 30%; the held-out rate is 2/4 = 50%. It preserves the source and keeps the procedure in trial. This proves the procedural/tool contract, not a measured gain in general research intelligence.
- The personality catalog accounts for **55 worker tools, 15 host tools and 13 bundled playbooks**.

The measurements above describe the development checks on 25 October, not a rerun of this release. Private runtime traces and task history are not distributed. User project data was not used for the learning tests.

## Remaining qualification

Public DuckDuckGo discovery returned a blocked/no-readable-results error in a live check; parsing and approval tests pass, but reliable general search is not established. Direct URL fetching and configured Bright Data remain separate capabilities. Numeric prose, substantive source relevance, causal interpretation and meaningful deliverable coverage still require stronger end-to-end oracles. A successful fetch alone does not prove a table's figures or a scientific conclusion. Live MLX was not running during this check; its updated protocol path was tested with a controlled peer. This update is a bounded experience-learning loop, not a guarantee of continual scientific improvement.

