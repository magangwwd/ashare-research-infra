# Evidence and validation log

2026-10-18: Implemented research scaffold and ClinicalTrials.gov v2 API client. No successful live API request, GitHub Actions run, or Google Drive round-trip has been established in this log.

## Required real-world checks
- Run tests in GitHub Actions and record workflow run URL, commit SHA and result.
- Run PubMed and ClinicalTrials.gov requests and retain response timestamp, record counts, sample identifiers and checksums.
- Verify ClinicalTrials.gov returned schema against normalized fields. The `last_update_posted` field requires careful review: current mapping is provisional and MUST NOT be published as the study update date.
- Confirm military hospital identity, department, doctor and public volume from dated primary sources.
- Validate no patient-identifiable records.
- Test Google Drive upload, download, hash equality, and independent freshness alert before enabling schedule.
