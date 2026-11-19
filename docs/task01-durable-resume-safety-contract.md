# Task01 durable resume safety contract (design only)

Status: **DRAFT / NOT IMPLEMENTED**. This document authorizes no Drive write, workflow dispatch, or production pointer update. The current local ledger and local lock in `drive_batch_plan.py` are not cross-run durable.

## Immutable resume identity

Persist `source_run_id`, `stage_id`, checkpoint artifact ID and expiry, canonical inventory SHA256, inventory file count and byte total, and the SHA256 of the production `current.json` bytes read before staging. A ledger from another source, stage, inventory, or pointer is rejected. Expired or missing checkpoint: stop; never reconstruct identity from a different run.

## Single writer and durable compare-and-swap

Before any stage write, acquire a remotely enforced exclusive lease scoped to `(source_run_id, stage_id, inventory_sha256)`, with an owner token, fencing generation, and bounded expiry. GitHub concurrency groups or a local `.lock` file alone do not establish a distributed lease. Every ledger mutation must use a conditional write against the exact remote generation/ETag; failed preconditions stop the writer. Expired lease owners must not be able to commit after a newer fencing generation exists. Do not silently steal or remove another owner's lease.

## Receipts and batches

Each batch has at most 500 canonical inventory paths. After upload, read each staged object from Drive, hash its actual media bytes with SHA256, verify exact path/parent, file ID, size, source and stage, and persist receipts by remote conditional write. On restart, treat the persisted ledger as an untrusted hint: re-read all claimed remote objects and verify SHA256 before excluding them from missing-only upload. Duplicate paths, duplicate file IDs, unexpected remote objects, conflicting versions, and any identity mismatch fail closed. Do not accept Drive metadata MD5 or a filename match as SHA256 proof.

## Fault injection before staging trial

Exercise cancellation before upload, after upload but before receipt, after receipt but before ledger CAS, and after ledger CAS; duplicate runner startup; stale lease holder; lost lease renewal; changed production pointer; altered inventory; corrupted staged media; wrong parent; and expired checkpoint. Each must either resume only freshly verified missing files under a valid lease or stop without modifying the production pointer. Preserve previous production version and unrelated Raw/Clean data.

## Publication gate

A separate guarded publisher must prove all inventory bytes/SHA256, checkpoint provenance, zero missing files, no competing Task01 producer, stable original pointer and manifest identity, successful rollback rehearsal, and two independent readbacks of the new production pointer and manifest. Only then may it report a production release. The Gold downloader, frozen V1, existing Task01 crons, and 08:00 morning check remain unchanged. Five valid trading days of shadow comparison plus PIT membership and frozen candidate/ranking consistency are additional prerequisites for any later Gold deduplication switch.
