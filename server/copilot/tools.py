"""Ops Copilot tools — read-only wrappers over the same data the API endpoints
serve, plus a single proposal-only mutation (`propose_restocking_order`).

Design notes:
- All tools are in-process Python — no HTTP loopback to FastAPI. They call the
  same `apply_filters`/`filter_by_month` helpers and the `mock_data` module.
- Every tool returns a dict (or list of dicts) that JSON-serializes cleanly so
  it can be sent straight back to the model as the tool_result content.
- The schemas live next to the functions and are exported as TOOL_SCHEMAS so
  the agent loop can pass them to Anthropic verbatim.
- `propose_restocking_order` does NOT persist — it returns a draft plus a
  signed `proposal_id` the server stores; the UI must call /api/copilot/approve
  to actually submit it. This is the safety boundary that keeps prompt
  injection from triggering writes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from typing import Any, Callable, Dict, List, Optional

from mock_data import (
    inventory_items,
    orders,
    demand_forecasts,
    backlog_items,
    spending_summary,
    monthly_spending,
    category_spending,
    purchase_orders,
)

# Quarter mapping mirrors main.py — kept here to avoid pulling main into tests.
_QUARTER_MAP = {
    "Q1-2025": ["2025-01", "2025-02", "2025-03"],
    "Q2-2025": ["2025-04", "2025-05", "2025-06"],
    "Q3-2025": ["2025-07", "2025-08", "2025-09"],
    "Q4-2025": ["2025-10", "2025-11", "2025-12"],
}


def _apply_filters(
    items: list,
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list:
    out = items
    if warehouse and warehouse != "all":
        out = [i for i in out if i.get("warehouse") == warehouse]
    if category and category != "all":
        out = [i for i in out if i.get("category", "").lower() == category.lower()]
    if status and status != "all":
        out = [i for i in out if i.get("status", "").lower() == status.lower()]
    return out


def _filter_by_month(items: list, month: Optional[str]) -> list:
    if not month or month == "all":
        return items
    if month.startswith("Q") and month in _QUARTER_MAP:
        months = _QUARTER_MAP[month]
        return [i for i in items if any(m in i.get("order_date", "") for m in months)]
    return [i for i in items if month in i.get("order_date", "")]


# ---------------------------------------------------------------------------
# Read-only tools (auto-execute, no proposal step)
# ---------------------------------------------------------------------------

def get_inventory_items(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    sort_by: str = "value",
    limit: int = 20,
) -> list[dict]:
    """Return individual inventory item rows (not aggregates).

    Use this when the user asks about a specific SKU, the highest/lowest item
    by some dimension, or otherwise needs row-level data. `sort_by`:
    `"value"` (qty × unit_cost desc — default), `"quantity"` (qty_on_hand
    desc), `"unit_cost"` (desc), `"shortage"` (gap below reorder desc).
    """
    items = _apply_filters(inventory_items, warehouse=warehouse, category=category)

    if sort_by == "quantity":
        items = sorted(items, key=lambda i: i["quantity_on_hand"], reverse=True)
    elif sort_by == "unit_cost":
        items = sorted(items, key=lambda i: i["unit_cost"], reverse=True)
    elif sort_by == "shortage":
        items = sorted(
            items,
            key=lambda i: max(0, i["reorder_point"] - i["quantity_on_hand"]),
            reverse=True,
        )
    else:  # default + fallback: "value"
        items = sorted(
            items,
            key=lambda i: i["quantity_on_hand"] * i["unit_cost"],
            reverse=True,
        )

    # Trim oversized fields and compute the value the agent likely wants.
    out = []
    for i in items[: max(1, min(limit, 50))]:
        out.append(
            {
                "sku": i["sku"],
                "name": i["name"],
                "category": i["category"],
                "warehouse": i["warehouse"],
                "quantity_on_hand": i["quantity_on_hand"],
                "reorder_point": i["reorder_point"],
                "unit_cost": i["unit_cost"],
                "total_value": round(i["quantity_on_hand"] * i["unit_cost"], 2),
                "supplier_name": i.get("supplier_name"),
                "lead_time_days": i.get("lead_time_days"),
                "low_stock": i["quantity_on_hand"] <= i["reorder_point"],
            }
        )
    return out


def get_orders_list(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None,
    sort_by: str = "value",
    limit: int = 20,
) -> list[dict]:
    """Return individual order rows (not aggregates).

    Use this when the user asks about specific orders — biggest, latest, most
    delayed, etc. `sort_by`: `"value"` (total_value desc — default), `"date"`
    (order_date desc), `"delay"` (delivery delay desc).
    """
    filtered = _filter_by_month(
        _apply_filters(orders, warehouse=warehouse, category=category, status=status),
        month,
    )

    if sort_by == "date":
        filtered = sorted(filtered, key=lambda o: o.get("order_date", ""), reverse=True)
    elif sort_by == "delay":
        def _delay(o):
            ad = o.get("actual_delivery")
            ed = o.get("expected_delivery")
            return (ad or "") > (ed or "")
        filtered = sorted(filtered, key=_delay, reverse=True)
    else:
        filtered = sorted(filtered, key=lambda o: o.get("total_value", 0), reverse=True)

    # Strip the items[] sub-array to a count so we don't blow up tokens.
    out = []
    for o in filtered[: max(1, min(limit, 50))]:
        out.append(
            {
                "id": o["id"],
                "order_number": o["order_number"],
                "customer": o["customer"],
                "status": o["status"],
                "warehouse": o.get("warehouse"),
                "category": o.get("category"),
                "order_date": o["order_date"],
                "expected_delivery": o.get("expected_delivery"),
                "actual_delivery": o.get("actual_delivery"),
                "total_value": o["total_value"],
                "item_count": len(o.get("items", [])),
            }
        )
    return out


def get_inventory_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    """Aggregate, filter-aware inventory snapshot. Returns totals + per-category
    breakdown rather than the raw 32-row table to keep tokens cheap."""
    items = _apply_filters(inventory_items, warehouse=warehouse, category=category)
    total_value = sum(i["quantity_on_hand"] * i["unit_cost"] for i in items)
    low_stock = [
        i for i in items if i["quantity_on_hand"] <= i["reorder_point"]
    ]
    by_cat: dict = {}
    for i in items:
        c = i["category"]
        slot = by_cat.setdefault(c, {"category": c, "item_count": 0, "value": 0.0})
        slot["item_count"] += 1
        slot["value"] += i["quantity_on_hand"] * i["unit_cost"]
    return {
        "filters": {"warehouse": warehouse, "category": category},
        "item_count": len(items),
        "total_inventory_value": round(total_value, 2),
        "low_stock_count": len(low_stock),
        "by_category": sorted(
            (
                {"category": c["category"], "item_count": c["item_count"], "value": round(c["value"], 2)}
                for c in by_cat.values()
            ),
            key=lambda x: x["value"],
            reverse=True,
        ),
    }


def get_low_stock(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Items at or below their reorder point. Each row includes severity (a
    fraction of reorder_point) so the model can prioritize without re-deriving."""
    items = _apply_filters(inventory_items, warehouse=warehouse, category=category)
    out = []
    for i in items:
        if i["quantity_on_hand"] > i["reorder_point"]:
            continue
        ratio = i["quantity_on_hand"] / i["reorder_point"] if i["reorder_point"] else 0
        out.append(
            {
                "sku": i["sku"],
                "name": i["name"],
                "category": i["category"],
                "warehouse": i["warehouse"],
                "supplier_name": i.get("supplier_name"),
                "quantity_on_hand": i["quantity_on_hand"],
                "reorder_point": i["reorder_point"],
                "unit_cost": i["unit_cost"],
                "lead_time_days": i.get("lead_time_days"),
                "severity": "critical" if ratio <= 0.5 else "warning",
            }
        )
    return sorted(out, key=lambda x: x["severity"] == "critical", reverse=True)


def get_suppliers(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Per-supplier rollup of item count, inventory value, categories covered,
    avg lead time, and low-stock exposure."""
    items = _apply_filters(inventory_items, warehouse=warehouse, category=category)
    by: dict = {}
    for i in items:
        name = i.get("supplier_name") or "Unknown"
        s = by.setdefault(
            name,
            {
                "name": name,
                "item_count": 0,
                "total_inventory_value": 0.0,
                "categories": set(),
                "lead_sum": 0,
                "lead_n": 0,
                "low_stock_count": 0,
            },
        )
        s["item_count"] += 1
        s["total_inventory_value"] += i["quantity_on_hand"] * i["unit_cost"]
        s["categories"].add(i["category"])
        if i.get("lead_time_days") is not None:
            s["lead_sum"] += i["lead_time_days"]
            s["lead_n"] += 1
        if i["quantity_on_hand"] <= i["reorder_point"]:
            s["low_stock_count"] += 1
    out = [
        {
            "name": s["name"],
            "item_count": s["item_count"],
            "total_inventory_value": round(s["total_inventory_value"], 2),
            "categories": sorted(s["categories"]),
            "avg_lead_time_days": round(s["lead_sum"] / s["lead_n"], 1) if s["lead_n"] else 0.0,
            "low_stock_count": s["low_stock_count"],
        }
        for s in by.values()
    ]
    return sorted(out, key=lambda x: x["total_inventory_value"], reverse=True)


def get_orders_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None,
) -> dict:
    """Aggregate order metrics (count, revenue, fulfillment) so the model
    doesn't drown in 200+ raw order rows."""
    filtered = _filter_by_month(
        _apply_filters(orders, warehouse=warehouse, category=category, status=status),
        month,
    )
    by_status: dict = {}
    total_value = 0.0
    for o in filtered:
        s = o.get("status", "Unknown")
        by_status[s] = by_status.get(s, 0) + 1
        total_value += o.get("total_value", 0)
    return {
        "filters": {"warehouse": warehouse, "category": category, "status": status, "month": month},
        "order_count": len(filtered),
        "total_revenue": round(total_value, 2),
        "avg_order_value": round(total_value / len(filtered), 2) if filtered else 0.0,
        "by_status": by_status,
    }


def get_backlog() -> list[dict]:
    """Outstanding backlog items with priority and days delayed. No filter args
    — backlog is its own slice of the data."""
    out = []
    po_index = {p["backlog_item_id"]: p for p in purchase_orders}
    for b in backlog_items:
        row = dict(b)
        row["has_purchase_order"] = b["id"] in po_index
        out.append(row)
    return out


def get_demand_forecast() -> list[dict]:
    """Per-SKU demand forecast: current, forecasted, trend, unit cost, lead time."""
    return [dict(f) for f in demand_forecasts]


def get_quarterly_reports(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """Quarterly aggregates over (filtered) orders."""
    filtered = _apply_filters(orders, warehouse=warehouse, category=category, status=status)
    quarters: dict = {}
    for o in filtered:
        d = o.get("order_date", "")
        q = None
        for qname, months in _QUARTER_MAP.items():
            if any(m in d for m in months):
                q = qname
                break
        if not q:
            continue
        slot = quarters.setdefault(
            q,
            {"quarter": q, "total_orders": 0, "total_revenue": 0.0, "delivered_orders": 0},
        )
        slot["total_orders"] += 1
        slot["total_revenue"] += o.get("total_value", 0)
        if o.get("status") == "Delivered":
            slot["delivered_orders"] += 1
    out = []
    for q in quarters.values():
        if q["total_orders"]:
            q["avg_order_value"] = round(q["total_revenue"] / q["total_orders"], 2)
            q["fulfillment_rate"] = round(q["delivered_orders"] / q["total_orders"] * 100, 1)
        q["total_revenue"] = round(q["total_revenue"], 2)
        out.append(q)
    return sorted(out, key=lambda x: x["quarter"])


def get_monthly_trends(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """Month-over-month order count + revenue."""
    filtered = _apply_filters(orders, warehouse=warehouse, category=category, status=status)
    months: dict = {}
    for o in filtered:
        m = o.get("order_date", "")[:7]
        if not m:
            continue
        slot = months.setdefault(m, {"month": m, "order_count": 0, "revenue": 0.0})
        slot["order_count"] += 1
        slot["revenue"] += o.get("total_value", 0)
    out = list(months.values())
    for r in out:
        r["revenue"] = round(r["revenue"], 2)
    return sorted(out, key=lambda x: x["month"])


def get_spending_breakdown() -> dict:
    """Pre-computed spending summary + monthly + per-category, lifted directly
    from spending.json. The agent uses this for cost questions that aren't
    derivable from orders alone."""
    return {
        "summary": spending_summary,
        "monthly": monthly_spending,
        "by_category": category_spending,
    }


# ---------------------------------------------------------------------------
# UI-control tools — these run client-side. The server's "execution" is just
# acking the request to the model; the agent.py loop also emits a separate
# `ui_action` SSE event that the frontend reacts to (navigate, set filter,
# pulse a highlight). The model receives `{queued_action: ...}` as the tool
# result so it knows the action was dispatched and can continue narrating.
# ---------------------------------------------------------------------------

_ROUTES = {
    "/", "/inventory", "/orders", "/demand", "/spending",
    "/reports", "/restocking", "/backlog", "/suppliers", "/low-stock",
}


def navigate_to_page(route: str) -> dict:
    """Send a navigation directive to the frontend."""
    if route not in _ROUTES:
        return {"error": f"unknown route: {route}", "valid_routes": sorted(_ROUTES)}
    return {"queued_action": "navigate", "route": route}


_FILTERS = {"period", "location", "category", "status"}


def set_filter(filter: str, value: str) -> dict:
    """Set one of the global FilterBar filters."""
    if filter not in _FILTERS:
        return {"error": f"unknown filter: {filter}", "valid_filters": sorted(_FILTERS)}
    return {"queued_action": "set_filter", "filter": filter, "value": value}


_HIGHLIGHT_KINDS = {
    # Nav tabs
    "nav:overview", "nav:inventory", "nav:orders", "nav:finance",
    "nav:demand", "nav:reports", "nav:restocking", "nav:backlog",
    "nav:suppliers", "nav:low-stock",
    # Global filter dropdowns
    "filter:period", "filter:location", "filter:category", "filter:status",
    # In-page card / section targets (data-copilot-card attributes)
    "card:low-stock-table",        # /low-stock — the items table
    "card:low-stock-summary",      # /low-stock — the 4 stat cards
    "card:suppliers-table",        # /suppliers — the supplier breakdown
    "card:suppliers-summary",      # /suppliers — the 4 stat cards
    "card:restocking-budget",      # /restocking — the budget slider
    "card:restocking-table",       # /restocking — the recommended items
    "card:restocking-summary",     # /restocking — the 4 stat cards
    "card:reports-quarterly",      # /reports — the quarterly perf table
    "card:reports-trend",          # /reports — the monthly trend chart
    "card:backlog-table",          # /backlog — the items table
    "card:backlog-summary",        # /backlog — the 4 stat cards
    "card:dashboard-kpi",          # /  — the KPI strip
    "card:dashboard-order-health", # /  — the donut + metrics
    "card:dashboard-inventory-by-category",
    "card:dashboard-shortages",
    "card:dashboard-top-products",
    # Whole page
    "page:current",
}

# `sku:<SKU>` highlights one row in any inventory/low-stock/backlog/restocking
# table tagged with data-copilot-sku. SKUs come from `get_low_stock` /
# `get_inventory_summary` / etc. — anything matching this loose shape is OK.
import re as _re
_SKU_PATTERN = _re.compile(r"^sku:[A-Za-z0-9_\-]+$")


def highlight_element(kind: str, note: Optional[str] = None) -> dict:
    """Pulse a soft outline around a UI element to direct the user's attention."""
    is_sku = bool(_SKU_PATTERN.match(kind)) if isinstance(kind, str) else False
    if kind not in _HIGHLIGHT_KINDS and not is_sku:
        return {
            "error": f"unknown highlight target: {kind}",
            "valid_kinds": sorted(_HIGHLIGHT_KINDS),
            "hint": "Or use sku:<SKU> (e.g. 'sku:SRV-302') to highlight one table row.",
        }
    return {"queued_action": "highlight", "kind": kind, "note": note}


# ---------------------------------------------------------------------------
# Proposal-only mutation: propose a restocking order. Does NOT submit.
# The server holds the payload server-side under a signed proposal_id; the UI
# must explicitly call /api/copilot/approve to commit it.
# ---------------------------------------------------------------------------

# Module-level proposal store keyed by signed id. Mirrors the rest of the demo
# (in-memory, resets on server restart). NOT shared with main.py's restocking
# store — only /approve copies into that one.
_pending_proposals: Dict[str, dict] = {}

_PROPOSAL_SECRET = os.environ.get("COPILOT_PROPOSAL_SECRET") or secrets.token_hex(32)


def _sign(proposal_id: str) -> str:
    """HMAC tag so an attacker can't fabricate a proposal_id by guessing."""
    return hmac.new(
        _PROPOSAL_SECRET.encode(),
        proposal_id.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


def propose_restocking_order(budget: float) -> dict:
    """Greedy gap-fill against the demand forecast (same algorithm as the
    /restocking page). Returns a draft order + a signed proposal_id; does NOT
    persist. The UI must call /api/copilot/approve with the id to commit."""
    if budget <= 0:
        return {"error": "budget must be positive", "budget": budget}

    # Rank by gap = forecasted - current; pick greedily, partial fits allowed.
    ranked = sorted(
        (
            {
                **f,
                "gap": max(0, f["forecasted_demand"] - f["current_demand"]),
            }
            for f in demand_forecasts
        ),
        key=lambda f: f["gap"],
        reverse=True,
    )

    items: list = []
    remaining = budget
    for f in ranked:
        if remaining <= 0 or f["gap"] == 0 or not f.get("unit_cost"):
            continue
        desired = f["gap"]
        full_cost = desired * f["unit_cost"]
        qty = desired if full_cost <= remaining else int(remaining // f["unit_cost"])
        if qty <= 0:
            continue
        line_cost = qty * f["unit_cost"]
        items.append(
            {
                "item_sku": f["item_sku"],
                "item_name": f["item_name"],
                "quantity": qty,
                "unit_cost": f["unit_cost"],
                "lead_time_days": f.get("lead_time_days", 14),
                "line_cost": round(line_cost, 2),
            }
        )
        remaining -= line_cost

    total = round(sum(i["line_cost"] for i in items), 2)
    max_lead = max((i["lead_time_days"] for i in items), default=0)

    proposal_id = secrets.token_urlsafe(12)
    signed_id = f"{proposal_id}.{_sign(proposal_id)}"

    plan = {
        "proposal_id": signed_id,
        "kind": "restocking_order",
        "budget": budget,
        "items": items,
        "total_value": total,
        "max_lead_time_days": max_lead,
        "summary": (
            f"Restock {len(items)} items for ${total:,.2f} (budget ${budget:,.2f}). "
            f"Max lead time {max_lead} days."
        ),
    }
    # Stash plan keyed by full signed id so /approve only needs the id.
    _pending_proposals[signed_id] = plan
    return plan


def consume_proposal(signed_id: str) -> Optional[dict]:
    """Pop a proposal by id after verifying the HMAC. Called by /approve.
    Returns the plan or None if missing/forged."""
    if "." not in signed_id:
        return None
    proposal_id, tag = signed_id.rsplit(".", 1)
    expected = _sign(proposal_id)
    if not hmac.compare_digest(tag, expected):
        return None
    return _pending_proposals.pop(signed_id, None)


# ---------------------------------------------------------------------------
# Tool registry — schemas Anthropic accepts in `tools=...` plus the dispatch
# table the agent loop uses to actually run them.
# ---------------------------------------------------------------------------

# Most filter params share a shape — DRY them out.
_FILTER_PROPS_BASIC = {
    "warehouse": {
        "type": "string",
        "description": "Warehouse name (e.g. 'San Francisco', 'London', 'Tokyo') or omit / use 'all' for every warehouse.",
    },
    "category": {
        "type": "string",
        "description": "Item category (e.g. 'Circuit Boards', 'Sensors') or omit / 'all'.",
    },
}

_FILTER_PROPS_ORDERS = {
    **_FILTER_PROPS_BASIC,
    "status": {
        "type": "string",
        "description": "Order status: 'Delivered', 'Shipped', 'Processing', 'Backordered'. Omit / 'all' for all.",
    },
    "month": {
        "type": "string",
        "description": "Either 'YYYY-MM' (e.g. '2025-03') or quarter 'Q1-2025'..'Q4-2025'. Omit / 'all' for all.",
    },
}


TOOL_SCHEMAS: List[dict] = [
    {
        "name": "get_inventory_items",
        "description": (
            "Return individual inventory item ROWS (not aggregates). Use this "
            "whenever the user asks about a specific SKU, the highest/lowest "
            "item by some dimension, or otherwise needs row-level data — "
            "questions like 'which SKU has the most stock?' or 'what's the "
            "single highest-value item?'. Each row includes sku, name, "
            "category, warehouse, quantity_on_hand, reorder_point, unit_cost, "
            "total_value (qty × cost), supplier_name, lead_time_days, and a "
            "low_stock boolean. The list is pre-sorted; pick the right "
            "`sort_by`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTER_PROPS_BASIC,
                "sort_by": {
                    "type": "string",
                    "enum": ["value", "quantity", "unit_cost", "shortage"],
                    "description": "'value' (qty × unit_cost desc — default), 'quantity' (units desc), 'unit_cost' (price desc), 'shortage' (gap below reorder desc).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (1-50, default 20).",
                },
            },
        },
    },
    {
        "name": "get_orders_list",
        "description": (
            "Return individual order ROWS (not aggregates). Use this when the "
            "user asks about specific orders — biggest, most recent, most "
            "delayed, by a particular customer, etc. Each row has id, "
            "order_number, customer, status, warehouse, category, order_date, "
            "expected_delivery, actual_delivery, total_value, and item_count "
            "(items array is stripped to a count to keep tokens cheap)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTER_PROPS_ORDERS,
                "sort_by": {
                    "type": "string",
                    "enum": ["value", "date", "delay"],
                    "description": "'value' (total_value desc — default), 'date' (order_date desc), 'delay' (delivery delay desc).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (1-50, default 20).",
                },
            },
        },
    },
    {
        "name": "get_inventory_summary",
        "description": (
            "Aggregate inventory snapshot: total item count, total inventory "
            "value, low-stock count, and per-category breakdown. Filter by "
            "warehouse and/or category. Use this for high-level inventory "
            "questions; for the actual at-risk items, use get_low_stock."
        ),
        "input_schema": {
            "type": "object",
            "properties": _FILTER_PROPS_BASIC,
        },
    },
    {
        "name": "get_low_stock",
        "description": (
            "Items at or below their reorder point, sorted with critical "
            "(<=50% of reorder) first. Includes supplier and lead time so "
            "the agent can reason about replenishment risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": _FILTER_PROPS_BASIC,
        },
    },
    {
        "name": "get_suppliers",
        "description": (
            "Per-supplier rollup: item count, total inventory value, "
            "categories supplied, average lead time, and low-stock count. "
            "Sorted by inventory value desc."
        ),
        "input_schema": {
            "type": "object",
            "properties": _FILTER_PROPS_BASIC,
        },
    },
    {
        "name": "get_orders_summary",
        "description": (
            "Aggregate order metrics: count, total revenue, average order "
            "value, and counts by status. Filter by warehouse, category, "
            "status, and/or month. Returns aggregates not row-by-row data."
        ),
        "input_schema": {
            "type": "object",
            "properties": _FILTER_PROPS_ORDERS,
        },
    },
    {
        "name": "get_backlog",
        "description": (
            "Outstanding backlog items with quantity needed, quantity "
            "available, days delayed, priority, and whether a purchase "
            "order exists. No filter params."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_demand_forecast",
        "description": (
            "Per-SKU demand forecast over the next 30 days: current demand, "
            "forecasted demand, trend (increasing/stable/decreasing), unit "
            "cost, and supplier lead time. No filter params."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_quarterly_reports",
        "description": (
            "Quarterly performance: order count, revenue, average order "
            "value, and fulfillment rate per quarter. Filter by warehouse, "
            "category, status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {k: v for k, v in _FILTER_PROPS_ORDERS.items() if k != "month"},
        },
    },
    {
        "name": "get_monthly_trends",
        "description": (
            "Month-over-month order count and revenue. Filter by warehouse, "
            "category, status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {k: v for k, v in _FILTER_PROPS_ORDERS.items() if k != "month"},
        },
    },
    {
        "name": "get_spending_breakdown",
        "description": (
            "Pre-computed spending: summary totals, monthly series, and "
            "per-category breakdown."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "navigate_to_page",
        "description": (
            "Navigate the dashboard to a specific page. Use this when the user "
            "asks to 'go to', 'show me', or 'open' a page, or when the "
            "information you found lives on a particular page and you want to "
            "take the user there. The navigation happens in the user's browser; "
            "no need to ask permission."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "route": {
                    "type": "string",
                    "enum": [
                        "/", "/inventory", "/orders", "/demand", "/spending",
                        "/reports", "/restocking", "/backlog", "/suppliers",
                        "/low-stock",
                    ],
                    "description": "Target route path.",
                },
            },
            "required": ["route"],
        },
    },
    {
        "name": "set_filter",
        "description": (
            "Apply a value to one of the global filter dropdowns at the top of "
            "the dashboard. The filter then applies to every page until reset. "
            "Use 'all' as the value to clear a filter. After setting filters, "
            "subsequent tool calls and the user's next message will reflect the "
            "new filter context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["period", "location", "category", "status"],
                },
                "value": {
                    "type": "string",
                    "description": (
                        "For 'period': 'all' or 'YYYY-MM' (e.g. '2025-03'). "
                        "For 'location': 'all' or one of 'San Francisco', "
                        "'London', 'Tokyo' (exact casing). "
                        "For 'category': 'all' or lowercase one of "
                        "'circuit boards', 'sensors', 'actuators', "
                        "'controllers', 'power supplies'. "
                        "For 'status': 'all' or lowercase one of "
                        "'delivered', 'shipped', 'processing', 'backordered'. "
                        "If unsure of casing, the UI normalizes anyway, but "
                        "prefer the canonical form above."
                    ),
                },
            },
            "required": ["filter", "value"],
        },
    },
    {
        "name": "highlight_element",
        "description": (
            "Direct the user's attention to a specific UI element by pulsing "
            "a soft outline around it for a couple of seconds. Use sparingly "
            "and only when you want to show the user where something lives."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "Highlight target. Prefer the most specific target "
                        "available. In order of specificity:\n"
                        "  - `sku:<SKU>` — pulses ONE row in any table (e.g. "
                        "'sku:SRV-302'). Use this whenever the user is asking "
                        "about a specific item and that item is on the current "
                        "page. This is the most precise highlight available.\n"
                        "  - `card:<key>` — pulses one card / section. Valid "
                        "keys include: " + ", ".join(
                            sorted(k for k in _HIGHLIGHT_KINDS if k.startswith("card:"))
                        ) + ".\n"
                        "  - `nav:<page>` — pulses one nav tab. Valid: " + ", ".join(
                            sorted(k for k in _HIGHLIGHT_KINDS if k.startswith("nav:"))
                        ) + ".\n"
                        "  - `filter:<which>` — pulses one filter dropdown. "
                        "Valid: filter:period, filter:location, "
                        "filter:category, filter:status.\n"
                        "  - `page:current` — last-resort fallback; pulses the "
                        "whole content frame. Easy to miss; only use when no "
                        "more-specific target fits."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional short caption to render alongside the pulse.",
                },
            },
            "required": ["kind"],
        },
    },
    {
        "name": "propose_restocking_order",
        "description": (
            "Build a proposed restocking order using greedy gap-fill against "
            "the demand forecast, capped at the user's budget. Returns a "
            "DRAFT plan plus a proposal_id — does NOT submit the order. The "
            "UI surfaces an Approve button that the user must click to commit. "
            "Always summarize the plan in plain English so the user knows what "
            "they're approving."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "budget": {
                    "type": "number",
                    "description": "Maximum total dollars to spend on restocking, e.g. 50000.",
                },
            },
            "required": ["budget"],
        },
    },
]


# Dispatch table — name → callable. Kept separate from the schema list so the
# schema can be passed to Anthropic without dragging in callable references.
TOOL_DISPATCH: Dict[str, Callable[..., Any]] = {
    "get_inventory_items": get_inventory_items,
    "get_orders_list": get_orders_list,
    "get_inventory_summary": get_inventory_summary,
    "get_low_stock": get_low_stock,
    "get_suppliers": get_suppliers,
    "get_orders_summary": get_orders_summary,
    "get_backlog": get_backlog,
    "get_demand_forecast": get_demand_forecast,
    "get_quarterly_reports": get_quarterly_reports,
    "get_monthly_trends": get_monthly_trends,
    "get_spending_breakdown": get_spending_breakdown,
    "navigate_to_page": navigate_to_page,
    "set_filter": set_filter,
    "highlight_element": highlight_element,
    "propose_restocking_order": propose_restocking_order,
}


def run_tool(name: str, payload: dict) -> str:
    """Execute a tool by name. Returns a JSON string ready to ship back as a
    tool_result content block. Errors are caught and surfaced to the model
    so it can recover instead of the request blowing up."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        result = fn(**(payload or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
    except Exception as e:
        return json.dumps({"error": f"{name} failed: {type(e).__name__}: {e}"})
    return json.dumps(result, default=str)
