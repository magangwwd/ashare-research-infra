# Validation evidence (2026-10-18 UTC)

Status: **research pilot only**. Do not publish unreviewed medical advice, comparative cure claims, or invented patient counts.

## Verified GitHub Actions run
- PR: https://github.com/magangwwd/ashare-research-infra/pull/44 (draft, not merged)
- Workflow run: https://github.com/magangwwd/ashare-research-infra/actions/runs/39087522329
- Run ID: `39087522329`
- Head commit tested: `c9f6e36622fd6c42c3e2cd44e6d6a5d973dc3163`
- Run result: success. Four unit tests passed.
- PubMed E-utilities real request: HTTP 200; three sampled PMIDs: `42831172`, `42787620`, `42727243`; SHA-256 of HTTP body `3ce2310f06046efe4fd185189de9d6dce2c7f125780e497b7b1c544ac7fab831`.
- ClinicalTrials.gov v2 real request: HTTP 200; NCT samples: `NCT02139683`, `NCT00488722`, `NCT01060345`; SHA-256 of HTTP body `b008e81c168b87d9e0585c3c4cb23bb5b0e89f764ef01cadee403b4ee16ec7c7`.
- These are **smoke checks of limited real search responses**, not backfill, incremental-collection completeness, or source medical review.

## GitHub artifact + Google Drive raw-file round trip
- GitHub artifact ID: `12120884663`, created for run `39087522329`, file `bci-public-api-smoke-39087522329.zip` (475 bytes).
- GitHub API-reported artifact digest: `sha256:82641dd3d5e2155e21d5b2b5afa7ab214cebb1fda9b7c6114e78532917468587`.
- Created Drive folder: https://drive.google.com/drive/folders/1cXlP5QNatkjiz7tLDjXKgKry5Jklz3A5
- Uploaded ZIP through the connected Google Drive account: https://drive.google.com/file/d/1-RWcS29VCK_SljTUQX00glvQUvKkWw1G/view
- Re-fetched raw ZIP through Google Drive connector; 475 bytes; SHA-256 independently computed as `82641dd3d5e2155e21d5b2b5afa7ab214cebb1fda9b7c6114e78532917468587`, matching GitHub artifact digest. ZIP contains `smoke_report.json` with `result: pass`.
- Also tested Google Drive native document write/read/move inside the folder: https://docs.google.com/document/d/1aeb9NyjWx-QGUzAiUOVP9HvIz4fvArVfSdenDJ7BdPQ/edit
- **Important scope:** This proves the **ChatGPT-connected** Drive identity can store/read the artifact and bytes match. It does **not** prove the repository's GitHub Actions Drive secrets or unattended upload work.

## Environment caveat
- Local container network requests to `eutils.ncbi.nlm.nih.gov` and `clinicaltrials.gov` failed DNS resolution. Do not confuse that environment issue with the confirmed success on GitHub-hosted runners.

## Not yet validated
- Unattended GitHub Actions -> Google Drive upload with repository secrets and service identity.
- Historical backfill, incremental pagination, update/diff handling and freshness alarms.
- Medical literature completeness, ranking validity, specialist/department currency, volume counts, clinical interpretation.
- Official hospital sources for clinical services and yearly breast cancer patient volumes.

## Safety and source rules
- No individually identifiable patient data.
- Hospital historical aliases and campus parent-child relationships must not be double counted.
- Patients vs surgeries vs inpatient visits are distinct statistical denominators.
- Do not equate public specialist profile, paper count, trial enrollment, or third-party rank with measured treatment effectiveness.
