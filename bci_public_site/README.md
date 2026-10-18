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

## Public audience design (October 2026)

- `index.html`: default **患者与家属版** (patient/family) with plain-language prevention and screening, questions for diagnosis and treatment discussions, recovery/follow-up, and links to actual NCI patient pages plus the Chinese CACA guideline bibliography. Patient content is educational, not a clinical interpretation, triage or diagnosis.
- `professional.html`: **医疗专业人员版** with PubMed metadata, ClinicalTrials.gov registrations, FDA announcement links, and bibliography/source views. Research and approval statuses are never clinical recommendations.
- Reciprocal audience switcher on both pages. Shared public snapshot at `data/site-data.json`, refreshed using the existing workflow; no new data API or patient-data intake.
- Removed visitor-facing GitHub and source-code links, technical API entry points, and raw HTTP status display. Hosting and private automation remain server-side, not exposed in page navigation.
- Build/Pages workflow must publish both HTML files, both JS scripts and both CSS files; never expose OAuth secrets, patient records or unlicensed paper full text.

## Google Drive data-first website and unattended update pipeline

The first-screen product on both audience pages is now the **latest verified Google Drive snapshot** (actual timestamp, snapshot ID, category counts, research lists, source hyperlinks and age/staleness warnings). General patient education is intentionally lower on the patient page. The same `data/site-data.json` is used for both audiences, so both pages show an identical snapshot date and record counts.

- Source collector on trusted default branch: `.github/workflows/bci-drive-collector.yml`, daily around **07:30 China/Singapore** (23:30 UTC previous day); PubMed, ClinicalTrials.gov and official-source metadata; original abstract material is **not published**.
- The collector keeps a temporary GitHub Artifact and uploads a timestamped **immutable ZIP archive** into the existing `Breast_Cancer_Intelligence` Drive folder. Upload is verified by downloading the same bytes and comparing SHA-256. A failed upload causes a failed Action.
- The public site: `.github/workflows/bci-pages.yml` deploys after a successfully completed collection and also checks at approximately **08:15 China/Singapore** (00:15 UTC). It accesses the latest eligible Drive ZIP through the trusted repository OAuth Secret, checks file hashes and builds **public metadata only**.
- New evidence is **not invented** if no new Drive snapshot has arrived or one is invalid. The previous published website remains visible with its real historical capture date; in the browser its age warning increases over time.
- The data are recent, limited search samples and registrations, not an exhaustive historical registry or medical treatment recommendations.
- GitHub Pages hosting, private OAuth and source code remain backend operations. Visitor-facing pages and the published JSON have no GitHub/source-code/API management links.
- No personal patient case histories, unlicensed research-paper abstract redistribution, unverified surgeon rankings or invented hospital cure rates.

Acceptance required before claiming unattended updates: verify real main-branch collector GitHub Action, Drive upload receipt and SHA-256 roundtrip, a subsequent Pages Action that read the newest Drive snapshot, and the deployed public JSON reflecting the same snapshot ID. Until then, automated daily capture is *configured but unproven*.

## 2026-10-18: 医患就医导航与云盘医学编辑数据

Public website remains patient-first at `index.html` with the independent professional research index at `professional.html`. New capabilities:
- Interactive **care pathway** by screening/abnormal finding, examination and biopsy, treatment planning, and survivorship; each stage has questions to discuss with licensed clinicians and original source links.
- **Hospital specialties**: browse by region, professional focus, or search name; compare up to 3 records side by side. The comparison separates verifiable official specialty characteristics from facts requiring confirmation. It is not a hospital ranking or cure-rate comparison. Military hospital records backed only by retrospective clinical research are clearly labelled as unverified for current appointments and service capacity.
- **Emerging therapies**: brief evidence-level labels for ADC, endocrine/CDK4/6, selected immunotherapy, ctDNA/MRD research; no "new miracle drug" or individual prescription.
- **Research connection**: latest title/registry records from immutable PubMed/ClinicalTrials.gov snapshots and links to official medical sites; no submission of private patient information.
- The independent Google Drive editorial source is the native document `BCI_public_care_navigation_v1_医院诊疗科普结构化数据` in the existing `Breast_Cancer_Intelligence` folder. Its published subset is stored as `bci_public_site/data/care-guide.json`.
- `.github/workflows/bci-pages.yml` exports and validates the actual Drive Google Doc on every trusted main build; nightly schedule and successful data collection trigger updated Pages content. Hospital official-source assessments are *editorial and not fully clinically reviewed*. Manual review is required to keep healthcare descriptions current, not simply an HTTP 200 response.

The site is a general health education navigation, not a diagnostic tool, clinical decision support system, appointment booking, patient forum with identifiable health disclosures, or rankings. Requests for live consultations, patient uploads and individual recommendations require separate clinical oversight and privacy compliance.
