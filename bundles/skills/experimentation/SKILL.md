---
name: experimentation
description: "Experimentation skill - create, run, and document technology experiments"
---

experimentation mode: HANDS-ON DISCOVERY

when this skill is active, you systematically create and run experiments.
this is a comprehensive guide to learning by doing with minimal risk.


PHASE 0: EXPERIMENT ENVIRONMENT SETUP


verify experiment infrastructure

  <terminal>mkdir -p ~/experiments</terminal>
  <terminal>ls -la ~/experiments</terminal>

  <terminal>mkdir -p ~/experiments/active</terminal>
  <terminal>mkdir -p ~/experiments/completed</terminal>
  <terminal>mkdir -p ~/experiments/failed</terminal>

  <terminal>mkdir -p ~/experiments/templates</terminal>


create experiment template

  <create><file>~/experiments/templates/README.md</file><content># [experiment-name]

purpose: [clear statement of what we're testing]
hypothesis: [what we expect to learn]
status: [planned/in-progress/completed/failed/abandoned]

## metadata

created: <date>
started: <date>
completed: <date>
estimated effort: [hours]
actual effort: [hours]

## context

why this experiment:
  - [what sparked interest]
  - [problem it might solve]

questions to answer:
  - [question 1]
  - [question 2]
  - [question 3]

## experiment design

variables:
  - independent: [what we change]
  - dependent: [what we measure]
  - controlled: [what stays constant]

success criteria:
  - [criterion 1]
  - [criterion 2]

## setup

steps:
  [1] [setup step 1]
  [2] [setup step 2]
  [3] [setup step 3]

dependencies:
  - [package/tool 1]
  - [package/tool 2]

## execution

phase 1: [name]
  - [what we did]
  - [what we observed]
  - [time spent]

phase 2: [name]
  - [what we did]
  - [what we observed]
  - [time spent]

## findings

what worked:
  - [working aspect 1]
  - [working aspect 2]

what didn't:
  - [failing aspect 1]
  - [failing aspect 2]

surprises:
  - [unexpected discovery 1]
  - [unexpected discovery 2]

## measurements

quantitative data:
  - [metric 1]: [value]
  - [metric 2]: [value]

qualitative observations:
  - [observation 1]
  - [observation 2]

## code

key files:
  - [file]: purpose
  - [file]: purpose]

## conclusions

hypothesis validated: [yes/no/partial]

answers to questions:
  - [question 1]: [answer]
  - [question 2]: [answer]
  - [question 3]: [answer]

recommendations:
  - [recommendation 1]
  - [recommendation 2]

## next steps

if continuing:
  - [next experiment]

if adopting:
  - [integration steps]

if abandoning:
  - [why not useful]

## lessons learned

technical:
  - [technical lesson 1]
  - [technical lesson 2]

process:
  - [what we'd do differently]
  - [what worked well]

## artifacts

screenshots: [link]
logs: [link]
data: [link]

## references

documentation: [link]
inspiration: [link]
related experiments: [link]
</content></create>


verify tools for experiments

  <terminal>which git</terminal>
  <terminal>which node</terminal>
  <terminal>which python</terminal>
  <terminal>which docker</terminal>

initialize version control for experiments:

  <create><file>~/experiments/.gitignore</file><content># node_modules
node_modules/

# python
__pycache__/
*.pyc
venv/
.venv/

# environment
.env
.env.local

# ide
.vscode/
.idea/

# logs
*.log

# build artifacts
dist/
build/

# temp files
*.tmp
.DS_Store
</content></create>

  <terminal>cd ~/experiments && git init</terminal>


PHASE 1: EXPERIMENT INITIATION


step 1: define experiment hypothesis

answer:
  - what specific question are we answering?
  - what do we expect to find?
  - how will we know if it worked?

example hypotheses:
  - "hypothesis: [tool] can reduce build time by [X]%"
  - "hypothesis: [framework] reduces boilerplate by [X]%"
  - "hypothesis: [pattern] simplifies [problem]"


step 2: scope the experiment

define boundaries:
  - minimal viable experiment: what's the smallest test that answers the question?
  - timebox: maximum hours to invest before re-evaluating
  - stop conditions: what would make us stop early?

example scoping:
  - minimal: hello world + one realistic use case
  - timebox: 2 hours initial, then re-evaluate
  - stop: if setup takes > 30 minutes, abort


step 3: create experiment directory

  <terminal>mkdir -p ~/experiments/active/<experiment-name></terminal>

  <terminal>cd ~/experiments/active/<experiment-name> && cp ~/experiments/templates/README.md .</terminal>

  <terminal>cd ~/experiments/active/<experiment-name> && git init</terminal>


step 4: initialize project structure

for javascript/node:
  <terminal>cd ~/experiments/active/<experiment-name> && npm init -y</terminal>

  <create><file>~/experiments/active/<experiment-name>/package.json</file><content>{
  "name": "<experiment-name>",
  "version": "0.0.1",
  "description": "experiment: [purpose]",
  "main": "index.js",
  "scripts": {
    "start": "node index.js",
    "test": "echo \"no tests specified\" && exit 0"
  },
  "keywords": ["experiment"],
  "author": "tech-dude",
  "license": "MIT"
}
</content></create>

for python:
  <terminal>cd ~/experiments/active/<experiment-name> && python -m venv venv</terminal>
  <terminal>cd ~/experiments/active/<experiment-name> && source venv/bin/activate && pip install --upgrade pip</terminal>

  <create><file>~/experiments/active/<experiment-name>/requirements.txt</file><content># experiment dependencies
# add packages as needed
</content></create>

  <create><file>~/experiments/active/<experiment-name>/.python-version</file><content>3.11
</content></create>


step 5: commit initial structure

  <terminal>cd ~/experiments/active/<experiment-name> && git add .</terminal>
  <terminal>cd ~/experiments/active/<experiment-name> && git commit -m "init: initialize experiment"</terminal>


PHASE 2: MINIMAL VIABLE IMPLEMENTATION


step 1: hello world

create the simplest possible example:

  <create><file>~/experiments/active/<experiment-name>/index.js</file><content>
// hello world for <tech>
// purpose: verify basic setup

console.log('<tech> hello world');
</content></create>

  <terminal>cd ~/experiments/active/<experiment-name> && npm install <package></terminal>

  <terminal>cd ~/experiments/active/<experiment-name> && npm start</terminal>

verify it runs:
  [ ] no errors
  [ ] expected output
  [ ] understand what happened


step 2: add one realistic use case

implement the simplest real scenario:

  <create><file>~/experiments/active/<experiment-name>/example.js</file><content>
// realistic use case 1
// purpose: test actual functionality

const <Tech> = require('<tech>');

async function main() {
  // setup
  const instance = new <Tech>();
  
  // execute
  const result = await instance.doSomething();
  
  // verify
  console.log('result:', result);
}

main().catch(console.error);
</content></create>

  <terminal>cd ~/experiments/active/<experiment-name> && node example.js</terminal>


step 3: measure baseline

document performance/complexity:

  <create><file>~/experiments/active/<experiment-name>/benchmark.js</file><content>
// baseline measurement
// purpose: measure performance/complexity

const <Tech> = require('<tech>');

console.time('operation');
const result = <Tech>.method();
console.timeEnd('operation');

console.log('memory:', process.memoryUsage());
console.log('result:', result);
</content></create>

  <terminal>cd ~/experiments/active/<experiment-name> && node benchmark.js</terminal>


step 4: document initial findings

update experiment readme:

  <edit><file>~/experiments/active/<experiment-name>/README.md</file><find>## execution</find><replace>## execution

phase 1: hello world
  - setup time: [X] minutes
  - errors encountered: [none/list]
  - first run success: yes/no

phase 2: realistic use case
  - implementation time: [X] minutes
  - worked as expected: yes/no
  - unexpected behavior: [description]

## execution</replace></edit>

  <terminal>cd ~/experiments/active/<experiment-name> && git add .</terminal>
  <terminal>cd ~/experiments/active/<experiment-name> && git commit -m "feat: implement minimal example"</terminal>


PHASE 3: ITERATIVE EXPLORATION


step 1: expand to multiple use cases

add 2-3 more examples:

  <create><file>~/experiments/active/<experiment-name>/examples/use-case-2.js</file><content>
// use case 2: [description]
// testing: [what we're testing]

const <Tech> = require('<tech>');

// implementation
</content></create>

  <create><file>~/experiments/active/<experiment-name>/examples/use-case-3.js</file><content>
// use case 3: [description]
// testing: [what we're testing]

const <Tech> = require('<tech>');

// implementation
</content></create>


step 2: stress test

push boundaries:

  <create><file>~/experiments/active/<experiment-name>/stress-test.js</file><content>
// stress test
// purpose: find limits and edge cases

const <Tech> = require('<tech>');

async function stressTest() {
  console.log('starting stress test...');
  
  // test 1: large input
  console.log('test 1: large input');
  const largeInput = /* generate large data */;
  console.time('large');
  const largeResult = <Tech>.process(largeInput);
  console.timeEnd('large');
  
  // test 2: many requests
  console.log('\ntest 2: concurrent requests');
  const promises = [];
  for (let i = 0; i < 100; i++) {
    promises.push(<Tech>.process(i));
  }
  await Promise.all(promises);
  console.log('completed 100 concurrent requests');
  
  // test 3: edge cases
  console.log('\ntest 3: edge cases');
  const edgeCases = [null, undefined, '', [], {}];
  for (const testCase of edgeCases) {
    try {
      const result = <Tech>.process(testCase);
      console.log(`${testCase}: ${result}`);
    } catch (e) {
      console.log(`${testCase}: ERROR - ${e.message}`);
    }
  }
}

stressTest().catch(console.error);
</content></create>

  <terminal>cd ~/experiments/active/<experiment-name> && node stress-test.js</terminal>


step 3: compare with alternatives

if applicable, test competing tools:

  <create><file>~/experiments/active/<experiment-name>/comparison/alternative-a.js</file><content>
// alternative a implementation
// same use case as <tech>

const AlternativeA = require('alternative-a');

// implement same logic
</content></create>

  <create><file>~/experiments/active/<experiment-name>/comparison/compare.js</file><content>
// comparison benchmark
// purpose: compare <tech> vs alternatives

const <Tech> = require('<tech>');
const AlternativeA = require('alternative-a');

console.log('comparing implementations...\n');

console.log('<tech>:');
console.time('<tech>');
<Tech>.run();
console.timeEnd('<tech>');

console.log('\nAlternative A:');
console.time('alternative-a');
AlternativeA.run();
console.timeEnd('alternative-a');
</content></create>

  <terminal>cd ~/experiments/active/<experiment-name> && node comparison/compare.js</terminal>


step 4: document learnings

update readme with findings:

  <edit><file>~/experiments/active/<experiment-name>/README.md</file><find>## findings</find><replace>## findings

what worked:
  - [feature 1]: worked well, intuitive api
  - [feature 2]: performed as expected

what didn't:
  - [issue 1]: confusing error messages
  - [issue 2]: missing documentation

surprises:
  - [unexpected 1]: [discovery]
  - [unexpected 2]: [discovery]

## findings</replace></edit>


PHASE 4: EVALUATION AND DECISION


step 1: evaluate against criteria

review original hypothesis:

  - did we answer our questions?
  - did we learn what we expected?
  - was it worth the time?

score the technology:

  ease of setup: [1-10]
  documentation quality: [1-10]
  api design: [1-10]
  performance: [1-10]
  community support: [1-10]
  overall: [X]/50


step 2: decision framework

adopt if:
  - solves real problem
  - easy to integrate
  - well maintained
  - clear benefit over current solution

experiment further if:
  - promising but needs more exploration
  - uncertain about long-term viability
  - competing alternatives unclear

abandon if:
  - doesn't solve problem
  - too complex for benefit
  - poor documentation
  - unmaintained


step 3: create decision document

  <create><file>~/experiments/active/<experiment-name>/DECISION.md</file><content># Experiment Decision: <experiment-name>

date: <date>
decision: [adopt/experiment-further/abandon]

## summary

experimented with: [technology]
time invested: [X] hours
hypothesis: [original hypothesis]

## evaluation

### what we learned

positive findings:
  - [positive 1]
  - [positive 2]

negative findings:
  - [negative 1]
  - [negative 2]

### scoring

criteria                score    notes
---------------------- ------   -----
ease of setup           [X]/10   [notes]
documentation           [X]/10   [notes]
api design              [X]/10   [notes]
performance             [X]/10   [notes]
community               [X]/10   [notes]
---------------------- ------   -----
total                   [X]/50

## decision

[adopt/experiment-further/abandon]

reasoning:
  - [reason 1]
  - [reason 2]
  - [reason 3]

## next steps

if adopting:
  - [integration step 1]
  - [integration step 2]
  - target project: [which project]

if experimenting further:
  - [what to try next]
  - [when to re-evaluate]

if abandoning:
  - [why not useful]
  - [alternative approaches]

## lessons

for tech-dude:
  - [lesson 1]
  - [lesson 2]

for future experiments:
  - [what we'd do differently]
</content></create>


step 4: archive or move

if completed:

  <terminal>mv ~/experiments/active/<experiment-name> ~/experiments/completed/</terminal>

if failed:

  <terminal>mv ~/experiments/active/<experiment-name> ~/experiments/failed/</terminal>

update experiment registry:

  <edit><file>~/experiments/registry.md</file><find>## completed experiments</find><replace>- [<experiment-name>](completed/<experiment-name>/README.md) - [summary] - <date>
## completed experiments</replace></edit>


PHASE 5: DOCUMENTATION AND SHARING


step 1: create summary report

  <create><file>~/knowledge/experiments/<experiment-name>-summary.md</file><content># <experiment-name> Summary

experiment: <experiment-name>
completed: <date>
decision: [adopt/abandon]

## what we tested

[brief description of technology/experiment]

## key findings

working:
  - [finding 1]
  - [finding 2]

not working:
  - [finding 1]

## recommendations

for tech-dude:
  - [recommendation 1]
  - [recommendation 2]

for others:
  - [general insight]

## code examples

```javascript
// most useful snippet
```

## resources

experiment location: ~/experiments/[completed/failed]/<experiment-name>/

related knowledge:
  - [link 1]
  - [link 2]
</content></create>


step 2: update knowledge base

if adopting:

  <create><file>~/knowledge/technologies/<tech>-guide.md</file><content># <tech> Usage Guide

adopted: <date>

## quick start

install:
  ```bash
  npm install <package>
  ```

basic usage:
  ```javascript
  const <Tech> = require('<tech>');
  // code
  ```

## patterns we use

pattern 1: [name]
  ```javascript
  // example
  ```

pattern 2: [name]
  ```javascript
  // example
  ```

## gotchas

  - [gotcha 1]
  - [gotcha 2]

## tech-dude's projects using this

  - [project 1]: [how used]
  - [project 2]: [how used]

## related experiments

  - [<experiment>](~/experiments/completed/<experiment>/)
</content></create>


step 3: share with community (optional)

create gist or blog post if valuable:

  <terminal>gh gist create ~/experiments/completed/<experiment-name>/DECISION.md -d "experiment: <experiment-name>"</terminal>


PHASE 6: EXPERIMENT CHECKLIST


before starting:
  [ ] clear hypothesis defined
  [ ] scope and timebox set
  [ ] success criteria established
  [ ] stop conditions identified

during experiment:
  [ ] hello world works
  [ ] at least one realistic use case implemented
  [ ] baseline measurements taken
  [ ] notes taken at each step
  [ ] commits made regularly

after experiment:
  [ ] findings documented
  [ ] decision made
  [ ] lessons learned captured
  [ ] knowledge base updated
  [ ] experiment archived


PHASE 7: MANDATORY EXPERIMENTATION RULES


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS START WITH HYPOTHESIS
      experiments must answer specific questions
      random exploration wastes time

  [2] TIMEBOX EVERY EXPERIMENT
      set time limits before starting
      re-evaluate at time limit

  [3] START MINIMAL
      hello world first
      then realistic use case
      then expand if valuable

  [4] DOCUMENT AS YOU GO
      don't wait until the end
      capture insights when fresh
      commit frequently

  [5] MEASURE SOMETHING
      qualitative observations are good
      quantitative data is better
      both together is best

  [6] COMPARE WITH ALTERNATIVES
      understanding relative value
      requires comparison
      not just absolute assessment

  [7] ARCHIVE PROPERLY
      completed → ~/experiments/completed/
      failed → ~/experiments/failed/
      keep context for future reference

  [8] EXTRACT LESSONS
      failure is learning
      document what didn't work
      it's as valuable as successes

  [9] MAKE DECISIONS
      don't just experiment forever
      adopt, continue, or abandon
      move forward

  [10] SHARE KNOWLEDGE
      update tech-dude's knowledge base
      make findings discoverable
      connect to related topics


FINAL REMINDERS


experiments are for learning

you're not building production code
you're building understanding


fail fast, fail forward

quick failure = saved time
documented failure = shared knowledge


minimal is powerful

hello world → realistic case → explore
stop at each stage and evaluate
don't over-invest early


now go build something and learn from it.