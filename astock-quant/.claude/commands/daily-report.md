Run the daily H20 report workflow: update daily prices, recompute financial scores, refresh LightGBM predictions, auto-refresh Kronos if stale (>14 days), then write a new archived report to financial_data/reports/.

Execute the following bash command and stream its output to the user:

```bash
cd "G:/我的云端硬盘/stock" && python generate_latest_report.py $ARGUMENTS
```

After the command completes:
- If it succeeded, show the path of the newly generated report (look for the `LATEST_REPORT=` line in the output).
- If it failed, show the error and suggest a fix based on the Common Issues table in CLAUDE.md.
