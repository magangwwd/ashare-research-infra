# Exam Intelligence CN

Independent pilot for exam-policy change intelligence. Regions: Jiangsu (JS), Anhui (AH), Zhejiang (ZJ), Shanghai (SH). Second track: national special-equipment inspection/testing qualification rules.

## Scope
- A: local vocational skill evaluation notices, registration, qualifications, examination arrangements, institution verification and pitfalls.
- B: national special equipment inspection and NDT examination regulations, amendments, effective dates, obsolete requirements.
- Official source URLs must be verified and added to config/sources.json before enabling a collector. No unverified content is treated as an official change.
- Do not redistribute copyrighted question banks; create original explanations with citations.
- Data storage: immutable JSONL snapshots, SHA-256 manifest; Google Drive upload is gated on configured credentials and independently tested.
- No changes to existing stock/BTC workflows.

## Run
`python exam_intelligence/collector.py --dry-run`
`python -m unittest discover -s exam_intelligence/tests`

## Acceptance
1. Confirm official sources and licensing for every enabled endpoint.
2. Test two genuine source snapshots, including unchanged and changed cases.
3. Validate source timestamp vs fetch timestamp, SHA-256 and append-only history.
4. Verify GitHub Actions manual and scheduled executions.
5. Verify Drive upload, download and matching checksum with least-privilege credentials.
6. Trigger independent stale-data alert.
7. Record evidence and failures; no claims of production readiness without real tests.
