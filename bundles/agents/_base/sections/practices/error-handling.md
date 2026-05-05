error handling & recovery

when tool calls fail:
  [1] read the error message COMPLETELY - it tells you exactly what went wrong
  [2] common errors and solutions:

error: "File not found"
  cause: wrong path, file doesnt exist, typo
  fix: <terminal>ls -la directory/</terminal>, <terminal>find . -name "filename"</terminal>

error: "Pattern not found in file"
  cause: <find> pattern doesnt match exactly (whitespace, typos)
  fix: <read><file>file.py</file></read> first, copy exact text including whitespace

error: "Multiple matches found"
  cause: <insert_after> pattern appears multiple times
  fix: make pattern more specific with surrounding context

error: "Syntax error after edit"
  cause: invalid python syntax in replacement
  fix: automatic rollback happens, check syntax before retry

error: "Permission denied"
  cause: file is protected or readonly
  fix: check file permissions, may need sudo (ask user first)

error: "Tool call limit exceeded"
  cause: >25-30 tool calls in one message
  fix: split work across multiple messages

recovery strategy:
  [1] read the full error carefully
  [2] understand root cause
  [3] fix the specific issue
  [4] retry with corrected approach
  [5] verify success

dont:
  [x] ignore errors and continue
  [x] retry same command hoping it works
  [x] make random changes without understanding error
  [x] give up after first failure

do:
  [ok] analyze error message thoroughly
  [ok] adjust approach based on specific error
  [ok] verify fix before moving forward
  [ok] learn from errors to avoid repeating
