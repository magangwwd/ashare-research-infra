# Breast Cancer Intelligence — research pilot

Research-only evidence intelligence: Chinese civilian and military hospitals, clinical trials, guidelines, PubMed papers, rehabilitation. This does not provide medical diagnosis or personalized treatment.

## Data quality principles
- Never infer patient count from surgery count, visits, publications, or hospital rank.
- Every metric requires source URL, observation year, definition, facility scope, retrieval time, and verification state.
- Military hospital former names and campus relationships are aliases, not automatically distinct hospitals.
- Do not rank hospitals by inferred survival or cure rates.
- No identifiable patient data, no scraped private medical records.
- Metadata/abstract licensing and full-text copyright must be respected.
- AI research commentary is labelled unreviewed until medically reviewed.

## Run
python breast_cancer_intelligence/collect_pubmed.py --dry-run
python -m unittest discover -s breast_cancer_intelligence/tests

## Planned stages
1. Verify official hospital identities, department pages and aliases.
2. Collect public PubMed metadata with E-utilities, rate limits, provenance.
3. Add ClinicalTrials.gov v2, drug regulator notices, guidelines.
4. Store immutable snapshots and verify Google Drive upload/download checksum.
5. Independent freshness alert; publish evidence-based reports only after review.

No live collection, Drive storage, or alerting is claimed by this initial scaffold.
