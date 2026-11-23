"""kollabor-rpc: RPC transport for StateService abstraction."""

from .client import RpcClient
from .context import RpcCallerContext, current_rpc_caller_context
from .errors import (
    WEB_QUEUE_CAPACITY_REJECTED,
    RpcError,
    RpcHandlerError,
    RpcMethodNotFound,
    RpcTimeoutError,
)
from .models import RpcRequest, RpcResponse, new_request_id
from .server import RpcServer
from .transport import (
    LARGE_BUFFER_LIMIT,
    open_unix_connection_with_large_buffer,
)

__all__ = [
    "RpcRequest",
    "RpcResponse",
    "new_request_id",
    "RpcError",
    "RpcTimeoutError",
    "RpcMethodNotFound",
    "RpcHandlerError",
    "WEB_QUEUE_CAPACITY_REJECTED",
    "RpcServer",
    "RpcCallerContext",
    "current_rpc_caller_context",
    "RpcClient",
    "LARGE_BUFFER_LIMIT",
    "open_unix_connection_with_large_buffer",
]
