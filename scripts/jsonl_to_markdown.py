#!/usr/bin/env python3
"""
JSONL to Markdown Converter
Converts raw LLM interaction JSONL files to readable markdown format
"""

import json
import os
import sys
from datetime import datetime


def format_timestamp(iso_string):
    """Convert ISO timestamp to readable format"""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_string


def count_tags(content):
    """Count think tags in content"""
    opening = content.count("<think>")
    closing = content.count("</think>")
    return opening, closing


def extract_tools(content):
    """Extract tool calls from content"""
    tools = []
    import re

    # Extract terminal commands
    terminal_pattern = r"<terminal>(.*?)</terminal>"
    terminals = re.findall(terminal_pattern, content, re.DOTALL)
    tools.extend([("terminal", cmd.strip()) for cmd in terminals])

    return tools


def format_content_preview(content, max_length=500):
    """Format content with preview"""
    if len(content) <= max_length:
        return content
    return (
        f"{content[:max_length]}...\n\n[Content truncated - {len(content)} total chars]"
    )


def convert_jsonl_to_markdown(jsonl_path, output_path=None, full_content=False):
    """Convert JSONL file to markdown format"""

    if output_path is None:
        output_path = jsonl_path.replace(".jsonl", ".md")

    filename = os.path.basename(jsonl_path)

    # Start building markdown
    md_lines = []
    md_lines.append("# Raw LLM Conversation")
    md_lines.append(f"**Source**: `{filename}`\n")
    md_lines.append(f"**Converted**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    md_lines.append("---\n")

    interaction_count = 0
    total_opening_tags = 0
    total_closing_tags = 0

    try:
        with open(jsonl_path, "r") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    interaction_count += 1

                    md_lines.append(f"## Interaction #{interaction_count}\n")

                    # Extract request info
                    if "request" in data:
                        request = data["request"]
                        md_lines.append("### 📤 Request\n")

                        if "timestamp" in data:
                            md_lines.append(
                                f"**Timestamp**: {format_timestamp(data['timestamp'])}\n"
                            )

                        # Handle both request structures (direct and via payload)
                        payload = request.get("payload", request)

                        if "model" in payload:
                            md_lines.append(f"**Model**: `{payload['model']}`\n")

                        if "temperature" in payload:
                            md_lines.append(
                                f"**Temperature**: {payload['temperature']}\n"
                            )

                        if "max_tokens" in payload:
                            md_lines.append(
                                f"**Max Tokens**: {payload['max_tokens']}\n"
                            )

                        if "messages" in payload:
                            md_lines.append(
                                f"**Message Count**: {len(payload['messages'])}\n\n"
                            )

                            # Show all messages in the request
                            for idx, msg in enumerate(payload["messages"], 1):
                                role = msg.get("role", "unknown")
                                content = msg.get("content", "")

                                if isinstance(content, list):
                                    # Handle content array
                                    text_parts = [
                                        c.get("text", "")
                                        for c in content
                                        if c.get("type") == "text"
                                    ]
                                    content = "\n".join(text_parts)

                                if content:
                                    emoji = (
                                        "👤"
                                        if role == "user"
                                        else "🤖" if role == "assistant" else "⚙️"
                                    )
                                    md_lines.append(
                                        f"#### {emoji} Message {idx} ({role})\n\n"
                                    )

                                    if full_content or len(content) <= 500:
                                        md_lines.append(f"```\n{content}\n```\n\n")
                                    else:
                                        md_lines.append(
                                            f"```\n{format_content_preview(content, 500)}\n```\n\n"
                                        )

                    # Extract response info
                    if "response" in data and "data" in data["response"]:
                        response_data = data["response"]["data"]
                        md_lines.append("### 📥 Response\n")

                        if "model" in response_data:
                            md_lines.append(f"**Model**: `{response_data['model']}`\n")

                        if "usage" in response_data:
                            usage = response_data["usage"]
                            inp = usage.get("input_tokens", 0)
                            out = usage.get("output_tokens", 0)
                            md_lines.append(f"**Tokens**: Input={inp}, Output={out}\n")

                        # Extract actual content
                        if (
                            "choices" in response_data
                            and len(response_data["choices"]) > 0
                        ):
                            message = response_data["choices"][0].get("message", {})
                            content = message.get("content", "")

                            # Count tags
                            opening, closing = count_tags(content)
                            total_opening_tags += opening
                            total_closing_tags += closing
                            orphaned = closing - opening

                            if opening > 0 or closing > 0:
                                md_lines.append("\n**🏷️ Think Tags**:\n")
                                md_lines.append(f"- Opening `<think>`: {opening}\n")
                                md_lines.append(f"- Closing `</think>`: {closing}\n")
                                md_lines.append(f"- Balance: {orphaned:+d}\n")

                                if orphaned != 0:
                                    md_lines.append(
                                        "- ⚠️ **ORPHANED TAGS DETECTED!**\n"
                                    )

                            # Extract tools
                            tools = extract_tools(content)
                            if tools:
                                md_lines.append(f"\n**🔧 Tools Used**: {len(tools)}\n")
                                for tool_type, tool_cmd in tools[:5]:  # Show first 5
                                    md_lines.append(
                                        f"- `{tool_type}`: `{tool_cmd[:80]}`\n"
                                    )
                                if len(tools) > 5:
                                    md_lines.append(
                                        f"- ... and {len(tools) - 5} more\n"
                                    )

                            # Content - always show full content
                            md_lines.append(
                                f"\n**📄 Response Content** ({len(content)} chars):\n\n"
                            )
                            md_lines.append("```\n")
                            md_lines.append(content)
                            md_lines.append("\n```\n")

                    md_lines.append("\n---\n\n")

                except json.JSONDecodeError as e:
                    md_lines.append(f"⚠️ Error parsing line {line_num}: {e}\n\n")
                except Exception as e:
                    md_lines.append(f"⚠️ Error processing line {line_num}: {e}\n\n")

        # Summary
        md_lines.append("## 📊 Summary\n")
        md_lines.append(f"- **Total Interactions**: {interaction_count}\n")
        md_lines.append(f"- **Total `<think>` tags**: {total_opening_tags}\n")
        md_lines.append(f"- **Total `</think>` tags**: {total_closing_tags}\n")
        md_lines.append(
            f"- **Orphaned tags**: {total_closing_tags - total_opening_tags:+d}\n"
        )

        if total_closing_tags - total_opening_tags != 0:
            md_lines.append(
                "\n⚠️ **WARNING**: Orphaned tags detected in this conversation!\n"
            )
        else:
            md_lines.append("\n✅ All think tags are properly paired.\n")

        # Write to file
        with open(output_path, "w") as f:
            f.write("\n".join(md_lines))

        return output_path, interaction_count

    except FileNotFoundError:
        print(f"❌ Error: File not found: {jsonl_path}")
        return None, 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return None, 0


def main():
    """Main CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert JSONL conversation files to readable markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert single file (preview mode)
  %(prog)s raw_llm_interactions_2025-11-07_131847.jsonl

  # Convert with full content
  %(prog)s raw_llm_interactions_2025-11-07_131847.jsonl --full

  # Specify output file
  %(prog)s input.jsonl -o output.md

  # Convert latest conversation (paths are under ~/.kollab/projects/<project>/)
  %(prog)s ~/.kollab/projects/my_project/conversations/raw/*.jsonl --latest
        """,
    )

    parser.add_argument("input", help="Input JSONL file or glob pattern")
    parser.add_argument(
        "-o", "--output", help="Output markdown file (default: input.md)"
    )
    parser.add_argument(
        "-f",
        "--full",
        action="store_true",
        help="Include full request messages (default: preview only). Response content is always full.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Process only the latest file if glob pattern given",
    )

    args = parser.parse_args()

    # Handle glob patterns
    if "*" in args.input:
        import glob

        files = sorted(glob.glob(args.input))
        if not files:
            print(f"❌ No files match pattern: {args.input}")
            return 1

        if args.latest:
            files = [files[-1]]

        print(f"📂 Found {len(files)} file(s)")

        for file_path in files:
            print(f"\n🔄 Converting: {os.path.basename(file_path)}")
            output_path, count = convert_jsonl_to_markdown(
                file_path, args.output, args.full
            )
            if output_path:
                print(f"✅ Created: {output_path} ({count} interactions)")
    else:
        # Single file
        if not os.path.exists(args.input):
            print(f"❌ File not found: {args.input}")
            return 1

        print(f"🔄 Converting: {args.input}")
        output_path, count = convert_jsonl_to_markdown(
            args.input, args.output, args.full
        )

        if output_path:
            print(f"✅ Created: {output_path}")
            print(f"📊 Processed {count} interaction(s)")
            print(
                "\n💡 Tip: Use --full to see complete request messages (responses are always full)"
            )
        else:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
