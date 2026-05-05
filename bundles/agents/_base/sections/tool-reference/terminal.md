terminal command system (subprocess backend):

basic foreground execution:
  <terminal>ls -la</terminal>                     # temp session, capture output
  <terminal>git status</terminal>                 # wait for completion
  <terminal>python -m pytest tests/</terminal>    # returns all output

background persistent sessions:
  <terminal background="true" name="dev">npm run dev</terminal>
  <terminal background="true" name="build" timeout="10m">npm run build</terminal>
  <terminal background="true" name="logs" cwd="/var/log">tail -f syslog</terminal>

  attributes:
    background="true"  # required for persistent session
    name="..."         # session name (auto-gen if omitted)
    timeout="5m"       # auto-kill after duration (30s, 5m, 1h)
    cwd="/path"        # working directory

  use for:
    - dev servers (npm run dev, python -m http.server)
    - long-running processes (build scripts, watch commands)
    - interactive tools (btop, htop - full TTY support)

session management:
  <terminal-status>*</terminal-status>              # list all sessions
  <terminal-status>dev</terminal-status>            # check specific session
  <terminal-output lines="50">dev</terminal-output> # capture recent output
  <terminal-kill>dev</terminal-kill>                # kill session
  <terminal-kill>*</terminal-kill>                  # kill all managed sessions

typical workflow:
  1. start background: <terminal background="true" name="dev">npm run dev</terminal>
  2. check status:     <terminal-status>dev</terminal-status>
  3. view output:      <terminal-output>dev</terminal-output>
  4. clean up:         <terminal-kill>dev</terminal-kill>

user can view live: /terminal view dev (interactive with keyboard)

BLOCKED commands:
  - kollab, python main.py with --detached or --agent flags
  - any command that spawns a new kollab/agent process
  use <hub_spawn name="type">task</hub_spawn> to spawn agents instead
