"""Ops Copilot agent loop.

Wraps Anthropic's `client.messages.stream()` with a manual tool-use loop so we
can:
  - emit SSE events to the UI as tool calls happen (not just at the end);
  - gate `propose_restocking_order` behind a separate Approve step;
  - persist every event (user msg, assistant text, tool call, tool result) to
    disk via store.append_event so a session survives server restarts.

Design choices baked in (from the plan):
  - Model: claude-sonnet-4-6 (user-selected; the user said "start with sonnet")
  - Adaptive thinking enabled — the recommended mode on Sonnet 4.6
  - System prompt + tool schemas marked cache_control=ephemeral so the static
    prefix is cached after the first turn
  - Page context (current route + filters) is appended as a fresh user message
    on each turn, NOT into the system prompt. This keeps the cache prefix
    stable across filter changes (see shared/prompt-caching.md).
  - The agent has NO write tools. The single proposal tool returns a draft;
    /api/copilot/approve commits.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

from anthropic import Anthropic

from . import store
from .tools import TOOL_SCHEMAS, run_tool


MODEL = "claude-haiku-4-5"
MAX_TURNS = 8  # bound the tool-call loop so a wedged model can't spin forever


SYSTEM_PROMPT = """You are the Ops Copilot for a factory inventory management web app called Catalyst Components. The user is operating the dashboard; you sit in a side panel and help them reason across pages — AND you can drive the UI directly.

You have three groups of tools:

(1) READ-ONLY DATA TOOLS — wrap the same API endpoints the dashboard uses (inventory, orders, suppliers, low-stock, demand forecast, reports, spending). Use these whenever the user asks about a slice of data.

(2) UI CONTROL TOOLS — you can navigate pages, set filters, and highlight elements:
- `navigate_to_page(route)` — take the user to a different page. Don't ask permission; just do it when they ask to "show", "open", or "go to" a page, or when the answer to their question lives on another page.
- `set_filter(filter, value)` — apply one of the global filters (period / location / category / status). When the user asks a scoped question (e.g. "in Tokyo", "for Q3"), apply the matching filter so the dashboard mirrors what you're talking about.
- `highlight_element(kind)` — pulse a bright neon-magenta outline + glow around a specific UI element. Targets in order of preference:
  • `sku:<SKU>` — pulses a SINGLE table row in any inventory / low-stock / backlog / restocking table (e.g. `sku:SRV-302`). **This is the most precise highlight available — use it whenever the user is asking about a specific item.**
  • `card:<key>` — pulses one card / section.
  • `nav:<page>` — pulses one nav tab.
  • `filter:<which>` — pulses one filter dropdown.
  • `page:current` — last resort; pulses the whole content frame (easy to miss).

**SHOW-DON'T-TELL IS THE DEFAULT.** When the user's question is about something visible in the dashboard, drive them there in the same turn as the answer — DO NOT wait for them to say "show me", "where is it?", or "highlight it". Concretely:

- User asks about a specific item/SKU → in ONE turn: fetch the data, `set_filter` if a relevant filter is implied ("in Tokyo" → set location), `navigate_to_page` to the page that displays it, `highlight_element` with `sku:<SKU>` for the exact row.
- User asks about a category or page-level metric → same chain ending in `card:<key>` for the relevant section.
- User asks "where do I find X?" → navigate + highlight, don't just describe.

Combining navigate + filter + highlight in a single turn is the expected pattern, not the exception. The user shouldn't have to ask twice.

**Filter scope by page — do NOT set irrelevant filters:**
- `/demand`, `/backlog`, `/spending`, `/restocking` show globally-aggregated data; setting location/category filters can make these pages display empty results. Do NOT set warehouse/category/status filters when navigating to these pages.
- `/inventory`, `/orders`, `/low-stock`, `/suppliers`, `/reports`, `/` (Overview) honor warehouse + category (and orders/reports honor status + month too).
- If the user asks about a metric on a page that doesn't segment by their implied filter (e.g. "what's the demand in London?" — demand isn't warehouse-specific), answer with the global view and note the limitation rather than setting an empty-page filter.

(3) PROPOSAL-ONLY MUTATION — `propose_restocking_order(budget)` returns a draft + an Approve button surfaced in the UI. The user must click Approve before it commits. Always summarize the plan in plain English so they know what they're approving.

How to behave:
- Be concise. The panel is narrow; long answers are hard to read.
- When the user asks about a slice of data, USE the tools — don't guess from prior context.
- The user has filters set in the dashboard (warehouse, category, status, time period). Each user message includes a JSON block with the current `route` and `filters`. Default to using those filters in your tool calls unless the user clearly asks for something broader (e.g. "across all warehouses").
- After tool calls, synthesize — don't just dump raw JSON back to the user.
- For numbers, prefer "$141K" / "1.4M" formatting over raw floats.
- **Be a guide, not just a chatbot.** When you mention a page, navigate them there. When you reference a UI control, highlight it. When you cite a filter value, set it. This is what makes you an Ops Copilot vs. a read-only Q&A bot.
- If the user asks for something that would require writing data the tools don't support (e.g. delete an order, edit inventory), say so plainly — don't pretend you did it.
- No emojis. The product UI is professional / handwritten-sketchbook themed.
"""


def _build_system_blocks() -> list[dict]:
    """System content as a list of text blocks with cache_control on the last
    one — Anthropic caches everything from `tools` through the marked block."""
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _format_user_message(user_text: str, page_context: dict | None) -> str:
    """Render the user message with the page context as a fenced JSON block.

    Putting context inside the user message (not the system prompt) keeps the
    cached prefix invariant when filters change — only this fresh user message
    differs across turns.
    """
    if not page_context:
        return user_text
    ctx_json = json.dumps(page_context, sort_keys=True)
    return f"{user_text}\n\n<page_context>{ctx_json}</page_context>"


async def run_chat(
    conversation_id: str,
    user_message: str,
    page_context: dict | None = None,
) -> AsyncIterator[dict]:
    """Run one user turn through the agent loop, yielding SSE-shaped events.

    Yielded events (the chat endpoint serializes these as SSE data lines):
      {type: "text",     delta: "..."}             — assistant text token
      {type: "tool_use", name: "...", input: {...}}— tool call about to run
      {type: "tool_result", name: "...", result: ...}
      {type: "proposal", name: "...", proposal: {...}}
                                                   — a proposal payload that
                                                     needs Approve in the UI
      {type: "error",    message: "..."}
      {type: "done",     stop_reason: "..."}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield {"type": "error", "message": "ANTHROPIC_API_KEY is not set on the server."}
        yield {"type": "done", "stop_reason": "error"}
        return

    client = Anthropic(api_key=api_key)

    # Persist + replay history
    rendered_user = _format_user_message(user_message, page_context)
    store.append_event(
        conversation_id,
        {"kind": "user_message", "text": rendered_user, "raw_text": user_message},
    )

    messages = store.build_message_history(conversation_id)

    for _ in range(MAX_TURNS):
        # We accumulate the assistant turn's text + tool_use blocks here so we
        # can both (a) ship them on the wire to the UI and (b) persist them as
        # discrete events to the JSONL store.
        try:
            # Note: no `thinking` param. Adaptive thinking is Opus 4.6+ /
            # Sonnet 4.6 only; Haiku 4.5 doesn't accept it. Skipping thinking
            # also keeps the loop snappy for an interactive copilot.
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=_build_system_blocks(),  # type: ignore[arg-type]
                tools=TOOL_SCHEMAS,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            ) as stream:
                # Stream raw text deltas to the UI as Claude produces them.
                # The stream helper also accumulates a final Message for us.
                for text_delta in stream.text_stream:
                    yield {"type": "text", "delta": text_delta}

                final = stream.get_final_message()
        except Exception as e:
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
            yield {"type": "done", "stop_reason": "error"}
            return

        # Persist every block in the assistant turn — text first, then tool_use
        # in document order so the JSONL replay reconstructs the same content
        # array (matters for tool_use_id ↔ tool_result_id pairing).
        for block in final.content:
            if block.type == "text":
                store.append_event(
                    conversation_id,
                    {"kind": "assistant_text", "text": block.text},
                )
            elif block.type == "tool_use":
                store.append_event(
                    conversation_id,
                    {
                        "kind": "tool_use",
                        "tool_use_id": block.id,
                        "name": block.name,
                        "input": block.input,
                    },
                )

        # Append the full assistant turn to in-memory messages for the next
        # iteration. We MUST convert per block type with only the fields the
        # API accepts on INPUT — `block.model_dump()` includes output-only
        # SDK fields like `parsed_output` on text blocks, which trigger a
        # 400 "Extra inputs are not permitted" on the next request.
        def _to_input_block(b):
            if b.type == "text":
                return {"type": "text", "text": b.text}
            if b.type == "tool_use":
                return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            if b.type == "thinking":
                # Preserve signature verbatim — the API validates it.
                return {"type": "thinking", "thinking": b.thinking, "signature": b.signature}
            if b.type == "redacted_thinking":
                return {"type": "redacted_thinking", "data": b.data}
            # Unknown block type — drop rather than crash; the model will adapt.
            return None

        cleaned = [blk for blk in (_to_input_block(b) for b in final.content) if blk]
        messages.append({"role": "assistant", "content": cleaned})

        if final.stop_reason != "tool_use":
            yield {"type": "done", "stop_reason": final.stop_reason}
            return

        # Run every tool_use block, ship results to the UI + persist them, then
        # loop with an appended user-turn carrying all tool_result blocks.
        tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
        tool_result_content: list[dict] = []

        for block in tool_use_blocks:
            yield {
                "type": "tool_use",
                "name": block.name,
                "input": block.input,
            }
            result_json = run_tool(block.name, block.input or {})
            try:
                parsed = json.loads(result_json)
            except json.JSONDecodeError:
                parsed = result_json

            store.append_event(
                conversation_id,
                {
                    "kind": "tool_result",
                    "tool_use_id": block.id,
                    "name": block.name,
                    "content": result_json,
                },
            )
            tool_result_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_json,
                }
            )

            # Surface proposals as their own UI event so the panel can render
            # an Approve/Reject card instead of a raw tool result.
            if block.name == "propose_restocking_order" and isinstance(parsed, dict) and parsed.get("proposal_id"):
                store.append_event(
                    conversation_id,
                    {"kind": "proposal", "proposal": parsed},
                )
                yield {"type": "proposal", "name": block.name, "proposal": parsed}
            else:
                yield {
                    "type": "tool_result",
                    "name": block.name,
                    "result": parsed,
                }

            # UI-control tools (navigate / set_filter / highlight) return a
            # `queued_action` payload. We piggyback a separate `ui_action`
            # event on the SSE stream so the frontend can react — the model
            # already has the tool_result ack so it continues narrating.
            if (
                isinstance(parsed, dict)
                and "queued_action" in parsed
                and "error" not in parsed
            ):
                store.append_event(
                    conversation_id,
                    {"kind": "ui_action", "action": parsed},
                )
                yield {"type": "ui_action", "action": parsed}

        messages.append({"role": "user", "content": tool_result_content})

    # Hit MAX_TURNS without natural end_turn — bail.
    yield {"type": "error", "message": f"Hit tool-loop ceiling ({MAX_TURNS} turns)."}
    yield {"type": "done", "stop_reason": "loop_ceiling"}
