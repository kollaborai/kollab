"""Thought Orchestrator - spawns parallel instances and synthesizes results.

This is the core engine. When triggered, it:
1. Selects N methodologies for the question
2. Spawns N pipe-mode kollabor instances in parallel
3. Each child gets a modified system prompt for its methodology
4. Collects stdout from all children
5. Synthesizer combines them into enriched context
6. Returns the synthesis for injection into the main conversation

The socket server is available for future real-time streaming but
v1 uses subprocess stdout capture for simplicity and reliability.
"""

import asyncio
import logging
import os
import re
import sys
from typing import Callable, List, Optional

from .methodologies import Methodology, select_methodologies
from .synthesizer import synthesize_thoughts

logger = logging.getLogger(__name__)

# Strip ANSI escape sequences from child output
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\[\?[0-9;]*[a-zA-Z]|\x1b\[s")


def _extract_response(text: str) -> str:
    """Extract the actual LLM response from pipe-mode output.

    Pipe mode outputs: response text + optional status bar remnants.
    Strip all ANSI, unicode box drawing, and control chars.
    """
    # Strip ANSI escape sequences
    clean = _ANSI_RE.sub("", text)
    # Strip unicode box drawing characters (status bar)
    clean = re.sub(r"[▄▀█░⠠⠵⌘⌁∷]+", "", clean)
    # Strip control characters except newline
    clean = re.sub(r"[\r\x00-\x09\x0b-\x1f]+", " ", clean)
    # Collapse multiple spaces/newlines
    clean = re.sub(r"\n\s*\n\s*\n+", "\n\n", clean)
    return clean.strip()


class ThoughtOrchestrator:
    """Spawns and manages parallel thinking instances."""

    def __init__(self, config=None):
        self.config = config
        self._child_processes: List[asyncio.subprocess.Process] = []

    async def ponder(
        self,
        question: str,
        conversation_context: str = "",
        count: int = 3,
        timeout: float = 45.0,
        methodology_tags: Optional[List[str]] = None,
        profile_name: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """Spawn parallel thinkers and return synthesized reasoning.

        Args:
            question: The user's question/message to ponder.
            conversation_context: Recent conversation for context.
            count: Number of parallel thinkers to spawn.
            timeout: Max seconds to wait for all thinkers.
            methodology_tags: Optional filter for methodology selection.
            profile_name: LLM profile to use (inherits parent's profile).
            on_status: Callback for status updates (methodology_name, status).

        Returns:
            Synthesized reasoning string, or None if pondering failed.
        """
        methodologies = select_methodologies(count=count, tags=methodology_tags)

        logger.info(
            f"Pondering with {len(methodologies)} methodologies: "
            f"{[m.name for m in methodologies]}"
        )

        if on_status is not None:
            names = ", ".join(m.name for m in methodologies)
            on_status(f"Spawning {len(methodologies)} thinkers: {names}")

        try:
            # Spawn all child instances in parallel
            spawn_tasks = []
            for methodology in methodologies:
                task = asyncio.create_task(
                    self._spawn_thinker(
                        methodology=methodology,
                        question=question,
                        conversation_context=conversation_context,
                        timeout=timeout,
                        profile_name=profile_name,
                    )
                )
                spawn_tasks.append((methodology, task))

            # Wait for all to complete (or timeout)
            results = {}
            for methodology, task in spawn_tasks:
                try:
                    output = await task
                    if output and output.strip():
                        clean = _extract_response(output)
                        if clean:
                            results[methodology.name] = {
                                "methodology": methodology.name,
                                "content": clean,
                                "summary": "",
                                "status": "done",
                            }
                            if on_status is not None:
                                on_status(
                                    f"{methodology.name}: done " f"({len(clean)} chars)"
                                )
                    else:
                        if on_status is not None:
                            on_status(f"{methodology.name}: no output")
                except Exception as e:
                    logger.error(f"Thinker {methodology.name} failed: {e}")
                    if on_status is not None:
                        on_status(f"{methodology.name}: failed")

            if not results:
                logger.warning("No thinkers produced output")
                return None

            if on_status is not None:
                on_status(
                    f"Synthesizing {len(results)}/{len(methodologies)} "
                    f"perspectives..."
                )

            # Synthesize all perspectives
            synthesis = synthesize_thoughts(
                question=question,
                results=results,
            )

            return synthesis

        except Exception as e:
            logger.error(f"Pondering failed: {e}")
            return None
        finally:
            await self._cleanup_children()

    async def _spawn_thinker(
        self,
        methodology: Methodology,
        question: str,
        conversation_context: str,
        timeout: float = 45.0,
        profile_name: Optional[str] = None,
    ) -> str:
        """Spawn a single child instance in pipe mode and return its output."""
        child_input = self._build_child_input(
            methodology=methodology,
            question=question,
            conversation_context=conversation_context,
        )

        kollab_cmd = self._find_kollab_command()

        # Environment for child
        env = os.environ.copy()
        env["KOLLAB_DEEP_THOUGHT_CHILD"] = "1"
        env["KOLLAB_DEEP_THOUGHT_METHODOLOGY"] = methodology.name

        # Build command args
        cmd_args = [
            *kollab_cmd,
            "--pipe",
            "--simple",
            "--timeout",
            f"{max(int(timeout) - 5, 15)}s",
        ]

        # Inherit parent's LLM profile
        if profile_name:
            cmd_args.extend(["--profile", profile_name])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._child_processes.append(process)

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=child_input.encode()),
                timeout=timeout,
            )

            output = stdout.decode().strip() if stdout else ""
            errors = stderr.decode().strip() if stderr else ""

            if errors:
                logger.debug(f"Child {methodology.name} stderr: {errors[:200]}")

            if output:
                logger.info(f"Child {methodology.name} produced {len(output)} chars")
            else:
                logger.warning(f"Child {methodology.name} produced no output")

            return output

        except asyncio.TimeoutError:
            logger.warning(f"Child {methodology.name} timed out")
            return ""
        except Exception as e:
            logger.error(f"Failed to spawn child {methodology.name}: {e}")
            return ""

    def _build_child_input(
        self,
        methodology: Methodology,
        question: str,
        conversation_context: str,
    ) -> str:
        """Build the input string for a child instance."""
        parts = []

        parts.append(f"[Deep Thought - {methodology.name}]")
        parts.append(methodology.system_prompt)
        parts.append("")

        parts.append("[Instructions]")
        parts.append(
            "Think through the following question using the methodology above. "
            "Focus entirely on reasoning and analysis. "
            "Do NOT use any tools. Do NOT execute commands. "
            "Do NOT ask questions. Just provide your analysis directly. "
            "Be thorough but concise - aim for 300-800 words."
        )
        parts.append("")

        if conversation_context:
            parts.append("[Recent Conversation Context]")
            parts.append(conversation_context)
            parts.append("")

        parts.append("[Question to Analyze]")
        parts.append(question)

        return "\n".join(parts)

    def _find_kollab_command(self) -> List[str]:
        """Find the command to launch kollabor."""
        project_root = self._find_project_root()
        if project_root:
            main_py = project_root / "main.py"
            if main_py.exists():
                return [sys.executable, str(main_py)]

        import shutil

        kollab_path = shutil.which("kollab")
        if kollab_path:
            return [kollab_path]

        return [sys.executable, "-m", "kollabor"]

    def _find_project_root(self):
        """Find the kollabor project root directory."""
        from pathlib import Path

        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "main.py").exists() and (parent / "kollabor").is_dir():
                return parent
        return None

    async def _cleanup_children(self):
        """Kill any remaining child processes."""
        for proc in self._child_processes:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
        self._child_processes.clear()
