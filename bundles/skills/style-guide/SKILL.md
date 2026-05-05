---
name: style-guide
description: "Style Guide skill - creating and enforcing documentation style guides"
---

style-guide mode: CONSISTENCY IS PROFESSIONALISM

when this skill is active, you follow strict style guide discipline.
this is a comprehensive guide to creating and enforcing documentation standards.


PHASE 0: ENVIRONMENT/PREREQUISITES VERIFICATION

before creating ANY documentation, verify the style guide environment is ready.


check for existing style guide

  <terminal>find . -name "*style*guide*" -o -name "*documentation*guide*" | head -10</terminal>

  <terminal>find . -name "GUIDELINES.md" -o -name "CONTRIBUTING.md" | head -5</terminal>

  <terminal>ls -la docs/ 2>/dev/null || echo "no docs directory"</terminal>

if style guide exists, read it first:
  <read><file>docs/STYLE_GUIDE.md</file></read>

understand existing conventions before making changes.


check project documentation structure

  <terminal>ls -la *.md 2>/dev/null</terminal>

  <terminal>ls -la docs/ 2>/dev/null | head -20</terminal>

  <terminal>find . -name "*.md" -type f | head -20</terminal>

identify existing documentation patterns to match or improve.


check for common style guide locations

  <terminal>ls -la | grep -i "style\|guide\|contribute"</terminal>

  <terminal>cat .github/CONTRIBUTING.md 2>/dev/null | head -50</terminal>

  <terminal>cat docs/CONTRIBUTING.md 2>/dev/null | head -50</terminal>


check target audience

before creating style guide, identify:
  [ ] who reads this documentation?
  [ ] what is their expertise level?
  [ ] what is their primary language?
  [ ] what cultural contexts apply?

audience categories:
  - developers: technical precision, code examples, API details
  - end users: simple language, step-by-step instructions, screenshots
  - business: feature benefits, use cases, minimal jargon
  - mixed: layered content with expandable sections


PHASE 1: UNDERSTANDING STYLE GUIDES


what is a style guide?

a style guide is a set of standards for writing and formatting documentation.
it ensures consistency across all documentation in a project.

purpose:
  - consistent voice and tone
  - uniform formatting
  - standardized terminology
  - predictable structure
  - easier maintenance


why style guides matter

without style guide:
  - inconsistent terminology confuses readers
  - varying formats distract from content
  - multiple writers create disjointed docs
  - maintenance becomes difficult

with style guide:
  - readers know what to expect
  - writers have clear guidelines
  - onboarding new writers is faster
  - documentation feels professional


components of a comprehensive style guide

  [1] voice and tone guidelines
      personality, formality level, perspective

  [2] word choice standards
      preferred terms, avoided terms, jargon policy

  [3] formatting conventions
      heading structure, code blocks, lists, emphasis

  [4] grammar and mechanics
      punctuation, capitalization, abbreviation rules

  [5] structural patterns
      document organization, section ordering, templates

  [6] terminology glossary
      project-specific terms and definitions

  [7] inclusive language guidelines
      bias-free communication standards

  [8] accessibility standards
      readability, alt text, semantic structure


PHASE 2: VOICE AND TONE


defining voice

voice is the personality expressed in writing.
it should remain consistent across all documentation.

voice dimensions:
  - formal vs casual
  - technical vs accessible
  - concise vs detailed
  - authoritative vs collaborative

example voice definitions:

  professional but approachable voice:
    - use clear, direct language
    - avoid slang and overly casual expressions
    - write as if speaking to a respected colleague
    - prefer "you" over passive constructions

  technical and precise voice:
    - use exact terminology
    - provide complete technical details
    - avoid vague statements
    - include code examples for clarity


defining tone

tone is the emotional attitude conveyed in specific contexts.
tone can vary by document type while voice remains constant.

tone by document type:

  tutorials: encouraging and patient
    - acknowledge complexity
    - validate user progress
    - anticipate common mistakes

  reference docs: neutral and concise
    - present facts without commentary
    - minimize explanatory text
    - focus on accuracy

  error messages: helpful and non-judgmental
    - explain what went wrong
    - suggest solutions
    - avoid blaming the user

  release notes: excited but professional
    - highlight improvements
    - acknowledge contributors
    - maintain credibility


voice and tone checklist

for each document:
  [ ] is the voice consistent throughout?
  [ ] does the tone match the document type?
  [ ] would this sound like it came from the same project
    as other documentation?
  [ ] is the perspective consistent (first-person plural,
    second-person, etc.)


PHASE 3: WORD CHOICE STANDARDS


preferred terminology

establish standard terms for key concepts:

  example: user terminology
    - use "user" not "end-user"
    - use "sign in" not "log in"
    - use "password" not "passcode"
    - use "application" not "app" (in formal docs)

  example: technical terminology
    - use "function" not "method" (unless OO method)
    - use "argument" not "parameter" (for passed values)
    - use "run" not "execute" (for commands)
    - use "file path" not "filepath"


avoided terms

list terms to avoid and alternatives:

  avoided vs preferred:
    - "utilize" -> "use"
    - "in order to" -> "to"
    - "prior to" -> "before"
    - "on the fly" -> "dynamically"
    - "hopefully" -> remove (express uncertainty differently)
    - "simply" -> remove (assumes simplicity)
    - "just" -> remove (minimizes complexity)
    - "basically" -> remove (vague filler)
    - "very" -> remove (weak intensifier)
    - "easy" -> remove (subjective)


jargon policy

define when jargon is acceptable:

  acceptable jargon:
    - industry-standard terms the audience knows
    - terms defined in the project glossary
    - API names, function names, variable names

  avoid jargon:
    - internal codenames not exposed to users
    - slang specific to one team
    - abbreviations without prior definition
    - technical metaphors (kill process, spawn thread)


technical terms handling

  first mention:
    "The Model Context Protocol (MCP) enables..."

  subsequent mentions:
    "MCP provides..."

  always define acronyms on first use in each document.


capitalization rules

  sentence case for most content:
    - "Click the submit button to continue."
    - "See the authentication section for details."

  title case for:
    - document titles
    - section headings (if using title case style)
    - proper names

  lowercase for:
    - "the internet"
    - "web" (as a general concept)
    - "online" (one word)
    - "email" (one word)
    - "login" / "logout" (as verbs, not adjectives)


PHASE 4: FORMATTING CONVENTIONS


heading structure

use consistent heading hierarchy:

  heading hierarchy:
    # Document title (used once)

    ## Major section (main divisions)

    ### Subsection (components of sections)

    #### Detail level (specific topics)

    ##### Rare use (nested details)

heading style:
  - ATX style (# prefix) preferred
  - setext style (underlines) acceptable for top level
  - consistent capitalization (sentence case or title case)
  - no trailing punctuation
  - blank line before and after


code blocks

use fenced code blocks with language specifiers:

  ```python
  def example():
      return "code"
  ```

  ```bash
  command --argument
  ```

  ```javascript
  const example = "code";
  ```

code block guidelines:
  [ ] always specify language for syntax highlighting
  [ ] show complete, runnable examples when possible
  [ ] include output comments when helpful
  [ ] limit line length to ~80 characters within code
  [ ] use real code, tested for correctness


inline code

use backticks for:
  - function names: `fetch_data()`
  - variables: `user_id`
  - file names: `config.json`
  - directories: `/usr/local/bin`
  - commands: `npm install`
  - configuration keys: `database.url`

do not use backticks for:
  - emphasis (use italics in markdown)
  - technical terms that are not code elements
  - generic concepts


lists

use consistent list formatting:

  unordered lists (bullets):
    - use hyphens (-) for markdown compatibility
    - start each item on new line
    - use blank line before list
    - capitalize first word
    - add period at end if item is complete sentence

  ordered lists (numbering):
    1. use numerals (1., 2., 3.)
    2. indent nested lists with 4 spaces
    3. maintain logical sequence
    4. use for step-by-step instructions

  definition lists:
    term
      : definition and explanation

    use for key-value explanations


emphasis

use emphasis sparingly for maximum effect:

  italics (markdown *word* or _word_):
    - first use of new terms
    - titles of standalone works
    - foreign words not yet adopted
    - mathematical variables

  bold (markdown **word** or __word__):
    - key terms on first definition
    - warnings or critical information
    - UI labels and button names
    - field names in forms

  avoid:
    - multiple styles in one sentence
    - emphasis for entire paragraphs
    - all-caps for emphasis


links

create meaningful link text:

  good:
    - See the [authentication guide](docs/auth.md) for details.
    - Download the [latest release](https://example.com/releases).

  bad:
    - Click [here](docs/auth.md) for details.
    - Go to [this link](https://example.com/releases).

link guidelines:
  - descriptive link text
  - include protocol for external links
  - prefer relative links for internal docs
  - test all links regularly


PHASE 5: GRAMMAR AND MECHANICS


punctuation rules

  serial comma:
    - use serial comma (Oxford comma): "a, b, and c"
    - improves clarity in technical writing

  colons:
    - use to introduce lists, code blocks, explanations
    - capitalize first word after colon if complete sentence
    - no capital if fragment or list

  semicolons:
    - join related independent clauses
    - separate complex list items
    - use sparingly in technical docs

  dashes:
    - em dash (--) without spaces for breaks
    - en dash (-) for ranges (2020-2024)
    - hyphen (-) for compound words


capitalization standards

  capitalize:
    - proper nouns: Python, JavaScript, GitHub
    - brand names: PostgreSQL, Redis
    - trade names: iPhone, macOS
    - product names when official: AWS, Azure

  lowercase:
    - generic terms: database, server, client
    - services unless official name: s3 not S3
    - internet, web (as general concepts)

  title case in headings:
    - capitalize major words
    - lowercase articles, conjunctions, prepositions
    - capitalize first and last word always


abbreviation rules

  first use:
    "The Application Programming Interface (API) allows..."

  subsequent uses:
    "The API handles..."

  common abbreviations that need no introduction:
    - API, HTTP, URL, JSON, XML
    - CPU, RAM, SSD, GPU
    - UI, UX, CLI, GUI

  avoid:
    - project-specific abbreviations without definition
    - latin abbreviations: use "for example" not "e.g."


numbers and measurements

  spell out:
    - zero through nine: "three files"
    - numbers at sentence start: "Seven users..."

    use numerals for:
    - 10 and above: "42 requests"
    - measurements with units: "5 MB", "100 ms"
    - version numbers: "version 2.0"
    - percentages: "25 percent" (spell out "percent")

  large numbers:
    - use commas: "1,000,000 users"
    - consider scientific notation for very large numbers

  date formats:
    - ISO 8601 for dates: 2024-01-15
    - avoid regional formats: not "01/15/2024" or "15/01/2024"


PHASE 6: STRUCTURAL PATTERNS


document template structure

standard document organization:

  title: clear, descriptive, includes key topic

  description: one-sentence summary of document purpose

  prerequisites: what user needs before starting
    - knowledge requirements
    - required tools
    - necessary permissions

  overview/summary: high-level explanation

  main content: organized by topic or step

  see also: related documentation links

  appendix: supplementary information


tutorial structure

step-by-step tutorial template:

  ## Title: Verb-based, outcome-focused
  ## "Configure Authentication"

  Description:
    Learn how to set up OAuth2 authentication for your API.

  Prerequisites:
    - Basic knowledge of REST APIs
    - Python 3.8+ installed
    - Account with OAuth provider

  Time estimate: 15 minutes

  Steps:
    1. First clear action
       explanation of what and why

    2. Second action
       continue with clear instructions

  What's next:
    - Secure your API tokens
    - Configure scopes
    - Handle token refresh


reference documentation structure

reference page template:

  ## [Component/Function Name]

  Brief description: one or two sentences

  Signature/prototype:
    ```language
    function_name(param1, param2) -> return_type
    ```

  Parameters:
    - `param1`: type - description
    - `param2`: type - description

  Returns:
    type - description of return value

  Raises/Exceptions:
    - `ErrorType`: when and why

  Examples:
    ```language
    result = function_name(arg1, arg2)
    ```

  See also:
    - related_function()
    - related_guide.md


troubleshooting structure

troubleshooting template:

  ## [Problem Description]

  Symptom:
    Clear description of what user experiences

  Cause:
    Technical explanation of root cause

  Solutions:

    Solution 1: [Title]
    Step-by-step resolution...

    Solution 2: [Title]
    Alternative approach...

  Prevention:
    How to avoid this issue


PHASE 7: TERMINOLOGY GLOSSARIES


creating a project glossary

every project should maintain a terminology glossary:

  template:
    ## Term

    Definition: clear, concise explanation

    Context: when and where this term is used

    Synonyms: related terms to avoid confusion

    Examples: usage in context


example glossary entries:

  ## Agent

  Definition: An autonomous process that performs tasks
  within the Kollab system.

  Context: Agents are spawned using the tglm command and
  operate independently to complete assigned work.

  Synonyms: subprocess, worker, bot (avoid)

  Examples: "Launch the PhaseBTypeDefinitions agent to
  add TypeScript types."


  ## Hook

  Definition: A registered function that executes in response
  to specific events in the application.

  Context: Plugins register hooks to intercept and modify
  application behavior.

  Synonyms: event handler, callback (use specific term)

  Examples: "The plugin registers a pre_api_request hook
  to modify outgoing API calls."


maintaining the glossary

  [ ] add new terms when introducing concepts
  [ ] update definitions as product evolves
  [ ] mark deprecated terms clearly
  [ ] cross-reference related terms
  [ ] include in documentation build process


glossary formatting

  alphabetical organization:
    ## A
    ### Agent
    ### API

    ## B
    ### Buffer

    ## H
    ### Hook

  cross-references:
    "See also: Event Bus"

  pronunciation guide for difficult terms:
    "asynchronous (ay-SINK-ron-us)"


PHASE 8: INCLUSIVE LANGUAGE GUIDELINES


principles of inclusive writing

  respect:
    - use language that respects all readers
    - acknowledge diverse backgrounds
    - avoid assumptions about user identity

  clarity:
    - specific terms over general ones
    - direct language over euphemisms
    - precision over tradition


gender-neutral language

  avoid gendered pronouns for general users:
    - use "they" as singular pronoun
    - use "the user" instead of "he or she"
    - rephrase to avoid pronouns

  examples:
    - "The user can save their work" (not "his or her work")
    - "Each developer manages their own workspace"
    - "When a user logs in, they have access to..."

  gendered terms to avoid:
    - "manpower" -> "workforce" or "staff"
    - "mankind" -> "humanity" or "people"
    - "master/slave" -> "primary/replica" or "main/follower"
    - "blacklist/whitelist" -> "blocklist/allowlist"


ability-inclusive language

  avoid metaphorical disability terms:
    - "blind to" -> "unaware of" or "ignores"
    - "crippled" -> "broken" or "non-functional"
    - "lame" -> "poor" or "ineffective"
    - "paralyzed" -> "stuck" or "unable to proceed"

  focus on functionality, not limitations:
    - "accessible interface" (describe what is available)
    - "supports keyboard navigation" (specific feature)


cultural inclusivity

  avoid culture-specific idioms:
    - "it's a piece of cake" -> "it's straightforward"
    - "hang tight" -> "please wait"
    - "in a nutshell" -> "in summary" or "briefly"

  avoid culture-specific references as defaults:
    - specify time zones: "14:00 UTC" not "2:00 PM"
    - use ISO date formats: 2024-01-15
    - avoid sports metaphors in international docs


age-inclusive language

  avoid age-based assumptions:
    - "newbie" -> "newcomer" or "beginner"
    - "junior/senior" -> specific experience levels
    - avoid generational labels (boomer, millennial)


PHASE 9: ACCESSIBILITY STANDARDS


readability guidelines

target reading level:
  - aim for 8th-10th grade reading level
  - use simple sentence structures
  - one idea per sentence
  - limit sentence length to ~20 words

  test readability:
    <terminal>npm install -g readable</terminal>
    <terminal>readable docs/*.md</terminal>


heading structure for screen readers

  proper heading hierarchy:
    - dont skip levels (h1 -> h3)
    - use headings for structure, not styling
    - unique heading text within document

  descriptive headings:
    - "Configuring the database" (good)
    - "Configuration" (vague)
    - "Step 1" (not descriptive)


alt text for images

  all images need alt text:
    ![Diagram of the system architecture showing
    the three main components and their connections]

    ![A screenshot of the settings panel with
    the authentication options highlighted]

  alt text guidelines:
    - describe content, not appearance
    - include relevant details
    - keep concise (125 characters typical limit)
    - end images with " showing..." if screenshot
    - pure decorative images use empty alt=""


code accessibility

  code blocks for screen readers:
    - ensure code can be announced
    - provide text explanations for complex code
    - line breaks in important places
    - comments for non-obvious logic

  syntax considerations:
    - avoid ascii art in code
    - use unicode symbols carefully
    - explain unicode characters in text


link accessibility

  descriptive link text:
    - "Download the Python SDK" (good)
    - "Click here" (bad - no context)

  link indicators:
    - indicate external links explicitly
    - warn about opening new windows/tabs
    - distinguish download links


color and visual accessibility

in documentation:
  - dont rely on color alone to convey meaning
  - provide text alternatives for color-coded info
  - ensure sufficient color contrast in diagrams
  - describe visual elements in text


PHASE 10: INTERNATIONALIZATION CONSIDERATIONS


writing for translation

  simple sentence structure:
    - subject-verb-object order
    - one clause per sentence
    - avoid nested clauses

  avoid:
    - idioms and colloquialisms
    - culture-specific references
    - puns and wordplay
    - phrasal verbs with multiple meanings

  examples:
    - "break down" -> "analyze" or "decompose"
    - "set up" -> "configure" or "create"
    - "look into" -> "investigate"


dates, times, and numbers

  use formats that translate well:
    - "2024-01-15" not "1/15/24" or "15/1/24"
    - "14:00 UTC" not "2:00 PM EST"
    - "1,000,000" with thousand separators

  explain:
    - time zone abbreviations
    - regional measurement units
    - currency symbols and codes


context for translation

  provide context for ambiguous terms:
    - "run (execute)" vs "run (sequence)"
    - "batch (processing)" vs "batch (group)"

  use glossaries:
    - maintain translation glossary
    - define technical terms
    - specify terms NOT to translate


PHASE 11: STYLE GUIDE ENFORCEMENT


automated checks

use linters for documentation:

  markdown linting:
    <terminal>npm install -g markdownlint-cli</terminal>
    <terminal>markdownlint docs/**/*.md</terminal>

  write-good for clarity:
    <terminal>npm install -g write-good</terminal>
    <terminal>write-good docs/*.md</terminal>

  vale for custom rules:
    <terminal>pip install vale</terminal>
    <terminal>vale docs/*.md</terminal>


vale configuration

create `.vale.ini`:

  <create>
  <file>.vale.ini</file>
  <content>
  [*.md]
  BasedOnStyles = Google
  TokenIgnorers = (\[.*?\]\(.*?\)|`[^`]+`)

  Google.Headings = NO
  Google.HeadingLength = NO
  </content>
  </create>

create custom vocab:

  <create>
  <file>vocab.txt</file>
  <content>
  # Project-specific terminology

  # Acceptable terms
  Kollabor
  hook
  agent
  plugin
  terminal

  # Terms to avoid
  blacklist->blocklist
  whitelist->allowlist
  master->primary
  slave->replica
  </content>
  </create>


pre-commit integration

  # .pre-commit-config.yaml
  repos:
    - repo: https://github.com/igorshubovych/markdownlint-cli
      rev: v0.37.0
      hooks:
        - id: markdownlint
          args: [--fix]

    - repo: https://github.com/errata-ai/vale
      rev: v2.20.0
      hooks:
        - id: vale


ci integration

  # github actions example
  name: Documentation Lint

  on: [push, pull_request]

  jobs:
    lint:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3

        - name: Install markdownlint
          run: npm install -g markdownlint-cli

        - name: Lint markdown
          run: markdownlint docs/**/*.md


PHASE 12: REVIEW CHECKLISTS


document creation checklist

before publishing new documentation:

  structure:
    [ ] has descriptive title
    [ ] has description/summary
    [ ] has appropriate heading hierarchy
    [ ] has logical flow
    [ ] has see-also links if applicable

  content:
    [ ] accurate and up to date
    [ ] complete for the topic
    [ ] code examples tested
    [ ] links verified
    [ ] screenshots current

  style:
    [ ] follows voice guidelines
    [ ] uses preferred terminology
    [ ] consistent formatting
    [ ] proper grammar and punctuation
    [ ] inclusive language

  accessibility:
    [ ] headings descriptive
    [ ] images have alt text
    [ ] links have descriptive text
    [ ] code is readable


style guide audit checklist

periodically review the style guide itself:

  [ ] all sections present and complete
  [ ] examples reflect current best practices
  [ ] terminology matches current product
  [ ] inclusive language guidelines current
  [ ] tooling instructions work
  [ ] team feedback incorporated


documentation review process

establish review workflow:

  1. author creates documentation
  2. author completes self-review checklist
  3. peer review by documentation owner
  4. technical review by subject matter expert
  5. accessibility review if major document
  6. updates based on feedback
  7. final approval and merge

  use pull requests for all documentation changes.


peer review guidelines

when reviewing others documentation:

  clarity:
    [ ] is the main point clear?
    [ ] could this be misunderstood?
    [ ] are steps in logical order?

  completeness:
    [ ] is information missing?
    [ ] are prerequisites listed?
    [ ] are edge cases covered?

  accuracy:
    [ ] are code examples correct?
    [ ] are technical details accurate?
    [ ] are links valid?

  style:
    [ ] does it follow the style guide?
    [ ] is terminology consistent?
    [ ] is tone appropriate?

  provide specific, actionable feedback.


PHASE 13: STYLE GUIDE DOCUMENTATION


creating the style guide document

structure the style guide itself:

  <create>
  <file>docs/STYLE_GUIDE.md</file>
  <content>
  # Documentation Style Guide

  ## About this guide

  This guide establishes standards for all [Project] documentation.
  Following these guidelines ensures consistency across our docs.

  ## Quick reference

  - Voice: professional but approachable
  - Perspective: second-person ("you")
  - Headings: sentence case, ATX style
  - Code: fenced blocks with language specified
  - Links: descriptive text, not "click here"

  ## Table of contents

  1. [Voice and Tone](#voice-and-tone)
  2. [Word Choice](#word-choice)
  3. [Formatting](#formatting)
  4. [Grammar](#grammar)
  5. [Structure](#structure)
  6. [Inclusive Language](#inclusive-language)
  7. [Accessibility](#accessibility)

  ## Voice and Tone

  [detailed voice guidelines with examples]

  ## Word Choice

  [terminology standards, preferred/avoided terms]

  ## Formatting

  [markdown conventions, code blocks, lists]

  ## Grammar

  [punctuation, capitalization, numbers]

  ## Structure

  [document templates, organization patterns]

  ## Inclusive Language

  [guidelines for bias-free communication]

  ## Accessibility

  [standards for readable documentation]

  ## Glossary

  [project-specific terminology definitions]

  ## Resources

  - [Template files](templates/)
  - [Review checklist](checklist.md)
  - [Contribution guide](CONTRIBUTING.md)
  </content>
  </create>


template files

provide ready-to-use templates:

  docs/templates/
    tutorial.md
    reference.md
    guide.md
    troubleshooting.md
    api.md

  each template includes:
    - section placeholders
    - format examples
    - embedded instructions


PHASE 14: MAINTAINING THE STYLE GUIDE


evolving the guide

  when to update:
    - new documentation patterns emerge
    - product introduces new terminology
    - team feedback indicates issues
    - industry best practices change

  update process:
    1. propose change with rationale
    2. gather feedback from doc team
    3. update style guide
    4. communicate changes to writers
    5. update affected documentation


versioning style guides

for major style guide changes:
  - maintain previous version
  - document migration path
  - set transition timeline
  - tag old documents with version

  example:
    docs/STYLE_GUIDE.md (current, v2.0)
    docs/STYLE_GUIDE_v1.md (archived)


training and onboarding

  new writer checklist:
    [ ] read style guide completely
    [ ] review example documentation
    [ ] complete style exercises
    [ ] submit sample for review
    [ ] attend style guide walkthrough

  ongoing training:
    - quarterly style guide refreshers
    - common mistakes workshop
    - tool training (vale, markdownlint)
    - peer review practice


PHASE 15: COMMON STYLE ISSUES AND FIXES


issue: inconsistent terminology

  problem:
    "user" / "end-user" / "client" used interchangeably

  fix:
    - establish preferred term in glossary
    - search and replace across docs
    - add vale rule for prohibited terms

    <terminal>find docs/ -name "*.md" -exec sed -i 's/end-user/user/g' {} +</terminal>


issue: unclear link text

  problem: "click here", "this link", "more"

  fix:
    - rewrite with descriptive text
    - explain context and purpose
    - include action if applicable

    before: "Click here to install."

    after: "Install the package using pip."


issue: wall of text

  problem: long paragraphs without breaks

  fix:
    - break into shorter paragraphs
    - use lists for clarity
    - add headings for structure
    - target 3-5 sentences per paragraph


issue: missing context

  problem: documentation assumes knowledge

  fix:
    - add prerequisites section
    - define terms on first use
    - provide background information
    - link to related concepts


issue: outdated examples

  problem: code examples no longer work

  fix:
    - test all code examples
    - add testing to ci pipeline
    - version examples with api
    - add "tested with" labels


PHASE 16: STYLE GUIDE RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] CHECK for existing style guide FIRST
      if project has style guide, read it
      follow existing conventions
      propose changes rather than ignoring

  [2] DEFINE terminology before using
      introduce new terms clearly
      add to glossary if project has one
      use consistently throughout document

  [3] WRITE for your audience
      identify who will read this
      match technical level to audience
      explain jargon or avoid it
      provide context for complex topics

  [4] USE inclusive language
      avoid gendered pronouns for general users
      use respectful, person-first language
      avoid culture-specific idioms
      replace ableist metaphors

  [5] ENSURE accessibility
      provide alt text for all images
      use descriptive headings
      create descriptive link text
      maintain heading hierarchy

  [6] FOLLOW formatting conventions
      consistent heading style
      fenced code blocks with language
      proper list formatting
      blank lines around blocks

  [7] BE consistent within the document
      same term = same concept
      same formatting = same meaning
      check before finalizing

  [8] TEST code examples
      all code must run
      include expected output
      note any prerequisites

  [9] PROOFREAD before publishing
      spelling and grammar check
      run automated linters
      review against checklist

  [10] SEEK feedback on important docs
      subject matter expert review
      peer review for clarity
      accessibility review if public


PHASE 17: DOCUMENTATION REVIEW CHECKLIST


use this checklist for every document:

content quality:
  [ ] information is accurate
  [ ] content is complete
  [ ] examples are correct
  [ ] links work and go to relevant content
  [ ] screenshots are current

clarity:
  [ ] purpose is clear
  [ ] audience is appropriate
  [ ] explanations are understandable
  [ ] jargon is explained or avoided
  [ ] steps are in logical order

structure:
  [ ] has clear title
  [ ] has summary/description
  [ ] uses consistent headings
  [ ] organizes information logically
  [ ] includes see-also links

style:
  [ ] follows voice guidelines
  [ ] uses preferred terminology
  [ ] consistent capitalization
  [ ] proper punctuation
  [ ] inclusive language

formatting:
  [ ] correct heading hierarchy
  [ ] code blocks have language tags
  [ ] lists formatted correctly
  [ ] links have descriptive text
  [ ] no markdown rendering errors

accessibility:
  [ ] headings are descriptive
  [ ] images have alt text
  [ ] sufficient color contrast
  [ ] code is readable
  [ ] link text describes destination


FINAL REMINDERS


consistency creates trust

when documentation feels consistent, users trust the content.
inconsistent style suggests inconsistent quality.
the style guide is your foundation.


style guides evolve

no style guide is perfect from the start.
gather feedback from writers and readers.
update as the project grows.
stay open to improvements.


clarity over cleverness

technical writing should be invisible.
the reader should focus on the content, not the writing.
if you notice the style, it might be getting in the way.


good documentation is good business

clear documentation reduces support burden.
well-written docs attract users.
consistent style enables collaboration.
invest in your docs.


when in doubt

be clear over clever.
be direct over diplomatic.
be specific over general.
be kind over cool.

now go create clear, consistent documentation.
