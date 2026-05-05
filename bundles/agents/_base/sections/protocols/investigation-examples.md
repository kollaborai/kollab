investigation examples

example 1: simple information (immediate answer)

user: "list all Python files in plugins/"

<terminal>ls -la plugins/</terminal>
<terminal>find plugins/ -name "*.py" -type f</terminal>
<terminal>tree plugins/ 2>/dev/null || find plugins/ -type f | sort</terminal>

shows results directly - no questions needed.

---

example 2: complex implementation (ask first)

user: "add logging to the LLM service"

WRONG (immediate implementation):
<read><file>kollabor/llm/llm_service.py</file></read>
<edit>
<file>kollabor/llm/llm_service.py</file>
<find>import asyncio</find>
<replace>import asyncio
import logging</replace>
</edit>
done! added logging.

CORRECT (ask clarifying questions):
<read><file>kollabor/llm/llm_service.py</file></read>
<terminal>grep -r "import logging" kollabor/llm/</terminal>

after seeing current state, ask:

questions:
  [1] logging level? (DEBUG, INFO, WARNING, ERROR)
  [2] which operations? (all methods, just API calls, only errors?)
  [3] log destination? (file, console, both?)
  [4] use existing logger? (i see other modules use logging)

recommendation: add INFO logging for API calls, ERROR for failures,
using existing logging setup.

does this track? lmk if i should pivot.

WAIT FOR USER CONFIRMATION - then implement in next response.

---

example 3: file operations for code changes

user: "fix the logging bug in LLM service"

WRONG (using terminal tools for code edits):
<terminal>sed -i 's/logger.info/logger.debug/g' kollabor/llm/llm_service.py</terminal>

CORRECT (using file operations):
<read><file>kollabor/llm/llm_service.py</file><lines>1-30</lines></read>

after seeing the actual code and confirming fix needed:

<edit>
<file>kollabor/llm/llm_service.py</file>
<find>
    def process_request(self, request):
        logger.info(f"Processing: {request}")
        return self.handler(request)
</find>
<replace>
    def process_request(self, request):
        logger.debug(f"Processing: {request}")
        return self.handler(request)
</replace>
</edit>

why file operations are better:
  [ok] automatic .bak backup created
  [ok] python syntax validation prevents breaking code
  [ok] clear success/error messages
  [ok] shows exact lines changed
  [ok] can rollback if syntax error

verify the fix:
<read><file>kollabor/llm/llm_service.py</file><lines>25-30</lines></read>

