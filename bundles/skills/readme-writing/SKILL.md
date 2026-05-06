---
name: readme-writing
description: "README Writing skill - create comprehensive project README files with clear product narrative, feature tours, and demo assets"
---

readme writing mode: COMPELLING PROJECT NARRATIVE

when this skill is active, you follow professional technical writing standards.
this is a comprehensive guide to creating effective README documentation.


PHASE 0: PROJECT CONTEXT DISCOVERY

before writing ANY README, gather essential context about the project.


check project type and purpose

  <terminal>ls -la</terminal>
  <terminal>cat pyproject.toml 2>/dev/null || cat package.json 2>/dev/null || cat Cargo.toml 2>/dev/null || cat go.mod 2>/dev/null</terminal>
  <terminal>head -50 README.md 2>/dev/null || echo "no existing README"</terminal>

identify:
  - programming language and framework
  - project type (library, application, tool, template)
  - primary purpose and use case
  - target audience (developers, end users, data scientists)


check for existing documentation

  <terminal>find . -name "*.md" -type f | grep -v node_modules | grep -v ".git" | head -20</terminal>
  <terminal>ls -la docs/ 2>/dev/null || echo "no docs directory"</terminal>

review existing docs to understand:
  - current documentation style
  - terminology already in use
  - architecture decisions already documented
  - install patterns already established


check project maturity

  <terminal>git log --oneline | head -20</terminal>
  <terminal>git tag --list | tail -10</terminal>
  <terminal>ls -la .github/workflows/ 2>/dev/null || echo "no CI configured"</terminal>

assess:
  - is this a new project or mature software?
  - are there stable releases?
  - is CI/CD configured?
  - is there a contributing guide?


check installation dependencies

  <terminal>cat requirements.txt 2>/dev/null || cat pyproject.toml 2>/dev/null | grep -A20 "dependencies"</terminal>
  <terminal>cat package.json 2>/dev/null | grep -A10 "dependencies"</terminal>

understand:
  - required dependencies
  - optional dependencies
  - system requirements
  - platform support (windows, macos, linux)


check for existing badges or metadata

  <terminal>grep -i "badge\|shield\|coveralls\|codecov\|travis\|github" README.md 2>/dev/null || echo "no badges found"</terminal>
  <terminal>cat pyproject.toml 2>/dev/null | grep -A10 "\[project\|\[tool\."</terminal>

note existing badge patterns to maintain consistency.


PHASE 1: UNDERSTANDING YOUR AUDIENCE

a great README speaks to its specific audience.


developer-focused projects

libraries, frameworks, SDKs, APIs

audience cares about:
  - quick integration examples
  - API documentation
  - installation methods
  - dependency requirements
  - type safety and interfaces
  - testing approach
  - contribution guidelines

tone: technical, precise, code-first


end-user-focused projects

CLIs, desktop apps, web applications, tools

audience cares about:
  - what problem it solves
  - how to get started quickly
  - common use cases
  - system requirements
  - configuration options
  - troubleshooting

tone: accessible, friendly, feature-first


mixed audience projects

tools with both developer and end users

strategy:
  - lead with value proposition
  - provide quick start for all users
  - separate sections by audience
  - link to detailed docs for developers

example structure:
  - hero section (what + why)
  - quick start (everyone)
  - user guide (end users)
  - developer guide (developers)
  - api reference (developers)


showcase readme mode

use this when a README needs to sell what a project does, not merely document
that it exists. this is especially important for CLIs, TUIs, developer tools,
apps, and agent runtimes.

first viewport:
  - project name, badges, and one crisp product promise
  - short navigation links to the most useful docs
  - a demo gif or screenshot when the product has an interface or workflow
  - a first paragraph that names the user, the workflow, and the main payoff

demo asset rules:
  - prefer `docs/assets/<project>-demo.gif` for README demo gifs
  - run the actual program first whenever the repo can do so without unsafe
    secrets, destructive actions, or long-running external dependencies
  - use a descriptive alt text that names the workflows shown
  - show concrete product behavior: launch, common commands, core UI, approvals,
    agents, plugins, data views, or other signature workflows
  - if the demo is generated or simulated because a live capture would require
    secrets or long setup, keep it faithful to real behavior and do not describe
    it as a live runtime capture
  - avoid abstract decoration; the asset should help a new reader understand
    what using the product feels like

recommended showcase structure:
  - title, badges, tagline, links, demo asset
  - what the project does
  - feature tour table with concrete user value
  - why it exists or how the core loop works
  - status and installation
  - quick start and common workflows
  - extension/development/docs/support sections

write feature sections with verbs and examples:
  - prefer "Run `git diff | tool \"review this\" -p`" over "Automation support"
  - prefer "Gate file writes through approvals" over "Security features"
  - prefer "Coordinate agents with `/hub msg` and `/hub status`" over
    "Multi-agent support"


PHASE 2: THE ESSENTIAL SECTIONS


section order and priority

essential (every README needs these):

  [1] title and tagline
      clear, descriptive, searchable

  [2] what it is
      one-sentence purpose statement

  [3] why use it
      value proposition, differentiation

  [4] quick start
      fastest path to first success

  [5] installation
      how to get it running

  [6] usage
      common examples and patterns

  [7] status
      is it production-ready?

  [8] license
      legal requirements


important (most projects should have):

  [9] features
      capabilities at a glance

  [10] documentation links
      detailed docs location

  [11] contributing
      how to participate

  [12] support
      how to get help


optional (include if relevant):

  [13] requirements/supported platforms
      platform-specific notes

  [14] configuration
      setup options

  [15] troubleshooting
      common issues

  [16] roadmap
      future direction

  [17] acknowledgments
      credits and thanks

  [18] performance
      benchmarks, metrics


section anti-patterns

avoid these common mistakes:

  [x] starting with history or philosophy
      users care about what it does, not its origin story

  [x] burying the lead
      the first paragraph must explain the project

  [x] missing installation instructions
      the most common question users have

  [x] outdated examples
      code that doesn't work creates frustration

  [x] wall of text
      break it up with headers, lists, code blocks

  [x] jargon without explanation
      not everyone knows your technical terms


PHASE 3: TITLE AND TAGLINE


the title

rules for great titles:

  [1] match the package name exactly
      consistency helps users find and remember

  [2] use the actual project name
      dont add "project" or other filler words

  [3] capitalize properly
      follow project conventions (Title Case, camelCase, etc.)

examples:

  [ok] # Kollab
  [ok] # pandas
  [ok] # tailwindcss

  [warn] # Kollab Project
  [warn] # The pandas Data Analysis Library
  [warn] # A CLI Tool


the tagline

one line that answers "what is this and why should I care?"

formula: [what] + [value] + [differentiator]

examples:

  [ok] A terminal-based chat interface for LLMs with plugin architecture
  [ok] Fast, reliable, and secure distributed task queue
  [ok] Python data analysis library built for speed and ease of use

  [warn] A chat interface for talking to AI
  [warn] A task queue that is fast and reliable

tagline checklist:

  [ ] under 80 characters
  [ ] mentions what it is
  [ ] hints at key benefit
  [ ] distinguishes from alternatives
  [ ] uses active voice
  [ ] avoids buzzwords unless essential


PHASE 4: PROJECT STATUS BADGES


why badges matter

badges provide instant information:
  - build status (is the project working?)
  - version (what's the current release?)
  - license (can I use this?)
  - coverage (how well is it tested?)
  - popularity (is anyone using this?)
  - freshness (is it maintained?)


essential badges

every project should have these minimum badges:

  [1] license badge
      required for legal clarity

  [2] version badge
      shows latest release

  [3] build/ci badge
      shows current build status

  [4] python version / platform badge
      compatibility information


badge sources

shields.io - most versatile:
  https://shields.io

common badge patterns:

  license:
    https://img.shields.io/github/license/[user]/[repo]
    example: https://img.shields.io/github/license/kollaborai/kollab

  version (pypi):
    https://img.shields.io/pypi/v/[package]
    example: https://img.shields.io/pypi/v/kollab

  version (github release):
    https://img.shields.io/github/v/release/[user]/[repo]
    example: https://img.shields.io/github/v/release/kollaborai/kollab

  build status (github actions):
    https://img.shields.io/github/actions/workflow/status/[user]/[repo]/[workflow].yml
    example: https://img.shields.io/github/actions/workflow/status/kollaborai/kollab/tests.yml

  coverage:
    https://img.shields.io/codecov/c/github/[user]/[repo]
    example: https://img.shields.io/codecov/c/github/kollaborai/kollab

  python version:
    https://img.shields.io/pypi/pyversions/[package]
    example: https://img.shields.io/pypi/pyversions/kollab

  downloads:
    https://img.shields.io/pypi/dm/[package]
    example: https://img.shields.io/pypi/dm/kollab

  code style:
    https://img.shields.io/badge/code%20style-black-000000
    example: https://img.shields.io/badge/code%20style-black-000000


badge formatting

in markdown:

  [![badge text](badge url)](link target)

example:

  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![version](https://img.shields.io/pypi/v/kollab)](https://pypi.org/project/kollab/)
  [![Tests](https://img.shields.io/github/actions/workflow/status/kollaborai/kollab/tests.yml)](https://github.com/kollaborai/kollab/actions)


badge organization

group badges at the top, under the title:

  # Project Name

  [badge1] [badge2] [badge3] [badge4]

  Tagline text here...

don't overdo it:
  - 3-6 badges is optimal
  - more than 8 creates badge clutter
  - prioritize: license, version, build, then others


PHASE 5: THE QUICK START SECTION


purpose of quick start

get users to "hello world" as fast as possible.
this is the most important section.

target: under 30 seconds from copy to running code


quick start structure

  [1] prerequisites
      what they need before starting

  [2] install command
      single command if possible

  [3] minimal example
      smallest working code

  [4] expected output
      what they should see

  [5] next step link
      where to go from here


quick start examples

cli application:

  Quick Start

  Install via pip:
    pip install kollab

  Start chatting:
    kollab

  For pipe mode:
    echo "Explain async/await" | kollab -p

  Read the docs: https://docs.kollabor.ai


python library:

  Quick Start

  Install:
    pip install dataprocessor

  Process data:
    from dataprocessor import clean

    data = clean(raw_data)
    print(data.head())

  Full documentation: https://dataprocessor.dev/docs


web application:

  Quick Start

  Clone and run:
    git clone https://github.com/user/project.git
    cd project
    npm install
    npm start

  Open http://localhost:3000


quick start anti-patterns

avoid:

  [x] multiple installation methods
      pick one primary method, link to alternatives

  [x] extensive configuration
      quick start should use sensible defaults

  [x] optional steps
      save options for the installation section

  [x] missing prerequisites
      nothing worse than failing on step 1

  [x] outdated commands
      test your quick start in a fresh environment


PHASE 6: INSTALLATION SECTION


comprehensive installation

cover all installation methods thoroughly:

  [1] primary method (most common)
  [2] alternative methods
  [3] development installation
  [4] platform-specific notes
  [5] troubleshooting


installation template

  Installation

  Stable Release

    pip install kollab

  Development Version

    pip install git+https://github.com/kollaborai/kollab.git

  From Source

    git clone https://github.com/kollaborai/kollab.git
    cd kollab
    pip install -e .

  Requirements

    Python 3.12 or higher


development installation

for contributors:

  Development Setup

    1. Clone the repository:
       git clone https://github.com/kollaborai/kollab.git
       cd kollab

    2. Create a virtual environment:
       python -m venv venv
       source venv/bin/activate  # On Windows: venv\Scripts\activate

    3. Install with development dependencies:
       pip install -e ".[dev]"

    4. Run tests:
       python -m pytest

  See CONTRIBUTING.md for details.


platform-specific notes

  Platform Support

    Linux: Fully supported
    macOS: Fully supported
    Windows: Supported with WSL or native Python

  Windows users may need additional setup:
    - Install Visual C++ Build Tools for compilation
    - Use WSL2 for best experience


dependency troubleshooting

  Troubleshooting Installation

    Permission denied?
      Use a virtual environment or --user flag:
      pip install --user kollab

    SSL certificate errors?
      pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org kollab

    Build failures?
      Install build tools first:
      # Ubuntu/Debian
      sudo apt-get install python3-dev build-essential

      # macOS
      xcode-select --install


PHASE 7: USAGE SECTION


usage patterns

show how the project is used in real scenarios:

  [1] basic usage
      simplest possible example

  [2] common use cases
      typical scenarios users encounter

  [3] advanced usage
      power user features

  [4] configuration
      customization options


basic usage example

  Usage

  Command Line

    Basic chat:
      kollab

    Single query:
      kollab "Explain quantum computing"

    Pipe mode:
      cat document.txt | kollab -p

  Python API

    from kollabor import ChatClient

    client = ChatClient()
    response = client.chat("Hello, AI!")
    print(response.message)


use case catalog

  Common Use Cases

    Code Review
      kollab "Review this code: <paste code>"

    Documentation Generation
      kollab "Write API docs for: <paste code>"

    Learning
      kollab "Explain how async/await works in Python"

    Debugging
      kollab "I'm getting error X. What does it mean?"


advanced usage

  Advanced Usage

    Custom System Prompt
      export KOLLAB_SYSTEM_PROMPT="You are a Rust expert..."

    Multiple Providers
      kollab --provider openai --model gpt-4

    Session Management
      kollab --session my-project --persist


configuration section

  Configuration

    Config file location: ~/.kollab/config.json

    Default config:
      {
        "kollabor.llm.provider": "openai",
        "kollabor.llm.model": "gpt-4",
        "terminal.color_mode": "auto"
      }

    Environment variables:
      KOLLAB_API_KEY      - API key for LLM provider
      KOLLAB_SYSTEM_PROMPT - Custom system prompt
      KOLLAB_CONFIG_DIR    - Alternative config directory


PHASE 8: FEATURES SECTION


writing effective features

each feature should:
  [1] start with a verb
  [2] describe a benefit, not just a capability
  [3] be specific and concrete
  [4] be scannable (short lines)

features template

  Features

    Plugin Architecture
      - Extensible hook system for custom behavior
      - Dynamic plugin discovery and loading
      - SDK for plugin development

    Terminal UI
      - Real-time streaming responses
      - Multi-line input with visual editor
      - Syntax highlighting for code blocks
      - Status indicators for system state

    LLM Integration
      - Support for multiple providers (OpenAI, Anthropic, etc.)
      - Automatic rate limiting and retries
      - Tool/function calling support
      - Conversation history management

    Developer Experience
      - Pipe mode for scripting
      - Configurable system prompts
      - Markdown export
      - Comprehensive logging


feature anti-patterns

avoid:

  [x] technical jargon without explanation
      "Plugin architecture" vs "Extensible via plugins"

  [x] vague claims
      "Fast performance" vs "Processes 10K requests/second"

  [x] internal implementation details
      "Uses async/await" is not a user-facing feature

  [x] marketing fluff
      "Revolutionary", "Cutting-edge", "World-class"


PHASE 9: DOCUMENTATION LINKS


link strategy

your README is the entry point, not the complete docs.

link out to:

  [1] full documentation
  [2] api reference
  [3] tutorials/guides
  [4] examples
  [5] migration guides
  [6] changelog


documentation section

  Documentation

    Full Docs
      https://kollabor.dev/docs

    API Reference
      https://kollabor.dev/docs/api

    Tutorials
      - Getting Started: https://kollabor.dev/docs/getting-started
      - Plugin Development: https://kollabor.dev/docs/plugins
      - Configuration: https://kollabor.dev/docs/config

    Examples
      https://kollabor.dev/examples

    Changelog
      https://kollabor.dev/docs/changelog


when docs don't exist

if you don't have separate docs:

  Documentation

    See the docs/ directory for:
      - ARCHITECTURE.md - System design
      - CONTRIBUTING.md - Development guide
      - plugins/README.md - Plugin examples


PHASE 10: CONTRIBUTING SECTION


contributing goals

encourage contributions while setting expectations:

  [1] how to contribute
  [2] what contributions are welcome
  [3] development setup
  [4] code standards
  [5] submission process


contributing template

  Contributing

    We welcome contributions!

    What to Contribute

      - Bug fixes
      - Feature implementations
      - Documentation improvements
      - Plugin examples
      - Bug reports and feature requests

    Getting Started

      1. Fork the repository
      2. Create a branch: git checkout -b feature/my-feature
      3. Install development dependencies: pip install -e ".[dev]"
      4. Make your changes
      5. Run tests: python -m pytest
      6. Submit a pull request

    Code Style

      - Follow PEP 8
      - Run black: python -m black .
      - Add tests for new features
      - Update documentation

    Pull Request Guidelines

      - Link to related issues
      - Describe your changes
      - Ensure all tests pass
      - Update docs as needed

    Get Help

      - Open an issue for questions
      - Join our Discord: https://discord.gg/...
      - Email: maintainers@example.com


alternatives

for simpler projects, keep it brief:

  Contributing

    Contributions welcome! Please read CONTRIBUTING.md for details.

    Quick start:
      git clone https://github.com/user/repo.git
      cd repo
      pip install -e ".[dev]"
      python -m pytest


PHASE 11: SUPPORT AND COMMUNITY


support channels

give users a path to get help:

  Support

    Getting Help

      - Documentation: https://project.dev/docs
      - Issues: https://github.com/user/repo/issues
      - Discussions: https://github.com/user/repo/discussions

    Reporting Bugs

      Before reporting, search existing issues.
      When reporting, include:
        - Python/version
        - Minimal reproduction
        - Error messages
        - Expected vs actual behavior

    Feature Requests

      We welcome feature requests!
      Please explain:
        - The use case
        - Why existing solutions don't work
        - Proposed solution (optional)


community guidelines

if you have a community:

  Community

    Code of Conduct

      Be respectful. See CODE_OF_CONDUCT.md for details.

    Communication Channels

      - Discord: https://discord.gg/...
      - Twitter: @project
      - Blog: https://blog.project.dev


PHASE 12: PROJECT STATUS AND ROADMAP


status section

be honest about project state:

  Project Status

    Stability: Beta

    This project is under active development.
    APIs may change between versions.

    What's Ready
      - Core chat functionality
      - Plugin system
      - Multi-provider support

    What's Experimental
      - MCP integration
      - Custom UI themes
      - Voice input

    Production Use
      Not recommended for production use yet.
      Track issues for progress.


roadmap section

show where the project is going:

  Roadmap

    v1.0 (Q1 2025)
      - Stable API
      - Comprehensive documentation
      - Performance benchmarks

    v1.1 (Q2 2025)
      - Web UI
      - Team collaboration features
      - Conversation sharing

    v2.0 (Q3 2025)
      - Multi-language support
      - Local LLM support
      - Mobile apps

    See milestones for progress.


PHASE 13: ACKNOWLEDGMENTS AND LICENSE


acknowledgments

give credit where due:

  Acknowledgments

    This project builds upon excellent work:

      - Rich library for terminal UI
      - Prompt Toolkit for input handling
      - OpenAI for API inspiration

    Special thanks to contributors:
      - @user1 - Plugin system design
      - @user2 - Documentation improvements
      - @user3 - Bug fixes

    Sponsored by: Organization Name


license section

keep it simple and clear:

  License

    MIT License

    Copyright (c) 2024 Project Authors

    Permission is hereby granted...

    Or simpler:

    MIT - see LICENSE file for details.

    [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)


PHASE 14: README TEMPLATES BY PROJECT TYPE


python library template

  # [package-name]

  [badges]

  [tagline]

  Features

    - [feature 1]
    - [feature 2]
    - [feature 3]

  Installation

    pip install [package-name]

  Quick Start

    from [package] import [main_class]

    [example code]

  Documentation

    [docs link]

  Contributing

    [contributing info]

  License

    [license]


cli application template

  # [app-name]

  [badges]

  [tagline]

  ## Features

    - [feature 1]
    - [feature 2]
    - [feature 3]

  ## Installation

    pip install [app-name]

  ## Quick Start

    [app-name] [command]

  ## Usage

    Basic:
      [app-name] [basic-usage]

    Advanced:
      [app-name] [flags]

  ## Documentation

    [docs link]

  ## Contributing

    [contributing info]

  ## License

    [license]


web application template

  # [app-name]

  [badges]

  [tagline]

  ## Features

    - [feature 1]
    - [feature 2]
    - [feature 3]

  ## Quick Start

    docker run -p 3000:3000 [image-name]

    Or:

    git clone [repo]
    cd [app-name]
    npm install
    npm start

  ## Development

    npm install
    npm run dev

  ## Configuration

    [config details]

  ## Documentation

    [docs link]

  ## Contributing

    [contributing info]

  ## License

    [license]


PHASE 15: README QUALITY CHECKLIST


before finalizing your README, verify:

  [ ] title matches project name exactly
  [ ] tagline explains what and why in one line
  [ ] badges load correctly (click to test)
  [ ] quick start works from scratch
  [ ] installation commands tested on fresh system
  [ ] code examples are runnable
  [ ] all links work
  [ ] sections are in logical order
  [ ] no typos or grammatical errors
  [ ] consistent formatting throughout
  [ ] project status is clear
  [ ] license is specified
  [ ] support info is present


testing your README

actually test what you wrote:

  <terminal># create fresh environment</terminal>
  <terminal>python -m venv test_readme</terminal>
  <terminal>source test_readme/bin/activate</terminal>
  <terminal># follow your own quick start exactly</terminal>
  <terminal># does it work?</terminal>


PHASE 16: COMMON ANTI-PATTERNS


anti-pattern: wall of text

problem: huge paragraphs users won't read

solution: use lists, headers, code blocks

  before:
    This project is a terminal-based chat application that allows you to interact
    with large language models through a command line interface and includes a
    plugin system for extensibility and supports multiple providers...

  after:
    Kollab is a terminal-based chat interface for LLMs.

    Key features:
      - Plugin architecture for extensibility
      - Multi-provider support (OpenAI, Anthropic, etc.)
      - Pipe mode for scripting


anti-pattern: missing quick start

problem: users must read entire README to start

solution: put quick start immediately after badges

  structure:
    # Title
    [badges]
    [tagline]

    ## Quick Start
    [fastest path to running]

    ## Features
    ...


anti-pattern: assuming knowledge

problem: technical terms without explanation

solution: explain or link to explanations

  before:
    Uses a hook system for extensibility...

  after:
    Plugin system based on hooks - functions that run at specific points
    in the application lifecycle. See Plugin Development Guide for details.


anti-pattern: outdated badges

problem: badges showing wrong status

solution: use dynamic badges that update automatically

  [ok] https://img.shields.io/github/actions/workflow/status/...
  [x] https://img.shields.io/badge/status-passing-brightgreen  (manual)


anti-pattern: marketing language

problem: hype words that don't inform

solution: use specific, factual language

  before:
    Revolutionary AI chat tool with world-class features...
  after:
    Terminal chat interface supporting OpenAI, Anthropic, and local models...


PHASE 17: MAINTAINING YOUR README


keep it current

your README should evolve with the project:

  [ ] update badges when adding CI
  [ ] update version on each release
  [ ] add new features to features list
  [ ] update examples when API changes
  [ ] remove deprecated options
  [ ] update status as project matures


README reviews

periodically review your README:

  <terminal># ask yourself these questions</terminal>
  <terminal># 1. does the quick start still work?</terminal>
  <terminal># 2. are all links valid?</terminal>
  <terminal># 3. is the project status accurate?</terminal>
  <terminal># 4. would a new user understand this?</terminal>


PHASE 18: README RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER skip the quick start section
      users need to see value immediately
      if nothing else, include a one-liner example

  [2] ALWAYS test your own quick start
      in a fresh environment
      if it doesn't work, fix the README or the code

  [3] include badges for essential info
      license (required)
      version (if released)
      build status (if CI exists)

  [4] lead with value, not history
      what it is and why it matters first
      project story comes later (if at all)

  [5] use code fences for all examples
      readers must distinguish text from code
      specify language for syntax highlighting

  [6] link only to existing content
      dont link to TODO docs
      either create the doc or remove the link

  [7] update README with API changes
      examples must work with current version
      stale examples are worse than no examples

  [8] keep it scannable
      use headers, lists, short paragraphs
      most users skim, they don't read word-for-word

  [9] specify license clearly
      legal requirement for reuse
      include badge and SPDX identifier

  [10] provide a way to get help
      issue tracker, email, discord
       users will have questions


PHASE 19: README SESSION CHECKLIST


before starting:

  [ ] checked project type and language
  [ ] identified target audience
  [ ] reviewed existing documentation
  [ ] checked for existing README
  [ ] tested installation method

for each section:

  [ ] title matches project name
  [ ] tagline is clear and concise
  [ ] badges load correctly
  [ ] quick start works from scratch
  [ ] installation is comprehensive
  [ ] usage examples are runnable
  [ ] features are specific and benefit-focused
  [ ] links all work
  [ ] status is accurate
  [ ] license is specified

after completing:

  [ ] test entire README in fresh environment
  [ ] check all links
  [ ] verify badges render
  [ ] spell check
  [ ] get someone else to review it


FINAL REMINDERS


your README is your project's front door

it's often the first thing potential users see.
make it welcoming, clear, and actionable.


clarity over cleverness

write for someone who doesn't know your project.
avoid in-jokes, obscure references, internal terminology.
say what you mean directly.


examples beat explanations

show, don't just tell.
code examples explain more than paragraphs.
make every example runnable.


keep it living

update README as the project evolves.
stale docs lose trust.
current docs build confidence.


the test

can someone unfamiliar with your project:
  [ ] understand what it does in 5 seconds?
  [ ] install it in 30 seconds?
  [ ] use it for something useful in 2 minutes?

if yes, you have a great README.

now go write documentation that welcomes users in.
