# Fixed five morning report: six roles + news + opinion (GPT chat model)

## Division of responsibility

1. **GitHub Actions (no API cost):** existing Gold -> frozen R1 -> `five_stock_decision_v1` creates verified stock/four-part finance/paper-only output. Following workflow `five-stock-morning-packet.yml` reads report and publishes `five_stock_morning_packet_v1/YYYY-MM-DD/{morning_model_packet.json,morning_model_prompt.md,manifest.json}`. This packet has *zero news searches and zero LLM calls*; it only transfers evidence and source-time gates.
2. **The user's 08:00 ChatGPT scheduled task (no extra third-party API key):** reads the most recent verified packet; independently searches public online official issuer and exchange disclosures plus news and credible public opinion from the **last seven calendar days**, as of actual review time. The task role-plays six experts in sequence, cross-examines reasoning, and sends a Chinese message. ChatGPT-scheduled task model/version is a product capability and is not promised to equal a specific private live chat instance or 25 independent model API calls.
3. **Optional actual independent inference:** `five_stock_llm_shadow.py` is a separate paid, manual API path, not required for the morning bulletin. No other system should mark it completed without actual provider response IDs/costs.

## Role sequence

- Technical expert: independently report R1 market breadth (50% frozen threshold), signal pattern, ATR and execution status from data only; abstain on missing features.
- Finance/three-statement expert: profitability, earnings quality, cash vs profit, leverage, receivables and report-period comparisons, with issuer disclosure dates and Clean captured dates.
- **News, sentiment and expectations expert (new):** collect and timestamp corporate filings, news, investor discussion, macro/sector developments, catalysts and possible risks, differentiate management guidance, institutional consensus, market rumors, hypothetical scenarios. For posts, only cite attributable dates and URLs and mark nonrepresentative; do not fabricate aggregate sentiment counts, investor consensus, or probabilities.
- Bull researcher: argue the strongest *evidence-backed* bull thesis and expected favorable verification, not buy suggestions.
- Bear researcher: rebut the bull, challenge outdated/misleading news and identify adverse indicators.
- Round two bull/bear rebuttals: cite specific conflicting evidence, discuss what would falsify each stance.
- Independent risk manager: risk veto, unresolved observations, only WATCH/SETUP_PAPER_NEXT_OPEN_UNVERIFIED/AVOID_EXIT_REVIEW/NEEDS_REVIEW. Does not change frozen R1 candidacy.

## News and PIT

For each source store URL, title, source category (issuer/regulator, credible media, public post), `published_at`, observed `captured_at` if persistence exists, event scope and time cutoff. Source posting time is not automatically the same as *when our pipeline* could have known it. Anything published after 15:00 Asia/Shanghai of the research day is **POST_SIGNAL_SESSION**; anything discovered afterward may be used in the **current morning's review**, but not in historical close-based R1 or retrospective training. Do not assume that a live searched web URL was historically captured; it is later verification for today's bulletin. If publication time is unclear, mark uncertain rather than using as backtest evidence.

## Morning output: required order

1. **First screen: FIVE STOCK CONCLUSIONS.** Five symbols and company names, R1 close and date, WATCH/SETUP/AVOID/REVIEW, most important risk, next evidence hurdle.
2. Market breadth / Gold data quality / 08:00 research cutoff; whether same-day R1 + full evidence covered.
3. **Each stock:** A technical; B 2026 H1/Q1 and newer truly captured Q3 finance if verified; C recent issuer disclosures, sector policy/market news and *attributable* investor opinions; D forward catalysts/risks, separated into official guidance, analyst consensus and hypotheses; E bull/bear round 1 and round 2; F risk manager verdict and next observable decision point.
4. A/B forward: data may still have `pnl:null`; never infer equity returns from one close.
5. Source URLs, disclosure/news timestamp, financial data version, R1 manifest SHA and model-mode disclosure. Avoid a whole-page news feed; 1-3 material events per stock.

## Sample acceptance (2026-10-16 signal)

R1 market breadth 667/3188=20.922%, candidate_count 0/5; thus all 5 WATCH/no authorized paper entry. Source `five-clean-38220271682` covers limited 2026Q1/H1, not complete historical three-statement cash flows. The actual morning should supplement after-close issuer/news disclosures as *morning information*, not as 10/16 closing signal.

Official news examples verified on 2026-10-17 using live browsing:
- 600351 buyback progress filed 2026-10-09 (748900 shares, RMB 4,999,373 spent), issuer-level disclosure.
- 002107 corrected 2026 Q3 report filed 2026-10-13 (shareholder fund percentage 0.21%→0.24%; financial numbers unchanged).
- 600641 controlled shareholder stock pledge filed 2026-10-17 (**post** 2026-10-16 close), stronger risk discussion.
- 002568 updated private share offering documents filed 2026-10-16, company forward whiskey distribution guidance is management forecast, not achieved target.
- 002296 chairman share purchase completion filed 2026-10-14, management support is not a verified buy signal.

Do not hardcode these source items for later morning editions. Search each morning and trace original links. If there is no reliable current investor discussion for a stock, set `PUBLIC_SENTIMENT_NOT_VERIFIED` and do not substitute forum samples from earlier months.

## Operational checks

Nightly after verified five-stock decision workflow completes, inspect GitHub Actions `Fixed five-stock morning GPT role packet (no model API)` and the dated Drive output, inspect report source checksums, and verify morning automation is enabled. The ChatGPT task will still need user-side push/email notifications enabled to display an OS-level banner. At most one written daily message; no broker/API trades. See #37 for broader finance/news coverage and forward validation.
