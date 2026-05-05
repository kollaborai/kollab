security considerations

never commit secrets:
  [x] API keys
  [x] passwords
  [x] tokens
  [x] private keys
  [x] database credentials

check before committing:
  <terminal>git diff</terminal>
  <terminal>grep -r "api_key\|password\|secret" .</terminal>
  <read><file>.gitignore</file></read>

if secrets in code:

move to environment variables or config files

<edit>
<file>config.py</file>
<find>API_KEY = "sk-abc123"</find>
<replace>API_KEY = os.getenv("API_KEY")</replace>
</edit>

<terminal>echo ".env" >> .gitignore</terminal>
<terminal>echo "config.local.json" >> .gitignore</terminal>

validating user input:

always validate and sanitize:
  [ok] check types: isinstance(value, expected_type)
  [ok] check ranges: 0 <= value <= max_value
  [ok] sanitize strings: escape special characters
  [ok] validate formats: regex matching for emails, urls

sql injection prevention:
  wrong: query = f"SELECT * FROM users WHERE name = '{user_input}'"
  correct: query = "SELECT * FROM users WHERE name = ?"
           cursor.execute(query, (user_input,))

command injection prevention:
  wrong: os.system(f"ls {user_input}")
  correct: subprocess.run(["ls", user_input], check=True)

agent spawning:
  NEVER spawn agents via terminal commands. this is blocked and
  will fail. do not run kollab, python main.py, or any variant
  to start new agent processes.

  the ONLY way to spawn agents:
    <hub_spawn name="agent-type">task description</hub_spawn>

  this ensures the agent joins the hub mesh, gets a designation,
  and is visible to all peers. terminal-spawned agents are rogue
  processes invisible to the hub.
