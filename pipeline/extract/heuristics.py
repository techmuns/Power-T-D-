"""Regex / keyword feature extractor.

Cheap first-pass over every document's extracted text. Pulls out the
mechanical features the framework doc needs (voltage classes mentioned,
MVA/GVA capacity, order book size, key approval mentions). Anything
requiring real reading - mix percentages, customer-wise order book,
mgmt commentary tone - is left for the LLM extractor.

Every feature row carries the verbatim quote so the dashboard can
deep-link to evidence per the framework doc's section 3 source-tagging.
"""

from __future__ import annotations
import json
import re
from datetime import datetime, timezone

from ..db import connect

# ---------- patterns ----------

# voltage class mentions: 33 kV, 132 kV, 220 kV, 400 kV, 765 kV, 800 kV, 1200 kV
VOLTAGE_RE = re.compile(
    r"\b(\d{2,4})\s*kV\b",
    re.IGNORECASE,
)

# HVDC, FACTS, STATCOM, SVC, etc.
GRID_TECH_RE = re.compile(
    r"\b(HVDC|VSC[- ]?HVDC|LCC[- ]?HVDC|STATCOM|SVC|FACTS|TCSC|UPFC|"
    r"GIS|AIS|reactor|capacitor\s*bank)\b",
    re.IGNORECASE,
)

# capacity: e.g. "220 GVA", "1,250 MVA", "75,000 MT"
CAPACITY_RE = re.compile(
    r"\b([\d,]+(?:\.\d+)?)\s*(GVA|MVA|MT|ckm|km|MW|GW)\b",
    re.IGNORECASE,
)

# order book / inflow: "order book of ₹4,500 crore", "Rs 12,300 cr"
ORDER_BOOK_RE = re.compile(
    r"order\s*(?:book|inflow|backlog|in\s*hand)[^.\n]{0,80}?"
    r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|billion|bn|lakh\s*crore)",
    re.IGNORECASE,
)

# approvals mention
APPROVAL_RE = re.compile(
    r"\b(approved|approval|empanell?ed|vendor)\s+(?:by|with|from)\s+"
    r"([A-Z][A-Z&\s]{2,30})",
)

# AT&C losses (DISCOM)
ATC_RE = re.compile(
    r"AT&?C\s*loss(?:es)?\s*(?:of|at|stood\s*at|reduced\s*to)?\s*([\d.]+)\s*%",
    re.IGNORECASE,
)

# capex / investment: "capex of ₹1,200 crore"
CAPEX_RE = re.compile(
    r"\bcapex[^.\n]{0,60}?(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*"
    r"(crore|cr|billion|bn)",
    re.IGNORECASE,
)

# Order win: "received order of ₹500 crore", "won contract worth ₹1,200 cr"
ORDER_WIN_RE = re.compile(
    r"(?:received|secured|won|bagged|awarded)\s+(?:an?\s+)?"
    r"(?:order|contract)s?\s*"
    r"(?:worth|of|valued\s*at|aggregating|amounting\s*to)?\s*"
    r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*"
    r"(crore|cr|billion|bn|lakh\s*crore)",
    re.IGNORECASE,
)

# Customer-name mentions (high-value approval / order signal)
KEY_CUSTOMERS = (
    "PGCIL", "Power Grid", "NTPC", "SECI", "RVNL", "ONGC",
    "GAIL", "Adani", "JSW", "Tata Steel", "Reliance",
    "PFC", "REC", "BHEL", "Hitachi", "Siemens", "GE",
    "Saudi Electricity", "Aramco", "DEWA", "EDF", "ENGIE",
    "TenneT", "PJM",
)
CUSTOMER_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in KEY_CUSTOMERS) + r")\b"
)

# "approved up to XXX kV" -- claimed capability
APPROVED_UPTO_RE = re.compile(
    r"approved\s+(?:up\s+to|for)\s+([\d,]+)\s*kV",
    re.IGNORECASE,
)

# fixed-price vs pass-through mentions
FIXED_PRICE_RE = re.compile(
    r"\bfixed[- ]price[^.\n]{0,80}?(\d{1,3}(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
PASS_THROUGH_RE = re.compile(
    r"\bpass[- ]through[^.\n]{0,80}?(\d{1,3}(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)

# export share %
EXPORT_PCT_RE = re.compile(
    r"\bexports?\s*(?:share|contribut\w*)?[^.\n]{0,40}?(\d{1,3}(?:\.\d+)?)\s*%"
    r"|\b(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s+(?:revenue|order\s*book)\s+)?(?:from\s+)?exports?",
    re.IGNORECASE,
)

# book-to-bill: "book to bill of 2.5x"
BOOK_TO_BILL_RE = re.compile(
    r"book[- ]to[- ]bill[^.\n]{0,40}?([\d.]+)\s*x?", re.IGNORECASE,
)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _to_inr_cr(amount: float, unit: str) -> float:
    u = unit.lower().strip()
    if u in ("crore", "cr"):
        return amount
    if u in ("billion", "bn"):
        return amount * 100         # 1 billion = 100 crore
    if u in ("lakh crore",):
        return amount * 100_000
    return amount


def _ctx(text: str, start: int, end: int, pad: int = 80) -> str:
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return text[a:b].replace("\n", " ").strip()


def _extract(text: str) -> list[dict]:
    out: list[dict] = []

    # Voltages - keep the SET of voltage classes mentioned (with counts)
    counts: dict[str, int] = {}
    for m in VOLTAGE_RE.finditer(text):
        kv = int(m.group(1))
        if 10 <= kv <= 1200:
            counts[f"{kv}kV"] = counts.get(f"{kv}kV", 0) + 1
    for cls, n in counts.items():
        out.append({
            "feature": "voltage_class_mentioned",
            "value_text": cls,
            "value_num": float(n),
            "unit": "count",
            "evidence": cls,
        })

    # Grid tech tokens
    techs: dict[str, int] = {}
    for m in GRID_TECH_RE.finditer(text):
        tok = m.group(1).upper().replace(" ", "")
        techs[tok] = techs.get(tok, 0) + 1
    for tech, n in techs.items():
        out.append({
            "feature": "grid_tech_mentioned",
            "value_text": tech,
            "value_num": float(n),
            "unit": "count",
            "evidence": tech,
        })

    # Capacities
    for m in CAPACITY_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        unit = m.group(2).upper()
        out.append({
            "feature": f"capacity_{unit.lower()}",
            "value_num": n,
            "unit": unit,
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Order book
    for m in ORDER_BOOK_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        cr = _to_inr_cr(n, m.group(2))
        out.append({
            "feature": "order_book_inr_cr",
            "value_num": cr,
            "unit": "INR crore",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Approvals
    for m in APPROVAL_RE.finditer(text):
        who = m.group(2).strip()
        if len(who) < 3 or len(who) > 40:
            continue
        out.append({
            "feature": "approval_mention",
            "value_text": who,
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # AT&C losses
    for m in ATC_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        out.append({
            "feature": "atc_loss_pct",
            "value_num": n,
            "unit": "%",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Capex mentions
    for m in CAPEX_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        cr = _to_inr_cr(n, m.group(2))
        out.append({
            "feature": "capex_inr_cr",
            "value_num": cr,
            "unit": "INR crore",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Order wins
    for m in ORDER_WIN_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        cr = _to_inr_cr(n, m.group(2))
        out.append({
            "feature": "order_win_inr_cr",
            "value_num": cr,
            "unit": "INR crore",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Key customer mentions
    cust_counts: dict[str, int] = {}
    for m in CUSTOMER_RE.finditer(text):
        c = m.group(1)
        cust_counts[c] = cust_counts.get(c, 0) + 1
    for cust, n in cust_counts.items():
        out.append({
            "feature": "customer_mention",
            "value_text": cust,
            "value_num": float(n),
            "unit": "count",
            "evidence": cust,
        })

    # "approved up to XX kV" - claimed capability
    for m in APPROVED_UPTO_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        out.append({
            "feature": "approved_up_to_kv",
            "value_num": n,
            "unit": "kV",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Fixed-price exposure
    for m in FIXED_PRICE_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        out.append({
            "feature": "fixed_price_pct",
            "value_num": n,
            "unit": "%",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Pass-through %
    for m in PASS_THROUGH_RE.finditer(text):
        n = _num(m.group(1))
        if n is None:
            continue
        out.append({
            "feature": "pass_through_pct",
            "value_num": n,
            "unit": "%",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Export share %
    for m in EXPORT_PCT_RE.finditer(text):
        n = _num(m.group(1) or m.group(2))
        if n is None or n > 100:
            continue
        out.append({
            "feature": "export_share_pct",
            "value_num": n,
            "unit": "%",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    # Book-to-bill
    for m in BOOK_TO_BILL_RE.finditer(text):
        n = _num(m.group(1))
        if n is None or n > 10:  # sanity: book-to-bill rarely > 10
            continue
        out.append({
            "feature": "book_to_bill",
            "value_num": n,
            "unit": "x",
            "evidence": _ctx(text, m.start(), m.end()),
        })

    return out


def ingest() -> int:
    """Run heuristics over every document.extract_status='ok' that
    doesn't yet have heuristic features."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    with connect() as conn:
        # Find docs that are extracted but not yet feature-extracted
        rows = conn.execute(
            """
            SELECT d.id AS doc_id, d.company_id, d.doc_kind, d.full_text,
                   d.pdf_url
            FROM documents d
            WHERE d.extract_status = 'ok'
              AND d.id NOT IN (
                  SELECT DISTINCT document_id FROM features
                  WHERE extractor='heuristic' AND document_id IS NOT NULL
              )
            """
        ).fetchall()
        print(f"  heur: scanning {len(rows)} documents")
        for r in rows:
            text = r["full_text"] or ""
            if not text.strip():
                continue
            feats = _extract(text)
            for f in feats:
                conn.execute(
                    """
                    INSERT INTO features(
                        company_id, document_id, feature,
                        value_text, value_num, unit, evidence, page_no,
                        extractor, source_tag, confidence, as_of, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (r["company_id"], r["doc_id"], f["feature"],
                     f.get("value_text"), f.get("value_num"),
                     f.get("unit"), f.get("evidence")[:1000] if f.get("evidence") else None,
                     None,
                     "heuristic", "fact_company", 0.6, None, now),
                )
                written += 1
    print(f"  heur: {written} features written")
    return written
