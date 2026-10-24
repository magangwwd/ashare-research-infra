# Task 01 cross-run Drive publication recovery — implementation gate

Issue: #99. This branch is an isolated engineering proposal; it does not alter production uploads.

## Verified failure mode
`data_pipeline/drive_publish.py:publish()` generates a fresh timestamp/UUID staging directory on every invocation, then uploads every accepted file again. The 240-minute Task 01 job can time out before upload + remote SHA256 verification finishes. Per-file resumable retry from PR #96 does not recover across Actions runs.

## Safe implementation sequence
1. Separate immutable accepted-snapshot identity from the GitHub run ID. Calculate a deterministic snapshot fingerprint from a canonical, sorted list of relative paths, byte counts, and SHA256 plus acceptance as-of. Never reuse a staging folder when the fingerprint differs.
2. Under Drive `staging`, locate a uniquely identified resumable folder with the matching fingerprint recorded in a metadata file. Fail closed on zero/multiple conflicting candidates; do not delete or overwrite staging automatically. Existing legacy random folders are not automatically trusted.
3. Recursively inventory remote paths; reject duplicate names, unexpected files, symlinks, wrong MIME types, and path traversal. For each existing file, compare byte count and downloaded SHA256 with the accepted local file before skipping. Upload only absent files; re-inventory and verify after each resumed batch. A mismatched file is an error, never silently overwritten.
4. Make Drive publishing a separate bounded job from daily update, acceptance and recovery drill; consume a previously accepted immutable checkpoint. Use concurrency protection for one publisher per dataset, with cancellation disabled. Do not manually restart an active run.
5. Re-read old `current.json` and verify the old manifest before starting; commit the new pointer only after every file, the production manifest and the final remote inventory are verified. Detect concurrent pointer changes; abort instead of overwriting.
6. Record a machine-readable publication report: expected/uploaded/reused counts, bytes, hashes, manifest SHA256, old/new pointer IDs, previous snapshot recovery check and GitHub run ID.

## Required offline/mock tests
- Identical remote files are skipped across simulated process restart.
- Missing remote files are uploaded once.
- Wrong remote size or SHA256 stops without overwriting or switching pointer.
- Duplicate remote paths or ambiguous staging identities stop safely.
- Upload timeout retains staging and unchanged current pointer; a subsequent run resumes.
- Mismatched local fingerprint never resumes an unrelated staging version.
- Manifest readback failure prevents pointer switch.
- Concurrent pointer update prevents stale publication.
- Old version remains recoverable after successful switch.

## Production acceptance gate
Do not label VERIFIED_DONE until the real Drive inventory equals the accepted manifest in file count, paths, bytes and per-file SHA256; the manifest is read back; `current.json` points to the verified new version; and the previous version can be recovered. CI alone is insufficient.

## Current operations
Allow existing runs #40011271103 and #40032524926 to finish or resolve; do not start another full upload. Do not delete staging folders or touch production pointers while preparing this patch.
