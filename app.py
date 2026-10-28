import os
import json
import re
import sqlite3
import time
from urllib import request as urlrequest, error as urlerror

from flask import Flask, jsonify, render_template, request
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("PCARD_DB", os.path.join(BASE_DIR, "pcards.db"))

app = Flask(__name__)

SCHEMA = (
    "pcards(Year INTEGER, Month INTEGER, FullName TEXT, Description TEXT, "
    "Amount REAL, Vendor TEXT, TransactionDate TEXT, PostedDate TEXT, MCC TEXT)"
)

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def as_dicts(rows):
    return [dict(r) for r in rows]

def read_only_sql(sql):
    sql = (sql or "").strip()
    forbidden = (
        r"\b(attach|detach|pragma|insert|update|delete|drop|alter|create|"
        r"replace|vacuum|reindex|load_extension|sqlite_master|transaction|"
        r"commit|rollback)\b"
    )
    if (
        ";" in sql
        or not re.match(r"^(select|with)\b", sql, re.I)
        or not re.search(r"\bpcards\b", sql, re.I)
        or re.search(forbidden, sql, re.I)
    ):
        raise ValueError("Only one read-only SELECT/WITH query against pcards is allowed.")
    return sql

def run_sql(sql, params=()):
    sql = read_only_sql(sql)
    with connect() as db:
        return as_dicts(db.execute(sql, params).fetchall())

def year_value(value):
    if value in (None, ""):
        return None
    value = int(value)
    if not 1900 <= value <= 2100:
        raise ValueError("Invalid year.")
    return value

def filters(args, include_description=True):
    where, params = [], []

    year = year_value(args.get("year"))
    if year is not None:
        where.append("Year = ?")
        params.append(year)

    if args.get("month") not in (None, ""):
        month = int(args["month"])
        if not 1 <= month <= 12:
            raise ValueError("Month must be between 1 and 12.")
        where.append("Month = ?")
        params.append(month)

    for key, column in (
        ("employee", "FullName"),
        ("vendor", "Vendor"),
        ("mcc", "MCC"),
    ):
        value = (args.get(key) or "").strip()
        if value:
            where.append(f"{column} LIKE ? COLLATE NOCASE")
            params.append("%" + value + "%")

    if include_description:
        value = (args.get("description") or "").strip()
        if value:
            where.append("Description LIKE ? COLLATE NOCASE")
            params.append("%" + value + "%")

    for key, operator in (("min_amount", ">="), ("max_amount", "<=")):
        value = (args.get(key) or "").strip()
        if value:
            where.append(f"Amount {operator} ?")
            params.append(float(value))

    for key, column, operator in (
        ("transaction_from", "TransactionDate", ">="),
        ("transaction_to", "TransactionDate", "<="),
        ("posted_from", "PostedDate", ">="),
        ("posted_to", "PostedDate", "<="),
    ):
        value = (args.get(key) or "").strip()
        if value:
            where.append(f"date({column}) {operator} date(?)")
            params.append(value)

    return where, params

# Part II: required controls 1-7 plus seven student-defined controls 8-14.
CONTROL_CHECKS = {
    "control_1": (
        "Control 1 - annual spending over $50,000",
        """SELECT FullName AS Name, ROUND(SUM(Amount),2) AS TotalAmount
           FROM pcards WHERE Year=2014
           GROUP BY FullName HAVING SUM(Amount)>50000
           ORDER BY TotalAmount DESC"""
    ),
    "control_2": (
        "Control 2 - monthly spending over $10,000",
        """SELECT FullName AS Name, Month, ROUND(SUM(Amount),2) AS TotalAmount
           FROM pcards WHERE Year=2014
           GROUP BY FullName, Month HAVING SUM(Amount)>10000
           ORDER BY Month ASC, TotalAmount DESC"""
    ),
    "control_3": (
        "Control 3 - transaction over $5,000",
        """SELECT Amount, FullName AS Name, Description, Vendor,
                  TransactionDate, PostedDate, MCC
           FROM pcards WHERE Year=2014 AND Amount>5000
           ORDER BY Amount ASC"""
    ),
    "control_4": (
        "Control 4 - split purchase: same person, vendor and day",
        """WITH g AS (
             SELECT FullName, Vendor, TransactionDate,
                    COUNT(*) AS TransactionCount, ROUND(SUM(Amount),2) AS CombinedAmount
             FROM pcards WHERE Year=2014 AND Amount>0 AND Amount<=5000
             GROUP BY FullName, Vendor, TransactionDate
             HAVING COUNT(*)>1 AND SUM(Amount)>5000
           ), numbered AS (
             SELECT p.Amount, p.FullName AS Name, p.Description, p.Vendor,
                    p.TransactionDate, p.PostedDate, p.MCC,
                    g.TransactionCount, g.CombinedAmount,
                    ROW_NUMBER() OVER (
                      PARTITION BY p.FullName, p.Vendor, p.TransactionDate
                      ORDER BY p.Amount ASC, p.PostedDate ASC, p.Description ASC
                    ) AS SplitLine
             FROM pcards p JOIN g
               ON g.FullName=p.FullName AND g.Vendor=p.Vendor
              AND g.TransactionDate=p.TransactionDate
             WHERE p.Year=2014 AND p.Amount>0 AND p.Amount<=5000
           )
           SELECT 'SPLIT-' || Name || '-' || Vendor || '-' || TransactionDate AS SplitGroup,
                  SplitLine, TransactionCount, CombinedAmount AS SplitGroupTotal,
                  Amount, Name, Description, Vendor, TransactionDate, PostedDate, MCC
           FROM numbered
           ORDER BY TransactionDate ASC, Name ASC, Vendor ASC, SplitLine ASC"""
    ),
    "control_5": (
        "Control 5 - split purchase: two cardholders",
        """WITH g AS (
             SELECT Vendor, TransactionDate,
                    COUNT(*) AS TransactionCount,
                    COUNT(DISTINCT FullName) AS CardholderCount,
                    SUM(Amount) AS CombinedAmount
             FROM pcards WHERE Year=2014
             GROUP BY Vendor, TransactionDate
             HAVING COUNT(*)=2 AND COUNT(DISTINCT FullName)=2
                AND SUM(Amount)>5000
           )
           SELECT p.Amount, p.FullName AS Name, p.Description, p.Vendor,
                  p.TransactionDate, p.PostedDate, p.MCC,
                  g.CardholderCount, ROUND(g.CombinedAmount,2) AS CombinedAmount
           FROM pcards p JOIN g
             ON g.Vendor=p.Vendor AND g.TransactionDate=p.TransactionDate
           WHERE p.Year=2014
           ORDER BY p.TransactionDate ASC"""
    ),
    "control_6": (
        "Control 6 - split purchase: two vendors",
        """WITH g AS (
             SELECT FullName, TransactionDate,
                    COUNT(*) AS TransactionCount,
                    COUNT(DISTINCT Vendor) AS VendorCount,
                    SUM(Amount) AS CombinedAmount
             FROM pcards WHERE Year=2014
             GROUP BY FullName, TransactionDate
             HAVING COUNT(*)=2 AND COUNT(DISTINCT Vendor)=2
                AND SUM(Amount)>5000
           )
           SELECT p.Amount, p.FullName AS Name, p.Description, p.Vendor,
                  p.TransactionDate, p.PostedDate, p.MCC,
                  g.VendorCount, ROUND(g.CombinedAmount,2) AS CombinedAmount
           FROM pcards p JOIN g
             ON g.FullName=p.FullName AND g.TransactionDate=p.TransactionDate
           WHERE p.Year=2014
           ORDER BY p.TransactionDate ASC"""
    ),
    "control_7": (
        "Control 7 - hotel/motel/resort/inn with food/restaurant MCC",
        """SELECT p.Amount, p.FullName AS Name, p.Description, p.Vendor,
                  p.TransactionDate, p.PostedDate, p.MCC
           FROM pcards p
           WHERE p.Year=2014
             AND EXISTS (
               SELECT 1 FROM pcards h
               WHERE h.Year=2014 AND h.FullName=p.FullName
                 AND h.TransactionDate=p.TransactionDate
                 AND (LOWER(COALESCE(h.MCC,'')) LIKE '%hotel%'
                   OR LOWER(COALESCE(h.MCC,'')) LIKE '%motel%'
                   OR LOWER(COALESCE(h.MCC,'')) LIKE '%resort%'
                   OR LOWER(COALESCE(h.MCC,'')) LIKE '%inn%')
             )
             AND EXISTS (
               SELECT 1 FROM pcards f
               WHERE f.Year=2014 AND f.FullName=p.FullName
                 AND f.TransactionDate=p.TransactionDate
                 AND (LOWER(COALESCE(f.MCC,'')) LIKE '%food%'
                   OR LOWER(COALESCE(f.MCC,'')) LIKE '%restaurant%')
             )
           ORDER BY p.FullName ASC, p.Amount ASC"""
    ),
}

STUDENT_CONTROL_DEFINITIONS = {
    # These seven are student-defined tests selected from documented
    # Part I P-card restrictions. The assignment leaves Controls 8-14
    # for the student to define; these are explicit, explainable tests.
    "control_8": {
        "title": "Control 8 - prepayments and deposits",
        "terms": ["prepayment", "prepaid", "deposit", "advance payment"],
    },
    "control_9": {
        "title": "Control 9 - student organization purchases",
        "terms": ["student organization", "student org", "student club", "student union"],
    },
    "control_10": {
        "title": "Control 10 - trade-in purchases",
        "terms": ["trade-in", "trade in", "tradein"],
    },
    "control_11": {
        "title": "Control 11 - conflict-of-interest indicators",
        "terms": ["conflict of interest", "conflict-of-interest", "family member", "immediate family", "financial interest"],
    },
    "control_12": {
        "title": "Control 12 - employee-owned company indicators",
        "terms": ["employee owned", "employee-owned", "employee owned company", "university employee owned"],
    },
    "control_13": {
        "title": "Control 13 - recurring monthly payments over $5,000 per year",
    },
    "control_14": {
        "title": "Control 14 - sales-tax indicators",
        "terms": ["sales tax", "tax charged", "tax", "sales-tax"],
    },
}

# Literal-search student-defined controls. These deliberately search
# Description/Vendor/MCC and do not claim that a keyword proves a violation.
for check_id, definition in STUDENT_CONTROL_DEFINITIONS.items():
    if check_id == "control_13":
        continue
    clauses = []
    for term in definition["terms"]:
        escaped = term.replace("'", "''")
        clauses.extend([
            f"LOWER(COALESCE(Description,'')) LIKE '%{escaped.lower()}%'",
            f"LOWER(COALESCE(Vendor,'')) LIKE '%{escaped.lower()}%'",
            f"LOWER(COALESCE(MCC,'')) LIKE '%{escaped.lower()}%'",
        ])
    CONTROL_CHECKS[check_id] = (
        definition["title"],
        f"""SELECT Year, Month, FullName AS Name, Description, Amount, Vendor,
                   TransactionDate, PostedDate, MCC
            FROM pcards
            WHERE Year=2014 AND ({' OR '.join(clauses)})
            ORDER BY TransactionDate ASC, Amount DESC"""
    )

# Control 13: a documented Part I risk is regular monthly maintenance,
# lease/rental, or service payments whose fiscal-year total exceeds $5,000.
# We flag recurring vendor activity (at least two months) with a 2014 total
# over $5,000; the result is a review population, not a conclusion that a PO
# was required or missing.
CONTROL_CHECKS["control_13"] = (
    STUDENT_CONTROL_DEFINITIONS["control_13"]["title"],
    """WITH monthly AS (
           SELECT FullName, Vendor, Month,
                  ROUND(SUM(Amount),2) AS MonthlyAmount
           FROM pcards
           WHERE Year=2014 AND Amount>0
             AND Vendor IS NOT NULL AND TRIM(Vendor)<>''
           GROUP BY FullName, Vendor, Month
         ), recurring AS (
           SELECT FullName, Vendor,
                  COUNT(*) AS MonthsWithPurchases,
                  ROUND(SUM(MonthlyAmount),2) AS AnnualVendorTotal
           FROM monthly
           GROUP BY FullName, Vendor
           HAVING COUNT(*)>=2 AND SUM(MonthlyAmount)>5000
         )
         SELECT r.FullName AS Name, r.Vendor,
                r.MonthsWithPurchases, r.AnnualVendorTotal AS SplitGroupTotal,
                p.Amount, p.Description, p.TransactionDate,
                p.PostedDate, p.MCC
         FROM recurring r
         JOIN pcards p
           ON p.Year=2014 AND p.FullName=r.FullName AND p.Vendor=r.Vendor
         ORDER BY r.AnnualVendorTotal DESC, p.TransactionDate ASC, p.Amount DESC"""
)

# Part III: Questions 1-4 plus four distinct student-defined fraud tests.
FRAUD_CHECKS = {
    "fraud_1": (
        "Question 1 - Benford analysis",
        """WITH d AS (
             SELECT CAST(substr(CAST(CAST(ABS(Amount) AS INTEGER) AS TEXT),1,1) AS INTEGER) Digit
             FROM pcards WHERE Year=2014 AND Amount>=1
           ), c AS (
             SELECT Digit, COUNT(*) N FROM d
             WHERE Digit BETWEEN 1 AND 9 GROUP BY Digit
           ), t AS (SELECT SUM(N) N FROM c)
           SELECT x.Digit,
                  ROUND(100.0*COALESCE(c.N,0)/NULLIF(t.N,0),3) AS Percentage
           FROM (SELECT 1 Digit UNION ALL SELECT 2 UNION ALL SELECT 3
                 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
                 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) x
           LEFT JOIN c ON c.Digit=x.Digit CROSS JOIN t
           ORDER BY x.Digit"""
    ),
    "fraud_2": (
        "Question 2 - potential duplicate transactions",
        """SELECT p.TransactionDate, p.Vendor, p.FullName, p.Amount
           FROM pcards p JOIN (
             SELECT TransactionDate, Vendor, FullName, Amount
             FROM pcards WHERE Year=2014 AND Amount>0
             GROUP BY TransactionDate, Vendor, FullName, Amount
             HAVING COUNT(*)>1
           ) d
             ON d.TransactionDate=p.TransactionDate AND d.Vendor=p.Vendor
            AND d.FullName=p.FullName AND d.Amount=p.Amount
           WHERE p.Year=2014 AND p.Amount>0
           ORDER BY p.TransactionDate ASC, p.Vendor ASC"""
    ),
    "fraud_3": (
        "Question 3 - employees repeatedly associated with possible duplicates",
        """WITH d AS (
             SELECT TransactionDate, Vendor, FullName, Amount
             FROM pcards WHERE Year=2014 AND Amount>0
             GROUP BY TransactionDate, Vendor, FullName, Amount
             HAVING COUNT(*)>1
           )
           SELECT FullName, COUNT(*) AS DuplicateOccasions
           FROM d GROUP BY FullName
           ORDER BY DuplicateOccasions DESC, FullName ASC"""
    ),
    "fraud_4": (
        "Question 4 - four-digit round-number transactions",
        """SELECT Amount, Vendor, Description, FullName
           FROM pcards
           WHERE Year=2014 AND Amount>0 AND Amount>=1000 AND Amount<10000
             AND ROUND(Amount,2)=CAST(Amount AS INTEGER)
             AND CAST(ROUND(Amount,2) AS INTEGER)%1000=0
           ORDER BY Vendor ASC, FullName ASC"""
    ),
    "fraud_5": (
        "Question 5 - repeated same-day vendor activity",
        """SELECT FullName, Vendor, TransactionDate,
                  COUNT(*) AS TransactionCount,
                  ROUND(SUM(Amount),2) AS CombinedAmount
           FROM pcards WHERE Year=2014 AND Amount>0
           GROUP BY FullName, Vendor, TransactionDate
           HAVING COUNT(*)>1 AND SUM(Amount)>5000
           ORDER BY CombinedAmount DESC, TransactionDate ASC"""
    ),
    "fraud_6": (
        "Question 6 - repeated vendor activity by employee",
        """SELECT FullName, Vendor, COUNT(*) AS TransactionCount,
                  ROUND(SUM(Amount),2) AS TotalAmount
           FROM pcards WHERE Year=2014 AND Amount>0
           GROUP BY FullName, Vendor
           HAVING COUNT(*)>=10
           ORDER BY TransactionCount DESC, TotalAmount DESC"""
    ),
    "fraud_7": (
        "Question 7 - weekend transactions",
        """SELECT Amount, FullName, Description, Vendor,
                  TransactionDate, PostedDate, MCC
           FROM pcards
           WHERE Year=2014 AND Amount>0
             AND CAST(strftime('%w',TransactionDate) AS INTEGER) IN (0,6)
           ORDER BY TransactionDate ASC, Amount DESC"""
    ),
    "fraud_8": (
        "Question 8 - exact round-dollar transactions",
        """SELECT Amount, FullName, Description, Vendor,
                  TransactionDate, PostedDate, MCC
           FROM pcards
           WHERE Year=2014 AND Amount>=1000
             AND Amount=CAST(Amount AS INTEGER)
           ORDER BY Amount DESC, Vendor ASC, FullName ASC"""
    ),
}

def check_catalog():
    items = []
    for key, (title, sql) in CONTROL_CHECKS.items():
        items.append({"id": key, "title": title, "type": "control"})
    for key, (title, sql) in FRAUD_CHECKS.items():
        items.append({"id": key, "title": title, "type": "fraud"})
    return items

# Google Gemini
def available_models(api_key):
    req = urlrequest.Request(
        "https://generativelanguage.googleapis.com/v1beta/models",
        method="GET",
        headers={"x-goog-api-key": api_key},
    )
    with urlrequest.urlopen(req, timeout=30) as response:
        data = json.load(response)
    return [
        m["name"].split("/", 1)[-1]
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]

def choose_models(api_key):
    configured = os.environ.get("GEMINI_MODEL", "").strip()
    try:
        available = available_models(api_key)
    except Exception:
        available = []

    preferred = [
        configured,
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    models = []
    for model in preferred:
        if model and model in available and model not in models:
            models.append(model)

    # If the key's model list differs from the preferred list, use available
    # non-image Flash models as fallbacks.
    for model in available:
        if (
            "flash" in model.lower()
            and "image" not in model.lower()
            and model not in models
        ):
            models.append(model)

    if not models:
        models = [configured or "gemini-3.8-flash"]

    return models


def gemini_request(api_key, model, payload):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model}:generateContent"
    )
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # 429/5xx responses can be transient. Keep the HTTP code and
        # Gemini message so the caller can decide whether to retry/fallback.
        message = detail[:800]
        raise RuntimeError(f"Gemini API request failed ({exc.code}): {message}")
    except urlerror.URLError as exc:
        raise RuntimeError(f"Could not reach the Gemini API: {exc.reason}")


def ask_ai_for_query(question):
    api_key = (os.environ.get("PCARD_AUDIT_GEMINI_KEY") or "").strip().strip("\"'")
    if not api_key:
        raise RuntimeError("AI is not configured. Set the PCARD_AUDIT_GEMINI_KEY Windows environment variable before starting the app.")

    instructions = f"""
Translate the user's P-card audit question into one safe SQLite query.

Schema: {SCHEMA}

Return JSON with exactly: title, sql, explanation.
Rules:
- sql must be one SELECT or WITH query only.
- Use only pcards and its listed fields.
- Never use semicolons or modification commands.
- Use calendar year 2014 when requested.
- Do not make semantic judgments about whether a transaction is prohibited or fraudulent.
- For keyword questions, use literal LIKE matching on the specified field.
"""
    payload = {
        "contents": [{"parts": [{"text": instructions + "\nQuestion: " + question}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    models = choose_models(api_key)
    last = None
    for model in models:
        for attempt in range(2):
            try:
                data = gemini_request(api_key, model, payload)
                last = None
                break
            except RuntimeError as exc:
                last = exc
                text_error = str(exc)
                # Gemini documents 429 and 5xx as retryable/transient in
                # appropriate cases. Retry once, then try the next model.
                retryable = any(code in text_error for code in ("(429)", "(500)", "(502)", "(503)", "(504)"))
                if retryable and attempt == 0:
                    time.sleep(2)
                    continue
                break
        if last is None:
            break

    if last is not None:
        raise last

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini returned an empty or invalid response.")

    try:
        plan = json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"^```json\s*|\s*```$", "", text.strip())
        try:
            plan = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini returned text that was not valid JSON.") from exc

    if not isinstance(plan, dict):
        raise RuntimeError("Gemini returned an unexpected response format.")

    sql = plan.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise RuntimeError("Gemini did not return a SQL query.")

    plan["sql"] = read_only_sql(sql)
    plan["title"] = str(plan.get("title") or "AI-generated query")
    plan["explanation"] = str(plan.get("explanation") or "")
    return plan


# Prohibited-purchase search terms from Part IV. These create review flags only;
# they never prove that a transaction is prohibited.
DIRECT_PROHIBITED_TERMS = {
    "alcohol": "Potential prohibited transaction - alcohol",
    "cash": "Potential prohibited transaction - cash/cash advance",
    "cash advance": "Potential prohibited transaction - cash/cash advance",
    "atm": "Potential prohibited transaction - ATM/cash",
    "decoration": "Potential prohibited transaction - decorations",
    "decorations": "Potential prohibited transaction - decorations",
    "donation": "Potential prohibited transaction - donation/sponsorship",
    "sponsorship": "Potential prohibited transaction - donation/sponsorship",
    "gasoline": "Potential prohibited transaction - gasoline",
    "fuel": "Potential prohibited transaction - gasoline/fuel",
    "gift": "Potential prohibited transaction - gift/gift card",
    "gift card": "Potential prohibited transaction - gift/gift card",
    "gift certificate": "Potential prohibited transaction - gift certificate",
    "insurance": "Potential prohibited transaction - insurance",
    "late fee": "Potential prohibited transaction - late fee",
    "late charge": "Potential prohibited transaction - late fee",
    "postage": "Potential prohibited transaction - mail/postage",
    "post office": "Potential prohibited transaction - mail/postage",
    "postal": "Potential prohibited transaction - mail/postage",
    "moving": "Potential prohibited transaction - moving expense",
    "personal": "Potential prohibited transaction - personal purchase",
    "membership": "Potential prohibited transaction - personal/individual membership",
    "dues": "Potential prohibited transaction - membership dues",
    "salary": "Potential prohibited transaction - salary/wages/benefits",
    "salaries": "Potential prohibited transaction - salary/wages/benefits",
    "wage": "Potential prohibited transaction - salary/wages/benefits",
    "wages": "Potential prohibited transaction - salary/wages/benefits",
    "benefit": "Potential prohibited transaction - salary/wages/benefits",
    "benefits": "Potential prohibited transaction - salary/wages/benefits",
    "incentive": "Potential prohibited transaction - service/incentive award",
    "award": "Potential prohibited transaction - service/incentive award",
}

TRAVEL_FOOD_TERMS = ("food", "restaurant", "meal", "meals")
HOTEL_MCC_TERMS = ("hotel", "motel", "resort", "inn")

def normalized_search_term(term):
    return re.sub(r"\s+", " ", (term or "").strip().lower())

def add_prohibited_flags(rows, field, term, db):
    """Add conservative review flags for prohibited-purchase searches.

    A keyword match is never treated as proof of a violation. Restaurant/food
    is special: Part II/IV's travel-food control requires a hotel/motel/resort/inn
    MCC for the same employee on the same day.
    """
    normalized = normalized_search_term(term)
    direct_label = DIRECT_PROHIBITED_TERMS.get(normalized)

    # Build the same-day hotel context once, rather than running a query for every row.
    hotel_days = set()
    if normalized in TRAVEL_FOOD_TERMS or any(x in normalized for x in TRAVEL_FOOD_TERMS):
        hotel_rows = db.execute(
            """SELECT DISTINCT FullName, TransactionDate
               FROM pcards
               WHERE Year=2014
                 AND (LOWER(COALESCE(MCC,'')) LIKE '%hotel%'
                   OR LOWER(COALESCE(MCC,'')) LIKE '%motel%'
                   OR LOWER(COALESCE(MCC,'')) LIKE '%resort%'
                   OR LOWER(COALESCE(MCC,'')) LIKE '%inn%')"""
        ).fetchall()
        hotel_days = {(r["FullName"], r["TransactionDate"]) for r in hotel_rows}

    for row in rows:
        flags = row.setdefault("_flags", [])

        # Search-term-driven prohibited flag. This is intentionally a review flag.
        if direct_label:
            flags.append({"level": "review", "label": direct_label})

        # Food/restaurant requires the hotel context from the assignment.
        if normalized in TRAVEL_FOOD_TERMS or any(x in normalized for x in TRAVEL_FOOD_TERMS):
            key = (row.get("FullName"), row.get("TransactionDate"))
            if key in hotel_days:
                flags.append({
                    "level": "high",
                    "label": "Potential prohibited transaction - food/restaurant with hotel travel context (Control 7)",
                })
            else:
                # Explain why the literal search itself is not enough.
                flags.append({
                    "level": "review",
                    "label": "Food/restaurant keyword - verify travel status / Control 7",
                })

        # Avoid duplicate labels if a term happens to match multiple rules.
        seen = set()
        row["_flags"] = [f for f in flags if not (f["label"] in seen or seen.add(f["label"]))]


# Search dashboard: Description and Vendor are separate literal searches.
def search_transactions(args):
    field = (args.get("field") or "").strip()
    keyword = (args.get("keyword") or "").strip()
    if field not in ("Description", "Vendor", "MCC"):
        raise ValueError("Search field must be Description or Vendor.")
    if not keyword:
        raise ValueError("Enter a keyword.")

    if args.get("start_date") and not args.get("transaction_from"):
        args = dict(args)
        args["transaction_from"] = args.get("start_date")
    if args.get("end_date") and not args.get("transaction_to"):
        args = dict(args)
        args["transaction_to"] = args.get("end_date")

    where, params = filters(args, include_description=False)
    where.append(f"{field} LIKE ? COLLATE NOCASE")
    params.append("%" + keyword + "%")

    sql = f"""SELECT Year, Month, FullName, Description, Amount, Vendor,
                     TransactionDate, PostedDate, MCC
              FROM pcards
              WHERE {' AND '.join(where)}
              ORDER BY TransactionDate ASC, Amount DESC
              LIMIT 1000"""

    with connect() as db:
        rows = as_dicts(db.execute(sql, params).fetchall())

    for row in rows:
        row["match_field"] = field
        row["match_term"] = keyword
        row["match_type"] = "literal keyword match"

        # Always make the literal search visible as a review flag.
        # A keyword hit is not a classification or proof of a violation.
        row["_flags"] = [{
            "level": "review",
            "label": (
                f'Keyword match — {field}: "{keyword}"'
            )
        }]

        row["follow_up_flags"] = [
            f'Keyword match — {field}: "{keyword}"'
        ]

        if row["Amount"] is not None and float(row["Amount"]) > 5000:
            row["_flags"].append({
                "level": "high",
                "label": (
                    "Potential Control 3 review - "
                    "transaction over $5,000"
                )
            })
            row["follow_up_flags"].append(
                "Potential Control 3 review - transaction over $5,000"
            )
    return rows



def check_possible_vendor_split(db, year):
    """
    One Click Assistant: Possible vendor split.

    Control 4:
      Same employee + same vendor + same calendar day.
      Multiple transactions, each <= $5,000.
      Combined total > $5,000.

    Control 5:
      Exactly two different employees/cardholders + same vendor +
      same calendar day.
      Multiple transactions, each <= $5,000.
      Combined total > $5,000.
      Exact same-employee/vendor/date/amount duplicate payments are
      excluded from the Control 5 population.
    """

    source = db.execute(
        """
        SELECT
            Year, Month, FullName, Description, Amount, Vendor,
            TransactionDate, PostedDate, MCC
        FROM pcards
        WHERE Year = ?
          AND Amount > 0
          AND Amount <= 5000
          AND Vendor IS NOT NULL
          AND TRIM(Vendor) <> ''
        ORDER BY TransactionDate, Vendor, FullName, Amount
        """,
        (year,)
    ).fetchall()

    rows = [dict(r) for r in source]

    # Normalize only for grouping. The original transaction values are
    # preserved for display.
    vendor_day = {}

    for row in rows:
        vendor = re.sub(
            r"\s+",
            " ",
            str(row.get("Vendor") or "").strip()
        ).casefold()

        # The database stores values such as "5/19/2014 0:00:00".
        # The control is based on the calendar day, not the time.
        raw_date = str(
            row.get("TransactionDate") or ""
        ).strip()

        day = raw_date.split(" ")[0]

        employee = str(
            row.get("FullName") or ""
        ).strip()

        if not vendor or not day or not employee:
            continue

        vendor_day.setdefault(
            (vendor, day),
            []
        ).append(row)

    results = []

    # ------------------------------------------------------------
    # CONTROL 4
    # Same employee + same vendor + same day
    # ------------------------------------------------------------

    for (vendor_key, day), group in vendor_day.items():

        by_employee = {}

        for row in group:
            employee_key = str(
                row.get("FullName") or ""
            ).strip().casefold()

            by_employee.setdefault(
                employee_key,
                []
            ).append(row)

        for employee_key, employee_rows in by_employee.items():

            if len(employee_rows) < 2:
                continue

            group_total = sum(
                float(r.get("Amount") or 0)
                for r in employee_rows
            )

            if group_total <= 5000:
                continue

            group_id = (
                f"C4|{vendor_key}|{day}|{employee_key}"
            )

            for row in employee_rows:

                item = dict(row)

                item.update({
                    "SplitGroup": group_id,
                    "SplitGroupTotal": round(
                        group_total,
                        2
                    ),
                    "SplitControl": "Control 4",
                    "_flags": [{
                        "level": "high",
                        "label": (
                            "Control 4 — Possible vendor split "
                            "(same employee/vendor/day)"
                        )
                    }]
                })

                results.append(item)

    # ------------------------------------------------------------
    # CONTROL 5
    # Two different employees + same vendor + same day
    #
    # First remove exact duplicate-payment candidates:
    # same employee + vendor + date + amount.
    # ------------------------------------------------------------

    for (vendor_key, day), group in vendor_day.items():

        deduped = []

        seen_duplicate_signature = set()

        for row in group:

            employee_key = str(
                row.get("FullName") or ""
            ).strip().casefold()

            amount = round(
                float(row.get("Amount") or 0),
                2
            )

            signature = (
                employee_key,
                vendor_key,
                day,
                amount
            )

            # If the same employee/vendor/day/amount occurs more than
            # once, treat those repeated charges as possible duplicate
            # payments rather than using them to inflate Control 5.
            if signature in seen_duplicate_signature:
                continue

            seen_duplicate_signature.add(signature)
            deduped.append(row)

        employees = {
            str(r.get("FullName") or "")
            .strip()
            .casefold()
            for r in deduped
            if str(r.get("FullName") or "").strip()
        }

        # Assignment simplifies Control 5 to two cardholders.
        if len(employees) != 2:
            continue

        if len(deduped) < 2:
            continue

        group_total = sum(
            float(r.get("Amount") or 0)
            for r in deduped
        )

        if group_total <= 5000:
            continue

        group_id = (
            f"C5|{vendor_key}|{day}"
        )

        for row in deduped:

            item = dict(row)

            item.update({
                "SplitGroup": group_id,
                "SplitGroupTotal": round(
                    group_total,
                    2
                ),
                "SplitControl": "Control 5",
                "_flags": [{
                    "level": "high",
                    "label": (
                        "Control 5 — Possible vendor split "
                        "(two cardholders/same vendor/day)"
                    )
                }]
            })

            results.append(item)

    # Keep each group together for the frontend rowspan/group-total
    # rendering. Sort by transaction date as required by the assignment.
    results.sort(
        key=lambda r: (
            str(r.get("TransactionDate") or ""),
            str(r.get("Vendor") or ""),
            str(r.get("SplitControl") or ""),
            str(r.get("SplitGroup") or ""),
            str(r.get("FullName") or ""),
            float(r.get("Amount") or 0)
        )
    )

    return results


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/years")
def api_years():
    with connect() as db:
        rows = db.execute(
            "SELECT DISTINCT Year FROM pcards WHERE Year IS NOT NULL ORDER BY Year"
        ).fetchall()
    return jsonify({"years": [r["Year"] for r in rows]})


def resolve_check_id(raw):
    value = str(raw or "").strip()
    if value in CONTROL_CHECKS or value in FRAUD_CHECKS:
        return value

    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if normalized in CONTROL_CHECKS or normalized in FRAUD_CHECKS:
        return normalized

    # Accept common frontend values such as control1, control-1, q1, question1, fraud1.
    m = re.search(r"(?:control|check|question|q|fraud)[_\-\s]*(\d+)", normalized)
    if m:
        n = m.group(1)
        if re.search(r"(?:fraud|question|q)", normalized):
            candidate = f"fraud_{n}"
            if candidate in FRAUD_CHECKS:
                return candidate
        candidate = f"control_{n}"
        if candidate in CONTROL_CHECKS:
            return candidate

    # Also match the visible button title.
    for key, (title, _) in {**CONTROL_CHECKS, **FRAUD_CHECKS}.items():
        if value.lower() == title.lower():
            return key

    # Tolerate frontend labels such as "same vendor split" / "same-vendor-split".
    # Accept the short IDs used by some versions of the front end.
    aliases = {
        "annual_limit": "control_1",
        "annual_spending": "control_1",
        "yearly_limit": "control_1",
        "monthly_limit": "control_2",
        "monthly_spending": "control_2",
        "transaction_limit": "control_3",
        "single_transaction_limit": "control_3",
        "over_5000": "control_3",
        "possible_vendor_split": "control_4",
        "vendor_split": "control_4",
        "same_person_vendor_day": "control_4",
        "same_vendor_split": "control_4",
        "person_vendor_day": "control_4",
        "two_cardholders": "control_5",
        "two_cardholder_split": "control_5",
        "two_vendors": "control_6",
        "two_vendor_split": "control_6",
        "hotel_food": "control_7",
        "hotel_restaurant": "control_7",
        "round_number": "fraud_4",
        "round_numbers": "fraud_4",
        "round_number_pattern": "fraud_4",
        "round_numbers_pattern": "fraud_4",
        "four_digit_round": "fraud_4",
        "four_digit_round_number": "fraud_4",
        "four_digit_round_numbers": "fraud_4",
        "round_dollar": "fraud_8",
        "round_dollars": "fraud_8",
        "round_dollar_transactions": "fraud_8",
    }
    if normalized in aliases:
        return aliases[normalized]

    if "split" in normalized and "vendor" in normalized:
        if "two vendor" in normalized or "two_vendors" in normalized:
            return "control_6"
        if "cardholder" in normalized or "same_vendor" in normalized or "vendor_split" in normalized:
            return "control_4"
        if "person" in normalized:
            return "control_4"
        return "control_4"

    return None


@app.route("/api/control-check", methods=["GET", "POST"])
def api_control_check():
    data = request.get_json(silent=True) or request.args
    raw_check = data.get("check") or data.get("check_id") or data.get("id")
    check_id = resolve_check_id(raw_check)
    if not check_id:
        return jsonify({"error": f"Unknown check: {raw_check or '(empty)' }"}), 400

    raw_normalized = re.sub(r"[^a-z0-9]+", "_", str(raw_check or "").lower()).strip("_")

    # The UI intentionally has ONE "Possible vendor split" assistant.
    # Some versions of the button send possible_vendor_split, while older
    # versions send control_4. Treat all of these as the combined
    # Control 4 + Control 5 assistant.
    is_vendor_split = raw_normalized in {
        "possible_vendor_split",
        "vendor_split",
        "possible_split_purchase",
        "same_vendor_split",
        "control_4",
        "control_5",
    }

    year = data.get("year") or "2014"
    if is_vendor_split:
        try:
            year = int(year)
            if not 1900 <= year <= 2100:
                raise ValueError("Invalid year.")
            with connect() as db:
                rows = check_possible_vendor_split(db, year)
            return jsonify({
                "id": "possible_vendor_split",
                "title": "Possible vendor split — Controls 4 and 5",
                "rows": rows, "results": rows, "count": len(rows),
                "match_notes": [
                    "Control 4: same employee, same vendor and same calendar day; group total exceeds $5,000.",
                    "Control 5: exactly two cardholders, same vendor and same calendar day; group total exceeds $5,000.",
                    "Each included transaction is $5,000 or less.",
                    "Control 5 excludes repeated same-employee/vendor/date/amount charges treated as possible duplicate payments.",
                    "These are potential follow-up items, not confirmed violations."
                ],
                "potential_follow_up": True
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    check = CONTROL_CHECKS.get(check_id) or FRAUD_CHECKS.get(check_id)
    title, sql = check

    try:
        year = int(year)
        if not 1900 <= year <= 2100:
            raise ValueError("Invalid year.")
        sql = re.sub(r"\bYear\s*=\s*2014\b", f"Year={year}", sql)
        rows = run_sql(sql)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    apply_result_flags(rows, check_id=check_id)

    return jsonify({
        "id": check_id,
        "title": title,
        "rows": rows,
        "results": rows,
        "count": len(rows),
        "match_notes": ["Potential follow-up items; not confirmed violations."],
        "potential_follow_up": True,
    })

@app.route("/api/fraud-check", methods=["GET", "POST"])
def api_fraud_check():
    return api_control_check()

@app.route("/api/search", methods=["GET", "POST"])
def api_search():
    args = request.get_json(silent=True) if request.is_json else request.args
    field = (args.get("field") or "").strip()
    term = (args.get("term") or "").strip()

    if field not in ("Description", "Vendor", "MCC"):
        field = None

    try:
        # Map the existing UI date ids to the backend filter names.
        if args.get("start_date") and not args.get("transaction_from"):
            args = dict(args)
            args["transaction_from"] = args.get("start_date")
        if args.get("end_date") and not args.get("transaction_to"):
            args = dict(args)
            args["transaction_to"] = args.get("end_date")

        where, params = filters(args, include_description=False)

        # The normal Explore search is driven by the common filters.
        # Description/Vendor searches are literal field searches.
        if field and term:
            where.append(f"{field} LIKE ? COLLATE NOCASE")
            params.append("%" + term + "%")

        # Preserve the existing frontend's `highlight=prohibited` behavior
        # without guessing or semantically classifying transactions.
        sql = f"""SELECT Year, Month, FullName, Description, Amount, Vendor,
                         TransactionDate, PostedDate, MCC
                  FROM pcards
                  WHERE {' AND '.join(where) if where else '1=1'}
                  ORDER BY TransactionDate ASC, Amount DESC
                  LIMIT 1000"""

        with connect() as db:
            rows = as_dicts(db.execute(sql, params).fetchall())

            for row in rows:
                row["_flags"] = []
            if field and term:
                add_prohibited_flags(rows, field, term, db)
            else:
                apply_result_flags(rows, context=term, db=db)

        if field and term:
            match_notes = [
                f"Literal keyword match in {field}: {term}",
                "Potential prohibited-purchase flags are review indicators, not confirmed violations.",
            ]
            if normalized_search_term(term) in TRAVEL_FOOD_TERMS:
                match_notes.append("Food/restaurant is specially checked against same-day hotel/motel/resort/inn MCC activity under Control 7.")
        else:
            match_notes = [
                "Results are based on the selected filters.",
                "Flags are potential follow-up items, not confirmed violations.",
            ]

        return jsonify({
            "rows": rows,
            "results": rows,
            "count": len(rows),
            "match_notes": match_notes,
        })
    except (ValueError, sqlite3.Error) as exc:
        return jsonify({"error": str(exc)}), 400

@app.route("/api/suggest")
def api_suggest():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({
            "warning": "Enter a search idea first. Suggestions are literal field searches only.",
            "suggestions": [],
        })

    # Never classify the transaction. Offer the same literal phrase in separate fields.
    suggestions = [
        {"field": "Description", "term": q, "label": "Description"},
        {"field": "Vendor", "term": q, "label": "Vendor"},
        {"field": "MCC", "term": q, "label": "MCC"},
    ]

    return jsonify({
        "warning": "Choose a field: these are literal keyword searches only and do not determine whether a purchase is prohibited.",
        "suggestions": suggestions,
    })


def apply_result_flags(rows, context="", check_id=None, db=None):
    """Attach conservative, explainable review flags to returned rows.

    Flags are based on explicit numeric limits, the selected audit test, or
    literal terms present in the user's question. They are never proof of a
    prohibited purchase or fraud.
    """
    context_l = (context or "").lower()
    context_terms = re.sub(r"[^a-z0-9]+", " ", context_l).split()

    def number(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    prohibited_context = {
        "alcohol": "Potential prohibited transaction - alcohol",
        "cash": "Potential prohibited transaction - cash/cash advance/ATM",
        "atm": "Potential prohibited transaction - cash/cash advance/ATM",
        "decoration": "Potential prohibited transaction - decorations",
        "decorations": "Potential prohibited transaction - decorations",
        "donation": "Potential prohibited transaction - donation/sponsorship",
        "sponsorship": "Potential prohibited transaction - donation/sponsorship",
        "gasoline": "Potential prohibited transaction - gasoline/fuel",
        "fuel": "Potential prohibited transaction - gasoline/fuel",
        "gift": "Potential prohibited transaction - gift/gift card",
        "gift card": "Potential prohibited transaction - gift/gift card",
        "gift certificate": "Potential prohibited transaction - gift certificate",
        "insurance": "Potential prohibited transaction - insurance",
        "late fee": "Potential prohibited transaction - late fee",
        "postage": "Potential prohibited transaction - mail/postage",
        "post office": "Potential prohibited transaction - mail/postage",
        "moving": "Potential prohibited transaction - moving expense",
        "personal": "Potential prohibited transaction - personal purchase",
        "membership": "Potential prohibited transaction - personal/individual membership",
        "dues": "Potential prohibited transaction - membership dues",
        "salary": "Potential prohibited transaction - salary/wages/benefits",
        "wage": "Potential prohibited transaction - salary/wages/benefits",
        "benefit": "Potential prohibited transaction - salary/wages/benefits",
        "incentive": "Potential prohibited transaction - service/incentive award",
        "award": "Potential prohibited transaction - service/incentive award",
    }

    for row in rows:
        flags = row.setdefault("_flags", [])

        # A transaction over $5,000 is a Control 3 issue. Do NOT label
        # aggregate/group totals as an individual transaction over the limit.
        # Split-purchase groups get their own Control 4-6 flag below.
        amount = number(row.get("Amount"))
        if amount is not None and amount > 5000:
            flags.append({"level": "high", "label": "Potential Control 3 review - transaction over $5,000"})

        if check_id:
            labels = {
                "control_1": ("Potential Control 1 review - annual spending over $50,000", "high"),
                "control_2": ("Potential Control 2 review - monthly spending over $10,000", "high"),
                "control_3": ("Potential Control 3 review - transaction over $5,000", "high"),
                "control_4": ("Potential split-purchase review - same person/vendor/day; group total exceeds $5,000 (Control 4)", "high"),
                "control_5": ("Potential split-purchase review - two cardholders/same vendor/day (Control 5)", "high"),
                "control_6": ("Potential split-purchase review - two vendors/same person/day (Control 6)", "high"),
                "control_7": ("Potential prohibited transaction - food/restaurant with hotel travel context (Control 7)", "high"),
                "control_8": ("Control 8 — Potential prepayment/deposit review", "review"),
                "control_9": ("Control 9 — Potential student-organization purchase review", "review"),
                "control_10": ("Control 10 — Potential trade-in purchase review", "review"),
                "control_11": ("Control 11 — Potential conflict-of-interest indicator", "review"),
                "control_12": ("Control 12 — Potential employee-owned-company indicator", "review"),
                "control_13": ("Control 13 — Potential recurring annual vendor total over $5,000", "review"),
                "control_14": ("Control 14 — Potential sales-tax review", "review"),
            }
            if check_id in labels:
                label, level = labels[check_id]
                if check_id == "control_4":
                    group_total = number(row.get("SplitGroupTotal"))
                    if group_total is not None:
                        label = (
                            "Potential split-purchase review - same person/vendor/day; "
                            f"group total {group_total:,.2f} exceeds $5,000 (Control 4)"
                        )
                flags.append({"level": level, "label": label})
            elif check_id.startswith("control_"):
                flags.append({"level": "review", "label": f"{check_id} — student-defined control review"})
            elif check_id.startswith("fraud_"):
                fraud_labels = {
                    "fraud_1": "Fraud-risk indicator - Benford analysis",
                    "fraud_2": "Potential duplicate transaction",
                    "fraud_3": "Employee associated with potential duplicates",
                    "fraud_4": "Fraud-risk indicator - four-digit round amount",
                    "fraud_5": "Fraud-risk indicator - repeated same-day vendor activity",
                    "fraud_6": "Fraud-risk indicator - repeated vendor activity by employee",
                    "fraud_7": "Fraud-risk indicator - weekend transaction",
                    "fraud_8": "Fraud-risk indicator - round-dollar transaction",
                }
                if check_id in fraud_labels:
                    flags.append({"level": "review", "label": fraud_labels[check_id]})

        # ------------------------------------------------------------
        # AI ASSISTANT: attach a visible review flag to every returned row.
        #
        # The AI is a query generator. It should not silently classify
        # transactions, but every returned transaction should still show
        # why it is in the review population.
        # ------------------------------------------------------------

        if context_l:
            flags.append({
                "level": "review",
                "label": "AI query result — potential follow-up item"
            })

            # Map explicit assignment-control language in the user's
            # natural-language question to a visible control flag.
            # This is a label for why the row was returned, not an
            # automatic determination that a violation occurred.
            ai_control_phrases = {
                "control_8": ["control 8", "prepayment", "prepayments", "deposit", "deposits"],
                "control_9": ["control 9", "student organization", "student org", "student club"],
                "control_10": ["control 10", "trade-in", "trade in", "tradein"],
                "control_11": ["control 11", "conflict of interest", "conflict-of-interest", "financial interest", "immediate family"],
                "control_12": ["control 12", "employee owned", "employee-owned", "university employee owned"],
                "control_13": ["control 13", "regular monthly payments", "recurring monthly payments", "maintenance", "lease", "rental", "service agreement"],
                "control_14": ["control 14", "sales tax", "sales-tax", "tax charged"],
            }

            for ai_control, phrases in ai_control_phrases.items():
                if any(phrase in context_l for phrase in phrases):
                    title = STUDENT_CONTROL_DEFINITIONS[ai_control]["title"]
                    flags.append({
                        "level": "review",
                        "label": f"{title} — AI query match"
                    })
                    break

            # Explicit $5,000 transaction-limit question.
            if (
                "5000" in context_l
                or "$5,000" in context_l
                or "5,000" in context_l
                or "single transaction limit" in context_l
                or "transaction limit" in context_l
            ):
                if amount is not None and amount > 5000:
                    flags.append({
                        "level": "high",
                        "label": (
                            "Control 3 — transaction amount exceeds $5,000"
                        )
                    })

            # Four-digit round-number fraud test.
            if (
                "round number" in context_l
                or "round-number" in context_l
                or "four digit" in context_l
                or "four-digit" in context_l
                or "ends in 000" in context_l
            ):
                if (
                    amount is not None
                    and amount > 0
                    and float(amount).is_integer()
                    and int(abs(amount)) >= 1000
                    and int(abs(amount)) % 1000 == 0
                ):
                    flags.append({
                        "level": "review",
                        "label": (
                            "Fraud-risk indicator — four-digit round amount"
                        )
                    })

            # Duplicate-payment question.
            if (
                "duplicate" in context_l
                or "duplicates" in context_l
                or "double payment" in context_l
                or "double payments" in context_l
            ):
                flags.append({
                    "level": "review",
                    "label": (
                        "Fraud-risk indicator — possible duplicate transaction"
                    )
                })

            # --------------------------------------------------------
            # Possible vendor split.
            #
            # Match AI-returned transactions against the deterministic
            # Control 4/5 population. This makes the AI output carry the
            # same control-specific flag as the One Click Assistant.
            # --------------------------------------------------------

            split_context = any(
                phrase in context_l
                for phrase in (
                    "vendor split",
                    "split purchase",
                    "split-purchase",
                    "control 4",
                    "control 5",
                    "two cardholders",
                    "two employees",
                    "multiple cardholders",
                    "multiple employees",
                )
            )

            if split_context and db is not None:

                try:
                    target_year = int(
                        row.get("Year") or 2014
                    )

                    split_rows = check_possible_vendor_split(
                        db,
                        target_year
                    )

                    def split_signature(item):
                        vendor = re.sub(
                            r"\s+",
                            " ",
                            str(item.get("Vendor") or "").strip()
                        ).casefold()

                        day = str(
                            item.get("TransactionDate") or ""
                        ).split(" ")[0]

                        employee = str(
                            item.get("FullName") or ""
                        ).strip().casefold()

                        value = number(
                            item.get("Amount")
                        )

                        return (
                            vendor,
                            day,
                            employee,
                            round(value, 2)
                            if value is not None
                            else None
                        )

                    current_signature = split_signature(row)

                    matching_split_rows = [
                        item
                        for item in split_rows
                        if split_signature(item) ==
                        current_signature
                    ]

                    controls_found = {
                        str(item.get("SplitControl"))
                        for item in matching_split_rows
                    }

                    if (
                        "Control 5" in controls_found
                        and (
                            "control 5" in context_l
                            or "two cardholders" in context_l
                            or "two employees" in context_l
                            or "multiple cardholders" in context_l
                            or "multiple employees" in context_l
                            or "vendor split" in context_l
                            or "split purchase" in context_l
                            or "split-purchase" in context_l
                        )
                    ):
                        group_total = next(
                            (
                                number(
                                    item.get("SplitGroupTotal")
                                )
                                for item in matching_split_rows
                                if number(
                                    item.get("SplitGroupTotal")
                                ) is not None
                            ),
                            None
                        )

                        label = (
                            "Control 5 — Possible vendor split "
                            "(two cardholders/same vendor/day)"
                        )

                        if group_total is not None:
                            label += (
                                f"; group total "
                                f"${group_total:,.2f}"
                            )

                        flags.append({
                            "level": "high",
                            "label": label
                        })

                    elif "Control 4" in controls_found:
                        group_total = next(
                            (
                                number(
                                    item.get("SplitGroupTotal")
                                )
                                for item in matching_split_rows
                                if number(
                                    item.get("SplitGroupTotal")
                                ) is not None
                            ),
                            None
                        )

                        label = (
                            "Control 4 — Possible vendor split "
                            "(same employee/vendor/day)"
                        )

                        if group_total is not None:
                            label += (
                                f"; group total "
                                f"${group_total:,.2f}"
                            )

                        flags.append({
                            "level": "high",
                            "label": label
                        })

                except Exception:
                    # A flagging failure must never prevent the AI query
                    # itself from returning its read-only results.
                    pass

        # AI/search question: only flag a prohibited category when its literal
        # term is present in both the question and returned transaction fields.
        combined_text = " ".join(str(row.get(k) or "") for k in ("Description", "Vendor", "MCC")).lower()
        for term, label in prohibited_context.items():
            if term in context_l and term in combined_text:
                flags.append({"level": "review", "label": label})

        # Travel-food rule from the assignment: food/restaurant alone is not
        # enough; require same employee + same day hotel-like MCC context.
        if any(t in context_l for t in TRAVEL_FOOD_TERMS) and any(t in combined_text for t in TRAVEL_FOOD_TERMS):
            hotel = False
            if db is not None:
                hit = db.execute(
                    """SELECT 1 FROM pcards WHERE Year=? AND FullName=? AND TransactionDate=?
                       AND (LOWER(COALESCE(MCC,'')) LIKE '%hotel%'
                         OR LOWER(COALESCE(MCC,'')) LIKE '%motel%'
                         OR LOWER(COALESCE(MCC,'')) LIKE '%resort%'
                         OR LOWER(COALESCE(MCC,'')) LIKE '%inn%') LIMIT 1""",
                    (row.get("Year") or 2014, row.get("FullName") or row.get("Name"), row.get("TransactionDate")),
                ).fetchone()
                hotel = hit is not None
            flags.append({
                "level": "high" if hotel else "review",
                "label": "Potential prohibited transaction - food/restaurant with hotel travel context (Control 7)" if hotel
                         else "Food/restaurant keyword - verify travel status / Control 7",
            })

        seen = set()
        row["_flags"] = [f for f in flags if not (f["label"] in seen or seen.add(f["label"]))]
    return rows

@app.route("/api/ai/solve", methods=["POST"])
def api_ai_solve():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Please enter a question."}), 400
    try:
        plan = ask_ai_for_query(question)
        with connect() as db:
            rows = run_sql(plan["sql"])
            apply_result_flags(rows, context=question, db=db)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        # Return a useful client error instead of a generic Flask 500.
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        # Last-resort guard: the UI should never receive an opaque 500 for
        # an AI response-shape or serialization problem.
        app.logger.exception("AI solve failed")
        return jsonify({"error": f"AI assistant error: {type(exc).__name__}: {exc}"}), 500
    columns = list(rows[0].keys()) if rows else [
        "Year", "Month", "FullName", "Description", "Amount",
        "Vendor", "TransactionDate", "PostedDate", "MCC"
    ]
    return jsonify({
        # Keep several response aliases so the existing front-end can render
        # the AI result without requiring a front-end rewrite.
        "title": plan.get("title", "AI-generated query"),
        "sql": plan["sql"],
        "query": plan["sql"],
        "explanation": plan.get("explanation", ""),
        "rows": rows,
        "results": rows,
        "data": rows,
        "columns": columns,
        "count": len(rows),
        "match_notes": [
            "AI generated a read-only query; returned rows are not automatically classified as violations.",
            "Every AI result receives a visible potential-follow-up flag.",
            "Control-specific flags are added when the question corresponds to an explicit assignment control and the returned transaction matches that control.",
        ],
        "read_only": True,
        "plan": {
            "title": plan.get("title", "AI-generated query"),
            "sql": plan["sql"],
            "explanation": plan.get("explanation", ""),
        },
    })

@app.route("/api/transaction/<int:rowid>")
def api_transaction(rowid):
    with connect() as db:
        row = db.execute(
            "SELECT rowid AS _rowid, * FROM pcards WHERE rowid=?",
            (rowid,),
        ).fetchone()
    if row is None:
        return jsonify({"error": "Transaction not found."}), 404
    return jsonify(dict(row))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
