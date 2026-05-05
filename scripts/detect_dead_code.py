#!/usr/bin/env python3
"""Dead Code Detection Script for Kollab CLI.

This script performs comprehensive dead code analysis including:
- Backup file detection
- TODO/FIXME tracking
- Pass statement analysis
- Unused code detection (via vulture if available)
- Classification of findings
- Structured reporting

Usage:
    python scripts/detect_dead_code.py
    python scripts/detect_dead_code.py --output report.json
    python scripts/detect_dead_code.py --category confirmed_dead
    python scripts/detect_dead_code.py --format markdown > report.md
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Finding:
    """Represents a dead code finding."""

    category: str  # confirmed_dead, disconnected, placeholder, dynamic, backup
    confidence: str  # high, medium, low
    file_path: str
    line_number: Optional[int]
    code_snippet: Optional[str]
    description: str
    impact: str  # critical, high, medium, low
    recommended_action: str  # delete, review, keep, document


class DeadCodeDetector:
    """Comprehensive dead code detection."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.findings: List[Finding] = []
        self.stats: Dict[str, int] = defaultdict(int)

        # Patterns to ignore
        self.ignore_patterns = [
            "__pycache__",
            ".git",
            ".venv",
            "venv",
            ".pytest_cache",
            "*.pyc",
            ".archive",
            "backups",
        ]

        # Dynamic code patterns that appear unused but are called at runtime
        self.dynamic_patterns = [
            r"^on_.*",  # Event handlers: on_input, on_click
            r"^handle_.*",  # Handle methods: handle_event
            r"^cmd_.*",  # Command handlers: cmd_help
            r"^do_.*",  # Action methods: do_save
            r"^_handle_.*",  # Private handlers
            r"^test_.*",  # Test methods
            r"^.*Plugin$",  # Plugin classes
        ]

    def scan_all(self) -> List[Finding]:
        """Run all detection methods."""
        print("Starting dead code detection...")
        print()

        # 1. Find backup files
        print("[1/5] Scanning for backup files...")
        self.find_backup_files()

        # 2. Find TODO/FIXME items
        print("[2/5] Scanning for TODO/FIXME comments...")
        self.find_todos()

        # 3. Find pass statements
        print("[3/5] Scanning for pass statements...")
        self.find_pass_statements()

        # 4. Find NotImplementedError stubs
        print("[4/5] Scanning for NotImplementedError stubs...")
        self.find_not_implemented()

        # 5. Run vulture if available
        print("[5/5] Running vulture analysis...")
        self.run_vulture()

        print()
        print(f"Detection complete! Found {len(self.findings)} items.")
        return self.findings

    def find_backup_files(self) -> None:
        """Find backup and duplicate files."""
        backup_extensions = [".bak", ".backup", ".old", ".tmp"]
        backup_patterns = [
            r".*_copy\.",
            r".*_old\.",
            r".*~$",
            r".* copy\.",
        ]

        for py_file in self.get_python_files():
            # Check for backup by extension
            for ext in backup_extensions:
                if py_file.suffix == ext or str(py_file).endswith(ext):
                    self.add_finding(
                        category="backup",
                        confidence="high",
                        file_path=str(py_file.relative_to(self.root_dir)),
                        line_number=None,
                        code_snippet=None,
                        description=f"Backup file with {ext} extension",
                        impact="low",
                        recommended_action="delete",
                    )
                    self.stats["backup_files"] += 1

            # Check for backup by naming pattern
            filename = py_file.name
            for pattern in backup_patterns:
                if re.match(pattern, filename):
                    self.add_finding(
                        category="backup",
                        confidence="high",
                        file_path=str(py_file.relative_to(self.root_dir)),
                        line_number=None,
                        code_snippet=None,
                        description=f"Backup file matching pattern: {pattern}",
                        impact="low",
                        recommended_action="delete",
                    )
                    self.stats["backup_files"] += 1

    def find_todos(self) -> None:
        """Find TODO, FIXME, HACK, XXX comments."""
        todo_patterns = [
            (r"#\s*TODO:?\s*(.+)", "TODO"),
            (r"#\s*FIXME:?\s*(.+)", "FIXME"),
            (r"#\s*HACK:?\s*(.+)", "HACK"),
            (r"#\s*XXX:?\s*(.+)", "XXX"),
        ]

        for py_file in self.get_python_files():
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        for pattern, tag_type in todo_patterns:
                            match = re.search(pattern, line)
                            if match:
                                description = match.group(1).strip()
                                self.add_finding(
                                    category="placeholder",
                                    confidence="medium",
                                    file_path=str(py_file.relative_to(self.root_dir)),
                                    line_number=line_num,
                                    code_snippet=line.strip(),
                                    description=f"{tag_type}: {description}",
                                    impact="medium",
                                    recommended_action="review",
                                )
                                self.stats[f"todo_{tag_type.lower()}"] += 1
            except Exception as e:
                print(f"Error reading {py_file}: {e}")

    def find_pass_statements(self) -> None:
        """Find pass statements that might be stubs or incomplete code."""
        for py_file in self.get_python_files():
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse AST to find pass statements
                try:
                    tree = ast.parse(content, filename=str(py_file))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Pass):
                            # Get line number
                            line_num = node.lineno

                            # Get parent context (function, class, etc.)
                            parent_context = self.get_parent_context(content, line_num)

                            # Classify pass statement
                            if self.is_abstract_method(content, line_num):
                                category = "placeholder"
                                action = "keep"
                                confidence = "high"
                            else:
                                category = "disconnected"
                                action = "review"
                                confidence = "medium"

                            self.add_finding(
                                category=category,
                                confidence=confidence,
                                file_path=str(py_file.relative_to(self.root_dir)),
                                line_number=line_num,
                                code_snippet=parent_context,
                                description="Pass statement - potential stub or incomplete implementation",
                                impact="medium",
                                recommended_action=action,
                            )
                            self.stats["pass_statements"] += 1
                except SyntaxError:
                    pass  # Skip files with syntax errors

            except Exception as e:
                print(f"Error analyzing {py_file}: {e}")

    def find_not_implemented(self) -> None:
        """Find NotImplementedError raises."""
        for py_file in self.get_python_files():
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Look for NotImplementedError
                for line_num, line in enumerate(content.split("\n"), 1):
                    if "NotImplementedError" in line and "raise" in line:
                        self.add_finding(
                            category="placeholder",
                            confidence="high",
                            file_path=str(py_file.relative_to(self.root_dir)),
                            line_number=line_num,
                            code_snippet=line.strip(),
                            description="NotImplementedError stub - intentional placeholder",
                            impact="low",
                            recommended_action="keep",
                        )
                        self.stats["not_implemented"] += 1

            except Exception as e:
                print(f"Error reading {py_file}: {e}")

    def run_vulture(self) -> None:
        """Run vulture for unused code detection if available."""
        try:
            # Check if vulture is installed
            result = subprocess.run(
                ["vulture", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                print("  Vulture not installed. Skipping vulture analysis.")
                print("  Install: pip install vulture")
                return

            # Run vulture on core and plugins
            dirs_to_scan = ["core", "plugins"]
            for dir_name in dirs_to_scan:
                dir_path = self.root_dir / dir_name
                if not dir_path.exists():
                    continue

                result = subprocess.run(
                    [
                        "vulture",
                        str(dir_path),
                        "--min-confidence",
                        "80",
                        "--sort-by-size",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.stdout:
                    self.parse_vulture_output(result.stdout)

            print("  Vulture scan complete.")

        except FileNotFoundError:
            print("  Vulture not installed. Skipping.")
        except subprocess.TimeoutExpired:
            print("  Vulture scan timed out.")
        except Exception as e:
            print(f"  Vulture error: {e}")

    def parse_vulture_output(self, output: str) -> None:
        """Parse vulture output and create findings."""
        # Vulture output format:
        # path/to/file.py:123: unused function 'foo' (90% confidence)

        for line in output.strip().split("\n"):
            if not line.strip():
                continue

            # Parse line
            match = re.match(r"(.+?):(\d+):\s*(.+?)\s*\((\d+)%", line)
            if match:
                file_path, line_num, description, confidence = match.groups()

                # Determine category based on description
                if "unused import" in description:
                    category = "confirmed_dead"
                    impact = "low"
                    action = "delete"
                elif "unused function" in description:
                    # Check if it matches dynamic patterns
                    func_name = self.extract_function_name(description)
                    if self.is_dynamic_code(func_name):
                        category = "dynamic"
                        impact = "low"
                        action = "keep"
                    else:
                        category = "confirmed_dead"
                        impact = "medium"
                        action = "review"
                elif "unused variable" in description:
                    category = "confirmed_dead"
                    impact = "low"
                    action = "review"
                else:
                    category = "confirmed_dead"
                    impact = "medium"
                    action = "review"

                # Map vulture confidence to our scale
                conf_level = "high" if int(confidence) >= 80 else "medium"

                self.add_finding(
                    category=category,
                    confidence=conf_level,
                    file_path=file_path,
                    line_number=int(line_num),
                    code_snippet=None,
                    description=description,
                    impact=impact,
                    recommended_action=action,
                )
                self.stats["vulture_findings"] += 1

    def is_dynamic_code(self, name: str) -> bool:
        """Check if code name matches dynamic patterns."""
        for pattern in self.dynamic_patterns:
            if re.match(pattern, name):
                return True
        return False

    def extract_function_name(self, description: str) -> str:
        """Extract function name from vulture description."""
        # Example: "unused function 'foo'"
        match = re.search(r"'([^']+)'", description)
        return match.group(1) if match else ""

    def is_abstract_method(self, content: str, line_num: int) -> bool:
        """Check if pass statement is in an abstract method."""
        # Look backwards from line_num for @abstractmethod decorator
        lines = content.split("\n")
        if line_num > len(lines):
            return False

        # Check previous lines for decorator
        for i in range(max(0, line_num - 10), line_num):
            if "@abstractmethod" in lines[i] or "ABC" in lines[i]:
                return True

        return False

    def get_parent_context(self, content: str, line_num: int) -> str:
        """Get the parent function/class context for a line."""
        lines = content.split("\n")
        if line_num > len(lines):
            return ""

        # Look backwards to find def or class
        for i in range(line_num - 1, max(0, line_num - 20), -1):
            line = lines[i].strip()
            if line.startswith("def ") or line.startswith("class "):
                return lines[i].strip()

        return lines[line_num - 1].strip() if line_num > 0 else ""

    def get_python_files(self) -> List[Path]:
        """Get all Python files, excluding ignored patterns."""
        python_files = []

        for path in self.root_dir.rglob("*.py"):
            # Check if path should be ignored
            should_ignore = False
            for pattern in self.ignore_patterns:
                if pattern in str(path):
                    should_ignore = True
                    break

            if not should_ignore:
                python_files.append(path)

        return python_files

    def add_finding(
        self,
        category: str,
        confidence: str,
        file_path: str,
        line_number: Optional[int],
        code_snippet: Optional[str],
        description: str,
        impact: str,
        recommended_action: str,
    ) -> None:
        """Add a finding to the list."""
        finding = Finding(
            category=category,
            confidence=confidence,
            file_path=file_path,
            line_number=line_number,
            code_snippet=code_snippet,
            description=description,
            impact=impact,
            recommended_action=recommended_action,
        )
        self.findings.append(finding)

    def generate_report(self, format: str = "text") -> str:
        """Generate report in specified format."""
        if format == "json":
            return self.generate_json_report()
        elif format == "markdown":
            return self.generate_markdown_report()
        else:
            return self.generate_text_report()

    def generate_text_report(self) -> str:
        """Generate plain text report."""
        lines = []
        lines.append("=" * 80)
        lines.append("DEAD CODE DETECTION REPORT")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        lines.append("")

        # Summary
        lines.append("SUMMARY")
        lines.append("-" * 80)
        lines.append(f"Total Findings: {len(self.findings)}")
        lines.append("")

        # By category
        by_category = defaultdict(int)
        for finding in self.findings:
            by_category[finding.category] += 1

        lines.append("Findings by Category:")
        for category, count in sorted(by_category.items()):
            lines.append(f"  {category:20s}: {count:4d}")
        lines.append("")

        # By confidence
        by_confidence = defaultdict(int)
        for finding in self.findings:
            by_confidence[finding.confidence] += 1

        lines.append("Findings by Confidence:")
        for confidence, count in sorted(by_confidence.items()):
            lines.append(f"  {confidence:20s}: {count:4d}")
        lines.append("")

        # Statistics
        lines.append("Statistics:")
        for key, value in sorted(self.stats.items()):
            lines.append(f"  {key:30s}: {value:4d}")
        lines.append("")

        # Detailed findings
        lines.append("=" * 80)
        lines.append("DETAILED FINDINGS")
        lines.append("=" * 80)
        lines.append("")

        # Group by category
        for category in sorted(by_category.keys()):
            findings = [f for f in self.findings if f.category == category]
            lines.append("")
            lines.append(f"Category: {category.upper()}")
            lines.append("-" * 80)
            lines.append("")

            for finding in findings[:10]:  # Limit to first 10 per category
                lines.append(f"File: {finding.file_path}")
                if finding.line_number:
                    lines.append(f"Line: {finding.line_number}")
                lines.append(f"Confidence: {finding.confidence}")
                lines.append(f"Description: {finding.description}")
                lines.append(f"Action: {finding.recommended_action}")
                if finding.code_snippet:
                    lines.append(f"Code: {finding.code_snippet[:100]}")
                lines.append("")

            if len(findings) > 10:
                lines.append(f"... and {len(findings) - 10} more")
                lines.append("")

        return "\n".join(lines)

    def generate_json_report(self) -> str:
        """Generate JSON report."""
        report = {
            "generated": datetime.now().isoformat(),
            "summary": {
                "total_findings": len(self.findings),
                "by_category": dict(
                    defaultdict(
                        int,
                        {
                            f.category: sum(
                                1 for x in self.findings if x.category == f.category
                            )
                            for f in self.findings
                        },
                    )
                ),
                "statistics": dict(self.stats),
            },
            "findings": [asdict(f) for f in self.findings],
        }
        return json.dumps(report, indent=2)

    def generate_markdown_report(self) -> str:
        """Generate Markdown report."""
        lines = []
        lines.append("# Dead Code Detection Report")
        lines.append("")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"**Total Findings:** {len(self.findings)}")
        lines.append("")

        # By category
        by_category = defaultdict(int)
        for finding in self.findings:
            by_category[finding.category] += 1

        lines.append("### Findings by Category")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|-------|")
        for category, count in sorted(by_category.items()):
            lines.append(f"| {category} | {count} |")
        lines.append("")

        # Detailed findings
        lines.append("## Detailed Findings")
        lines.append("")

        for category in sorted(by_category.keys()):
            findings = [f for f in self.findings if f.category == category]
            lines.append(f"### {category.replace('_', ' ').title()}")
            lines.append("")

            for finding in findings[:20]:  # Limit to first 20
                lines.append(f"**File:** `{finding.file_path}`")
                if finding.line_number:
                    lines.append(f"**Line:** {finding.line_number}")
                lines.append(f"**Confidence:** {finding.confidence}")
                lines.append(f"**Description:** {finding.description}")
                lines.append(f"**Action:** {finding.recommended_action}")
                if finding.code_snippet:
                    lines.append("```python")
                    lines.append(finding.code_snippet[:200])
                    lines.append("```")
                lines.append("")

            if len(findings) > 20:
                lines.append(f"*... and {len(findings) - 20} more*")
                lines.append("")

        return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Detect dead code in Kollab CLI codebase"
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file path",
        default=None,
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["text", "json", "markdown"],
        default="text",
        help="Report format (default: text)",
    )
    parser.add_argument(
        "--category",
        "-c",
        help="Filter by category",
        default=None,
    )
    parser.add_argument(
        "--confidence",
        help="Minimum confidence level (high, medium, low)",
        default=None,
    )

    args = parser.parse_args()

    # Get project root (parent of scripts directory)
    script_dir = Path(__file__).parent
    root_dir = script_dir.parent

    # Create detector
    detector = DeadCodeDetector(root_dir)

    # Run scan
    findings = detector.scan_all()

    # Filter findings
    if args.category:
        findings = [f for f in findings if f.category == args.category]

    if args.confidence:
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        min_level = confidence_order.get(args.confidence.lower(), 0)
        findings = [
            f for f in findings if confidence_order.get(f.confidence, 0) >= min_level
        ]

    detector.findings = findings

    # Generate report
    report = detector.generate_report(format=args.format)

    # Output
    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to: {args.output}")
    else:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
