---
title: "Kollaborate"
created: 2026-02-24
modified: 2026-02-24
status: active
---
kollaborate: multi-agent parallel development

kollaborate is a revolutionary ai development system that deploys multiple
specialized agents in parallel via subprocess to achieve what would take months
traditionally in hours.

core principle
  26 agents working in parallel = 4000x speedup over traditional development
  from spec to production code in 90 minutes, not 6-12 months

how it works

spawning agents
  when the llm includes <agent> tags in its response, the agent orchestrator
  plugin (plugins/agent_orchestrator/) spawns new subprocess instances running
  kollab with the specified task.

  example:
    <agent>
    <name>PhaseBTypeDefinitions</name>
    <agent-type>coder</agent-type>
    <task>
    objective: add typescript type definitions

    context:
    - working with large codebase
    - need centralized type definitions

    todo:
    [ ] identify all type definitions
    [ ] extract to shared types module
    [ ] update imports

    success: types compile and tests pass
    </task>
    </agent>

  the plugin (orchestrator.py):
    - creates subprocess: kollab-PhaseBTypeDefinitions
    - starts kollab with --agent coder flag
    - sends task message to the agent
    - monitors for completion (idle detection via activity_monitor.py)
    - injects results back into parent session (via message_injector.py)

xml command reference

  <agent>
    spawn new agent with task
    attributes: name (optional)
    children: agent-type, skill, files, task

  <clone>
    spawn agent with conversation context
    children: name, conversation_file, files, task

  <team>
    spawn team lead that can spawn workers
    attributes: max_workers, lead_name
    children: task

  <status />
    list all active agents (no children)

  <capture>agent-name 50</capture>
    view last n lines from agent

  <message to="agent-name">content</message>
    send message to running agent

  <stop>agent-name</stop>
    stop agent and return output

  <context_inject>content</context_inject>
    inject content directly into agent context

  <broadcast>message</broadcast>
    send message to all running agents

note: the tglm/tlist/tcapture/tmsg/tstop shell commands are not implemented.
  use the xml commands above instead. the shell commands were a planned feature
  that never shipped.

agent naming conventions

  use descriptive names with phase prefixes:
    - PhaseA_CreateDirectoryStructure
    - PhaseB_ExtractModels
    - PhaseB_ApiImplementations
    - PhaseC_KeyboardHandling

  pattern: [Phase][Type][Purpose]

  examples of good names:
    - PhaseB-TypeDefinitions (extract types)
    - PhaseB-ApiSignatures (add api signatures)
    - konsensus-data-layer (kollabor konsensus pattern)
    - lint-file-01 (parallel lint fixing)

  bad names (avoid):
    - agent1 (no context)
    - helper (too vague)
    - fixer (unclear purpose)

multi-phase coordination pattern

  for complex refactoring, use sequential phases with parallel agents within:

  phase a - foundation (parallel)
    agents: PhaseA-CreateDirectoryStructure, PhaseA-ExtractModels,
            PhaseA-CreateBaseClasses
    focus: base classes, models, directory structure
    wait: all agents complete before proceeding

  phase b - core components (parallel)
    agents: PhaseB-Auth, PhaseB-Database, PhaseB-Validation
    focus: independent components with tdd
    method: each agent writes tests first, then implementation

  phase c - dependent components (parallel)
    agents: PhaseC-Controllers, PhaseC-Services, PhaseC-Views
    focus: components using phase b outputs
    watch: import issues are common here

  phase d - integration (sequential)
    agents: PhaseD-Integrator (single agent)
    focus: create facade that delegates to all components
    verify: maintains backward compatibility

  phase e - validation (parallel)
    agents: PhaseE-Tests, PhaseE-Imports, PhaseE-Docs
    focus: run full test suite, update imports, verify app still runs

  commit strategy: after each phase completes
    - enables easy rollback if something breaks
    - makes code review manageable
    - provides clear progress checkpoints

massive refactoring pattern

  for files 1,500+ lines that need modular decomposition:

  1. analyze the file
    read the file and provide analysis:
    - total lines and method count
    - identified responsibilities (srp violations)
    - code duplication patterns (dry violations)
    - suggested component breakdown
    - estimated phase structure

    ask user to confirm analysis before proceeding.

  2. create refactoring spec
    using docs/templates/refactoring_spec_template.md:
    - docs/refactoring_[component]_spec.md
    include:
    - detailed component breakdown with line ranges
    - phase structure (a-e)
    - exact agent commands for each phase
    - test strategy and dry principles
    - success criteria

  3. backup and launch phase a
    cp file.py file.py.backup

    launch phase a agents:
      - PhaseA-CreateDirectoryStructure
      - PhaseA-ExtractModels
      - PhaseA-CreateBaseClasses

    monitor: tlist | grep "PhaseA"
    commit when complete

  4. launch phase b
    launch phase b agents (core components with tdd)
    each agent creates tests first, then extracts code
    monitor: tcapture [agent_name] 20
    commit when complete

  5. launch phase c
    launch phase c agents (dependent components)
    monitor import issues carefully
    commit when complete

  6. launch phase d
    launch phase d agent (facade creation)
    creates new facade that delegates to all components
    maintains backward compatibility
    adds integration tests
    commit when complete

  7. launch phase e
    launch phase e agents (validation)
    run full test suite
    update imports across codebase
    verify application still runs
    final commit

  8. summary
    provide final summary:
    - original file: x lines -> facade: y lines (z% reduction)
    - components created: n
    - tests written: m
    - time taken: ~t hours
    - all tests passing: yes/no

real-world example: refactoring 2,757-line monolith

  phase a - extract foundations (parallel):
    <agent><models-extractor>
    <agent-type>coder</agent-type>
    <files>
      <file>monolith.py</file>
    </files>
    <task>
    objective: extract all data model classes from monolith.py

    context:
    - monolith.py is 2757 lines with mixed concerns
    - data models need to be separated

    todo:
    [ ] identify all data model classes
    [ ] extract to models/base.py
    [ ] update imports in monolith.py

    success: models in separate file, imports resolve
    </task>
    </models-extractor></agent>

    <agent><validators-extractor>
    <agent-type>coder</agent-type>
    <files>
      <file>monolith.py</file>
    </files>
    <task>
    objective: extract validation logic into validators.py
    [ ... similar structure ... ]
    </task>
    </validators-extractor></agent>

    <agent><database-layer>
    <agent-type>coder</agent-type>
    <files>
      <file>monolith.py</file>
    </files>
    <task>
    objective: extract database queries into database.py
    [ ... similar structure ... ]
    </task>
    </database-layer></agent>

  phase b - extract business logic (parallel):
    <agent><controllers-extractor>
    <agent-type>coder</agent-type>
    <files>
      <file>monolith.py</file>
    </files>
    <task>
    objective: extract controller logic from monolith.py
    [ ... similar structure ... ]
    </task>
    </controllers-extractor></agent>

  phase c - integration (sequential):
    <agent><facade-creator>
    <agent-type>coder</agent-type>
    <files>
      <file>monolith.py</file>
    </files>
    <task>
    objective: create facade.py that imports all extracted modules
    [ ... similar structure ... ]
    </task>
    </facade-creator></agent>

  result: 7 agents deployed in ~2 hours vs weeks of sequential work
          85% file size reduction while maintaining 100% functionality

kollabor konsensus pattern

  the kollabor konsensus pattern coordinates multiple specialist agents
  through a central orchestrator that decomposes complex tasks into
  parallelizable subtasks.

  key concept: one orchestrator + n specialists + integration phase

  step 1 - orchestrator analysis (you do this):
    read feature_spec.md
    explore codebase with terminal commands
    analyze requirements and identify parallel workstreams:
    - data layer (models, migrations)
    - business logic (services, controllers)
    - ui components (widgets, views)
    - testing (unit, integration)
    - documentation (api docs, user guides)

  step 2 - deploy specialist team (parallel):
    <agent><konsensus-data-layer>
    <agent-type>coder</agent-type>
    <skill>database</skill>
    <task>
    objective: implement complete data layer for feature
    [ ... detailed task with todo list ... ]
    </task>
    </konsensus-data-layer></agent>

    <agent><konsensus-business-logic>
    <agent-type>coder</agent-type>
    <task>
    objective: implement business logic services
    [ ... detailed task ... ]
    </task>
    </konsensus-business-logic></agent>

    <agent><konsensus-ui-components>
    <agent-type>coder</agent-type>
    <skill>ui-design</skill>
    <task>
    objective: build user interface components
    [ ... detailed task ... ]
    </task>
    </konsensus-ui-components></agent>

    <agent><konsensus-integration-tests>
    <agent-type>coder</agent-type>
    <skill>testing</skill>
    <task>
    objective: create comprehensive integration tests
    [ ... detailed task ... ]
    </task>
    </konsensus-integration-tests></agent>

    <agent><konsensus-documentation>
    <agent-type>coder</agent-type>
    <skill>documentation</skill>
    <task>
    objective: write complete documentation
    [ ... detailed task ... ]
    </task>
    </konsensus-documentation></agent>

  step 3 - consensus integration (sequential):
    <agent><konsensus-integrator>
    <agent-type>coder</agent-type>
    <task>
    objective: integrate all specialist outputs into working feature
    [ ... detailed integration task ... ]
    </task>
    </konsensus-integrator></agent>

  result: 6 specialists + 1 integrator = complete feature in parallel
    each specialist owns their domain completely
    integrator ensures consensus across all components

parallel execution patterns

  pattern 1: phased development
    sequential phases, parallel agents within each phase
    use for: large refactoring, architectural changes

  pattern 2: feature breakdown
    independent features in parallel
    use for: multi-feature implementation

  pattern 3: testing in parallel
    each agent tests different module
    use for: comprehensive test coverage

  pattern 4: lint fixing
    each agent fixes one file
    use for: fixing 20+ lint errors simultaneously

  pattern 5: kollabor konsensus
    orchestrated team intelligence
    use for: features requiring multiple layers (data + logic + ui)

agent creation best practices

  1. investigate first, then spawn
     use read/terminal tools to understand current state
     identify concrete tasks that can run in parallel

  2. write comprehensive task descriptions
     objective: clear statement of what to accomplish
     context: relevant constraints and background
     todo: step-by-step checklist
     success: how to verify completion

  3. use descriptive agent names
     prefix with phase: phasea, phaseb, featurex, etc.
     include purpose: extractor, validator, builder
     example: "phaseb-type-extractor" not "agent1"

  4. include verification in task
     each agent should validate its work
     run tests after changes
     check for regressions

  5. attach relevant files
     use <files><file>path/to/file.py</file></files>
     agents get these files in their context

  6. use agent-type and skill tags appropriately
     <agent-type>coder</agent-type> for development
     <agent-type>research</agent-type> for investigation
     <skill>debugging</skill> for bug fixes
     <skill>testing</skill> for test writing

when to use kollaborate

  do use:
    - complex multi-module implementations
    - architectural refactoring across many files
    - files 1,500+ lines (especially for refactoring)
    - feature development with clear dependencies
    - performance optimization tasks
    - api integration across frontend/backend
    - breaking down monolithic classes/files following solid principles
    - parallel testing/debugging (fix 20+ lint errors simultaneously)
    - independent tasks that can run concurrently

  don't use:
    - simple single-file changes
    - exploratory research tasks (use task tool with explore agent instead)
    - planning without implementation
    - tasks with serial dependencies (must complete in order)
    - spawning more than 10 agents at once (system limits)

agent consciousness transfer

  agents maintain full conversation context when spawned. they can read
  previous work, understand patterns, and continue where others left off.

  this enables:
    - phase b agents can see what phase a agents created
    - integrators can review all specialist outputs
    - no loss of context between phases

monitoring tips

  check agents every 5-10 minutes:
    tcapture [agent] 20

  help with import issues:
    tmsg [agent] "use 'from ...events import x' (3 dots)"

  if agent stuck:
    review output with tcapture, provide guidance

  stop if needed:
    tstop [agent]

  check all agents:
    tlist

commit strategy

  after each phase:
    git add [relevant files]
    git commit -m "refactor: [phase description]

    [detailed changes]
    [components/tests added]
    [line counts]"

  never include claude code attribution footers in commit messages:
    - no "generated with claude code..."
    - no "co-authored-by: claude <noreply@anthropic.com>"

under the hood

  implementation: plugins/agent_orchestrator/

  core components:
    - orchestrator.py: manages subprocess instances for agent execution
    - xml_parser.py: parses <agent> tags from llm responses
    - activity_monitor.py: detects agent completion via idle detection
    - message_injector.py: injects agent results back into parent session
    - file_attacher.py: attaches files to agent tasks

  session isolation:
    - each project uses separate hub namespace
    - process names: {project}-{agent-name}
    - environment variables:
      - kollabor_root_socket: for nested agent visibility
      - kollabor_agent_name: agent identifier

  ready detection:
    - polls subprocess output for stable output
    - waits for consecutive polls with identical content
    - falls back to fixed delay if detection fails

  completion detection:
    - monitors agent for idle periods
    - captures final output when session ends
    - injects results back to parent

xml command reference

  <agent>
    spawn new agent with task
    attributes: name (optional)
    children: agent-type, skill, files, task

  <clone>
    spawn agent with conversation context
    children: name, conversation_file, files, task

  <team>
    spawn team lead that can spawn workers
    attributes: max_workers, lead_name
    children: task

  <status />
    list all active agents (no children)

  <capture>agent-name 50</capture>
    view last n lines from agent

  <message to="agent-name">content</message>
    send message to running agent

  <stop>agent-name</stop>
    stop agent and return output

  <context_inject>content</context_inject>
    inject content directly into agent context

  <broadcast>message</broadcast>
    send message to all running agents

examples from the wild

  webgpu rope editor: 26 agents, 18,831+ lines, 90 minutes
  monolith refactor: 15 agents, 2,757 lines → 10+ modules, ~2 hours
  parallel lint fixing: 20+ files fixed simultaneously

further reading
  .claude/commands/refactor-massive.md - complete refactoring methodology
  bundles/agents/coder/sections/24-parallel-development.md - agent strategy
  bundles/agents/default/sections/24-parallel-development.md - general pattern
