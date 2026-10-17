# Brownfield interruption diagnosis — October 16, 2026

Attempt 3 remains paused and undispatched. Attempts 1 and 2 remain consumed as `INTERRUPTED`. No workflow was resumed, ledger reconciled, source changed, or approval submitted during this diagnosis.

## Evidence and timing

The observed problem is premature interruption accounting, not demonstrated Ollama unavailability. The server log contains successful generation and HTTP 200 completion after each corresponding ledger interruption. Times below are UTC; the server log uses America/Chicago (UTC−05:00).

| Attempt | Ledger STARTED | Ledger marked INTERRUPTED | Correlated server completion | Request duration |
| --- | --- | --- | --- | --- |
| 1 | 02:20:24.614220 | 02:21:10.002059 | 02:21:13 | 48.895237833 seconds |
| 2 | 02:23:21.602740 | 02:24:00.927404 | 02:24:14 | 53.077563833 seconds |

Server tasks 3835 and 5085 generated 1,249 and 1,269 tokens. Their prompts were 1,258 and 1,251 tokens, with context capacity 32,768 and `truncated = 0`. These timings strongly correlate with the two workflow dispatches; the server does not record the workflow attempt UUID. HTTP 200 and server generation do not prove that the client persisted or fully received the response. Neither raw implementation response is retained in this execution.

The orchestration treated a yielded command as a completed process and reconciled the ledger too early. A running `exec_command` session must be polled using its returned session ID; completion of its wrapper is not child-process termination. Earlier interruption reports did not establish process exit. The original client exit status/stderr were not retained, so their exact termination mechanism cannot be reconstructed.

The code exposes a concrete response-loss path: `reconcile()` changes every `STARTED` record without checking request liveness. Both execution adapters call `finish()` before saving the raw response. `finish()` rejects an already-interrupted attempt, and the exception handler's `fail()` rejects that same state. Thus a late successful response can be discarded before its file is written. This is consistent with the observed chronology; absent client stderr, that exact exception is not claimed as captured evidence.

## Infrastructure diagnostic

Ollama and its loaded runner were running; `/api/ps` reported the existing `qwen2.5-coder:latest` model with context 32,768. One isolated prompt requested only `OK`, with the same model and context. It returned HTTP 200, `OK`, and `done_reason: stop` in **0.355804 seconds**, generating two tokens. This is connectivity evidence only, not an engineering generation attempt or implementation evidence.

The inspected machine snapshot had substantial swap use (approximately 9,393 MiB of 10,240 MiB). This does not establish an OOM kill. The inspected process/log evidence contains no demonstrated model crash or OS kill for these requests. No explicit generation timeout or output-token cap was configured in the provider. There is no evidence that context truncation or an output limit caused these interruptions. A short successful probe does not establish long-request reliability.

## Preservation and restart safety

[Diagnostic JSON](ollama-diagnosis/diagnostic.json) contains the harmless request/response, attempt rows, and matching before/after SHA-256 inventories for all files under the execution directory, including SQLite databases and existing WAL/SHM files. [Server excerpt](ollama-diagnosis/server-before.log) preserves the generation timings. Both approved production hashes match the execution receipt. No generated status test or source transaction was created. Attempt 3 has no ledger row.

The checkpoint still identifies pending implementation work; its persisted attempt counter predates the consumed ledger entries. It cannot safely be treated as a fresh attempt. The current continuation script is hardcoded for attempt 2 and is not a safe attempt-3 resume mechanism. These are lifecycle blockers, not permission to reset the budget.

## Smallest safe correction before any future dispatch

Preserve and poll live process sessions through actual termination; never reconcile merely because a tool yielded. Add exclusive request ownership/liveness checks so reconciliation cannot race an active invocation. Durably save any received response before final ledger transition, and retain late responses without reopening consumed attempts. Reconcile checkpoint progress against the ledger without dispatching the same ordinal twice, and test this race before using the final attempt.

No model, policy, plan, attempt limit, or historical ledger was changed. No additional implementation request, Maven run, or regression run was necessary for this read-only diagnosis. Historical approvals and failed evidence remain unchanged. Further execution remains paused pending lifecycle correction and the user's decision; no release or deployment is authorized.
