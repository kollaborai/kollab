---
name: knowledge-management
description: "Knowledge Management skill - organize, maintain, and retrieve personal knowledge base"
---

knowledge-management mode: ORGANIZED INTELLIGENCE

when this skill is active, you systematically maintain tech-dude's knowledge base.
this is a comprehensive guide to building and sustaining personal knowledge.


PHASE 0: KNOWLEDGE BASE SETUP


verify knowledge structure

  <terminal>mkdir -p ~/knowledge</terminal>
  <terminal>mkdir -p ~/knowledge/technologies</terminal>
  <terminal>mkdir -p ~/knowledge/concepts</terminal>
  <terminal>mkdir -p ~/knowledge/patterns</terminal>
  <terminal>mkdir -p ~/knowledge/experiments</terminal>
  <terminal>mkdir -p ~/knowledge/trends</terminal>
  <terminal>mkdir -p ~/knowledge/projects</terminal>

  <terminal>ls -la ~/knowledge</terminal>


create master index

  <create><file>~/knowledge/index.md</file><content># Tech-Dude's Knowledge Base

last updated: <date>
total entries: <number>

## quick search

search by technology:
  - [see technologies index](technologies/)

search by concept:
  - [see concepts index](concepts/)

search by project:
  - [see projects index](projects/)

recent updates:
  - [recent update 1]
  - [recent update 2]
  - [recent update 3]

## knowledge areas

### technologies
[see technologies index](technologies/index.md)
  - frameworks and libraries
  - languages and runtimes
  - tools and utilities

### concepts
[see concepts index](concepts/index.md)
  - programming patterns
  - architectural concepts
  - design principles
  - algorithms and data structures

### patterns
[see patterns index](patterns/index.md)
  - code patterns
  - workflow patterns
  - problem-solving patterns

### experiments
[see experiments index](experiments/index.md)
  - research findings
  - poc results
  - lessons learned

### trends
[see trends index](trends/index.md)
  - weekly trend reports
  - monthly analysis
  - yearly synthesis

### projects
[see projects index](projects/index.md)
  - project documentation
  - architectural decisions
  - deployment notes

## tag cloud

frequently used tags:
  - [tag 1] - [count] entries
  - [tag 2] - [count] entries
  - [tag 3] - [count] entries

full tags:
  - [link to tags page](tags.md)

## statistics

entries by type:
  - technologies: [X]
  - concepts: [Y]
  - patterns: [Z]
  - experiments: [W]
  - trends: [V]
  - projects: [U]

entries by domain:
  - javascript/typescript: [X]
  - python: [Y]
  - rust: [Z]
  - devops: [W]
  - ai/ml: [V]

entries by confidence:
  - high confidence: [X]
  - medium confidence: [Y]
  - low confidence: [Z]

## maintenance

last cleanup: <date>
next review scheduled: <date>

todo:
  - [ ] consolidate duplicates
  - [ ] update outdated entries
  - [ ] fix broken links
  - [ ] add missing connections

## search strategies

looking for how to do something:
  1. check patterns index
  2. search specific technology
  3. check related concepts

looking for why something works:
  1. check concepts index
  2. search pattern documentation
  3. check research findings

looking for what to use:
  1. check technology comparisons
  2. check trend reports
  3. check experiment results

looking for where something is used:
  1. check projects index
  2. search technology entries
  3. check code references
</content></create>


create indexing system

  <create><file>~/knowledge/.update-index.sh</file><content>#!/bin/bash
# update knowledge base indices

echo "updating indices..."

# technology index
echo "# Technologies Index" > ~/knowledge/technologies/index.md
echo "" >> ~/knowledge/technologies/index.md
echo "last updated: $(date +%Y-%m-%d)" >> ~/knowledge/technologies/index.md
echo "" >> ~/knowledge/technologies/index.md
echo "## all technologies" >> ~/knowledge/technologies/index.md
echo "" >> ~/knowledge/technologies/index.md
find ~/knowledge/technologies -name "*.md" ! -name "index.md" | sort | while read file; do
  name=$(basename "$file" .md)
  desc=$(head -5 "$file" | grep -i "description\|purpose" | head -1 | sed 's/.*: //')
  echo "- [$name]($name.md) - $desc" >> ~/knowledge/technologies/index.md
done

# concept index
echo "# Concepts Index" > ~/knowledge/concepts/index.md
echo "" >> ~/knowledge/concepts/index.md
echo "last updated: $(date +%Y-%m-%d)" >> ~/knowledge/concepts/index.md
echo "" >> ~/knowledge/concepts/index.md
echo "## all concepts" >> ~/knowledge/concepts/index.md
echo "" >> ~/knowledge/concepts/index.md
find ~/knowledge/concepts -name "*.md" ! -name "index.md" | sort | while read file; do
  name=$(basename "$file" .md)
  echo "- [$name]($name.md)" >> ~/knowledge/concepts/index.md
done

echo "indices updated"
</content></create>

  <terminal>chmod +x ~/knowledge/.update-index.sh</terminal>
  <terminal>~/knowledge/.update-index.sh</terminal>


PHASE 1: KNOWLEDGE CAPTURE


step 1: capture new knowledge

when learning something new:

  <create><file>~/knowledge/<category>/<topic>.md</file><content># <topic>

captured: <date>
source: [where learned]
confidence: [high/medium/low]

## overview

[one-sentence summary]

## what it is

[clear explanation]

## why it matters

[why this knowledge is valuable]

## key concepts

- concept 1: [explanation]
- concept 2: [explanation]
- concept 3: [explanation]

## practical application

how to use:
  ```language
  // code example
  ```

when to use:
  - [situation 1]
  - [situation 2]

when not to use:
  - [situation 3]
  - [situation 4]

## examples

example 1: [context]
  ```language
  code
  ```

example 2: [context]
  ```language
  code
  ```

## common pitfalls

- pitfall 1: [description]
  - how to avoid: [solution]

- pitfall 2: [description]
  - how to avoid: [solution]

## related knowledge

see also:
  - [related topic 1](../category1/topic1.md)
  - [related topic 2](../category2/topic2.md)

prerequisite for:
  - [topic 3]

used in:
  - [project 1](../projects/project1.md)
  - [project 2](../projects/project2.md)

## resources

learned from:
  - [resource 1](url)
  - [resource 2](url)

further reading:
  - [link 1](url)
  - [link 2](url)

## tags

[tag1], [tag2], [tag3]

## notes

[personal notes, insights, thoughts]
</content></create>


step 2: capture learnings from experiments

extract knowledge from experiments:

  <read><file>~/experiments/completed/<experiment>/README.md</file></read>

  <create><file>~/knowledge/experiments/<experiment>-learnings.md</file><content># Learnings: <experiment>

experiment: [link to experiment]
completed: <date>

## what worked

[success 1]:
  - why it worked: [reason]
  - when to apply: [context]

[success 2]:
  - why it worked: [reason]
  - when to apply: [context]

## what didn't work

[failure 1]:
  - why it failed: [reason]
  - alternative approach: [solution]

[failure 2]:
  - why it failed: [reason]
  - alternative approach: [solution]

## key insights

insight 1: [discovery]
  - why it matters: [significance]

insight 2: [discovery]
  - why it matters: [significance]

## surprises

unexpected discovery 1: [description]
  - why unexpected: [reason]
  - implications: [what this means]

unexpected discovery 2: [description]
  - why unexpected: [reason]
  - implications: [what this means]

## recommendations

for tech-dude:
  - [recommendation 1]
  - [recommendation 2]

for similar experiments:
  - [tip 1]
  - [tip 2]

## code snippets

most useful pattern:
  ```language
  code
  ```

gotcha to avoid:
  ```language
  // bad
  code

  // good
  code
  ```

## connections

related technologies:
  - [tech 1](../technologies/tech1.md)
  - [tech 2](../technologies/tech2.md)

related experiments:
  - [experiment 1](experiments-1-learnings.md)
  - [experiment 2](experiments-2-learnings.md)
</content></create>


step 3: capture code patterns

document reusable patterns:

  <create><file>~/knowledge/patterns/<pattern-name>.md</file><content># Pattern: <pattern-name>

captured: <date>
language: [javascript/python/rust/etc]
category: [creational/structural/behavioral/architectural]

## what this pattern does

[description of problem it solves]

## when to use

use when:
  - [condition 1]
  - [condition 2]

don't use when:
  - [condition 3]
  - [condition 4]

## implementation

basic implementation:
  ```language
  // clean, simple implementation
  ```

with example:
  ```language
  // concrete use case
  ```

## variations

variation 1: [name]
  ```language
  code
  ```
  when to use: [context]

variation 2: [name]
  ```language
  code
  ```
  when to use: [context]

## trade-offs

advantages:
  - [advantage 1]
  - [advantage 2]

disadvantages:
  - [disadvantage 1]
  - [disadvantage 2]

## alternatives

alternative patterns:
  - [pattern 1](pattern1.md): [when better]
  - [pattern 2](pattern2.md): [when better]

## examples in tech-dude's code

used in:
  - [project/file](../projects/project.md): [context]
  - [project/file](../projects/project.md): [context]

## related patterns

similar to:
  - [pattern 1](pattern1.md): [relationship]

complementary to:
  - [pattern 2](pattern2.md): [relationship]
</content></create>


step 4: capture architectural decisions

document important decisions:

  <create><file>~/knowledge/adr/<date>-<decision-topic>.md</file><content># ADR: <decision title>

date: <date>
status: [proposed/accepted/deprecated/superseded]

## context

[what problem are we solving?]
[what are the constraints?]
[what are we trying to achieve?]

## decision

[what did we decide?]

## rationale

[why did we make this decision?]

- reason 1: [explanation]
- reason 2: [explanation]
- reason 3: [explanation]

## alternatives considered

alternative 1: [description]
  - pros:
    - [pro 1]
    - [pro 2]
  - cons:
    - [con 1]
    - [con 2]
  - why rejected: [reason]

alternative 2: [description]
  - pros:
    - [pro 1]
    - [pro 2]
  - cons:
    - [con 1]
    - [con 2]
  - why rejected: [reason]

## consequences

positive:
  - [consequence 1]
  - [consequence 2]

negative:
  - [consequence 1]
  - [consequence 2]

## implementation

what needs to happen:
  [ ] [action 1]
  [ ] [action 2]

migration path:
  [if applicable]

## related decisions

- [related ADR 1](../date-decision1.md)
- [related ADR 2](../date-decision2.md)
</content></create>


step 5: capture project knowledge

document project-specific knowledge:

  <create><file>~/knowledge/projects/<project-name>.md</file><content># Project: <project-name>

created: <date>
last updated: <date>
status: [active/paused/completed/archived]

## overview

what it does: [description]
why it exists: [purpose]
target users: [who]

## tech stack

frontend:
  - [framework]: [version]
  - [tool]: [version]

backend:
  - [language]: [version]
  - [framework]: [version]

infrastructure:
  - [service]: [configuration]
  - [tool]: [version]

## architecture

high-level design:
  [diagram or description]

key components:
  - [component 1]: [purpose]
  - [component 2]: [purpose]
  - [component 3]: [purpose]

data flow:
  [description of how data moves through system]

## key patterns used

architectural:
  - [pattern 1](../patterns/pattern1.md): [context]
  - [pattern 2](../patterns/pattern2.md): [context]

code:
  - [pattern 3](../patterns/pattern3.md): [context]

## important decisions

- [ADR: decision 1](../adr/date-decision.md)
- [ADR: decision 2](../adr/date-decision.md)

## setup

getting started:
  ```bash
  # commands
  ```

development:
  ```bash
  # commands
  ```

deployment:
  ```bash
  # commands
  ```

## known issues

- [issue 1]: [workaround]
- [issue 2]: [workaround]

## future improvements

planned features:
  - [feature 1]
  - [feature 2]

technical debt:
  - [debt 1]: [why it exists, plan to fix]
  - [debt 2]: [why it exists, plan to fix]

## resources

repo: [url]
documentation: [url]
deployed: [url]

## related knowledge

technologies used:
  - [tech 1](../technologies/tech1.md)
  - [tech 2](../technologies/tech2.md)

experiments related:
  - [experiment 1](../experiments/exp1-learnings.md)
</content></create>


PHASE 2: KNOWLEDGE ORGANIZATION


step 1: tag everything

create consistent tagging system:

  <create><file>~/knowledge/tags.md</file><content># Tags

last updated: <date>

## tech tags

- javascript
- typescript
- python
- rust
- go
- web
- backend
- frontend
- mobile
- desktop
- cli

## domain tags

- web-development
- api-design
- database
- devops
- testing
- security
- performance
- ml-ai
- data-science

## concept tags

- pattern
- architecture
- algorithm
- data-structure
- design-principle

## status tags

- active
- deprecated
- experimental
- production-ready
- learning-in-progress

## purpose tags

- tutorial
- reference
- research
- experiment
- decision
</content></create>

apply tags to entries:
  - include relevant tags in each entry
  - be consistent with tag names
  - limit to 3-5 tags per entry


step 2: create connections

link related knowledge:

  <edit><file>~/knowledge/<category>/<topic>.md</file><find>## related knowledge</find><replace>## related knowledge

see also:
  - [related topic](../category/topic.md)
  - [related topic](../category/topic.md)

prerequisite:
  - [prerequisite topic](../category/topic.md)

leads to:
  - [advanced topic](../category/topic.md)

used in projects:
  - [project](../projects/project.md)
  - [project](../projects/project.md)

## related knowledge</replace></edit>


step 3: create summaries

create quick-reference summaries:

  <create><file>~/knowledge/summaries.md</file><content># Quick Summaries

last updated: <date>

## technology summaries

### <tech 1>
- what it is: [one line]
- when to use: [one line]
- quick start: [command]
- key concept: [concept name]

### <tech 2>
[same structure]

## concept summaries

### <concept 1>
- definition: [one line]
- why it matters: [one line]
- key insight: [one line]

### <concept 2>
[same structure]

## pattern summaries

### <pattern 1>
- solves: [problem]
- when to use: [condition]
- code: [one-line example]

### <pattern 2>
[same structure]
</content></create>


PHASE 3: KNOWLEDGE RETRIEVAL


step 1: search strategies

finding how to do something:

  <terminal>rg "how to\|implement\|example" ~/knowledge/patterns --type md -A 2</terminal>

finding what something is:

  <terminal>rg "what it is\|definition\|overview" ~/knowledge/concepts --type md -A 2</terminal>

finding what to use:

  <terminal>rg "when to use\|vs\|alternative" ~/knowledge/technologies --type md -A 2</terminal>

finding where something is used:

  <terminal>rg "used in\|applied in" ~/knowledge/projects --type md -B 1 -A 2</terminal>


step 2: cross-reference lookup

for a technology, find:
  - documentation: ~/knowledge/technologies/<tech>.md
  - patterns using it: rg "<tech>" ~/knowledge/patterns
  - projects using it: rg "<tech>" ~/knowledge/projects
  - experiments with it: rg "<tech>" ~/knowledge/experiments

for a pattern, find:
  - documentation: ~/knowledge/patterns/<pattern>.md
  - technologies that use it: rg "<pattern>" ~/knowledge/technologies
  - examples in projects: rg "<pattern>" ~/knowledge/projects

for a concept, find:
  - documentation: ~/knowledge/concepts/<concept>.md
  - related patterns: rg "<concept>" ~/knowledge/patterns
  - implementations in code: rg "<concept>" ~/projects


step 3: create reading lists

topic-focused reading lists:

  <create><file>~/knowledge/reading-lists/<topic>.md</file><content># Reading List: <topic>

created: <date>

## overview

goal: [what tech-dude wants to achieve by learning this]
background: [current knowledge level]

## reading order

### prerequisites
- [ ] [entry 1](../category/entry1.md)
- [ ] [entry 2](../category/entry2.md)

### core concepts
- [ ] [entry 3](../category/entry3.md)
- [ ] [entry 4](../category/entry4.md)

### practical application
- [ ] [entry 5](../category/entry5.md)
- [ ] [entry 6](../category/entry6.md)

### advanced topics
- [ ] [entry 7](../category/entry7.md)

## notes

[space for notes while reading]

## after reading

key takeaways:
- [takeaway 1]
- [takeaway 2]

what to implement:
- [implementation idea 1]
- [implementation idea 2]

further reading:
- [topic 1](../reading-lists/topic1.md)
- [topic 2](../reading-lists/topic2.md)
</content></create>


PHASE 4: KNOWLEDGE MAINTENANCE


step 1: regular review schedule

daily:
  - [ ] update today's learnings
  - [ ] fix broken links found
  - [ ] add tags to new entries

weekly:
  - [ ] update indices
  - [ ] review entries from this week
  - [ ] make connections between new entries

monthly:
  - [ ] identify outdated entries
  - [ ] consolidate duplicates
  - [ ] update trend summaries

quarterly:
  - [ ] full knowledge base audit
  - [ ] reorganize for better structure
  - [ ] archive obsolete knowledge


step 2: identify outdated entries

find entries needing updates:

  <terminal>find ~/knowledge -name "*.md" -mtime +90 -exec grep -l "last updated" {} \;</terminal>

  for each outdated entry:
    [ ] check if still accurate
    [ ] update information
    [ ] update "last updated" date
    [ ] or mark as deprecated


step 3: consolidate duplicates

find duplicate topics:

  <terminal>find ~/knowledge -name "*.md" | xargs grep -l "^# [similar topic]"</terminal>

for duplicates:
  [ ] merge content
  [ ] keep best information
  [ ] update all references
  [ ] delete duplicates


step 4: fix broken links

find broken references:

  <terminal>find ~/knowledge -name "*.md" -exec grep -o '\]\([^(]*\)' {} \; | sort -u | while read link; do
    path=$(echo "$link" | sed 's/.\(.*\)./\1/')
    if [ ! -f "~/knowledge/$path" ]; then
      echo "broken: $link in $(grep -l "$link" ~/knowledge/*.md)"
    fi
  done</terminal>

fix or remove broken links


PHASE 5: KNOWLEDGE UTILIZATION


step 1: just-in-time learning

when facing a problem:

  1. search existing knowledge
     <terminal>rg "problem keyword" ~/knowledge</terminal>

  2. if found, apply solution
     - follow documented approach
     - update with learnings

  3. if not found, learn
     - research solution
     - document findings
     - capture for future


step 2: project support

before starting a project:

  <terminal>rg "[relevant tech]" ~/knowledge/technologies --type md -l</terminal>

  <terminal>rg "[relevant pattern]" ~/knowledge/patterns --type md -l</terminal>

  compile reading list of relevant knowledge

during project:

  - document new learnings
  - capture patterns discovered
  - note decisions made

after project:

  - extract reusable patterns
  - document lessons learned
  - update technology entries


step 3: share knowledge

create summaries for others:

  <create><file>~/knowledge/shared/<topic>-guide.md</file><content># Guide: <topic>

for: [who is this for]
by: tech-dude
date: <date>

## quick start

[fastest way to get value from this topic]

## essentials

only what you need to know:
- [essential 1]
- [essential 2]
- [essential 3]

## examples

practical examples:
```language
// code
```

## common mistakes

what to avoid:
- [mistake 1]: how to avoid
- [mistake 2]: how to avoid

## further resources

if you want to go deeper:
- [resource 1](url)
- [resource 2](url)
</content></create>


PHASE 6: MANDATORY KNOWLEDGE RULES


while this skill is active, these rules are MANDATORY:

  [1] CAPTURE IMMEDIATELY
      insights fade quickly
      document when fresh
      don't rely on memory

  [2] WRITE FOR FUTURE MARCO
      assume you'll forget details
      include context and reasoning
      explain the "why" not just "what"

  [3] CONNECT EVERYTHING
      knowledge in isolation is weak
      link related topics
      create knowledge graphs

  [4] BE CONSISTENT
      use standard formats
      consistent tagging
      predictable structure

  [5] REVIEW REGULARLY
      knowledge decays
      schedule reviews
      keep it current

  [6] CONSOLIDATE DUPLICATES
      redundant knowledge creates confusion
      merge when found
      keep single source of truth

  [7] MAKE IT ACTIONABLE
      theoretical knowledge has limited value
      include examples
      show how to apply

  [8] TAG MEANINGFULLY
      tags enable discovery
      use consistent taxonomy
      limit to essential tags

  [9] DOCUMENT SOURCES
      know where knowledge came from
      track credibility
      enable revisiting

  [10] SHARE VALUABLE KNOWLEDGE
      if it helped tech-dude, it might help others
      create guides
      contribute back


FINAL REMINDERS


knowledge is only valuable if used

don't just collect
apply to projects
solve real problems


your future self will thank you

the tech-dude of 6 months from now
will appreciate good documentation
invest in future you


building knowledge is a journey

start with capture
connect with organization
maintain with discipline

now go document something valuable.