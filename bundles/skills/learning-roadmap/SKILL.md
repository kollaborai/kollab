---
name: learning-roadmap
description: "Learning Roadmap skill - create structured learning plans for new technologies"
---

learning-roadmap mode: CURRICULUM DESIGN

when this skill is active, you create comprehensive learning roadmaps.
this is a comprehensive guide to mastering new technologies systematically.


PHASE 0: LEARNING INFRASTRUCTURE SETUP


verify learning environment

  <terminal>mkdir -p ~/learning-plans</terminal>
  <terminal>mkdir -p ~/learning-plans/active</terminal>
  <terminal>mkdir -p ~/learning-plans/completed</terminal>
  <terminal>mkdir -p ~/learning-plans/paused</terminal>

  <terminal>ls -la ~/learning-plans</terminal>


create learning templates

  <create><file>~/learning-plans/templates/roadmap.md</file><content># [technology] Learning Roadmap

created: <date>
target completion: <date>
status: [not-started/in-progress/completed/on-hold]

## overview

what: [brief description of technology]
why: [why tech-dude wants to learn this]
difficulty: [beginner/intermediate/advanced]
estimated time: [X] weeks
priority: [high/medium/low]

## prerequisites

knowledge:
  [ ] [prerequisite 1]
  [ ] [prerequisite 2]

tools:
  [ ] [required tool 1]
  [ ] [required tool 2]

environment:
  [ ] [environment requirement 1]

## learning objectives

by the end of this roadmap, tech-dude will be able to:

  [ ] [objective 1 - specific, measurable]
  [ ] [objective 2 - specific, measurable]
  [ ] [objective 3 - specific, measurable]

  project: build [specific project as capstone]

## phase 1: fundamentals (week 1)

### goals
  - understand core concepts
  - complete hello world
  - learn basic syntax/idioms

### resources
  - [resource 1 - documentation]
  - [resource 2 - tutorial]
  - [resource 3 - video course]

### exercises
  [ ] exercise 1: [specific task]
  [ ] exercise 2: [specific task]
  [ ] exercise 3: [specific task]

### deliverables
  - [ ] completed exercises
  - [ ] notes on core concepts
  - [ ] working hello world example

### time allocation
  - reading: [X] hours
  - exercises: [Y] hours
  - review: [Z] hours

## phase 2: practical application (week 2)

### goals
  - build first real project
  - understand common patterns
  - learn debugging workflow

### resources
  - [resource 1]
  - [resource 2]

### exercises
  [ ] mini-project 1: [description]
  [ ] mini-project 2: [description]
  [ ] mini-project 3: [description]

### deliverables
  - [ ] working mini-projects
  - [ ] documented code
  - ] reflection notes

### time allocation
  - reading: [X] hours
  - coding: [Y] hours
  - debugging: [Z] hours

## phase 3: deep dive (week 3)

### goals
  - explore advanced features
  - understand best practices
  - learn optimization techniques

### resources
  - [resource 1]
  - [resource 2]

### exercises
  [ ] advanced exercise 1: [description]
  [ ] advanced exercise 2: [description]
  [ ] performance tuning: [description]

### deliverables
  - [ ] advanced examples
  - [ ] performance benchmarks
  - [ ] best practices checklist

### time allocation
  - reading: [X] hours
  - practice: [Y] hours
  - research: [Z] hours

## phase 4: mastery (week 4)

### goals
  - build capstone project
  - teach/explain to others
  - contribute back to community

### resources
  - [resource 1]
  - [resource 2]

### exercises
  [ ] capstone project: [description]
  [ ] documentation: write tutorial/blog post
  [ ] contribution: open issue or pr

### deliverables
  - [ ] completed capstone project
  - [ ] published explanation/tutorial
  - [ ] community contribution

### time allocation
  - project: [X] hours
  - writing: [Y] hours
  - contribution: [Z] hours

## progress tracking

### week 1 status
  started: <date>
  completed: <date>
  hours invested: [X]

  exercises:
    [ ] exercise 1
    [ ] exercise 2
    [ ] exercise 3

  notes:
    [key insights]
    [struggles encountered]
    [adjustments needed]

### week 2 status
  [same structure]

### week 3 status
  [same structure]

### week 4 status
  [same structure]

## capstone project

name: [project name]
description: [what it does]
tech stack: [technologies used]

features:
  - [feature 1]
  - [feature 2]
  - [feature 3]

implementation timeline:
  - week 1: [milestone]
  - week 2: [milestone]
  - week 3: [milestone]
  - week 4: [milestone]

## resources by type

### official documentation
  - [link]

### books
  - [book] - [author]

### courses
  - [course] - [platform]

### tutorials
  - [tutorial] - [source]

### videos
  - [video] - [creator]

### community
  - [discord/slack]
  - [reddit]
  - [stackoverflow tag]

## notes section

### key concepts

concept 1: [name]
  - definition
  - why it matters
  - when to use

concept 2: [name]
  - [same structure]

### common patterns

pattern 1: [name]
  ```code
  example
  ```

pattern 2: [name]
  - usage context

### gotchas and pitfalls

  - [gotcha 1]: how to avoid
  - [gotcha 2]: how to avoid

## assessment rubric

knowledge:
  [ ] can explain concepts clearly
  [ ] knows when to use [technology]
  [ ] understands tradeoffs

skills:
  [ ] can build working projects
  [ ] can debug common issues
  [ ] can optimize performance

application:
  [ ] applies to real problems
  [ ] adapts patterns to context
  [ ] integrates with existing stack

## final reflection

what went well:
  - [success 1]
  - [success 2]

what was challenging:
  - [challenge 1]
  - [challenge 2]

what would i do differently:
  - [improvement 1]
  - [improvement 2]

next steps:
  - [advanced topic 1]
  - [related technology 2]
  - [project idea 3]

## connections

related technologies:
  - [tech 1]: [relationship]
  - [tech 2]: [relationship]

applicable projects:
  - [project 1]: [how used]
  - [project 2]: [how used]

prerequisite for:
  - [advanced topic 1]
  - [advanced topic 2]
</content></create>


create learning journal template

  <create><file>~/learning-plans/templates/journal.md</file><content># Learning Journal: [technology]

## day 1: <date>

topic: [what learned]

reading: [X] hours
practice: [Y] hours

key concepts:
  - [concept 1]
  - [concept 2]

exercises completed:
  [ ] [exercise 1]
  [ ] [exercise 2]

code written:
  ```language
  snippet
  ```

notes/insights:
  - [insight 1]
  - [insight 2]

struggles:
  - [struggle 1]
  - [struggle 2]

questions for further research:
  - [question 1]
  - [question 2]

## day 2: <date>
[repeat structure]
</content></create>


PHASE 1: ASSESS CURRENT STATE


step 1: evaluate tech-dude's existing knowledge

check related knowledge:

  <terminal>rg "<related-technology>" ~/knowledge --type md -l</terminal>

  <terminal>rg "<related-technology>" ~/learning-plans --type md</terminal>

assess skill level:
  - beginner: no prior experience
  - intermediate: some related knowledge
  - advanced: deep experience in related area


step 2: identify knowledge gaps

what tech-dude doesn't know:
  - [gap 1]
  - [gap 2]
  - [gap 3]

what tech-dude wants to do:
  - [goal 1]
  - [goal 2]

connect goals to learning:
  - to achieve [goal], tech-dude needs to learn [topic]


step 3: check prerequisites

verify required knowledge:

  <terminal>rg "[prerequisite]" ~/knowledge --type md | wc -l</terminal>

if prerequisites missing, create prerequisite roadmaps:
  - [prerequisite]: [action plan]


step 4: set learning goals

specific, measurable goals:
  - "be able to build X" not "learn Y"
  - "understand Z patterns" not "know about Z"
  - "complete project A" not "practice coding"

define success criteria:
  - can explain concepts to others
  - can build working project
  - can debug own code


PHASE 2: RESOURCE DISCOVERY


step 1: find official documentation

  <terminal>curl -s "https://<tech>.org/docs" | grep -o 'href="[^"]*"' | head -20</terminal>

  <terminal>gh search repos --language <lang> "<tech> documentation"</terminal>

evaluate documentation:
  - completeness
  - examples
  - tutorials
  - api reference


step 2: find learning resources

books:
  <terminal>gh search repos --language <lang> "<tech> book" --stars ">100"</terminal>

  <terminal>curl -s "https://www.goodreads.com/search?q=<tech>" | grep -o 'title":"[^"]*"' | head -10</terminal>

courses:
  <terminal>curl -s "https://www.udemy.com/api-2.0/search-courses?q=<tech>" | grep -o '"title":"[^"]*"' | head -10</terminal>

  <terminal>curl -s "https://www.coursera.org/search?query=<tech>" | grep -o 'title":"[^"]*"' | head -10</terminal>

tutorials:
  <terminal>gh search repos --language <lang> "<tech> tutorial" --stars ">50"</terminal>

videos:
  <terminal>curl -s "https://www.youtube.com/results?search_query=<tech>+tutorial" | grep -o 'videoId":"[^"]*"' | head -10</terminal>


step 3: find practice resources

exercises:
  <terminal>gh search repos --language <lang> "<tech> exercises"</terminal>

  <terminal>gh search repos --language <lang> "<tech> challenges"</terminal>

projects:
  <terminal>gh search repos --language <lang> "<tech> projects" --stars ">10"</terminal>

build-in-public:
  <terminal>rg "<tech>" ~/knowledge/trends --type md | head -20</terminal>


step 4: evaluate and select resources

criteria for selection:
  - up to date
  - comprehensive
  - practical examples
  - good reviews
  - fits learning style

select top 3-5 resources:
  - primary: [main resource]
  - backup: [alternative resource]
  - reference: [documentation]


PHASE 3: CURRICULUM DESIGN


step 1: structure learning phases

divide into progressive phases:

phase 1: foundations
  - core concepts
  - basic syntax
  - mental models

phase 2: practical skills
  - common patterns
  - debugging
  - tooling

phase 3: advanced topics
  - optimization
  - best practices
  - edge cases

phase 4: mastery
  - capstone project
  - teaching others
  - community contribution


step 2: define each phase

for each phase:
  - goals (what to achieve)
  - resources (what to use)
  - exercises (what to build)
  - deliverables (what to produce)
  - time allocation (how long)

example phase design:

  phase 1: foundations (week 1)
    goals:
      - understand [concept 1]
      - understand [concept 2]
    
    resources:
      - chapters 1-5 of [book]
      - official docs sections [X, Y]
    
    exercises:
      - exercise 1: build [simple thing]
      - exercise 2: implement [pattern]
    
    deliverables:
      - working examples
      - notes on concepts
    
    time: 10 hours


step 3: design capstone project

project criteria:
  - uses core concepts
  - solves real problem
  - can be portfolio piece
  - achievable in timeframe

project structure:

  <create><file>~/learning-plans/active/<tech>-capstone/README.md</file><content># Capstone Project: [name]

purpose: demonstrate mastery of <tech>

## overview

what it does:
  [description]

why it's valuable:
  [value proposition]

## features

must-have:
  [ ] [feature 1]
  [ ] [feature 2]

nice-to-have:
  [ ] [feature 3]
  [ ] [feature 4]

## tech stack

- [technology 1]: [purpose]
- [technology 2]: [purpose]

## learning objectives

this project demonstrates:
  - [objective 1]
  - [objective 2]
  - [objective 3]

## implementation plan

week 1:
  [ ] milestone 1

week 2:
  [ ] milestone 2

week 3:
  [ ] milestone 3

week 4:
  [ ] milestone 4

## deliverables

- [ ] working application
- [ ] documentation
- [ ] deployment
- [ ] write-up/reflection
</content></create>


step 4: create roadmap document

  <terminal>cp ~/learning-plans/templates/roadmap.md ~/learning-plans/active/<tech>-roadmap.md</terminal>

  <edit><file>~/learning-plans/active/<tech>-roadmap.md</file><find># \[technology\] Learning Roadmap</find><replace># <tech> Learning Roadmap</replace></edit>

fill in all sections with specific content


PHASE 4: LEARNING SCHEDULING


step 1: estimate time requirements

break down by phase:

  phase 1: [X] hours
    - reading: [Y] hours
    - exercises: [Z] hours

  phase 2: [X] hours
    - [breakdown]

  phase 3: [X] hours
    - [breakdown]

  phase 4: [X] hours
    - [breakdown]

total: [X] hours


step 2: create weekly schedule

example schedule:

  week 1: foundations
    monday: 2 hours - reading + notes
    tuesday: 2 hours - exercises
    wednesday: 2 hours - exercises
    thursday: 2 hours - review + reflection
    friday: 2 hours - catch up or deep dive

  [repeat for each week]


step 3: set check-in points

define progress markers:

  end of week 1:
    [ ] can explain core concepts
    [ ] completed all exercises
    [ ] journal updated

  end of week 2:
    [ ] built mini-project
    [ ] understands patterns
    [ ] can debug basic issues

  [repeat for each week]


step 4: create learning journal

  <terminal>cp ~/learning-plans/templates/journal.md ~/learning-plans/active/<tech>-journal.md</terminal>


PHASE 5: EXECUTION AND TRACKING


step 1: daily learning workflow

daily routine:

  1. review yesterday's notes
     <read><file>~/learning-plans/active/<tech>-journal.md</file></read>

  2. set learning goal for today
     specific, achievable goal

  3. learning session
     - reading/watching
     - note-taking
     - practice

  4. journal entry
     - what learned
     - what built
     - questions

  5. review progress
     - check off completed items
     - update roadmap


step 2: weekly review

end of each week:

  <terminal>rg "completed:" ~/learning-plans/active/<tech>-roadmap.md</terminal>

reflect on:
  - what went well
  - what was hard
  - what to adjust

adjust plan if needed:
  - extend difficult sections
  - compress easy sections
  - add resources if stuck


step 3: track metrics

monitor progress:

  concepts learned: [X]
  hours invested: [Y]
  exercises completed: [Z]
  projects built: [W]

compare to plan:
  - on track?
  - ahead/behind?
  - what adjustment needed?


step 4: handle roadblocks

when stuck:

  [1] take a break
      sometimes fresh eyes help

  [2] try different resource
      another explanation might click

  [3] ask community
      reddit, discord, stackoverflow

  [4] reduce scope
      learn simpler concept first

  [5] skip and return
      sometimes later context helps

document roadblocks:

  <edit><file>~/learning-plans/active/<tech>-journal.md</file><find>## roadblocks</find><replace>## roadblocks

### [date]: [topic]
  - what was hard: [description]
  - how i overcame it: [approach]
  - lesson learned: [insight]
</replace></edit>


PHASE 6: ASSESSMENT AND COMPLETION


step 1: self-assessment

at end of each phase:

  can i explain concepts?
    - teach to rubber duck
    - write explanation
    - record video

  can i apply concepts?
    - build something from scratch
    - solve new problem
    - debug unfamiliar code

  do i understand tradeoffs?
    - when to use vs when not to
    - advantages and disadvantages
    - alternatives


step 2: capstone project evaluation

against objectives:
  [ ] uses core concepts
  [ ] solves real problem
  [ ] demonstrates mastery

code quality:
  [ ] follows best practices
  [ ] well documented
  [ ] tested

project completeness:
  [ ] all features working
  [ ] user documentation
  [ ] deployed if applicable


step 3: final reflection

complete reflection section:

  <edit><file>~/learning-plans/active/<tech>-roadmap.md</file><find>## final reflection</find><replace>## final reflection

completed: <date>
total hours: [X]

what went well:
  - [success 1]
  - [success 2]

what was challenging:
  - [challenge 1]
  - [challenge 2]

what i would do differently:
  - [improvement 1]
  - [improvement 2]

biggest insights:
  - [insight 1]
  - [insight 2]

confidence level: [beginner/intermediate/advanced]

## final reflection</replace></edit>


step 4: archive and update knowledge

move to completed:

  <terminal>mv ~/learning-plans/active/<tech>-roadmap.md ~/learning-plans/completed/</terminal>

  <terminal>mv ~/learning-plans/active/<tech>-journal.md ~/learning-plans/completed/</terminal>

create knowledge entry:

  <create><file>~/knowledge/technologies/<tech>-mastery.md</file><content># <tech> Mastery Summary

completed: <date>
level: [beginner/intermediate/advanced]

## what i learned

core concepts:
  - [concept 1]
  - [concept 2]

practical skills:
  - [skill 1]
  - [skill 2]

best practices:
  - [practice 1]
  - [practice 2]

## projects built

  - [project 1]: [link/description]
  - [project 2]: [link/description]
  - [capstone]: [link/description]

## resources that helped

  - [resource 1] - [why helpful]
  - [resource 2] - [why helpful]

## when to use

best for:
  - [use case 1]
  - [use case 2]

avoid for:
  - [use case 3]

## common patterns

pattern 1: [name]
  ```code
  example
  ```

pattern 2: [name]
  - usage context

## gotchas

  - [gotcha 1]: how to avoid
  - [gotcha 2]: how to avoid

## next steps

advanced topics:
  - [topic 1]
  - [topic 2]

related technologies:
  - [tech 1]: [why related]
  - [tech 2]: [why related]
</content></create>


PHASE 7: MANDATORY LEARNING RULES


while this skill is active, these rules are MANDATORY:

  [1] START WITH CLEAR GOALS
      specific objectives over vague "learn X"
      define success before starting
      know when you're done

  [2] BUILD REAL THINGS
      theory without practice = forgotten
      exercises > reading
      projects > tutorials

  [3] DOCUMENT AS YOU LEARN
      journal daily
      capture insights immediately
      create reference material

  [4] PRACTICE ACTIVE RECALL
      don't just re-read
      test yourself
      teach to others

  [5] GET HANDS DIRTY
      break things
      debug errors
      learn from failures

  [6] FOLLOW YOUR CURIOSITY
      pivot to interesting tangents
      explore what excites you
      depth > breadth

  [7] REVIEW REGULARLY
      weekly check-ins
      reflect on progress
      adjust plan as needed

  [8] CONNECT TO PROJECTS
      apply to real problems
      use in actual code
      build portfolio pieces

  [9] SHARE KNOWLEDGE
      write tutorials
      answer questions
      contribute back

  [10] CELEBRATE MILESTONES
      acknowledge progress
      recognize growth
      keep momentum


FINAL REMINDERS


learning is a journey

not a destination
enjoy the process
value the struggle


mastery takes time

don't rush
depth over speed
understanding over completion


you are capable

believe in yourself
take it one step at a time
you've done it before, you'll do it again

now go learn something amazing.