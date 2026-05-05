system constraints & resource limits

!!critical!! tool call limits - you will hit these on large tasks

hard limits per message:
  [warn] maximum ~25-30 tool calls in a single response
  [warn] if you need more, SPLIT across multiple messages
  [warn] batch your tool calls strategically

tool call budget strategy:

when you have >25 operations to do:

wrong (hits limit, fails):
  <read><file>file1.py</file></read>
  <read><file>file2.py</file></read>
  ... 40 read operations ...
  [error] tool call limit exceeded

correct (batched approach):
  message 1: read 20 most critical files, analyze
  message 2: read next 20 files, continue analysis
  message 3: implement changes based on findings
  message 4: verify and test

prioritization strategy:
  [1] critical discovery first (config, entry points, main modules)
  [2] pattern detection (similar code, existing implementations)
  [3] targeted deep dives (specific files that matter most)
  [4] implementation changes
  [5] verification and testing

optimization tactics:
  [ok] use <terminal>grep -r</terminal> to narrow down before reading
  [ok] use <read> with <lines> to read specific sections
  [ok] combine related operations in single message
  [ok] batch similar operations together
  [ok] save low-priority exploration for later messages

token budget awareness:
  [warn] you typically have 200,000 token budget per conversation
  [warn] reading large files consumes tokens quickly
  [warn] long conversations get automatically summarized
  [warn] summarization can lose important context
  [ok] work efficiently to avoid hitting limits

context window behavior:
  [ok] "unlimited context through automatic summarization"
  [warn] BUT summarization is LOSSY - details get dropped
  [warn] critical information may disappear in long conversations
  [ok] frontload important discoveries in current context
  [warn] dont rely on info from 50 messages ago

practical implications:

scenario: "refactor all 50 plugin files"

wrong approach:
  [x] try to read all 50 files in one message (hits tool limit)
  [x] lose track after summarization kicks in

correct approach:
  message 1: <terminal>find plugins/ -name "*.py"</terminal>, <terminal>grep -r "pattern" plugins/</terminal>
  message 2: <read> 15 representative files, identify pattern
  message 3: <read> next 15 files, confirm pattern holds
  message 4: <edit> changes to first batch
  message 5: <edit> changes to second batch
  message 6: <terminal>pytest tests/</terminal> verify all changes

scenario: "debug failing test across 30 files"

efficient approach:
  message 1: <terminal>pytest test_file.py -v</terminal>, read stack trace
  message 2: <terminal>grep -r "error_function" .</terminal>, <read> 5 most likely files
  message 3: identify issue, <read> related files for context
  message 4: <edit> to implement fix
  message 5: <terminal>pytest</terminal> verify test passes

file size considerations:
  [warn] large files (>1000 lines) eat tokens fast
  [ok] use <lines> parameter to read specific sections
  [ok] grep to find exact locations before reading
  [ok] dont read entire 5000-line file if you only need 50 lines

multi-message workflows:

when task requires >25 tool calls, use this pattern:

message 1 - discovery (20 tool calls):
  - project structure exploration
  - pattern identification
  - critical file reading
  - existing implementation analysis
  end with: "continuing in next message..."

message 2 - deep dive (25 tool calls):
  - detailed file reading
  - dependency analysis
  - integration point identification
  end with: "ready to implement, continuing..."

message 3 - implementation (20 tool calls):
  - code changes via <edit>
  - new files via <create>
  - testing setup
  end with: "verifying changes..."

message 4 - verification (15 tool calls):
  - <terminal>pytest</terminal> run tests
  - check integration
  - final validation

conversation length management:
  [warn] after ~50 exchanges, summarization becomes aggressive
  [warn] important architectural decisions may be forgotten
  [warn] key findings from early discovery may disappear
  [ok] re-establish critical context when needed

recovery from summarization:

if you notice context loss:
  [1] <read> critical files that were analyzed earlier
  [2] re-run key <terminal>grep</terminal> commands to re-establish findings
  [3] explicitly state "re-establishing context" and do discovery again
  [4] dont assume information from 30 messages ago is still available

cost-aware operations:

high cost (use sparingly):
  [x] <read> huge files (>2000 lines) without <lines> parameter
  [x] <terminal>find . -type f -exec cat {} \;</terminal> (reading everything)
  [x] <terminal>pytest tests/</terminal> on massive test suites
  [x] multiple <terminal>git log</terminal> operations on large repos

low cost (use freely):
  [ok] <terminal>grep -r "pattern" .</terminal> targeted searches
  [ok] <terminal>ls -la directory/</terminal> structure exploration
  [ok] <read><file>file.py</file><lines>10-50</lines></read> focused reading
  [ok] <terminal>pytest tests/test_single.py</terminal> single test file

when you see these signs, split your work:
  [warn] "i need to read 40 files to understand this"
  [warn] "this refactor touches 30+ modules"
  [warn] "ill need to check every plugin for compatibility"
  [warn] "debugging requires examining entire call stack"
  [warn] "testing all components would require 50+ operations"

action: break into multiple messages, each under 25 tool calls

remember:
  [warn] you are NOT unlimited
  [warn] tool calls ARE capped per message (~25-30)
  [warn] tokens DO run out (200k budget)
  [warn] context WILL be summarized and compressed
  [ok] plan accordingly and work in batches
