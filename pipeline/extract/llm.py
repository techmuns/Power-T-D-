"""LLM-based feature extractor.

For each extracted document, runs a per-bucket extractor prompt and
asks the model to return strict JSON of framework-relevant fields,
each with verbatim evidence so the dashboard can show source on click.

Only runs if ANTHROPIC_API_KEY is set in the environment (e.g. as a
GitHub Actions repository secret). If the key is absent the function
returns 0 with a notice - no error.
"""

from __future__ import annotations
import json
import os
import re
from datetime import datetime, timezone

from ..db import connect

MODEL = "claude-haiku-4-5-20251001"   # cheap pass over the back-catalogue
MAX_CHARS = 60_000                    # truncate giant docs


PROMPTS_BY_BUCKET = {
    "transformer_manufacturer": """\
You are a buy-side Power T&D analyst. Read this filing for a transformer
manufacturer. Return ONLY JSON with this exact shape:

{
  "voltage_mix":     [{"voltage_class": "765kV"|"400kV"|"220kV"|"132kV"|"66kV"|"33kV"|"11kV"|"HVDC",
                       "share_pct": <number or null>,
                       "evidence": "<verbatim quote>"}],
  "max_mva_executed": {"mva": <number or null>, "evidence": "..."},
  "approvals":       [{"authority": "...", "product": "...", "evidence": "..."}],
  "order_book":      {"total_inr_cr": <number or null>,
                      "book_to_bill": <number or null>,
                      "evidence": "..."},
  "capex_plan":      {"amount_inr_cr": <number or null>,
                      "by_date": "<YYYY-MM-DD or null>",
                      "evidence": "..."},
  "exports_pct":     {"value": <number or null>, "evidence": "..."},
  "raw_material_pass_through_pct": {"value": <number or null>, "evidence": "..."}
}

If a field is not stated, use null. Never invent numbers.
""",

    "hvdc_facts_oem": """\
You are a buy-side Power T&D analyst. Read this filing for an HVDC /
FACTS / grid-stability OEM. Return ONLY JSON:

{
  "hvdc_scope":  [{"item": "converter_transformer"|"valves"|"control_system"|
                          "reactors"|"filters"|"protection"|"balance_of_plant",
                   "evidence": "..."}],
  "tech_type":   {"lcc": <true|false|null>, "vsc": <true|false|null>,
                  "evidence": "..."},
  "facts_products": [{"product": "STATCOM"|"SVC"|"TCSC"|"UPFC"|"shunt_reactor"|"capacitor_bank",
                      "evidence": "..."}],
  "order_book":  {"total_inr_cr": <number or null>, "evidence": "..."},
  "imported_content_pct": {"value": <number or null>, "evidence": "..."},
  "service_revenue_pct":  {"value": <number or null>, "evidence": "..."}
}
""",

    "transmission_epc": """\
You are a buy-side Power T&D analyst. Read this filing for a
transmission EPC company. Return ONLY JSON:

{
  "order_book":          {"total_inr_cr": <number>, "book_to_bill": <number>, "evidence": "..."},
  "order_mix":           [{"segment": "transmission_line"|"substation"|"distribution"|"other",
                           "share_pct": <number or null>,
                           "evidence": "..."}],
  "voltage_capability":  [{"voltage_class": "765kV"|"400kV"|"220kV"|"132kV"|"HVDC",
                           "evidence": "..."}],
  "fixed_price_pct":     {"value": <number or null>, "evidence": "..."},
  "row_responsibility":  {"who_bears": "client"|"epc_contractor"|"shared"|"unknown",
                          "evidence": "..."},
  "receivable_days":     {"value": <number or null>, "evidence": "..."},
  "ld_arbitration":      [{"description": "...", "amount_inr_cr": <number or null>,
                           "evidence": "..."}]
}
""",

    "transmission_asset_owner": """\
You are a buy-side Power T&D analyst. Read this filing for a
transmission asset owner. Return ONLY JSON:

{
  "regulated_vs_tbcb":  {"regulated_pct": <number or null>,
                         "tbcb_pct": <number or null>,
                         "evidence": "..."},
  "regulated_equity_inr_cr": {"value": <number or null>, "evidence": "..."},
  "cwip_inr_cr":             {"value": <number or null>, "evidence": "..."},
  "row_issues":              [{"project": "...", "issue": "...", "evidence": "..."}],
  "project_pipeline":        [{"name": "...", "voltage": "...", "value_inr_cr": <number or null>,
                               "stage": "won"|"under_bidding"|"identified",
                               "evidence": "..."}]
}
""",

    "distribution_discom": """\
You are a buy-side Power T&D analyst. Read this filing for a
distribution / DISCOM business. Return ONLY JSON:

{
  "atc_loss_pct":         {"value": <number or null>, "circle": "...", "evidence": "..."},
  "collection_efficiency_pct": {"value": <number or null>, "evidence": "..."},
  "smart_meter_progress": {"installed": <number or null>, "target": <number or null>,
                           "evidence": "..."},
  "regulated_equity_inr_cr": {"value": <number or null>, "evidence": "..."},
  "saidi":                {"value": <number or null>, "evidence": "..."},
  "saifi":                {"value": <number or null>, "evidence": "..."}
}
""",

    "smart_meters_automation": """\
You are a buy-side Power T&D analyst. Read this filing for a smart
meter / grid automation company. Return ONLY JSON:

{
  "model":            {"hardware_sale_pct": <number>, "annuity_pct": <number>,
                       "evidence": "..."},
  "meters_installed": {"value": <number or null>, "evidence": "..."},
  "meters_under_order": {"value": <number or null>, "evidence": "..."},
  "per_meter_realization_inr": {"value": <number or null>, "evidence": "..."},
  "software_service_pct": {"value": <number or null>, "evidence": "..."}
}
""",

    "towers_conductors_cables": """\
You are a buy-side Power T&D analyst. Read this filing for a towers /
conductors / cables company. Return ONLY JSON:

{
  "tower_capacity_mt":    {"value": <number or null>, "evidence": "..."},
  "voltage_capability":   [{"voltage_class": "765kV"|"400kV"|"220kV"|"132kV",
                            "evidence": "..."}],
  "ebitda_per_mt":        {"value": <number or null>, "evidence": "..."},
  "export_share_pct":     {"value": <number or null>, "evidence": "..."},
  "order_book_inr_cr":    {"value": <number or null>, "evidence": "..."},
  "metal_pass_through_pct": {"value": <number or null>, "evidence": "..."}
}
""",
}


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=api_key)


def _extract_json(s: str) -> dict | None:
    """Tolerant JSON pull from a model response that may include prose."""
    s = s.strip()
    # try outright parse
    try:
        return json.loads(s)
    except Exception:
        pass
    # find first {...} block
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _flatten(parsed: dict, prefix: str = "") -> list[dict]:
    """Convert nested JSON returned by the model into feature rows."""
    rows: list[dict] = []
    if isinstance(parsed, dict):
        # leaf dicts have {value/number, evidence} or similar
        if {"value", "evidence"} <= set(parsed.keys()) or \
           {"total_inr_cr"} <= set(parsed.keys()):
            num = parsed.get("value") or parsed.get("total_inr_cr") or parsed.get("amount_inr_cr")
            rows.append({
                "feature": prefix.strip(".") or "value",
                "value_num": num if isinstance(num, (int, float)) else None,
                "value_text": str(num) if not isinstance(num, (int, float)) else None,
                "evidence": (parsed.get("evidence") or "")[:1000],
            })
            return rows
        for k, v in parsed.items():
            rows.extend(_flatten(v, f"{prefix}{k}.").copy() if v is not None else [])
    elif isinstance(parsed, list):
        for item in parsed:
            rows.extend(_flatten(item, prefix))
    elif parsed is not None:
        rows.append({
            "feature": prefix.strip("."),
            "value_text": str(parsed),
            "value_num": parsed if isinstance(parsed, (int, float)) else None,
            "evidence": "",
        })
    return rows


def ingest(per_company_cap: int = 4) -> int:
    """Run LLM extraction on up to `per_company_cap` documents per company
    that have been text-extracted but not yet LLM-extracted."""
    client = _client()
    if client is None:
        print("  llm: ANTHROPIC_API_KEY not set or sdk missing - skipping")
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT d.id AS doc_id, d.company_id, d.doc_kind, d.full_text,
                   co.bucket, co.short
            FROM documents d
            JOIN companies co ON co.id = d.company_id
            WHERE d.extract_status='ok'
              AND d.id NOT IN (
                  SELECT DISTINCT document_id FROM features
                  WHERE extractor='llm' AND document_id IS NOT NULL
              )
            ORDER BY d.id DESC
            """
        ).fetchall()

        # rate-limit: at most N most-recent docs per company per run
        seen: dict[int, int] = {}
        queue: list = []
        for r in rows:
            cid = r["company_id"]
            if seen.get(cid, 0) >= per_company_cap:
                continue
            seen[cid] = seen.get(cid, 0) + 1
            queue.append(r)

        print(f"  llm: {len(queue)} documents queued (cap {per_company_cap}/company)")

        for r in queue:
            prompt = PROMPTS_BY_BUCKET.get(r["bucket"])
            if not prompt:
                continue
            text = (r["full_text"] or "")[:MAX_CHARS]
            if not text.strip():
                continue
            try:
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=2000,
                    system=prompt,
                    messages=[{
                        "role": "user",
                        "content": f"Filing for {r['short']} "
                                   f"({r['doc_kind']}):\n\n{text}",
                    }],
                )
                raw = resp.content[0].text if resp.content else ""
                parsed = _extract_json(raw)
                if not parsed:
                    print(f"    {r['short']:18s} [{r['doc_kind']}] - no JSON in reply")
                    continue
                feats = _flatten(parsed)
                for f in feats:
                    conn.execute(
                        """
                        INSERT INTO features(
                            company_id, document_id, feature,
                            value_text, value_num, unit, evidence,
                            extractor, source_tag, confidence, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (r["company_id"], r["doc_id"], f["feature"],
                         f.get("value_text"), f.get("value_num"),
                         None, (f.get("evidence") or "")[:1000],
                         "llm", "fact_company", 0.85, now),
                    )
                    written += 1
                print(f"    {r['short']:18s} [{r['doc_kind']:22s}] {len(feats)} features")
            except Exception as e:
                print(f"    {r['short']:18s} FAIL {type(e).__name__}: {str(e)[:120]}")
    print(f"  llm: {written} features written")
    return written
