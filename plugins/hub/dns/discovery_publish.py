"""Explicit discovery publisher with a stable service key.

Run separately from workspace coordinator elections. Serve ONLY the output
directory; the state directory contains a private key and must stay private.
Identity-only by default; advertise a relay only while its configured local
health endpoint confirms the supported protocol.
"""

import argparse
import fcntl
import http.client
import ipaddress
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import rfc8785
from nacl.signing import SigningKey

from .discovery import MAX_INTEGER, SCHEMA, WELL_KNOWN, DiscoveryError, normalize_target, verify_manifest
from .storage import _locked_atomic_write


def relay_healthy(url: str, *, origin: str | None = None) -> bool:
    """Bounded local readiness probe; operator config, never a remote URL."""
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        if (
            parsed.scheme != "http"
            or not address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/relay/v1/health"
        ):
            raise ValueError("relay health must be a private literal HTTP address at /relay/v1/health")
        port = parsed.port or 80
    except ValueError as exc:
        raise DiscoveryError("invalid_health_url", str(exc)) from exc
    connection = http.client.HTTPConnection(str(address), port, timeout=2)
    try:
        connection.request("GET", parsed.path)
        response = connection.getresponse()
        if response.status != 200:
            return False
        body = response.read(4097)
        if len(body) > 4096:
            return False
        value = json.loads(body)
        return (
            isinstance(value, dict)
            and value.get("protocol") == "kollab-relay/1"
            and value.get("status") == "ok"
            and (origin is None or value.get("origin") == origin)
        )
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def publish(origin: str, state_dir: Path, output: Path, *, relay_control: str | None = None) -> dict:
    target = normalize_target(origin)
    if target.explicit:
        raise DiscoveryError("invalid_origin", "publisher requires an HTTPS origin, not a document path")
    if relay_control is not None and relay_control != target.origin + "/relay/v1":
        raise DiscoveryError("invalid_relay_control", "relay control must be the same origin at /relay/v1")
    state_dir, output = state_dir.resolve(), output.resolve()
    if state_dir.is_relative_to(output.parent):
        raise DiscoveryError("private_state_exposed", "keep publisher state outside the served output directory")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Different from publisher.json's per-file publisher.lock: flock is not
    # reentrant across separately opened descriptors, even in one process.
    fd = os.open(state_dir / ".publisher-transaction.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        key_path, state_path = state_dir / "service.key", state_dir / "publisher.json"
        if key_path.exists() and not state_path.exists():
            raise DiscoveryError(
                "state_missing", "existing publisher key has no revision state; refusing to reset history"
            )
        if not key_path.exists():
            if state_path.exists():
                raise DiscoveryError(
                    "key_missing", "publisher state exists but its key is missing; refusing key replacement"
                )
            key_fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(key_fd, "wb") as stream:
                stream.write(SigningKey.generate().encode())
                stream.flush()
                os.fsync(stream.fileno())
        if key_path.stat().st_mode & 0o077:
            raise DiscoveryError("key_permissions", "service.key must be readable only by its owner (mode 600)")
        # Corrupt keys stop publication; never silently generate a replacement.
        key = SigningKey(key_path.read_bytes())
        public_key = key.verify_key.encode().hex()
        revision = 0
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state["origin"] != target.origin or state["public_key"] != public_key:
                raise DiscoveryError("publisher_conflict", "state directory belongs to another origin or key")
            revision = state["revision"]
            if type(revision) is not int or not 0 <= revision < MAX_INTEGER:
                raise DiscoveryError("invalid_revision", "publisher revision cannot advance")
        now = int(time.time())
        payload = {
            "v": "aid1",
            "schema": SCHEMA,
            "authority": target.authority,
            "coordinator": {
                "designation": "discovery",
                "aid": f"agent:discovery@{target.authority}",
                "public_key": public_key,
                "key_type": "ed25519",
                "protocols": [SCHEMA],
            },
            "endpoints": {"registry": target.origin + WELL_KNOWN},
            "discovery": {"principal_id": "ed25519:" + public_key, "roles": [], "bootstrap": []},
            "revision": revision + 1,
            "published_at": now,
            "expires_at": now + 300,
        }
        if relay_control:
            payload["endpoints"]["control"] = relay_control
            payload["coordinator"]["protocols"].append("kollab-relay/1")
            payload["discovery"]["roles"] = ["rendezvous", "relay"]
        payload["signature"] = key.sign(rfc8785.dumps(payload)).signature.hex()
        verify_manifest(payload, target)
        # Persist the counter first. A crash may skip a revision, never reuse it.
        _locked_atomic_write(state_path, {"origin": target.origin, "public_key": public_key, "revision": revision + 1})
        _locked_atomic_write(output, payload)
        # Only the public descriptor needs to be readable by the web server.
        output.chmod(0o644)
        return payload
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--watch", action="store_true", help="renew every 60 seconds; run under a service manager")
    parser.add_argument("--relay-control", help="same-origin /relay/v1; requires a healthy local relay")
    parser.add_argument("--relay-health", help="private literal HTTP /relay/v1/health probe URL")
    args = parser.parse_args()
    if bool(args.relay_control) != bool(args.relay_health):
        parser.error("--relay-control and --relay-health must be provided together")
    try:
        while True:
            healthy = bool(args.relay_health) and relay_healthy(
                args.relay_health, origin=normalize_target(args.origin).origin
            )
            payload = publish(
                args.origin, args.state_dir, args.output, relay_control=args.relay_control if healthy else None
            )
            print(
                f"published revision {payload['revision']}: {args.output} "
                f"(roles={','.join(payload['discovery']['roles']) or 'identity-only'}; "
                f"expires {payload['expires_at']})",
                flush=True,
            )
            if not args.watch:
                break
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"publication failed: {exc}\n")


if __name__ == "__main__":
    main()
