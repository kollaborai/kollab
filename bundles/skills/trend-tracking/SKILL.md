---
name: trend-tracking
description: "Trend Tracking skill - monitor and analyze technology trends and patterns"
---

trend-tracking mode: PATTERN RECOGNITION

when this skill is active, you systematically track and analyze technology trends.
this is a comprehensive guide to identifying meaningful patterns in tech evolution.


PHASE 0: TREND MONITORING SETUP


verify trend data sources

check github trending:
  <terminal>curl -s -o /dev/null -w "%{http_code}" https://github.com/trending</terminal>

check hacker news api:
  <terminal>curl -s -o /dev/null -w "%{http_code}" https://hacker-news.firebaseio.com/v0</terminal>

check reddit api:
  <terminal>curl -s -o /dev/null -w "%{http_code}" https://www.reddit.com/r/programming/hot.json</terminal>


create trend tracking structure

  <terminal>mkdir -p ~/knowledge/trends/weekly</terminal>
  <terminal>mkdir -p ~/knowledge/trends/monthly</terminal>
  <terminal>mkdir -p ~/knowledge/trends/yearly</terminal>

  <terminal>mkdir -p ~/knowledge/trends/data</terminal>


initialize trend database

  <create><file>~/knowledge/trends/trend-tracking.md</file><content># Trend Tracking Log

tracking started: <date>
last updated: <date>

## domains tracked

- javascript/typescript ecosystem
- python ecosystem
- rust ecosystem
- web development
- devops/infrastructure
- ai/ml tools
- developer experience

## tracking schedule

- daily: [lightweight checks]
- weekly: [comprehensive reports]
- monthly: [deep analysis]
- yearly: [big picture synthesis]

## signals to watch

- github star velocity
- npm download trends
- reddit discussion frequency
- hacker news front page
- job posting mentions
- conference talk topics
</content></create>


PHASE 1: DAILY TREND PULSE


step 1: github trending snapshot

capture trending repositories:

  <terminal>curl -s "https://github.com/trending" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -20</terminal>

capture by language:
  <terminal>curl -s "https://github.com/trending/python" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -10</terminal>

  <terminal>curl -s "https://github.com/trending/javascript" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -10</terminal>

  <terminal>curl -s "https://github.com/trending/rust" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -10</terminal>


step 2: hacker news pulse

capture top stories:

  <terminal>curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | head -c 200 | tr ',' '\n' | head -10</terminal>

get story details:
  <terminal>curl -s "https://hacker-news.firebaseio.com/v0/item/<story-id>.json"</terminal>

search for tech keywords:
  <terminal>curl -s "https://hn.algolia.com/api/v1/search?query=rust&tags=story&numericFilters=created_at_i>$(date -v-1d +%s)" | jq '.hits | length'</terminal>


step 3: package manager trends

npm trending:
  <terminal>npm search --long | head -20</terminal>

  <terminal>npm view <package> weeklyDownloads</terminal>

pypi trending:
  <terminal>curl -s "https://hugovk.top/top-pypi-packages/" | grep -A 1 "recent" | head -20</terminal>

  <terminal>pip index versions <package> 2>/dev/null | head -10</terminal>


step 4: social signals

reddit programming:
  <terminal>curl -s "https://www.reddit.com/r/programming/hot.json?limit=10" | grep -o '"title":"[^"]*"' | sed 's/"title":"//g' | sed 's/"//g' | head -10</terminal>

search for specific keywords:
  <terminal>curl -s "https://www.reddit.com/r/rust/hot.json?limit=5" | grep -o '"title":"[^"]*"' | sed 's/"title":"//g' | sed 's/"//g'</terminal>


step 5: create daily pulse log

  <create><file>~/knowledge/trends/data/daily-$(date +%Y-%m-%d).md</file><content># Daily Trend Pulse: $(date +%Y-%m-%d)

## github trending top 10

[repos from step 1]

## hacker news top 5

[stories from step 2]

## reddit programming top 5

[posts from step 3]

## signals detected

[any notable patterns or anomalies]

## tech-dude relevance

[anything relevant to current projects/interests]
</content></create>


PHASE 2: WEEKLY TREND ANALYSIS


step 1: aggregate weekly data

compile daily pulses:
  <terminal>find ~/knowledge/trends/data -name "daily-*.md" -mtime -7 -exec cat {} \;</terminal>

identify recurring mentions:
  <terminal>grep -h "github trending" ~/knowledge/trends/data/daily-*.md | sort | uniq -c | sort -rn | head -20</terminal>

  <terminal>grep -h "hacker news" ~/knowledge/trends/data/daily-*.md | sort | uniq -c | sort -rn | head -20</terminal>


step 2: analyze github velocity

check star growth of tracked repos:

  <terminal>cat > /tmp/check_star_growth.py << 'EOF'
import requests
import json

repos = [
    "owner/repo1",
    "owner/repo2",
]

for repo in repos:
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}")
        data = r.json()
        print(f"{repo}: {data['stargazers_count']} stars, updated: {data['updated_at']}")
    except Exception as e:
        print(f"{repo}: error - {e}")
EOF
python /tmp/check_star_growth.py</terminal>

find rising stars:
  <terminal>gh search repos --language <lang> --stars ">1000" --sort updated --order desc --limit 10</terminal>


step 3: analyze package downloads

npm download trends:
  <terminal>cat > /tmp/npm_trends.py << 'EOF'
import requests
import json

packages = ["react", "vue", "svelte", "solid-js"]

for pkg in packages:
    try:
        r = requests.get(f"https://api.npmjs.org/downloads/point/last-week/{pkg}")
        data = r.json()
        print(f"{pkg}: {data['downloads']} downloads/week")
    except Exception as e:
        print(f"{pkg}: error - {e}")
EOF
python /tmp/npm_trends.py</terminal>


step 4: community sentiment analysis

hacker news keyword frequency:
  <terminal>curl -s "https://hn.algolia.com/api/v1/search?tags=story&numericFilters=created_at_i>$(date -v-7d +%s)" | jq -r '.hits[].title' | sort | uniq -c | sort -rn | head -30</terminal>

reddit keyword analysis:
  <terminal>curl -s "https://www.reddit.com/r/programming/top.json?t=week&limit=100" | jq -r '.data.children[].data.title' | sort | uniq -c | sort -rn | head -30</terminal>


step 5: create weekly trend report

  <create><file>~/knowledge/trends/weekly/week-$(date +%Y-%W).md</file><content># Weekly Trend Report: Week $(date +%Y-%W)

period: $(date -v-monday +%Y-%m-%d) to $(date -v-sunday +%Y-%m-%d)

## highlights this week

[1] [trend name]
    - github stars: +[X]% (current: [Y]k)
    - downloads: [X]k/week
    - discussion: mentioned [X] times on HN
    - momentum: [rising/stable/declining]

[2] [trend name]
    - [same structure]

[3] [trend name]
    - [same structure]

## emerging patterns

### pattern 1: [pattern name]
    what we're seeing:
      - [observation 1]
      - [observation 2]
    
    potential drivers:
      - [driver 1]
      - [driver 2]
    
    prediction:
      - [what might happen next]

### pattern 2: [pattern name]
    [same structure]

## rising stars (github)

| repo | stars this week | total stars | category |
|------|----------------|-------------|----------|
| [repo] | [+X%] | [Y]k | [category] |
| [repo] | [+X%] | [Y]k | [category] |

## package velocity

| package | downloads/week | growth |
|---------|---------------|--------|
| [pkg] | [X]k | [+Y%] |
| [pkg] | [X]k | [+Y%] |

## community buzz

top topics:
  - [topic 1] - [frequency]
  - [topic 2] - [frequency]
  - [topic 3] - [frequency]

## tech-dude relevance

trends affecting tech-dude's stack:
  - [trend 1] - [how it relates]
  - [trend 2] - [how it relates]

action items:
  - [investigate]
  - [experiment with]
  - [ignore]

## signals to watch

keep an eye on:
  - [signal 1]
  - [signal 2]

## dead/declining trends

trends losing momentum:
  - [trend 1] - [evidence of decline]
  - [trend 2] - [evidence of decline]
</content></create>


PHASE 3: MONTHLY DEEP DIVE


step 1: monthly data aggregation

compile weekly reports:
  <terminal>find ~/knowledge/trends/weekly -name "week-*.md" -mtime -30 -exec cat {} \;</terminal>

analyze monthly patterns:
  <terminal>grep -h "highlights this week" ~/knowledge/trends/weekly/week-*.md | sort | uniq -c | sort -rn</terminal>

track trend persistence:
  <terminal>grep -h "rising stars" ~/knowledge/trends/weekly/week-*.md | grep -o "[^|]*" | sort | uniq -c | sort -rn</terminal>


step 2: category-specific analysis

javascript ecosystem:
  <terminal>curl -s "https://github.com/trending/javascript" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -20</terminal>

  <terminal>npm search --long javascript | head -30</terminal>

  identify framework trends:
  <terminal>grep -rh "react\|vue\|svelte\|angular\|solid" ~/knowledge/trends/data/daily-*.md | sort | uniq -c | sort -rn</terminal>

python ecosystem:
  <terminal>curl -s "https://github.com/trending/python" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -20</terminal>

  identify web framework trends:
  <terminal>grep -rh "django\|fastapi\|flask\|starlette" ~/knowledge/trends/data/daily-*.md | sort | uniq -c | sort -rn</terminal>

rust ecosystem:
  <terminal>curl -s "https://github.com/trending/rust" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -20</terminal>

ai/ml tools:
  <terminal>curl -s "https://github.com/trending" | grep -i "ai\|llm\|machine-learning" | grep -o 'href="/[^/]*/[^"]*"' | sed 's|href="||' | sed 's|"||' | head -20</terminal>


step 3: job market analysis

search job postings:
  <terminal>curl -s "https://www.indeed.com/q-<tech>-jobs.html" | grep -o "jobTitle\">[^<]*<" | head -10</terminal>

check skill requirements:
  <terminal>curl -s "https://stackoverflow.com/jobs?q=<tech>&l=Remote" | grep -o "<h2>[^<]*</h2>" | head -10</terminal>

track skill demand:
  <terminal>curl -s "https://github.com/search?q=<tech>+language%3Ajavascript&type=repositories&s=stars&o=desc" | grep -o "[0-9,]* results" | head -1</terminal>


step 4: conference and event tracking

check upcoming conferences:
  <terminal>curl -s "https://www.confs.tech/" | grep -o "<a href=\"[^\"]*\"[^>]*>[^<]*<[^>]*<[^>]*>" | grep "javascript\|python\|rust" | head -10</terminal>

check talk topics:
  <terminal>curl -s "https://www.youtube.com/results?search_query=<tech>+conference+2024" | grep -o "videoId\":\"[^\"]*\"" | head -10</terminal>


step 5: create monthly trend synthesis

  <create><file>~/knowledge/trends/monthly/$(date +%Y-%m).md</file><content># Monthly Trend Synthesis: $(date +%B %Y)

## executive summary

the big picture this month:
  - [major trend 1]
  - [major trend 2]
  - [unexpected development]

## sustained trends (3+ months)

[1] [trend name]
    - status: [accelerating/stable/decelerating]
    - evidence: [metrics]
    - tech-dude impact: [high/medium/low]

[2] [trend name]
    - [same structure]

## emerging trends (new this month)

[1] [trend name]
    - first appeared: [date]
    - growth rate: [metrics]
    - prediction: [where it's going]

[2] [trend name]
    - [same structure]

## declining trends

[1] [trend name]
    - peak: [when]
    - current state: [declining metrics]
    - likely cause: [why declining]

[2] [trend name]
    - [same structure]

## category breakdowns

### javascript/typescript

frameworks:
  - [framework] - [trend direction]

tools:
  - [tool] - [trend direction]

patterns:
  - [pattern] - [gaining/losing traction]

### python

frameworks:
  - [framework] - [trend direction]

data science:
  - [library] - [trend direction]

patterns:
  - [pattern] - [gaining/losing traction]

### rust

ecosystem growth:
  - [framework/tool] - [trend direction]

adoption areas:
  - [use case] - [trend direction]

### ai/ml

models:
  - [model/framework] - [trend direction]

tools:
  - [tool] - [trend direction]

## tech-dude's radar

### adopt now (high relevance, stable)
  - [tech] - [why for tech-dude]

### experiment (relevant, emerging)
  - [tech] - [why worth exploring]

### watch (interesting, not urgent)
  - [tech] - [what makes it interesting]

### ignore (not relevant)
  - [tech] - [why ignore]

## predictions for next month

[1] [trend] will [continue/accelerate/decelerate]
    - reasoning: [why]

[2] [new development] is likely
    - reasoning: [why]

[3] [current trend] may peak
    - reasoning: [why]

## data sources

- github trending: [frequency]
- hacker news: [frequency]
- npm downloads: [frequency]
- reddit: [frequency]
- job postings: [frequency]

## notes

[qualitative observations not captured in metrics]
</content></create>


PHASE 4: PATTERN RECOGNITION


step 1: identify trend lifecycles

detect early stage:
  - appeared in last week
  - high growth rate
  - limited documentation
  - few adopters yet

detect growth stage:
  - 2-6 months old
  - accelerating metrics
  - community forming
  - early adopters

detect maturity stage:
  - 6+ months old
  - stable metrics
  - strong ecosystem
  - wide adoption

detect decline stage:
  - slowing or negative growth
  - community quieting
  - newer alternatives
  - maintenance mode only


step 2: detect cross-pollination

identify patterns spreading between ecosystems:

  <terminal>grep -rh "concept pattern" ~/knowledge/trends/data/*.md | sort | uniq -c</terminal>

  examples:
  - patterns from rust appearing in javascript
  - patterns from ml appearing in web dev
  - patterns from cloud appearing in local dev


step 3: detect convergence

identify where ecosystems are merging:

  - javascript + rust (wasm, swc, turbopack)
  - python + typescript (type stubs, mypy)
  - devops + development (gitops, internal dev platforms)

  <terminal>grep -rh "javascript.*rust\|rust.*javascript" ~/knowledge/trends/data/*.md</terminal>


step 4: detect hype cycles

identify peak of inflated expectations:
  - excessive media coverage
  - rapid star growth without substance
  - many "hello world" projects
  - few production implementations

identify trough of disillusionment:
  - star growth plateauing
  - critical articles appearing
  - "is X dead?" discussions
  - real-world challenges documented

identify slope of enlightenment:
  - practical applications emerging
  - best practices forming
  - real production use cases
  - mature tooling

identify plateau of productivity:
  - stable, widespread adoption
  - clear best practices
  - minimal controversy
  - boring but useful


PHASE 5: SIGNAL VS NOISE FILTERING


step 1: separate real trends from hype

real trend indicators:
  - consistent growth over multiple time periods
  - production adoption by serious companies
  - strong documentation and examples
  - solving real problems
  - community consensus on value

hype indicators:
  - viral spike then crash
  - mostly blog posts, few real projects
  - solves trivial or nonexistent problem
  - driven by influencer/celebrity endorsement
  - frequent "framework of the week" turnover


step 2: evaluate tech-dude relevance filter

high relevance criteria:
  - solves problem tech-dude currently has
  - compatible with tech-dude's stack
  - within tech-dude's learning capacity
  - has clear ROI for tech-dude's goals

medium relevance criteria:
  - interesting but not immediately useful
  - future potential but requires more maturation
  - compatible but significant migration cost

low relevance criteria:
  - completely different domain
  - incompatible with tech-dude's constraints
  - solves problems tech-dude doesn't have
  - theoretical interest only


step 3: create tech-dude-specific trend score

calculate for each trend:

  relevance score = (problem alignment × 3) + (stack fit × 2) + (learning cost × -1) + (community health × 1)

  score interpretation:
    - 8-10: adopt now
    - 5-7: experiment
    - 3-4: watch
    - 0-2: ignore


PHASE 6: TREND ACTION PLANNING


step 1: create quarterly roadmap

based on trend analysis:

  <create><file>~/knowledge/trends/quarterly/q$(date +%Y)-$(date +%q).md</file><content># Quarterly Trend Roadmap: Q$(date +%q) $(date +%Y)

## priority investments

### this quarter focus

[1] [trend/technology]
    - effort required: [low/medium/high]
    - expected impact: [high/medium/low]
    - timeline: [specific dates]
    - success criteria: [what success looks like]

[2] [trend/technology]
    - [same structure]

### ongoing experiments

continue tracking:
  - [trend 1] - [checkpoint date]
  - [trend 2] - [checkpoint date]

## research backlog

topics to explore when time permits:
  - [topic 1] - [why interesting]
  - [topic 2] - [why interesting]

## ignore list

consciously ignoring:
  - [trend 1] - [reason]
  - [trend 2] - [reason]

## trend dependencies

some trends depend on others:
  - [trend a] requires [trend b] first
  - [trend c] enables [trend d]

## budget allocation

time investment:
  - experimentation: [X] hours/week
  - learning: [Y] hours/week
  - research: [Z] hours/week

## review schedule

- weekly: trend pulse check
- monthly: trend report review
- quarterly: roadmap adjustment
</content></create>


PHASE 7: MANDATORY TRACKING RULES


while this skill is active, these rules are MANDATORY:

  [1] CAPTURE DATA CONSISTENTLY
      daily/weekly/monthly schedules must be maintained
      gaps in data reduce trend detection accuracy

  [2] SEPARATE SIGNAL FROM NOISE
      not everything popular is meaningful
      focus on trends with substance

  [3] ALWAYS TIE TO MARCO'S CONTEXT
      general trends aren't helpful
      tech-dude-specific relevance is everything

  [4] TRACK NEGATIVES TOO
      declining trends are important
      hype bubbles need identification

  [5] IDENTIFY PATTERNS, NOT INDIVIDUAL EVENTS
      single data points don't make a trend
      look for sustained patterns

  [6] MAINTAIN SKEPTICISM
      viral ≠ valuable
      new ≠ better
      popular ≠ right for tech-dude

  [7] DOCUMENT REASONING
      why a trend is tracked
      why a decision is made
      future tech-dude needs to understand

  [8] RECOGNIZE HYPE CYCLES
      peak of inflated expectations ≠ long-term value
      trough of disillusionment ≠ failure

  [9] UPDATE KNOWLEDGE BASE
      all trend reports archived
      indexed and searchable
      connected to experiments

  [10] BALANCE EXPLORATION WITH EXECUTION
      don't just track, act on insights
      experiments validate trend value


FINAL REMINDERS


trend tracking is reconnaissance

you're mapping the landscape
not committing to every direction.


patterns emerge over time

weekly data points connect into monthly patterns
monthly patterns reveal quarterly themes
quarterly themes show yearly evolution


value comes from action

trends without action are just trivia
action without trends is random exploration
combine both for strategic growth


know when to stop tracking

not every trend needs infinite monitoring
retire dead trends
focus on what matters to tech-dude

now go find the next big thing.