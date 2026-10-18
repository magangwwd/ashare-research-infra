# BC / INSIGHT — 乳腺癌医学证据观察站

A public, client-side searchable GitHub Pages site, built from *verified Google Drive evidence archives*. It publishes **only public research metadata** (paper title, PMID, journal, DOI, trial registration information, official regulatory headlines, guideline bibliographic index) and always shows retrieval dates and limitations.

- Website source: `bci_public_site/`
- Latest public data: `data/site-data.json`; initial copy generated from the actual 2026-10-18 Drive archive.
- Rebuild: `python bci_public_site/scripts/build_site.py --zip <verified_googledrive_archived_zip> --out bci_public_site/data/site-data.json`
- GitHub Pages deployment: `.github/workflows/bci-pages.yml` (only on main).
- Google Drive refresh: `.github/workflows/bci-pages.yml` reads the newest allowed snapshot file from the **private Drive folder** using repository secret `GDRIVE_OAUTH_JSON` when available, validates checksums and replaces the staging data, then deploys.
- If the Drive read fails in a push-based initial deployment, the site uses the explicitly labelled last successful snapshot committed to the repository; on scheduled runs, an inaccessible Drive fails before deployment so that a stale dataset is not silently marked as updated.

Security: Never commit OAuth credentials, original private Drive materials, patient data, unlicensed full-text abstracts or clinical advice. The source folder ID is not a token, but the public site never needs it. Restrict deploy actions to the default branch.

The data are research-only, not medically reviewed, not exhaustive, and must not be used for personal clinical decisions, treatment rankings or inference of efficacy.
