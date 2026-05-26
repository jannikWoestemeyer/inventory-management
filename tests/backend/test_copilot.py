"""Ops Copilot test suite.

Two layers:

1. Tool-wrapper tests — call each tool function directly with no network.
2. Mocked SSE pipeline test — replace the Anthropic client with a fake that
   returns a canned tool-call sequence, then drive `agent.run_chat` and assert
   on the emitted events + on-disk JSONL.
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Make the `copilot` package importable without running server/main.py
SERVER_DIR = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER_DIR))

# Persist conversations under a tmp dir so tests don't leak into .copilot/
os.environ["COPILOT_STORE_DIR"] = str(
    Path(__file__).resolve().parent / "_copilot_tmp"
)
# Also pin the HMAC secret so proposal signing is deterministic across runs.
os.environ["COPILOT_PROPOSAL_SECRET"] = "test-secret-do-not-use-in-prod"

# Import order matters: store reads its dir from env at import time.
from copilot import tools, store, agent  # noqa: E402


# ---------------------------------------------------------------------------
# Tool-wrapper tests (no network, no Anthropic)
# ---------------------------------------------------------------------------


class TestCopilotTools:
    def test_tool_schemas_well_formed(self):
        assert len(tools.TOOL_SCHEMAS) == 15
        for schema in tools.TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"
            # Every schema name must have a matching dispatch entry.
            assert schema["name"] in tools.TOOL_DISPATCH

    def test_get_inventory_summary_no_filters(self):
        s = tools.get_inventory_summary()
        assert s["item_count"] == 32
        assert s["total_inventory_value"] > 400_000
        assert s["low_stock_count"] >= 1
        assert any(c["category"] == "Sensors" for c in s["by_category"])

    def test_get_inventory_items_sorts_by_value_desc(self):
        rows = tools.get_inventory_items(limit=5)
        assert len(rows) == 5
        values = [r["total_value"] for r in rows]
        assert values == sorted(values, reverse=True)
        # Required fields present
        for r in rows:
            assert {"sku", "name", "quantity_on_hand", "unit_cost", "total_value", "low_stock"} <= set(r)

    def test_get_inventory_items_sort_by_quantity(self):
        rows = tools.get_inventory_items(sort_by="quantity", limit=3)
        qtys = [r["quantity_on_hand"] for r in rows]
        assert qtys == sorted(qtys, reverse=True)

    def test_get_inventory_items_warehouse_filter(self):
        london = tools.get_inventory_items(warehouse="London", limit=50)
        assert london
        assert all(r["warehouse"] == "London" for r in london)

    def test_get_inventory_items_limit_clamped(self):
        # limit=0 floors to 1; limit=9999 caps to 50.
        assert len(tools.get_inventory_items(limit=0)) == 1
        assert len(tools.get_inventory_items(limit=9999)) <= 50

    def test_get_orders_list_sorts_by_value_desc(self):
        rows = tools.get_orders_list(limit=5)
        assert len(rows) == 5
        vals = [r["total_value"] for r in rows]
        assert vals == sorted(vals, reverse=True)
        # items[] stripped to a count
        for r in rows:
            assert "item_count" in r and "items" not in r

    def test_get_inventory_summary_warehouse_filter(self):
        all_items = tools.get_inventory_summary()["item_count"]
        tokyo = tools.get_inventory_summary(warehouse="Tokyo")
        assert tokyo["item_count"] > 0
        assert tokyo["item_count"] < all_items

    def test_get_low_stock_returns_only_low(self):
        rows = tools.get_low_stock()
        assert rows, "expected at least one low-stock item with our seed data"
        for r in rows:
            assert r["quantity_on_hand"] <= r["reorder_point"]
            assert r["severity"] in {"critical", "warning"}

    def test_get_suppliers_sorted_by_value_desc(self):
        rows = tools.get_suppliers()
        values = [r["total_inventory_value"] for r in rows]
        assert values == sorted(values, reverse=True)
        # Every supplier reports categories and a lead-time average.
        for r in rows:
            assert isinstance(r["categories"], list)
            assert r["avg_lead_time_days"] >= 0

    def test_get_orders_summary_filter_narrows_count(self):
        all_count = tools.get_orders_summary()["order_count"]
        delivered = tools.get_orders_summary(status="Delivered")
        assert 0 < delivered["order_count"] < all_count

    def test_get_demand_forecast_shape(self):
        rows = tools.get_demand_forecast()
        assert len(rows) == 9
        for r in rows:
            assert {"item_sku", "current_demand", "forecasted_demand", "trend"} <= set(r)

    def test_get_quarterly_reports_aggregates_four_quarters(self):
        rows = tools.get_quarterly_reports()
        assert len(rows) == 4
        assert {r["quarter"] for r in rows} == {
            "Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025",
        }

    def test_get_monthly_trends_is_chronological(self):
        rows = tools.get_monthly_trends()
        assert rows
        months = [r["month"] for r in rows]
        assert months == sorted(months)

    def test_get_spending_breakdown_keys(self):
        s = tools.get_spending_breakdown()
        assert set(s.keys()) == {"summary", "monthly", "by_category"}

    def test_navigate_to_page_valid(self):
        out = tools.navigate_to_page("/suppliers")
        assert out == {"queued_action": "navigate", "route": "/suppliers"}

    def test_navigate_to_page_rejects_unknown_route(self):
        out = tools.navigate_to_page("/not-a-page")
        assert "error" in out
        assert "/" in out["valid_routes"]

    def test_set_filter_valid(self):
        out = tools.set_filter(filter="location", value="Tokyo")
        assert out == {"queued_action": "set_filter", "filter": "location", "value": "Tokyo"}

    def test_set_filter_rejects_unknown_filter(self):
        out = tools.set_filter(filter="warehouse_id", value="x")
        assert "error" in out

    def test_highlight_element_valid(self):
        out = tools.highlight_element(kind="nav:low-stock", note="check this")
        assert out["queued_action"] == "highlight"
        assert out["kind"] == "nav:low-stock"
        assert out["note"] == "check this"

    def test_highlight_element_rejects_unknown_kind(self):
        out = tools.highlight_element(kind="bogus:thing")
        assert "error" in out

    def test_highlight_element_accepts_sku_pattern(self):
        out = tools.highlight_element(kind="sku:SRV-302", note="lowest stock")
        assert out["queued_action"] == "highlight"
        assert out["kind"] == "sku:SRV-302"

    def test_highlight_element_rejects_invalid_sku_chars(self):
        # SKUs are alphanumeric + dash/underscore; reject anything weirder so
        # the frontend selector can't be tricked by quote injection.
        out = tools.highlight_element(kind='sku:SRV"]><script>')
        assert "error" in out

    def test_propose_restocking_order_signs_id_and_does_not_persist(self):
        plan = tools.propose_restocking_order(budget=50_000)
        assert plan["proposal_id"].count(".") == 1
        assert plan["total_value"] > 0
        assert plan["items"]
        # The signed id resolves back to the stored plan.
        roundtrip = tools.consume_proposal(plan["proposal_id"])
        assert roundtrip is not None
        assert roundtrip["total_value"] == plan["total_value"]
        # Consuming pops it — second call returns None.
        assert tools.consume_proposal(plan["proposal_id"]) is None

    def test_propose_restocking_order_rejects_negative_budget(self):
        result = tools.propose_restocking_order(budget=-5)
        assert "error" in result

    def test_consume_proposal_rejects_forged_signature(self):
        plan = tools.propose_restocking_order(budget=10_000)
        good_id = plan["proposal_id"]
        proposal_id, _ = good_id.rsplit(".", 1)
        forged = f"{proposal_id}.0000000000000000"
        assert tools.consume_proposal(forged) is None
        # The real id still works after the forgery attempt.
        assert tools.consume_proposal(good_id) is not None

    def test_run_tool_returns_json_string(self):
        out = tools.run_tool("get_inventory_summary", {"warehouse": "all"})
        # Must be a JSON string, not a dict — agent.py round-trips this back
        # to Anthropic as tool_result content.
        assert isinstance(out, str)
        parsed = json.loads(out)
        assert parsed["item_count"] == 32

    def test_run_tool_handles_unknown_tool(self):
        out = json.loads(tools.run_tool("does_not_exist", {}))
        assert "error" in out

    def test_run_tool_handles_bad_arguments(self):
        # warehouse is a string param; passing a dict triggers a TypeError
        # path which we surface back to the model as an error string.
        out = json.loads(tools.run_tool("get_inventory_summary", {"unknown_param": 1}))
        assert "error" in out


# ---------------------------------------------------------------------------
# Disk-store tests
# ---------------------------------------------------------------------------


class TestCopilotStore:
    def test_append_and_reload_roundtrip(self, tmp_path, monkeypatch):
        # Use a clean tmp dir for this test so we don't pollute other tests.
        store_dir = tmp_path / "store"
        monkeypatch.setattr(store, "BASE_DIR", store_dir)
        monkeypatch.setattr(store, "CONV_DIR", store_dir / "conversations")
        store.CONV_DIR.mkdir(parents=True, exist_ok=True)

        conv = "test-roundtrip"
        store.append_event(conv, {"kind": "user_message", "text": "hi", "raw_text": "hi"})
        store.append_event(
            conv,
            {"kind": "tool_use", "tool_use_id": "tu_1", "name": "get_low_stock", "input": {}},
        )
        store.append_event(
            conv,
            {"kind": "tool_result", "tool_use_id": "tu_1", "name": "get_low_stock", "content": "[]"},
        )
        store.append_event(conv, {"kind": "assistant_text", "text": "all good"})

        events = list(store.load_events(conv))
        assert len(events) == 4
        assert events[0]["kind"] == "user_message"
        assert events[-1]["kind"] == "assistant_text"

        msgs = store.build_message_history(conv)
        # user → assistant(tool_use) → user(tool_result) → assistant(text)
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert msgs[1]["content"][0]["type"] == "tool_use"
        assert msgs[2]["content"][0]["type"] == "tool_result"
        assert msgs[3]["content"][0]["text"] == "all good"


# ---------------------------------------------------------------------------
# Mocked agent loop — no Anthropic API key needed
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for Anthropic's response blocks.

    `agent.run_chat` reads `.type`, `.text`, `.id`, `.name`, `.input`, and
    calls `.model_dump()` to round-trip the block into `messages=[]` on the
    next turn. That's all we have to satisfy.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


class _FakeMessage:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class _FakeStream:
    """Context-manager + iterator that mimics `client.messages.stream(...)`.

    `text_stream` yields each text-block's text once (mirrors how the real
    SDK chunks deltas — chunking granularity doesn't matter for our tests).
    `get_final_message()` returns the canned response.
    """

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for block in self._response.content:
            if getattr(block, "type", None) == "text":
                yield block.text

    def get_final_message(self):
        return self._response


class _FakeMessages:
    def __init__(self, scripted_responses):
        self._responses = list(scripted_responses)

    def stream(self, **_kwargs):
        if not self._responses:
            raise RuntimeError("scripted_responses exhausted")
        return _FakeStream(self._responses.pop(0))


class _FakeAnthropic:
    def __init__(self, scripted_responses):
        self.messages = _FakeMessages(scripted_responses)


def _drain(async_gen):
    """Drive an async generator from sync code and collect every yield."""
    import asyncio

    async def _go():
        out = []
        async for event in async_gen:
            out.append(event)
        return out

    return asyncio.run(_go())


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    # Per-test store so message-history reconstruction doesn't leak across tests.
    store_dir = tmp_path / "store"
    monkeypatch.setattr(store, "BASE_DIR", store_dir)
    monkeypatch.setattr(store, "CONV_DIR", store_dir / "conversations")
    store.CONV_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure the env-var path is set so `Anthropic(api_key=...)` doesn't bail.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


class TestAgentLoop:
    def test_simple_text_only_response(self, monkeypatch):
        scripted = [
            _FakeMessage(
                content=[_FakeContentBlock(type="text", text="hello there")],
                stop_reason="end_turn",
            )
        ]
        monkeypatch.setattr(agent, "Anthropic", lambda **_kw: _FakeAnthropic(scripted))

        events = _drain(
            agent.run_chat(
                conversation_id="t-simple",
                user_message="say hi",
                page_context={"route": "/", "filters": {"warehouse": "all"}},
            )
        )

        assert [e["type"] for e in events] == ["text", "done"]
        assert events[0]["delta"] == "hello there"
        assert events[1]["stop_reason"] == "end_turn"

        # Persisted: user message + assistant text.
        stored = list(store.load_events("t-simple"))
        assert {e["kind"] for e in stored} == {"user_message", "assistant_text"}

    def test_tool_call_then_final_text(self, monkeypatch):
        # Turn 1: model emits a tool_use; turn 2: model emits final text.
        scripted = [
            _FakeMessage(
                content=[
                    _FakeContentBlock(
                        type="tool_use",
                        id="tu_1",
                        name="get_inventory_summary",
                        input={"warehouse": "Tokyo"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[
                    _FakeContentBlock(type="text", text="Tokyo has N items.")
                ],
                stop_reason="end_turn",
            ),
        ]
        monkeypatch.setattr(agent, "Anthropic", lambda **_kw: _FakeAnthropic(scripted))

        events = _drain(
            agent.run_chat(
                conversation_id="t-tool",
                user_message="how much inventory in Tokyo?",
                page_context=None,
            )
        )

        # tool_use → tool_result → text → done
        types = [e["type"] for e in events]
        assert types == ["tool_use", "tool_result", "text", "done"]
        assert events[0]["name"] == "get_inventory_summary"
        assert events[0]["input"] == {"warehouse": "Tokyo"}
        # The tool actually ran against the real mock data — assert it's not an error.
        assert "error" not in events[1]["result"]
        assert events[1]["result"]["filters"]["warehouse"] == "Tokyo"
        assert events[2]["delta"] == "Tokyo has N items."

    def test_proposal_emits_dedicated_event(self, monkeypatch):
        scripted = [
            _FakeMessage(
                content=[
                    _FakeContentBlock(
                        type="tool_use",
                        id="tu_p",
                        name="propose_restocking_order",
                        input={"budget": 50_000},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[
                    _FakeContentBlock(type="text", text="Here is your draft.")
                ],
                stop_reason="end_turn",
            ),
        ]
        monkeypatch.setattr(agent, "Anthropic", lambda **_kw: _FakeAnthropic(scripted))

        events = _drain(
            agent.run_chat(
                conversation_id="t-proposal",
                user_message="draft a $50K restocking order",
                page_context=None,
            )
        )

        # Critically, a proposal tool emits {type: "proposal"}, NOT tool_result —
        # that's the UI affordance for "this needs Approve before it commits."
        types = [e["type"] for e in events]
        assert "proposal" in types
        assert "tool_result" not in types  # the proposal replaces tool_result

        proposal_event = next(e for e in events if e["type"] == "proposal")
        assert proposal_event["name"] == "propose_restocking_order"
        assert proposal_event["proposal"]["budget"] == 50_000
        assert proposal_event["proposal"]["proposal_id"]
        # And of course the proposal does NOT actually submit — the plan is
        # only in the proposals dict, not in any orders list.
        assert proposal_event["proposal"]["proposal_id"] in tools._pending_proposals

    def test_ui_tool_emits_ui_action_event(self, monkeypatch):
        scripted = [
            _FakeMessage(
                content=[
                    _FakeContentBlock(
                        type="tool_use",
                        id="tu_ui",
                        name="navigate_to_page",
                        input={"route": "/suppliers"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeContentBlock(type="text", text="Done — taking you there.")],
                stop_reason="end_turn",
            ),
        ]
        monkeypatch.setattr(agent, "Anthropic", lambda **_kw: _FakeAnthropic(scripted))

        events = _drain(
            agent.run_chat(
                conversation_id="t-ui",
                user_message="show me the suppliers page",
                page_context=None,
            )
        )

        types = [e["type"] for e in events]
        # UI tools emit BOTH a tool_result (ack to the model) AND a separate
        # ui_action event for the frontend to react to.
        assert "tool_result" in types
        assert "ui_action" in types
        ui = next(e for e in events if e["type"] == "ui_action")
        assert ui["action"]["queued_action"] == "navigate"
        assert ui["action"]["route"] == "/suppliers"

    def test_missing_api_key_short_circuits(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        events = _drain(
            agent.run_chat(
                conversation_id="t-no-key",
                user_message="anything",
                page_context=None,
            )
        )
        assert events[0]["type"] == "error"
        assert "ANTHROPIC_API_KEY" in events[0]["message"]
        assert events[-1] == {"type": "done", "stop_reason": "error"}
