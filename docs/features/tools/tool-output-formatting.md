---
title: "Tool Output Formatting"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Tool Output Formatting

Tool results are fed back to the LLM as user messages.

The format depends on result type:

Successful execution:
```
[terminal] $ ls -la
drwxr-xr-x  5 user  staff  160 Feb 24 10:00 .
drwxr-xr-x 10 user  staff  320 Feb 24 09:00 ..
-rw-r--r--  1 user  staff   42 Feb 24 10:00 file.txt
```

File edit with diff:
```
[file_edit] Edited config.json
--- before
+++ after
@@ -1 +1 @@
-"timeout": 30
+"timeout": 60
```

MCP tool result:
```
[mcp:filesystem:read_file]
Contents of /path/to/file.txt...
```

Error:
```
[terminal] ERROR: Command failed with exit code 1
```

