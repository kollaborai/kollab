#!/usr/bin/env python3
"""repoint_vhost.py HOST:PORT [DOMAIN] < old.conf > new.conf

Rewrite an nginx vhost that fronts the manual selfhost stack (relay workers, a health port and a static
key file server) so that every route reaches the one process `kollab relay serve` runs on HOST:PORT.

  upstream blocks          keep their options; their servers collapse to one `server HOST:PORT ...;`
  proxy_pass to a health   `location = /relay/v1/health` -> http://HOST:PORT
  proxy_pass to key file   `location = /.well-known/agent-keys[.json]` -> http://HOST:PORT/.well-known/agent-keys.json
  proxy_pass to an upstream name is left alone (the upstream now points at HOST:PORT)

Anything else that proxies to a literal address makes it stop with exit 3 and change nothing, and so does
a result that still names another address. With DOMAIN (edge_vhost.sh always passes it) the file must serve that
name and no other: every upstream and health route in it is rewritten, so a file that also served another site
(kollabor.ai) would have that site repointed too. The diff is the review: edge_vhost.sh shows it before applying.
"""

import re
import sys


def fail(message):
    sys.stderr.write(f"repoint_vhost: {message}\n")
    sys.exit(3)


args = sys.argv[1:]
target = args[0] if args else ""
domain = args[1] if len(args) == 2 else ""
if len(args) not in (1, 2) or not re.fullmatch(r"[0-9.]+:[0-9]{2,5}", target):
    fail("usage: repoint_vhost.py HOST:PORT [DOMAIN] < old.conf > new.conf")

text = sys.stdin.read()
if domain:
    names = {name for found in re.findall(r"^[ \t]*server_name[ \t]+([^;]*);", text, re.M) for name in found.split()}
    if names != {domain}:
        shown = " ".join(sorted(names)) or "none"
        fail(
            f"server_name in this file is {shown}, not only {domain}: every upstream and health route in it "
            "would be repointed, so another site's traffic would move too. Split the vhost or edit it by hand"
        )

ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}\b")
out, in_upstream, seen_server, location = [], False, False, ""
for line in text.splitlines(keepends=True):
    stripped = line.strip()
    if re.match(r"upstream\s+\S+\s*\{", stripped):
        in_upstream, seen_server = True, False
    elif in_upstream:
        if stripped == "}":
            in_upstream = False
        elif re.match(r"server\s+\S+", stripped):
            if not seen_server:
                seen_server = True
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}server {target} max_fails=2 fail_timeout=5s;\n")
            continue
    opened = re.match(r"location\s+(?:[=~^]+\s+)?(\S+)\s*\{", stripped)
    if opened:
        location = opened.group(1)
    direct = re.match(r"(\s*)proxy_pass\s+http://([0-9.]+:[0-9]+)(/\S*)?;", line)
    if direct:
        if "agent-keys" in location:
            new = f"http://{target}/.well-known/agent-keys.json"
        elif location.rstrip("/").endswith("/health"):
            new = f"http://{target}"
        else:
            fail(f"proxy_pass to a literal address in location {location!r}: {stripped}")
        out.append(f"{direct.group(1)}proxy_pass {new};\n")
        continue
    out.append(line)

result = "".join(out)
strays = sorted({a for a in ADDRESS.findall(result) if a != target})
if strays:
    fail(f"the result still names {', '.join(strays)}; refusing to write it")
if target not in result:
    fail("found nothing to repoint; refusing to write it")
sys.stdout.write(result)
