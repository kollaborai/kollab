---
name: tech-research
description: "Technology Research skill - systematic investigation of new technologies and tools"
---

tech-research mode: DEEP DIVE DISCOVERY

when this skill is active, you follow systematic technology research methodology.
this is a comprehensive guide to evaluating new technologies from multiple angles.


PHASE 0: RESEARCH ENVIRONMENT VERIFICATION

before researching ANY technology, verify your research tools are ready.


check internet connectivity and research sources

  <terminal>curl -s -o /dev/null -w "%{http_code}" https://www.google.com</terminal>

  <terminal>curl -s -o /dev/null -w "%{http_code}" https://api.github.com</terminal>

  <terminal>curl -s -o /dev/null -w "%{http_code}" https://www.npmjs.com</terminal>


verify github cli access

  <terminal>gh auth status</terminal>

if not authenticated:
  <terminal>gh auth login</terminal>


verify package manager access

  npm:
    <terminal>npm whoami</terminal>

  python:
    <terminal>pip --version</terminal>
    <terminal>pip search --help 2>&1 | grep -q "search" || echo "pip search disabled"</terminal>

  rust:
    <terminal>cargo --version</terminal>


verify knowledge base structure

  <terminal>mkdir -p ~/knowledge/technologies</terminal>
  <terminal>mkdir -p ~/knowledge/trends</terminal>
  <terminal>mkdir -p ~/knowledge/experiments</terminal>

  <terminal>ls -la ~/knowledge/</terminal>


check existing research

  <terminal>rg "research-log" ~/knowledge --type md -l</terminal>

  <terminal>find ~/knowledge -name "*research*" -type f</terminal>


PHASE 1: TECHNOLOGY DISCOVERY


step 1: initial broad search

search github for trending repositories:

  <terminal>gh search repos --language <lang> --stars ">1000" --sort stars --limit 20 "<keyword>"</terminal>

search npm/PyPI for packages:

  <terminal>npm search <keyword> --long</terminal>

  <terminal>pip search <keyword> 2>/dev/null || curl -s "https://pypi.org/search/?q=<keyword>" | grep -o 'package-link"[^>]*>[^<]*<' | head -20</terminal>


step 2: gather metadata

for each candidate technology, collect:

github statistics:
  <terminal>gh repo view <owner>/<repo></terminal>

  <terminal>gh api repos/<owner>/<repo>/stargazers --paginate | wc -l</terminal>

  <terminal>gh api repos/<owner>/<repo>/releases/latest --jq '.published_at, .tag_name'</terminal>

npm package details:
  <terminal>npm view <package> --json description,version,homepage,repository,keywords,license</terminal>

  <terminal>npm view <package> versions --json | tail -1</terminal>

  <terminal>npm view <package> time</terminal>


step 3: identify documentation sources

find official documentation:

  <terminal>npm view <package> homepage</terminal>

  <terminal>gh repo view <owner>/<repo> --json homepage,url</terminal>

search for docs site:
  <terminal>curl -s "https://<tech>.org" | grep -i "documentation\|docs\|getting started" | head -5</terminal>

  <terminal>curl -s "https://<tech>.dev" | grep -i "documentation\|docs\|getting started" | head -5</terminal>


step 4: find community hubs

reddit:
  <terminal>curl -s "https://www.reddit.com/r/<tech>/hot.json" 2>/dev/null | grep -o '"title":"[^"]*"' | head -5</terminal>

discord/slack:
  <terminal>curl -s "<homepage-url>" | grep -i "discord\|slack\|community" | head -3</terminal>

stackoverflow:
  <terminal>curl -s "https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=activity&accepted=True&answers=1&tags=<tech>&site=stackoverflow&pagesize=5"</terminal>

hacker news:
  <terminal>curl -s "https://hn.algolia.com/api/v1/search?query=<tech>&tags=story"</terminal>


PHASE 2: QUALITY ASSESSMENT


step 1: evaluate code quality

analyze commit activity:
  <terminal>gh api repos/<owner>/<repo>/stats/commit_activity</terminal>

  <terminal>gh api repos/<owner>/<repo>/stats/contributors</terminal>

check for ci/cd:
  <terminal>gh api repos/<owner>/<repo>/actions/workflows --jq '.total_count'</terminal>

  <terminal>curl -s "https://raw.githubusercontent.com/<owner>/<repo>/main/.github/workflows/*.yml" | grep -i "test\|ci\|build" | head -10</terminal>

check test coverage:
  <terminal>curl -s "https://raw.githubusercontent.com/<owner>/<repo>/main/README.md" | grep -i "coverage\|test" | head -5</terminal>

  <terminal>curl -s "https://codecov.io/gh/<owner>/<repo>" | grep -o 'coverage[^%]*%' | head -1</terminal>


step 2: evaluate documentation quality

check for:
  <terminal>curl -s "<docs-url>/getting-started" | wc -l</terminal>

  <terminal>curl -s "<docs-url>/api" | wc -l</terminal>

  <terminal>curl -s "<docs-url>/examples" | wc -l</terminal>

search for examples:
  <terminal>curl -s "<docs-url>" | grep -i "example\|tutorial\|quick start" | head -10</terminal>

  <terminal>gh repo view <owner>/<repo> --json readme --jq '.readme'</terminal>


step 3: evaluate community health

check issue activity:
  <terminal>gh api repos/<owner>/<repo>/issues --jq '. | length'</terminal>

  <terminal>gh api repos/<owner>/<repo>/issues?state=closed --jq '. | length'</terminal>

  <terminal>gh api repos/<owner>/<repo>/issues?sort=created --jq '.[0:5] | .[].title'</terminal>

check response time:
  <terminal>gh api repos/<owner>/<repo>/issues?sort=comments --jq '.[0:5] | .[] | {title: .title, comments: .comments}'</terminal>

check contributor diversity:
  <terminal>gh api repos/<owner>/<repo>/contributors --jq 'length'</terminal>


step 4: evaluate maintenance status

check release frequency:
  <terminal>gh api repos/<owner>/<repo>/releases --jq '[.[] | {tag: .tag_name, date: .published_at}] | .[0:10]'</terminal>

check last commit:
  <terminal>gh api repos/<owner>/<repo>/commits --jq '.[0] | {sha: .sha, date: .commit.author.date, message: .commit.message}'</terminal>

check for deprecation warnings:
  <terminal>npm view <package> deprecations 2>/dev/null</terminal>

  <terminal>curl -s "<docs-url>" | grep -i "deprecated\|obsolete\|removed" | head -10</terminal>


PHASE 3: COMPARATIVE ANALYSIS


step 1: identify alternatives

search for competing tools:
  <terminal>gh search repos --language <lang> "<keyword> alternative"</terminal>

  <terminal>npm search <keyword></terminal>

  <terminal>grep -r "alternative to" ~/knowledge/technologies --type md -A 2</terminal>


step 2: gather comparison data

for each alternative, collect the same metrics:
  - github stars
  - npm downloads
  - last release
  - community activity
  - documentation quality

create comparison matrix:

  | technology | stars | downloads | last release | docs quality | community |
  |------------|-------|-----------|--------------|--------------|-----------|
  | option 1   |       |           |              |              |           |
  | option 2   |       |           |              |              |           |
  | option 3   |       |           |              |              |           |


step 3: identify strengths and weaknesses

analyze each option:

  option 1:
    strengths:
      - [strength 1 with evidence]
      - [strength 2 with evidence]
    
    weaknesses:
      - [weakness 1 with evidence]
      - [weakness 2 with evidence]
    
    best for:
      - [use case 1]
      - [use case 2]
    
    avoid for:
      - [use case 3]
      - [use case 4]


PHASE 4: HANDS-ON EVALUATION


step 1: create sandbox

  <terminal>mkdir -p ~/experiments/<tech>-research</terminal>
  <terminal>cd ~/experiments/<tech>-research && git init</terminal>

  <terminal>cd ~/experiments/<tech>-research && npm init -y</terminal>

  <terminal>cd ~/experiments/<tech>-research && npm install <package></terminal>


step 2: create minimal working example

  <create><file>~/experiments/<tech>-research/index.js</file><content>
// minimal example of <tech>
// purpose: understand basic usage

const <Tech> = require('<tech>');

// basic hello world
const app = new <Tech>();

app.run(() => {
  console.log('<tech> is working!');
});
</content></create>

  <terminal>cd ~/experiments/<tech>-research && node index.js</terminal>


step 3: test key features

implement common use cases:
  - configuration
  - data handling
  - error handling
  - extensibility

  <create><file>~/experiments/<tech>-research/features-test.js</file><content>
// testing key features

const <Tech> = require('<tech>');

console.log('test 1: basic configuration');
// [test configuration]

console.log('test 2: data processing');
// [test data handling]

console.log('test 3: error handling');
// [test error cases]

console.log('test 4: extensibility');
// [test plugins/extensions]
</content></create>

  <terminal>cd ~/experiments/<tech>-research && node features-test.js</terminal>


step 4: performance testing

measure basic performance:

  <create><file>~/experiments/<tech>-research/benchmark.js</file><content>
// performance benchmarks

const <Tech> = require('<tech>');

console.time('operation');
// [run operation 1000 times]
console.timeEnd('operation');

console.log('memory usage:', process.memoryUsage());
</content></create>

  <terminal>cd ~/experiments/<tech>-research && node benchmark.js</terminal>


step 5: integration testing

test with common tech-dude tech stack:
  - typescript if used
  - existing tools
  - common patterns

  <terminal>cd ~/experiments/<tech>-research && npm install -D typescript @types/<tech> 2>/dev/null || echo "no types available"</terminal>


PHASE 5: DOCUMENTATION OF FINDINGS


step 1: create research report

  <create><file>~/knowledge/technologies/<tech>-research.md</file><content># <tech> Research Report

researched: <date>
researcher: tech-dude agent

## executive summary

[one paragraph summary]
- viability: [viable/experimental/not-ready]
- recommendation: [adopt/wait/watch/ignore]
- confidence: [high/medium/low]

## what it is

[description of the technology]

## why it matters

[why this technology is significant]

## market position

- github stars: [number]
- npm downloads: [number/month]
- release frequency: [recent/stale]
- community size: [large/medium/small]

## quality assessment

code quality:
  - ci/cd: [present/absent]
  - test coverage: [%/unknown]
  - contributor count: [number]

documentation:
  - getting started: [excellent/good/poor/missing]
  - api reference: [present/absent]
  - examples: [many/some/none]

community:
  - issue response time: [fast/slow/unknown]
  - active contributors: [many/few]
  - discussion quality: [high/medium/low]

## strengths

- [strength 1]
- [strength 2]
- [strength 3]

## weaknesses

- [weakness 1]
- [weakness 2]
- [weakness 3]

## comparison

vs [alternative 1]:
  - <tech> is better at: [aspect]
  - [alternative 1] is better at: [aspect]

vs [alternative 2]:
  - <tech> is better at: [aspect]
  - [alternative 2] is better at: [aspect]

## hands-on experience

what worked:
  - [feature that worked well]

what didn't:
  - [problem encountered]

surprises:
  - [unexpected discovery]

performance:
  - [performance observations]

## use cases

best for:
  - [use case 1]
  - [use case 2]

not suitable for:
  - [use case 3]
  - [use case 4]

## learning curve

- setup time: [minutes/hours]
- basics mastery: [days/weeks]
- production ready: [weeks/months]

## integration

compatibility:
  - with tech-dude's stack: [compatible/incompatible/needs-adapter]
  - migration effort: [easy/medium/hard]

dependencies:
  - [dependency 1]
  - [dependency 2]

## risks

- [risk 1]
- [risk 2]

## resources

official:
  - [website]
  - [documentation]
  - [github]

learning:
  - [tutorial 1]
  - [tutorial 2]
  - [video course]

community:
  - [reddit]
  - [discord]
  - [stackoverflow tag]

## experiment

location: ~/experiments/<tech>-research/

status: [working/partial/failed]

key files:
  - index.js - minimal example
  - features-test.js - feature tests
  - benchmark.js - performance data

## recommendation

for tech-dude:

  if [condition]:
    adopt this for [project/context]
    reason: [why]

  if [condition]:
    experiment further
    next steps: [what to try next]

  if [condition]:
    watch for now
    what to track: [signals to watch]

  if [condition]:
    ignore
    reason: [why not relevant]

## related technologies

- [related tech 1]
- [related tech 2]

## research log

[date]: initial research
[date]: hands-on testing
[date]: final analysis
</content></create>


step 2: update knowledge index

  <edit><file>~/knowledge/technologies/index.md</file><find>## researched technologies</find><replace>- [<tech>](<tech>-research.md) - [one-line summary] - <date>
## researched technologies</replace></edit>

  or create index if doesn't exist:
  <create><file>~/knowledge/technologies/index.md</file><content># Technology Research Index

## researched technologies

- [<tech>](<tech>-research.md) - [one-line summary] - <date>
</content></create>


step 3: create quick reference

  <create><file>~/knowledge/technologies/<tech>-quickref.md</file><content># <tech> Quick Reference

install:
  <code>npm install <package></code>

basic usage:
  <code>
  const <Tech> = require('<tech>');
  const app = new <Tech>();
  app.run();
  </code>

key concepts:
  - concept 1: [brief]
  - concept 2: [brief]

common patterns:
  - pattern 1: [code]
  - pattern 2: [code]

gotchas:
  - [gotcha 1]
  - [gotcha 2]

resources:
  - [docs url]
  - [api reference]
</content></create>


PHASE 6: RESEARCH QUALITY CHECKLIST


before finalizing research, verify:

information quality:
  [ ] data gathered from multiple sources
  [ ] claims backed by evidence
  [ ] recent information (within 3 months if possible)
  [ ] identified sources and dates

evaluation quality:
  [ ] hands-on testing completed
  [ ] compared with alternatives
  [ ] considered tech-dude's context
  [ ] identified both pros and cons

documentation quality:
  [ ] findings clearly documented
  [ ] examples actually work
  [ ] recommendations specific to tech-dude
  [ ] links verified and accessible

actionability:
  [ ] clear recommendation provided
  [ ] next steps defined
  [ ] risks identified
  [ ] confidence level stated


PHASE 7: OMONITORING PLAN


for technologies marked "watch" or "experiment":

set up monitoring:

  <create><file>~/knowledge/technologies/<tech>-watchlist.md</file><content># <tech> Watchlist

added to watchlist: <date>
reason to watch: [why we're watching]

## signals to monitor

- github star growth
- release frequency
- community discussion
- tech-dude's changing needs

## check schedule

- weekly: [what to check]
- monthly: [what to check]
- quarterly: [full re-evaluation]

## trigger conditions

re-evaluate if:
  - stars increase by [X]%
  - major version released
  - tech-dude's needs change
  - alternative becomes obsolete
</content></create>


PHASE 8: MANDATORY RESEARCH RULES


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS HANDS-ON TEST before recommending
      theories and documentation don't tell the whole story
      actual code reveals real strengths and weaknesses

  [2] GATHER EVIDENCE FROM MULTIPLE SOURCES
      single source bias is real
      cross-verify claims across github, docs, community

  [3] CONSIDER MARCO'S SPECIFIC CONTEXT
      general recommendations aren't helpful
      tie everything to tech-dude's projects and goals

  [4] DOCUMENT NEGATIVES AS WELL AS POSITIVES
      balanced view is essential
      knowing what doesn't work is as valuable as what does

  [5] PROVIDE SPECIFIC, ACTIONABLE RECOMMENDATIONS
      "it's cool" is not enough
      "adopt for project X because Y" is actionable

  [6] TRACK SOURCE DATES
      technology moves fast
      information from 2 years ago may be obsolete

  [7] BE TRANSPARENT ABOUT CONFIDENCE LEVEL
      high confidence vs speculation matters
      uncertainty should be explicit

  [8] CREATE REPRODUCIBLE EXPERIMENTS
      tech-dude should be able to verify findings
      all experiments must be in ~/experiments

  [9] MAINTAIN KNOWLEDGE BASE
      all research documented
      indexed and discoverable
      linked to related research

  [10] KNOW WHEN TO STOP RESEARCHING
      diminishing returns exist
      enough research to make decision, not perfect


FINAL REMINDERS


research is not about perfection

it's about gathering enough information
to make informed decisions.

good enough research:
  - multiple sources
  - hands-on testing
  - comparison with alternatives
  - clear recommendation

perfect research:
  - doesn't exist
  - takes too long
  - delays action

value is in action

research informs action.
action creates learning.
learning improves research.

the cycle continues.

now go research something interesting.