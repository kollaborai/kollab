"""File change feed and lane claim system for hub agents.

Tracks file modifications across parallel agents and manages
exclusive lane claims to prevent conflicting edits.
"""

import fnmatch
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CLAIM_TIMEOUT = 1800  # 30 minutes
DEFAULT_FEED_MAX = 500
DEFAULT_FEED_MAX_AGE = 86400  # 24 hours


class ChangeFeed:
    """Thread-safe file change tracker and lane claim manager."""

    def __init__(
        self,
        hub_dir: Optional[str] = None,
        claim_timeout: int = DEFAULT_CLAIM_TIMEOUT,
        feed_max: int = DEFAULT_FEED_MAX,
        feed_max_age: int = DEFAULT_FEED_MAX_AGE,
    ) -> None:
        self._lock = threading.Lock()
        self._claim_timeout = claim_timeout
        self._feed_max = feed_max
        self._feed_max_age = feed_max_age

        if hub_dir is None:
            from .presence import get_hub_dir

            hub_dir = str(get_hub_dir())
        self._hub_dir = Path(hub_dir)
        self._hub_dir.mkdir(parents=True, exist_ok=True)

        self._claims_path = self._hub_dir / "lane_claims.json"
        self._feed_path = self._hub_dir / "change_feed.jsonl"

        self._claims: dict[str, dict] = {}
        self._feed: list[dict] = []
        self._subscribers: dict[str, list[re.Pattern]] = {}

        self._load_claims()
        self._load_feed()

    # ── persistence ──────────────────────────────────────────────

    def _load_claims(self) -> None:
        if self._claims_path.exists():
            try:
                data = json.loads(self._claims_path.read_text())
                self._claims = data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                self._claims = {}

    def _save_claims(self) -> None:
        try:
            self._claims_path.write_text(json.dumps(self._claims, indent=2))
        except OSError:
            logger.warning("failed to save lane claims")

    def _load_feed(self) -> None:
        if self._feed_path.exists():
            try:
                lines = self._feed_path.read_text().strip().splitlines()
                entries = []
                for line in lines[-self._feed_max :]:
                    entry = json.loads(line)
                    if isinstance(entry, dict):
                        entries.append(entry)
                self._feed = entries
            except (json.JSONDecodeError, OSError):
                self._feed = []

    def _append_feed(self, entry: dict) -> None:
        try:
            with open(self._feed_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            logger.warning("failed to append to change feed")

    def _trim_feed_file(self) -> None:
        """Trim feed by count and age, then purge garbage entries."""
        if not self._feed:
            return
        changed = False

        # Age-based trim: drop entries older than feed_max_age
        if self._feed_max_age > 0:
            cutoff = time.time() - self._feed_max_age
            before = len(self._feed)
            self._feed = [e for e in self._feed if e.get("timestamp", 0) >= cutoff]
            if len(self._feed) < before:
                logger.info(
                    f"change_feed: trimmed {before - len(self._feed)} "
                    f"entries older than {self._feed_max_age}s"
                )
                changed = True

        # Garbage purge: drop entries with clearly invalid paths
        before = len(self._feed)
        self._feed = [e for e in self._feed if self._is_valid_entry(e)]
        if len(self._feed) < before:
            logger.info(
                f"change_feed: purged {before - len(self._feed)} garbage entries"
            )
            changed = True

        # Count-based trim: cap at feed_max
        if len(self._feed) > self._feed_max:
            self._feed = self._feed[-self._feed_max :]
            changed = True

        if changed:
            try:
                lines = [json.dumps(e) for e in self._feed]
                self._feed_path.write_text("\n".join(lines) + "\n")
            except OSError:
                logger.warning("failed to write trimmed change feed")

    @staticmethod
    def _is_valid_entry(entry: dict) -> bool:
        """Check if a feed entry has a valid file path.

        Filters out test garbage like literal "path", regex patterns,
        and other non-path strings that were inserted during testing.
        """
        path = entry.get("path", "")
        if not path:
            return False
        # Reject regex patterns and test strings
        if path in ("path", "(.*?)", ".*", "**"):
            return False
        # Reject paths that are pure regex metacharacters
        if all(c in r"\.^$*+?{}[]|():" for c in path):
            return False
        # Valid paths contain at least one alphanumeric or / or . or _
        if not any(c.isalnum() or c in "/._-" for c in path):
            return False
        return True

    def startup_purge(self) -> dict:
        """Purge garbage and stale entries from the feed on startup.

        Call once after initialization to clean up accumulated test data
        and expired entries from previous sessions.
        """
        with self._lock:
            before = len(self._feed)
            self._trim_feed_file()
            purged = before - len(self._feed)
            return {"purged": purged, "remaining": len(self._feed)}

    # ── lane claims ──────────────────────────────────────────────

    def claim(self, identity: str, path: str, task: str = "") -> dict:
        """Claim a file for exclusive work. Returns claim dict."""
        with self._lock:
            now = time.time()
            self.cleanup_expired()

            if path in self._claims:
                existing = self._claims[path]
                if existing["identity"] != identity:
                    return {
                        "status": "conflict",
                        "path": path,
                        "claimed_by": existing["identity"],
                        "expires_at": existing["expires_at"],
                        "task": existing.get("task", ""),
                    }

            expires_at = now + self._claim_timeout
            self._claims[path] = {
                "identity": identity,
                "path": path,
                "task": task,
                "claimed_at": now,
                "expires_at": expires_at,
            }
            self._save_claims()
            return {
                "status": "claimed",
                "path": path,
                "identity": identity,
                "expires_at": expires_at,
            }

    def release(self, identity: str, path: str) -> dict:
        """Release a claimed file."""
        with self._lock:
            if path not in self._claims:
                return {"status": "not_claimed", "path": path}

            existing = self._claims[path]
            if existing["identity"] != identity:
                return {
                    "status": "not_owner",
                    "path": path,
                    "claimed_by": existing["identity"],
                }

            del self._claims[path]
            self._save_claims()
            return {"status": "released", "path": path, "identity": identity}

    def release_all(self, identity: str) -> dict:
        """Release all claims for an identity."""
        with self._lock:
            released = []
            paths = [p for p, c in self._claims.items() if c["identity"] == identity]
            for path in paths:
                del self._claims[path]
                released.append(path)
            if released:
                self._save_claims()
            return {"status": "released_all", "identity": identity, "paths": released}

    def get_claims(self, identity: Optional[str] = None) -> dict:
        """Get current claims, optionally filtered by identity."""
        with self._lock:
            self.cleanup_expired()
            if identity:
                claims = {
                    p: c for p, c in self._claims.items() if c["identity"] == identity
                }
            else:
                claims = dict(self._claims)
            return {"claims": claims, "count": len(claims)}

    def cleanup_expired(self) -> dict:
        """Remove expired claims. Returns count of removed."""
        now = time.time()
        expired = [p for p, c in self._claims.items() if c["expires_at"] <= now]
        for path in expired:
            del self._claims[path]
        if expired:
            self._save_claims()
        return {"expired": len(expired), "paths": expired}

    # ── change feed ──────────────────────────────────────────────

    def record_change(self, identity: str, path: str, action: str = "edit") -> dict:
        """Record a file change and notify subscribers."""
        with self._lock:
            now = time.time()
            entry = {
                "identity": identity,
                "path": path,
                "action": action,
                "timestamp": now,
            }
            self._feed.append(entry)
            self._append_feed(entry)
            self._trim_feed_file()

            notified = self._check_subscribers(entry)
            return {
                "status": "recorded",
                "entry": entry,
                "notified": notified,
            }

    def get_recent(self, limit: int = 50) -> dict:
        """Get recent feed entries."""
        with self._lock:
            entries = self._feed[-limit:]
            return {"entries": entries, "count": len(entries)}

    def get_changes_for_file(self, path: str, limit: int = 20) -> dict:
        """Get recent changes for a specific file."""
        with self._lock:
            entries = [e for e in self._feed if e["path"] == path][-limit:]
            return {"entries": entries, "count": len(entries)}

    # ── subscriptions ────────────────────────────────────────────

    def subscribe(self, identity: str, pattern: str) -> dict:
        """Subscribe to file changes matching a glob pattern.

        Pattern uses fnmatch-style: * matches anything, ? matches
        single char, [seq] matches chars in sequence.
        Converted to regex internally.
        """
        with self._lock:
            # convert glob to regex (fnmatch handles *, ?, and [seq])
            regex = re.compile(fnmatch.translate(pattern))
            if identity not in self._subscribers:
                self._subscribers[identity] = []
            self._subscribers[identity].append(regex)
            return {
                "status": "subscribed",
                "identity": identity,
                "pattern": pattern,
            }

    def unsubscribe(self, identity: str, pattern: Optional[str] = None) -> dict:
        """Remove subscriptions. If no pattern, remove all for identity."""
        with self._lock:
            if identity not in self._subscribers:
                return {"status": "no_subscriptions", "identity": identity}

            if pattern is None:
                count = len(self._subscribers.pop(identity))
                return {
                    "status": "unsubscribed_all",
                    "identity": identity,
                    "count": count,
                }

            regex = re.compile(fnmatch.translate(pattern))
            before = len(self._subscribers[identity])
            self._subscribers[identity] = [
                r for r in self._subscribers[identity] if r.pattern != regex.pattern
            ]
            removed = before - len(self._subscribers[identity])
            if not self._subscribers[identity]:
                del self._subscribers[identity]
            return {
                "status": "unsubscribed",
                "identity": identity,
                "pattern": pattern,
                "removed": removed,
            }

    def get_subscriptions(self, identity: str) -> list:
        """Get subscription patterns for an agent."""
        with self._lock:
            subs = self._subscribers.get(identity, [])
            return list(subs)

    def get_subscribers_for(self, path: str) -> list[str]:
        """Return identities subscribed to changes for a given path."""
        matched = []
        for identity, patterns in self._subscribers.items():
            for regex in patterns:
                if regex.match(path):
                    matched.append(identity)
                    break
        return matched

    def _check_subscribers(self, entry: dict) -> list[str]:
        """Check who should be notified about a change."""
        return self.get_subscribers_for(entry["path"])
