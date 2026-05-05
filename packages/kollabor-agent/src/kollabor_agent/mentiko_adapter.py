"""Mentiko integration adapter for spawning kollabor agents.

When MENTIKO_CLI=kollab is set, mentiko uses this adapter to spawn
kollabor agents that join the hub mesh automatically.

Usage from mentiko:
    import os
    os.environ["MENTIKO_CLI"] = "kollabor"
    from kollabor_agent.mentiko_adapter import spawn_agent
    result = await spawn_agent(
        agent_name="coder",
        task="fix the auth bug",
        identity="bugfixer",
    )

CLI usage:
    python -m kollabor_agent.mentiko_adapter spawn coder --task "fix auth"
    python -m kollabor_agent.mentiko_adapter list
"""

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _find_kollabor_binary() -> str:
    """Find the kollabor CLI entrypoint.

    Checks for 'kollab' on PATH first, then falls back to
    python main.py in the dev directory.
    """
    kollab = shutil.which("kollab")
    if kollab:
        return kollab

    dev_main = Path.home() / "dev" / "kollab" / "main.py"
    if dev_main.exists():
        return f"python {dev_main}"

    raise FileNotFoundError(
        "Cannot find kollabor CLI. Install with 'pip install -e .' "
        "or ensure ~/dev/kollab/main.py exists."
    )


def _find_agent_bundles() -> List[str]:
    """List available agent bundle names."""
    search_paths = [
        Path.home() / "dev" / "kollab" / "bundles" / "agents",
    ]

    try:
        import kollabor_agent

        base = Path(kollabor_agent.__file__).parent.parent.parent
        search_paths.append(base / "bundles" / "agents")
    except Exception:
        pass

    for bundles_dir in search_paths:
        if bundles_dir.exists():
            return sorted(
                d.name
                for d in bundles_dir.iterdir()
                if d.is_dir() and (d / "system_prompt.md").exists()
            )

    return []


async def spawn_agent(
    agent_name: str,
    task: str,
    identity: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Spawn a kollabor agent that joins the hub mesh.

    Args:
        agent_name: Bundle name (e.g. "coder", "research").
        task: Initial task description for the agent.
        identity: Hub identity override (auto-generated if None).
        workspace: Working directory for the agent.

    Returns:
        Dict with agent_id, identity, pid, and status.

    Raises:
        FileNotFoundError: If kollabor CLI not found.
        RuntimeError: If spawn fails.
    """
    binary = _find_kollabor_binary()
    agent_id = f"mentiko-{uuid.uuid4().hex[:8]}"
    if not identity:
        identity = f"{agent_name}-{uuid.uuid4().hex[:4]}"

    cwd = workspace or os.getcwd()
    parent_pid = str(os.getpid())

    cmd_parts = binary.split()
    cmd_parts.extend(
        [
            "--agent",
            agent_name,
            "--as",
            identity,
            "--detached",
        ]
    )

    env = os.environ.copy()
    env["KOLLAB_PARENT_PID"] = parent_pid
    env["KOLLAB_AGENT_ID"] = agent_id

    if task:
        env["MENTIKO_INITIAL_TASK"] = task

    logger.info(
        f"Spawning agent: name={agent_name}, " f"identity={identity}, cwd={cwd}"
    )

    try:
        proc = subprocess.Popen(
            cmd_parts,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        logger.info(f"Agent spawned: identity={identity}, pid={proc.pid}")

        return {
            "agent_id": agent_id,
            "identity": identity,
            "pid": proc.pid,
            "status": "spawned",
            "agent_name": agent_name,
            "task": task,
            "workspace": cwd,
        }

    except Exception as e:
        raise RuntimeError(f"Failed to spawn agent '{agent_name}': {e}") from e


def _cli_main() -> None:
    """CLI entrypoint for mentiko adapter."""
    parser = argparse.ArgumentParser(
        prog="kollabor-mentiko",
        description="Mentiko adapter for spawning kollabor agents",
    )
    sub = parser.add_subparsers(dest="command")

    spawn_parser = sub.add_parser("spawn", help="Spawn a kollabor agent")
    spawn_parser.add_argument("agent_name", help="Agent bundle name")
    spawn_parser.add_argument("--task", required=True, help="Initial task")
    spawn_parser.add_argument("--name", help="Identity override")
    spawn_parser.add_argument("--cwd", help="Working directory")

    sub.add_parser("list", help="List available agent bundles")

    args = parser.parse_args()

    if args.command == "spawn":
        result = asyncio.run(
            spawn_agent(
                agent_name=args.agent_name,
                task=args.task,
                identity=args.name,
                workspace=args.cwd,
            )
        )
        print(f"agent_id:    {result['agent_id']}")
        print(f"identity:    {result['identity']}")
        print(f"pid:         {result['pid']}")
        print(f"status:      {result['status']}")

    elif args.command == "list":
        bundles = _find_agent_bundles()
        if bundles:
            print(f"Available agent bundles ({len(bundles)}):")
            for name in bundles:
                print(f"  - {name}")
        else:
            print("No agent bundles found.")

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
