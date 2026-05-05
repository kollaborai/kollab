# kollabor-rpc

`kollabor-rpc` is the lightweight RPC transport foundation for Kollabor daemon
and attach-mode state access.

It provides request/response models, error types, a method-dispatch server, a
client, and Unix-socket transport helpers. The current package is intentionally
small and stdlib-only.

## Current Role

- Represent RPC requests and responses with stable IDs.
- Dispatch named methods to async handlers.
- Provide client helpers for request/response calls.
- Wrap common RPC errors such as timeout, missing method, and handler failure.
- Provide Unix socket connection helpers with a larger buffer limit.

## Architecture

| Module | Responsibility |
|---|---|
| `models.py` | `RpcRequest`, `RpcResponse`, request ID helpers |
| `errors.py` | RPC exception hierarchy |
| `server.py` | handler registration and dispatch |
| `client.py` | request client abstraction |
| `transport.py` | Unix transport helpers and buffer settings |

## Usage

```python
from kollabor_rpc import RpcServer

server = RpcServer()


async def ping(params):
    return {"ok": True, "echo": params.get("echo")}


server.register("ping", ping)
reply = await server.handle_wire({
    "request_id": "req_1",
    "method": "ping",
    "params": {"echo": "hello"},
})
```

## Known Gaps

- This package is still a transport foundation; higher-level StateService method
  contracts live outside the package.
- Wire-format compatibility should be documented alongside the hub/attach socket
  protocol.
- Current tests focus on integration through hub sockets; package-local unit
  coverage should grow as the API stabilizes.

## Roadmap

### Phase 1: Contract docs

- Document the exact frame format used by attach clients and hub sockets.
- Add examples for both direct dispatch and socket-backed clients.
- Clarify which errors are transport failures versus handler failures.

### Phase 2: StateService integration

- Keep RPC generic, but document the StateService method namespace that uses it.
- Add contract tests for high-value state methods.
- Add compatibility checks for attached streaming and request/reply flows.

### Phase 3: Operational hardening

- Add timeout/cancellation guidance for callers.
- Improve observability for slow handlers and failed frames.
- Keep the package stdlib-only unless a transport dependency becomes essential.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-rpc/src/kollabor_rpc/*.py
python -m pytest tests/test_hub_rpc_integration.py -q
```

## Dependencies

None. This package is intentionally stdlib-only.

## License

MIT
