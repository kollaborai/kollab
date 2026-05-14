#!/usr/bin/env python3
"""
JSONL to Markdown Converter

Converts raw LLM interaction JSONL files (kollab _raw.jsonl) to readable
markdown. Handles both schemas:

  v1 (schema_version == 1): typed RawInteraction (see kollabor_ai/raw_log.py)
    - profile.{provider, model, base_url, streaming}
    - request.conversation_local (LocalMessage entries with metadata)
    - request.wire_request (provider-native dict, opt-in via --wire)
    - request.wire_provider (tag for wire_request shape)
    - request.tools
    - response.content, .token_usage, .tool_calls, .stop_reason, .thinking
    - turn_id, continuation_of (groups multi-call user turns)

  v0 (no schema_version): same response shape, but
    - top-level provider, model, streaming
    - request.messages (instead of conversation_local)
    - no wire_request, no turn_id, no thinking field
"""

import argparse
import glob as globmod
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ---------- schema-aware accessors ----------

def is_v1(entry: Dict[str, Any]) -> bool:
    """An entry is v1 if schema_version is set (>=1). Otherwise treat as v0."""
    return bool(entry.get("schema_version"))


def get_profile(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return {provider, model, base_url, streaming} regardless of schema."""
    if is_v1(entry):
        p = entry.get("profile") or {}
        return {
            "provider": p.get("provider", ""),
            "model": p.get("model", ""),
            "base_url": p.get("base_url", ""),
            "streaming": p.get("streaming", False),
        }
    return {
        "provider": entry.get("provider", ""),
        "model": entry.get("model", ""),
        "base_url": "",
        "streaming": entry.get("streaming", False),
    }


def get_messages(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the local conversation view (list of {role, content, metadata?})."""
    req = entry.get("request") or {}
    if is_v1(entry):
        return req.get("conversation_local") or []
    return req.get("messages") or []


def get_response(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return the response dict (same shape across v0/v1)."""
    return entry.get("response") or {}


# ---------- content normalization ----------

def normalize_content(content: Any) -> str:
    """Flatten message content to a string.

    Both schemas allow str OR list[{type, text, ...}] (Anthropic-style content
    arrays leak in from provider transforms). Drop non-text parts but keep
    them visible as a short marker.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for c in content:
            if not isinstance(c, dict):
                parts.append(str(c))
                continue
            ctype = c.get("type", "")
            if ctype == "text" and "text" in c:
                parts.append(c["text"])
            elif ctype == "tool_use":
                parts.append(
                    f"[tool_use: {c.get('name', '?')} "
                    f"input={json.dumps(c.get('input', {}), default=str)[:200]}]"
                )
            elif ctype == "tool_result":
                rc = c.get("content", "")
                if isinstance(rc, list):
                    rc = " ".join(
                        x.get("text", "") for x in rc if isinstance(x, dict)
                    )
                parts.append(f"[tool_result {c.get('tool_use_id', '?')}: {rc}]")
            else:
                parts.append(f"[{ctype or 'unknown'} part]")
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


# ---------- formatting helpers ----------

def format_timestamp(iso_string: str) -> str:
    try:
        s = (iso_string or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_string or "?"


def count_think_tags(content: str) -> Tuple[int, int]:
    return content.count("<think>"), content.count("</think>")


def extract_xml_tools(content: str) -> List[Tuple[str, str]]:
    """Pull XML-style tool invocations out of response content (legacy view)."""
    out: List[Tuple[str, str]] = []
    for cmd in re.findall(r"<terminal>(.*?)</terminal>", content, re.DOTALL):
        out.append(("terminal", cmd.strip()))
    return out


def truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}...\n\n[truncated - {len(text)} total chars]"


def role_marker(role: str) -> str:
    return {
        "user": "USER",
        "assistant": "ASSISTANT",
        "system": "SYSTEM",
        "tool": "TOOL",
    }.get(role, role.upper() or "UNKNOWN")


# ---------- markdown rendering ----------

def render_entry_header(
    entry: Dict[str, Any], idx: int, lines: List[str]
) -> None:
    profile = get_profile(entry)
    schema = "v1" if is_v1(entry) else "v0"

    lines.append(f"## Interaction #{idx}  [{schema}]\n")

    if entry.get("timestamp"):
        lines.append(f"- timestamp: {format_timestamp(entry['timestamp'])}")
    if entry.get("session_id"):
        lines.append(f"- session: `{entry['session_id']}`")
    if entry.get("duration_s") is not None:
        lines.append(f"- duration: {entry['duration_s']}s")

    if profile["provider"] or profile["model"]:
        lines.append(
            f"- profile: provider=`{profile['provider']}` "
            f"model=`{profile['model']}` "
            f"streaming={profile['streaming']}"
        )
        if profile["base_url"]:
            lines.append(f"- base_url: `{profile['base_url']}`")

    # v1-only: turn_id, continuation_of, wire_provider
    if is_v1(entry):
        turn_id = entry.get("turn_id") or ""
        cont = entry.get("continuation_of")
        if turn_id:
            lines.append(f"- turn_id: `{turn_id}`")
        if cont:
            lines.append(f"- continuation_of: `{cont}`")
        wp = (entry.get("request") or {}).get("wire_provider") or ""
        if wp:
            lines.append(f"- wire_provider: `{wp}`")

    if entry.get("cancelled"):
        lines.append("- cancelled: **true**")
    if entry.get("error"):
        lines.append(f"- error: `{entry['error']}`")

    lines.append("")


def render_request(
    entry: Dict[str, Any],
    lines: List[str],
    full_content: bool,
    show_wire: bool,
) -> None:
    request = entry.get("request") or {}
    messages = get_messages(entry)

    lines.append("### Request (conversation_local)\n")
    lines.append(f"- message count: {len(messages)}")
    tools = request.get("tools")
    if tools:
        lines.append(f"- tools attached: {len(tools)}")
    lines.append("")

    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown")
        content_str = normalize_content(msg.get("content", ""))
        meta = msg.get("metadata") or {}

        if not content_str and not meta:
            continue

        lines.append(f"#### Message {i} ({role_marker(role)})\n")

        if meta:
            lines.append(
                f"_metadata: `{json.dumps(meta, default=str)[:200]}`_\n"
            )

        if content_str:
            shown = content_str if full_content else truncate(content_str, 500)
            lines.append(f"```\n{shown}\n```\n")

    # Wire request (v1 only, opt-in)
    if show_wire and is_v1(entry):
        wire = request.get("wire_request")
        wp = request.get("wire_provider", "")
        lines.append(f"### Wire request (provider-native, `{wp}`)\n")
        if wire is None:
            lines.append("_wire_request was not captured for this entry._\n")
        else:
            wire_json = json.dumps(wire, indent=2, default=str)
            shown = (
                wire_json
                if full_content
                else truncate(wire_json, 2000)
            )
            lines.append(f"```json\n{shown}\n```\n")


def render_response(
    entry: Dict[str, Any],
    lines: List[str],
    show_chunks: bool,
) -> Tuple[int, int]:
    response = get_response(entry)
    content = response.get("content") or ""

    lines.append("### Response\n")

    usage = response.get("token_usage") or {}
    if usage:
        # Support both old (prompt_tokens/completion_tokens) and new
        # (input_tokens/output_tokens) usage keys.
        inp = (
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or 0
        )
        out = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or 0
        )
        total = usage.get("total_tokens") or (inp + out)
        cache_w = usage.get("cache_creation_tokens", 0)
        cache_r = usage.get("cache_read_tokens", 0)
        lines.append(
            f"- tokens: input={inp} output={out} total={total}"
            + (
                f" cache_write={cache_w} cache_read={cache_r}"
                if cache_w or cache_r
                else ""
            )
        )

    stop_reason = response.get("stop_reason") or ""
    if stop_reason:
        lines.append(f"- stop_reason: `{stop_reason}`")

    tool_calls = response.get("tool_calls") or []
    if tool_calls:
        lines.append(f"- tool_calls: {len(tool_calls)}")

    # v1 only: dedicated thinking field
    thinking = response.get("thinking")
    if thinking:
        lines.append("\n**Thinking** (model's separate thinking field):\n")
        lines.append(f"```\n{thinking}\n```\n")

    # Native tool calls
    if tool_calls:
        lines.append("\n**Tool calls** (native):\n")
        for tc in tool_calls:
            tc_in = json.dumps(tc.get("input", {}), default=str)
            if len(tc_in) > 200:
                tc_in = tc_in[:200] + "..."
            lines.append(
                f"- `{tc.get('name', '?')}` (id=`{tc.get('id', '?')}`) "
                f"input={tc_in}"
            )
        lines.append("")

    # Legacy XML-style tools embedded in content
    xml_tools = extract_xml_tools(content)
    if xml_tools:
        lines.append(f"\n**XML tools in content**: {len(xml_tools)}")
        for tname, cmd in xml_tools[:5]:
            lines.append(f"- `{tname}`: `{cmd[:80]}`")
        if len(xml_tools) > 5:
            lines.append(f"- ... and {len(xml_tools) - 5} more")
        lines.append("")

    # Think tag balance (legacy diagnostic)
    opening, closing = count_think_tags(content)
    if opening or closing:
        orphaned = closing - opening
        lines.append("\n**`<think>` tag balance**:")
        lines.append(f"- opening: {opening}")
        lines.append(f"- closing: {closing}")
        lines.append(f"- balance: {orphaned:+d}")
        if orphaned != 0:
            lines.append("- WARN: ORPHANED TAGS")
        lines.append("")

    # Always render full response content (debugging is the point)
    lines.append(f"**Response content** ({len(content)} chars):\n")
    lines.append("```")
    lines.append(content)
    lines.append("```\n")

    chunks = response.get("raw_chunks") or []
    if chunks:
        if show_chunks:
            chunks_json = json.dumps(chunks, indent=2, default=str)
            lines.append(
                f"\n**Raw chunks** ({len(chunks)} chunks):\n"
            )
            lines.append(f"```json\n{truncate(chunks_json, 5000)}\n```\n")
        else:
            lines.append(
                f"\n_raw_chunks: {len(chunks)} captured "
                "(pass --chunks to render)_\n"
            )

    return opening, closing


# ---------- main conversion ----------

def convert_jsonl_to_markdown(
    jsonl_path: str,
    output_path: Optional[str] = None,
    full_content: bool = False,
    show_wire: bool = False,
    show_chunks: bool = False,
) -> Tuple[Optional[str], int]:
    """Convert one JSONL file to a markdown file. Returns (path, count)."""

    if output_path is None:
        output_path = jsonl_path.replace(".jsonl", ".md")

    filename = os.path.basename(jsonl_path)

    md: List[str] = []
    md.append("# Raw LLM Conversation")
    md.append(f"- source: `{filename}`")
    md.append(f"- converted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md.append("")
    md.append("---\n")

    # Track schema versions encountered + turn grouping
    versions_seen = {"v0": 0, "v1": 0}
    last_turn_id: Optional[str] = None
    total_opening = 0
    total_closing = 0
    interaction_count = 0

    try:
        with open(jsonl_path, "r") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as e:
                    md.append(f"WARN: bad JSON on line {line_num}: {e}\n")
                    continue

                try:
                    interaction_count += 1
                    versions_seen["v1" if is_v1(entry) else "v0"] += 1

                    # Visually group continuations
                    if is_v1(entry):
                        cont = entry.get("continuation_of")
                        if cont:
                            md.append(
                                f"<!-- continuation of turn "
                                f"{cont[:8]} -->\n"
                            )
                        last_turn_id = entry.get("turn_id") or last_turn_id

                    render_entry_header(entry, interaction_count, md)
                    render_request(entry, md, full_content, show_wire)
                    op, cl = render_response(entry, md, show_chunks)
                    total_opening += op
                    total_closing += cl

                    md.append("\n---\n")
                except Exception as e:  # noqa: BLE001
                    md.append(
                        f"WARN: failed to render line {line_num}: {e}\n"
                    )

        # Summary
        md.append("## Summary")
        md.append(f"- total interactions: {interaction_count}")
        md.append(f"- v1 entries: {versions_seen['v1']}")
        md.append(f"- v0 entries: {versions_seen['v0']}")
        md.append(f"- total `<think>` opening: {total_opening}")
        md.append(f"- total `</think>` closing: {total_closing}")
        md.append(f"- orphaned: {total_closing - total_opening:+d}")
        if total_closing - total_opening != 0:
            md.append("\nWARN: orphaned `<think>` tags detected.")
        else:
            md.append("\nAll `<think>` tags balanced.")

        with open(output_path, "w") as f:
            f.write("\n".join(md))

        return output_path, interaction_count

    except FileNotFoundError:
        print(f"error: file not found: {jsonl_path}", file=sys.stderr)
        return None, 0
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return None, 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert kollab raw JSONL conversation logs to markdown. "
            "Supports v1 (schema_version) and v0 (legacy) formats."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert single file (truncated request previews)
  %(prog)s ~/.kollab/projects/<proj>/conversations/raw/abc_raw.jsonl

  # Convert with full request content
  %(prog)s abc_raw.jsonl --full

  # Include the wire_request (provider-native payload) -- v1 only
  %(prog)s abc_raw.jsonl --wire

  # Include raw_chunks (large; off by default)
  %(prog)s abc_raw.jsonl --chunks

  # Specify output
  %(prog)s input.jsonl -o output.md

  # Glob pattern (use --latest to pick the most recent)
  %(prog)s '~/.kollab/projects/my_proj/conversations/raw/*.jsonl' --latest
        """,
    )

    parser.add_argument("input", help="Input JSONL file or glob pattern")
    parser.add_argument(
        "-o", "--output", help="Output markdown file (default: input.md)"
    )
    parser.add_argument(
        "-f", "--full", action="store_true",
        help="Render full request message bodies (default: preview)",
    )
    parser.add_argument(
        "--wire", action="store_true",
        help="Render request.wire_request (provider-native payload, v1 only)",
    )
    parser.add_argument(
        "--chunks", action="store_true",
        help="Render response.raw_chunks (large; default shows count only)",
    )
    parser.add_argument(
        "--latest", action="store_true",
        help="Process only the latest file if a glob is supplied",
    )

    args = parser.parse_args()

    if "*" in args.input:
        files = sorted(globmod.glob(args.input))
        if not files:
            print(f"no files match pattern: {args.input}", file=sys.stderr)
            return 1
        if args.latest:
            files = [files[-1]]
        print(f"found {len(files)} file(s)")
        for path in files:
            print(f"\nconverting: {os.path.basename(path)}")
            out, count = convert_jsonl_to_markdown(
                path, args.output, args.full, args.wire, args.chunks
            )
            if out:
                print(f"  wrote: {out} ({count} interactions)")
    else:
        if not os.path.exists(args.input):
            print(f"file not found: {args.input}", file=sys.stderr)
            return 1
        print(f"converting: {args.input}")
        out, count = convert_jsonl_to_markdown(
            args.input, args.output, args.full, args.wire, args.chunks
        )
        if out:
            print(f"wrote: {out}")
            print(f"interactions: {count}")
            print(
                "tip: --full for full request bodies, --wire for "
                "wire_request, --chunks for raw_chunks"
            )
        else:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
