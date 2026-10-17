"""Optional independent paid LLM *shadow* bull/bear debate; cannot authorize trades.

Default is disabled. An operator must explicitly opt in, supply a server-side
API credential, model, and bounded request allowance. This is not TradingAgents
and must never be confused with the deterministic role analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from ai_research.manual_ai_review_pack import SYMBOLS, AUTHORIZATION

ROLES = ("bull_initial", "bear_initial", "bull_rebuttal", "bear_rebuttal", "risk_manager")
PROMPT_VERSION = "FIVE_LLM_SHADOW_RESEARCH_V1_20261017"
ENDPOINT = "https://api.openai.com/v1/responses"
PARTIAL_MODE = "INCOMPLETE_PARTIAL_LLM_SHADOW_NOT_VERIFIED"
PARTIAL_FILE = "llm_shadow.partial.json"
FINAL_FILE = "llm_shadow.json"


def write_json_atomic(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def text_from_response(reply):
    if reply.get("status") != "completed":
        raise ValueError("Model response not completed")
    parts = [c.get("text", "") for item in reply.get("output", [])
             if item.get("type") == "message"
             for c in item.get("content", []) if c.get("type") == "output_text"]
    result = "\n".join(parts).strip()
    if not result:
        raise ValueError("Model returned no text output")
    return result


def source_for_model(stock, report):
    """Only dated, structured source facts; no third-party prompt instructions."""
    return {"symbol": stock["symbol"], "as_of": report["as_of"],
            "raw_r1": {"candidate": stock["r1_candidate"], "status": stock["r1_status"],
                       "close": stock["close_raw_cny"]},
            "finance": stock["finance"], "news": stock["announcements"],
            "research_status": stock["research_status"],
            "unknowns": stock["waiting_for"]}


def call_api(payload, api_key, timeout=45):
    req = Request(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode(),
                  headers={"Authorization": "Bearer " + api_key,
                           "Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def run(report, model, api_key, max_requests, call=call_api, on_checkpoint=None):
    """Run the 25-call shadow debate.

    ``on_checkpoint`` (optional) receives a partial result dict after every
    completed call, so a failure on call N never discards the N-1 paid answers.
    """
    if not api_key or not model:
        raise ValueError("LLM API key and explicitly chosen model are both required")
    if (report.get("authorization") != AUTHORIZATION
            or report.get("mode") != "DETERMINISTIC_FIXED_FIVE_NO_LLM_NO_TRADE"
            or [s["symbol"] for s in report.get("stocks", [])] != list(SYMBOLS)):
        raise ValueError("Only exact fixed-five verified paper report supported")
    if max_requests != len(SYMBOLS) * len(ROLES):
        raise ValueError("Explicit allowance must be exactly 25 requests; no silent partial LLM debates")
    out = []
    count, input_tokens, output_tokens = 0, 0, 0
    for stock in report["stocks"]:
        facts = source_for_model(stock, report)
        debate = {}
        for role in ROLES:
            if count >= max_requests:
                raise ValueError("Request cap exceeded")
            directions = {
                "bull_initial": "Argue an evidence-based bullish interpretation; do not recommend orders.",
                "bear_initial": "Challenge bullish reasoning and cite actual risks, missing disclosures, and failures.",
                "bull_rebuttal": "Answer the bear's strongest objections specifically, concede unsupported ideas.",
                "bear_rebuttal": "Rebut the bull again; flag unsupported deductions and source timestamp mistakes.",
                "risk_manager": "Evaluate the two rounds, name the vetoes and unresolved checks; never approve entry.",
            }
            instructions = ("You are one independent market research role, NOT a trading authority. "
                            "Reply in Chinese, distinguish sourced facts from hypotheses and unknowns. "
                            "No fabricated news, price, fill, stop or certainty. "
                            "Use only provided F evidence ids and R1 facts. "
                            "Original R1 and risk vetoes are immutable. "
                            "NO_TRADE_AUTHORIZATION. " + directions[role])
            input_text = json.dumps({"facts": facts, "prior_role_outputs": debate},
                                    ensure_ascii=False, sort_keys=True)
            body = {"model": model, "instructions": instructions,
                    "input": input_text, "max_output_tokens": 350, "store": False}
            answer = call(body, api_key)
            output = text_from_response(answer)
            usage = answer.get("usage") or {}
            input_tokens += int(usage.get("input_tokens", 0))
            output_tokens += int(usage.get("output_tokens", 0))
            debate[role] = {"text": output, "response_id": answer.get("id"),
                            "request_sha256": hashlib.sha256(json.dumps(
                                body, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                            "response_sha256": hashlib.sha256(output.encode()).hexdigest()}
            count += 1
            if on_checkpoint is not None:
                on_checkpoint({"mode": PARTIAL_MODE, "prompt_version": PROMPT_VERSION,
                               "model": model, "as_of": report["as_of"],
                               "calls_completed": count, "calls_expected": max_requests,
                               "usage_reported": {"input_tokens": input_tokens,
                                                  "output_tokens": output_tokens},
                               "authorization": AUTHORIZATION,
                               "stocks": out + [{"symbol": stock["symbol"], "roles": dict(debate)}]})
        out.append({"symbol": stock["symbol"], "roles": debate,
                    "verification_status": "UNVALIDATED_LLM_SHADOW_NO_ENTRY_AUTHORIZATION"})
    return {"mode": "REAL_API_INFERRED_FIVE_ROLE_SHADOW_NOT_TRADINGAGENTS",
            "prompt_version": PROMPT_VERSION, "model": model,
            "as_of": report["as_of"], "created_utc": datetime.now(timezone.utc).isoformat(),
            "calls": count, "max_output_tokens_per_call": 350,
            "usage_reported": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "billing_status": "API_USAGE_MAY_INCUR_COST",
            "is_independent_actual_llm_inference": True, "historical_backtest_eligible": False,
            "affects_r1": False, "affects_live_orders": False,
            "authorization": AUTHORIZATION, "stocks": out}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--enable-paid-api", action="store_true")
    parser.add_argument("--model", default=os.environ.get("LLM_SHADOW_MODEL", ""))
    parser.add_argument("--max-requests", type=int, default=0)
    args = parser.parse_args()
    if not args.enable_paid_api:
        print("PAID_LLM_DISABLED_NO_NETWORK")
        return
    # Claim the output directory before spending anything: a pre-existing
    # directory must fail now, not after 25 billed calls.
    if args.output_dir.exists():
        raise SystemExit(f"Output directory already exists: {args.output_dir}; no API calls were made")
    report = json.loads(args.report_json.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    partial_path = args.output_dir / PARTIAL_FILE

    def checkpoint(snapshot):
        write_json_atomic(partial_path, snapshot)

    try:
        result = run(report, args.model, os.environ.get("OPENAI_API_KEY", ""),
                     args.max_requests, on_checkpoint=checkpoint)
    except Exception:
        saved = "yes" if partial_path.exists() else "no"
        print(f"LLM_SHADOW_FAILED partial_results_saved={saved} path={partial_path}")
        raise
    write_json_atomic(args.output_dir / FINAL_FILE, result)
    partial_path.unlink(missing_ok=True)
    print(json.dumps({"status": "SHADOW_INFERRED_NEEDS_HUMAN_REVIEW",
                      "calls": result["calls"], "as_of": result["as_of"]}))


if __name__ == "__main__":
    main()
