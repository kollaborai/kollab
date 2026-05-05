---
name: explore-agent-system
description: "Explore agent loading, discovery, and skill management system"
---

skill name: explore-agent-system

purpose:
  understand how kollabor agents are discovered, loaded, and switched.
  helps troubleshoot agent initialization, skill loading, and agent
  directory resolution issues.

when to use:
  [ ] need to list all available agents
  [ ] want to see active agent and its skills
  [ ] debugging agent switching problems
  [ ] tracing why an agent isn't loading
  [ ] verifying skill activation status
  [ ] understanding agent directory priority (local vs global)
  [ ] creating or modifying agents

methodology:

phase 1: agent discovery
  understand agent search paths
  list all discovered agents
  verify agent directory structure

phase 2: agent inspection
  examine active agent configuration
  check loaded skills
  verify agent.json settings

phase 3: skill troubleshooting
  trace skill loading process
  debug skill activation issues
  verify default skills

tools and commands:

core files to read:
  <read><file>kollabor/llm/agent_manager.py</file></read>
  <read><file>kollabor/utils/config_utils.py</file></read>

agent definitions:
  <read><file>agents/default/system_prompt.md</file></read>
  <read><file>agents/coder/system_prompt.md</file></read>
  <read><file>agents/kollabor/system_prompt.md</file></read>

grep patterns for agent discovery:
  <terminal>grep -r "class Agent" kollabor/llm/</terminal>
  <terminal>grep -r "AgentManager" kollabor/llm/</terminal>
  <terminal>grep -r "def.*agent" kollabor/utils/config_utils.py</terminal>

list all agent directories:
  <terminal>find agents -name "system_prompt.md" -type f</terminal>
  <terminal>ls -la agents/</terminal>

check local vs global agents:
  <terminal>ls -la .kollab/agents/</terminal>
  <terminal>ls -la ~/.kollab/agents/</terminal>

view agent configs:
  <terminal>find .kollab/agents -name "agent.json"</terminal>
  <terminal>find ~/.kollab/agents -name "agent.json"</terminal>

agent system architecture:

agentmanager (kollabor/llm/agent_manager.py)
  central coordinator for agent discovery and loading

key classes:
  - skill: represents a skill loaded from .md file
  - agent: represents an agent with system_prompt and skills
  - agentmanager: manages discovery, loading, switching

agent discovery paths:
  1. global: ~/.kollab/agents/ (user defaults)
  2. local: .kollab/agents/ (project-specific, overrides global)

local agents take priority over global agents with the same name.

agent directory structure:
  agents/<agent-name>/
    system_prompt.md    (required - base system prompt)
    agent.json          (optional - config with description, profile, default_skills)
    *.md                (optional - skill files)


skill class (kollabor/llm/agent_manager.py:29-99)
  attributes:
    - name: skill identifier (filename without .md)
    - content: full content of the skill file
    - file_path: path to the skill file
    - description: extracted from html comment at file start

  methods:
    - from_file(cls, file_path): load skill from .md file
    - to_dict(): convert to dictionary for serialization


agent class (kollabor/llm/agent_manager.py:101-260)
  attributes:
    - name: agent identifier (directory name)
    - directory: path to agent directory
    - system_prompt: base system prompt content
    - skills: dict of available skills (name -> skill)
    - active_skills: list of currently loaded skill names
    - profile: optional preferred llm profile
    - description: human-readable description
    - default_skills: skills to auto-load when activated

  methods:
    - from_directory(cls, agent_dir): load agent from directory
    - get_full_system_prompt(): get prompt with active skills appended
    - load_skill(skill_name): load skill into active context
    - unload_skill(skill_name): unload skill from active context
    - list_skills(): get all available skills
    - get_skill(name): get specific skill by name
    - to_dict(): convert to dictionary for serialization


agentmanager class (kollabor/llm/agent_manager.py:262-876)
  attributes:
    - _agents: dict of discovered agents (name -> agent)
    - _active_agent_name: currently selected agent name
    - global_agents_dir: ~/.kollab/agents/
    - local_agents_dir: .kollab/agents/

  key methods:
    - _discover_agents(): scan directories and load agents
    - get_agent(name): get agent by name
    - get_active_agent(): get current active agent
    - set_active_agent(name, load_defaults=true): switch agents
    - list_agents(): get all discovered agents
    - get_agent_names(): get list of agent names
    - load_skill(skill_name, agent_name=none): load skill for agent
    - unload_skill(skill_name, agent_name=none): unload skill
    - toggle_default_skill(skill_name, agent_name): toggle auto-load
    - refresh(): re-discover agents (preserves active skills)
    - create_agent(...): create new agent with directory
    - delete_agent(name): delete agent directory
    - update_agent(...): rename/update agent


agent initialization flow (kollabor/utils/config_utils.py)

initialize_system_prompt() runs on first startup:

step 1: check if local .kollab/agents/default/system_prompt.md exists
  if yes: use local (already set up)
  if no: continue to step 2

step 2: migrate from old system_prompt/ directory if exists
  if yes: copy to new agent structure
  if no: continue to step 3

step 3: seed bundled agents into global
  scans bundled agents/ at package root or cwd
  copies only missing files into ~/.kollab/agents/
  preserves existing user-customized global agent files

step 4: local agents are created only when user explicitly creates them
  startup does not auto-copy the default agent from global to local


system prompt resolution order (kollabor/utils/config_utils.py:158-221)

highest to lowest priority:
  1. cli --system-prompt argument
  2. kollabor_system_prompt environment variable (direct string)
  3. kollabor_system_prompt_file environment variable (file path)
  4. local .kollab/agents/default/system_prompt.md
  5. global ~/.kollab/agents/default/system_prompt.md
  6. fallback minimal default


phase 1: discover agents

step 1: list all available agents

from python:
  <read><file>kollabor/llm/agent_manager.py</file><lines>378-394</lines></read>

from command line:
  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
for name in am.get_agent_names():
    agent = am.get_agent(name)
    print(f'{name}: {agent.description or \"(no description)\"}')
    print(f'  directory: {agent.directory}')
    print(f'  skills: {len(agent.skills)}')
    print(f'  profile: {agent.profile or \"(none)\"}')
    print()
"</terminal>

expected output:
  default: default agent with standard system prompt
    directory: /path/to/.kollab/agents/default
    skills: 5
    profile: (none)

  coder: coding-focused agent
    directory: /home/user/.kollab/agents/coder
    skills: 3
    profile: claude-opus-4


step 2: check agent directory priority

verify local vs global:
  <terminal>echo "=== local agents ===" && ls -la .kollab/agents/ 2>/dev/null</terminal>
  <terminal>echo "=== global agents ===" && ls -la ~/.kollab/agents/</terminal>

local agents override global agents with the same name.
this allows project-specific customization.


step 3: examine agent structure

for a specific agent:
  <terminal>ls -la agents/default/</terminal>
  <terminal>cat agents/default/agent.json</terminal>

expected structure:
  system_prompt.md    (required)
  agent.json          (optional - may not exist)
  skill1.md
  skill2.md
  ...


phase 2: inspect active agent

step 1: get current active agent

  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
active = am.get_active_agent()
if active:
    print(f'active agent: {active.name}')
    print(f'description: {active.description or \"(none)\"}')
    print(f'profile: {active.profile or \"(none)\"}')
    print(f'directory: {active.directory}')
    print(f'total skills: {len(active.skills)}')
    print(f'active skills: {active.active_skills}')
    print(f'default skills: {active.default_skills}')
else:
    print('no active agent')
"</terminal>


step 2: list agent skills

  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
agent = am.get_active_agent()
if agent:
    for skill in agent.list_skills():
        active = '*' if skill.name in agent.active_skills else ' '
        default = 'd' if skill.name in agent.default_skills else ' '
        desc = skill.description[:40] + '...' if skill.description and len(skill.description) > 40 else skill.description or ''
        print(f'[{active}][{default}] {skill.name}: {desc}')
"</terminal>

legend:
  [*] = currently active/loaded
  [d] = default skill (auto-loads on agent activation)
  [ ] = available but not active


step 3: view full system prompt

  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
agent = am.get_active_agent()
if agent:
    prompt = agent.get_full_system_prompt()
    print(prompt)
"</terminal>

this shows base system_prompt + all active skills appended
with "## skill: {name}" headers.


phase 3: skill troubleshooting

issue 1: skill not loading

checklist:
  [ ] does skill file exist?
      <terminal>ls -la .kollab/agents/default/*.md</terminal>

  [ ] is skill file valid .md?
      <terminal>file .kollab/agents/default/myskill.md</terminal>

  [ ] was skill loaded correctly?
      check logs: <terminal>grep -i "skill" .kollab/logs/kollab.log | tail -20</terminal>

  [ ] is skill in active_skills list?
      see step 2 above

  [ ] was agent refreshed after adding skill?
      agentmanager.refresh() must be called to discover new files


issue 2: agent not discovered

checklist:
  [ ] does agent directory exist?
      <terminal>ls -la agents/</terminal>
      <terminal>ls -la ~/.kollab/agents/</terminal>

  [ ] does system_prompt.md exist?
      <terminal>ls agents/myagent/system_prompt.md</terminal>

  [ ] is agent directory valid (not __pycache__, etc)?
      <read><file>kollabor/llm/agent_manager.py</file><lines>294-305</lines></read>

  [ ] was application restarted after adding agent?
      agents are discovered at startup


issue 3: wrong agent active

checklist:
  [ ] what is _active_agent_name?
      <terminal>python -c "from kollabor.llm.agent_manager import agentmanager; print(agentmanager().active_agent_name)"</terminal>

  [ ] was set_active_agent called?
      check for: <terminal>grep -r "set_active_agent" kollabor/</terminal>

  [ ] is agent name correct?
      verify exact name: <terminal>ls agents/</terminal>


issue 4: default skills not loading

checklist:
  [ ] is skill in agent.json default_skills array?
      <terminal>cat agents/default/agent.json</terminal>

  [ ] was load_defaults=true when activating?
      set_active_agent(name, load_defaults=true)

  [ ] does skill actually exist?
      skill name must match filename without .md


issue 5: agent.json changes not taking effect

checklist:
  [ ] was agent.refresh() called?
      changes require refresh or restart

  [ ] is json valid?
      <terminal>python -c "import json; print(json.load(open('agents/default/agent.json')))"</terminal>

  [ ] was skill toggled correctly?
      toggle_default_skill() saves to agent.json


example workflow:

scenario: "create a new custom agent"

step 1: use agentmanager to create agent

  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager

am = agentmanager()

# create new agent
agent = am.create_agent(
    name='my-custom',
    description='specialized agent for my project',
    profile='claude-opus-4',
    system_prompt='''# my custom agent

you are specialized for this project.
focus on clean, documented code.
''',
    default_skills=['code-review', 'refactoring']
)

if agent:
    print(f'created agent: {agent.name}')
    print(f'directory: {agent.directory}')
else:
    print('agent creation failed - may already exist')
"</terminal>

step 2: verify agent was created

  <terminal>ls -la .kollab/agents/my-custom/</terminal>
  <terminal>cat .kollab/agents/my-custom/agent.json</terminal>

step 3: add skill files

create skills in the agent directory:
  <create><file>.kollab/agents/my-custom/my-skill.md</file><content><!-- my custom skill -->
skill name: my-skill
purpose: does something specific
...
</content></create>

step 4: switch to new agent

  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
success = am.set_active_agent('my-custom')
print(f'activated: {success}')
print(f'active agent: {am.active_agent_name}')
"</terminal>


example workflow 2:

scenario: "debug why a skill isn't loading"

step 1: verify skill file exists
  <terminal>ls -la .kollab/agents/default/*.md</terminal>

step 2: check skill is discovered
  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
agent = am.get_agent('default')
print('available skills:', [s.name for s in agent.list_skills()])
"</terminal>

step 3: try loading skill
  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
success = am.load_skill('my-skill', 'default')
print(f'loaded: {success}')
"</terminal>

step 4: check logs for errors
  <terminal>grep -i "skill\|agent" .kollab/logs/kollab.log | tail -30</terminal>


example workflow 3:

scenario: "copy agent from global to local for customization"

step 1: identify global agent
  <terminal>ls -la ~/.kollab/agents/</terminal>

step 2: copy to local
  <terminal>cp -r ~/.kollab/agents/coder .kollab/agents/</terminal>

step 3: customize local version
  <read><file>.kollab/agents/coder/system_prompt.md</file></read>
  <edit><file>.kollab/agents/coder/system_prompt.md</file><find>old content</find><replace>customized content</replace></edit>

step 4: refresh and verify
  <terminal>python -c "
from kollabor.llm.agent_manager import agentmanager
am = agentmanager()
am.refresh()
agent = am.get_agent('coder')
print(f'agent source: {agent.directory}')
if '.kollab' in str(agent.directory):
    print('using local agent (customized)')
else:
    print('using global agent (default)')
"</terminal>


advanced: programmatic agent inspection

create agent inspection script (inspect_agents.py):

  #!/usr/bin/env python3
  """inspect all agents and their skills."""
  import sys
  from pathlib import path
  sys.path.insert(0, str(path(__file__).parent))

  from kollabor.llm.agent_manager import agentmanager

  def main():
      am = agentmanager()

      print(f"=== agent discovery ===")
      print(f"local dir: {am.local_agents_dir}")
      print(f"global dir: {am.global_agents_dir}")
      print(f"total agents: {len(am.list_agents())}")
      print(f"active agent: {am.active_agent_name or '(none)'}")

      for agent in am.list_agents():
          is_local = "local" if agent.directory.is_relative_to(am.local_agents_dir) else "global"
          is_active = "*" if agent.name == am.active_agent_name else " "

          print(f"\n[{is_active}] {agent.name} ({is_local})")
          print(f"  dir: {agent.directory}")
          print(f"  desc: {agent.description or '(none)'}")
          print(f"  profile: {agent.profile or '(none)'}")

          if agent.skills:
              print(f"  skills ({len(agent.skills)}):")
              for skill in agent.list_skills():
                  active = "*" if skill.name in agent.active_skills else " "
                  default = "d" if skill.name in agent.default_skills else " "
                  print(f"    [{active}][{default}] {skill.name}")
          else:
              print(f"  skills: (none)")

          if agent.active_skills:
              print(f"  active: {', '.join(agent.active_skills)}")
          if agent.default_skills:
              print(f"  defaults: {', '.join(agent.default_skills)}")

  if __name__ == "__main__":
      main()

run:
  <terminal>python inspect_agents.py</terminal>


troubleshooting tips:

tip 1: agent not appearing
  - verify system_prompt.md exists in agent directory
  - check directory name is valid (no special chars)
  - ensure agent is under agents/ or .kollab/agents/
  - restart application after adding new agent

tip 2: skills not loading
  - skill files must end in .md
  - skill files cannot be named system_prompt.md
  - description must be in html comment at file start
  - call refresh() after adding new skill files

tip 3: local agent not overriding global
  - local must be in .kollab/agents/
  - agent names must match exactly
  - local directory must exist and be valid

tip 4: agent.json changes ignored
  - json must be valid (check syntax)
  - default_skills must reference existing skill files
  - call refresh() or restart after changes

tip 5: switching agents doesn't work
  - agent name must match exactly (case-sensitive)
  - agent must exist (check get_agent_names())
  - check logs for errors during activation
  - verify agent.json profile exists if specified


expected output:

when this skill executes successfully, you should be able to:

  [ ] list all discovered agents
  [ ] show active agent and its configuration
  [ ] display all skills for an agent with active/default status
  [ ] trace agent loading from local vs global directories
  [ ] create new agents programmatically
  [ ] debug why agents or skills aren't loading
  [ ] understand skill activation and default loading


status tags reference:

  [ok]   agent/skill is working correctly
  [warn] agent/skill has issues but still loads
  [error] agent/skill is failing or not loading
  [todo]  action needed to fix agent/skill

common exit conditions:

  [ok]   issue identified and resolved
  [ok]   workaround implemented
  [warn] issue understood but not fixed
  [error] root cause unclear, needs more investigation
