---
name: plot-structure
description: "Plot Structure skill - story architecture and pacing mastery"
---

plot structure mode: TENSION OVER TEMPLATE

when this skill is active, you follow story-first discipline.
this is a comprehensive guide to structuring compelling narratives.


PHASE 0: PROJECT IDENTIFICATION

before structuring ANY plot, identify the story's fundamental nature.


check format and length

  <read><file>project_brief.md</file></read>

if no project brief exists, create one:
  <create>
  <file>project_brief.md</file>
  <content>
  # Project Brief

  ## Format
  [ ] novel (80,000+ words)
  [ ] novella (20,000-50,000 words)
  [ ] short story (1,000-7,500 words)
  [ ] flash fiction (under 1,000 words)
  [ ] screenplay / script
  [ ] other: ___________

  ## Genre
  [primary genre]
  [secondary elements]

  ## Core Conflict
  [protagonist] wants [goal] but [antagonist/force] opposes because [reason]

  ## Stakes
  what happens if protagonist fails?
  what happens if protagonist succeeds?
  </content>
  </create>


format determines structure

different formats have different structural requirements:

novel:
  - room for subplots and multiple threads
  - can develop complex secondary characters
  - allows for extensive worldbuilding
  - typically follows 3-act or 4-act structure
  - target: 80,000-120,000 words

short story:
  - single core conflict
  - limited cast (3-5 characters max)
  - one primary location or tightly connected locations
  - follows micro-structure (setup, escalation, reversal)
  - target: 2,000-5,000 words

flash fiction:
  - one moment, one change
  - single character focus
  - immediate impact
  - structure: situation -> action -> revelation
  - target: 500-1,000 words

screenplay:
  - external action focus
  - visual storytelling
  - strict three-act structure
  - 90-120 pages = 90-120 minutes


identify target audience and genre expectations

  <terminal>find . -name "*genre*" -o -name "*audience*" -o -name "*market*" 2>/dev/null | head -5</terminal>

genre expectations shape structure:

thriller:
  - faster pacing throughout
  - higher stakes earlier
  - more plot twists
  - clearer external conflict

literary fiction:
  - can be slower
  - emphasis on character arc over plot beats
  - internal conflict may equal or exceed external
  - ambiguity allowed

romance:
  - meet-cute early
  - midpoint complications to relationship
  - black moment near end
  - happily ever after required

science fiction / fantasy:
  - worldbuilding integrated with plot
  - clear stakes (save world/kingdom/universe)
  - final confrontation with antagonist

match structure to reader expectations.


PHASE 1: THE THREE-ACT FOUNDATION

the three-act structure is not a formula. it's a shape that stories
naturally take because it mirrors how we experience change.


act one: setup (25% of story)

purpose: establish normal world, introduce characters, launch story

components:
  [ ] opening image - establish tone and world
  [ ] protagonist introduction - who are we following?
  [ ] status quo - what is normal life?
  [ ] inciting incident - something happens
  [ ] call to adventure - protagonist has opportunity
  [ ] refusal / hesitation - protagonist is reluctant (can be brief)
  [ ] decision to engage - protagonist commits
  [ ] act one climax - point of no return

act one ends when protagonist crosses the threshold. there is no going
back to normal life.

example: the hunger games
  - opening: katniss in district 12, hunting, feeding family
  - status quo: poverty, survival, protecting prim
  - inciting incident: prim's name is drawn
  - call: katniss volunteers
  - decision: leaves district for the games
  - act one climax: enters arena, game begins

word count guide (80k novel):
  act one: ~20,000 words


act two: confrontation (50% of story)

purpose: complications, escalating stakes, protagonist is tested

components:
  [ ] new world - protagonist navigates unfamiliar territory
  [ ] tests and allies - learning the rules, meeting characters
  [ ] training/gathering - preparing for confrontation
  [ ] midpoint - major revelation or shift
  [ ] stakes escalate - things get more serious
  [ ] all is lost - low point
  [ ] dark night of the soul - protagonist must find new strength

the midpoint is crucial. it recontextualizes everything.

before midpoint: problem seems like X
after midpoint: problem is actually Y

example: star wars (a new hope)
  - new world: death star, rescuing leia
  - tests: obi-wan's teachings, han's cynicism vs luke's belief
  - midpoint: death star destroys alderaan
  - before midpoint: rescue mission
  - after midpoint: escape + fight against empire
  - all is lost: obi-wan dies
  - dark night: luke must trust himself, not the force through another

word count guide (80k novel):
  act two: ~40,000 words


act three: resolution (25% of story)

purpose: final confrontation and transformation

components:
  [ ] return - protagonist emerges from dark night
  [ ] final push - gathering resources for last stand
  [ ] climax - protagonist faces antagonist/stakes directly
  [ ] sacrifice - protagonist gives something up (could be belief, safety,
       relationship, literal life)
  [ ] transformation - protagonist has changed
  [ ] new normal - world is different because of journey
  [ ] closing image - mirrors opening, shows change

the climax must be earned. everything in acts one and two prepares for
this moment.

example: the matrix
  - return: neo chooses to try to save morpheus
  - final push: weapons, entrance to building
  - climax: neo vs agent smith
  - sacrifice: neo chooses to stay and fight (believes)
  - transformation: becomes the one
  - new normal: can see code, has power
  - closing image: phone call into the system, flying

word count guide (80k novel):
  act three: ~20,000 words


PHASE 2: BEAT SHEET DEVELOPMENT

a beat sheet breaks your story into specific moments that create the
story's emotional rhythm.


blake snyder's save the cat beats (adapted for prose)

opening image (1%)
  - establish protagonist and world before change
  - show us normal life
  - hint at what's missing or wrong

theme stated (5%)
  - a character (not protagonist) states the theme
  - protagonist doesn't understand yet
  - will be proven/understood by end

setup (1-10%)
  - introduce protagonist's world and flaw
  - show what's missing
  - establish stakes (what protagonist has to lose)

catalyst (10%)
  - inciting incident
  - something breaks normal
  - creates opportunity for change

debate (10-20%)
  - protagonist resists the call
  - weighs options
  - fears the journey
  - finally decides to engage

break into two (20-25%)
  - act one climax
  - protagonist leaves comfort zone
  - point of no return

b story (25%)
  - introduce secondary plot (often relationship)
  - provides relief from main tension
  - often carries theme

fun and games (25-50%)
  - the promise of the premise
  - protagonist exploring new world
  - the "trailer moments"
  - what audience came to see

midpoint (50%)
  - false victory or false defeat
  - stakes raised
  - context shifts
  - time clocks introduced

bad guys close in (50-75%)
  - protagonist's team dissolves
  - plan fails
  - antagonist pressures
  - stakes escalate

all is lost (75%)
  - apparent defeat
  - whiff of death (emotional or literal)
  - hope seems lost

dark night of the soul (75-80%)
  - protagonist processes defeat
  - realizes the flaw that caused failure
  - finds new strength/truth
  - decides to try again with new approach

break into three (80%)
  - decision to face final challenge
  - new understanding applied
  - gathers what's needed for climax

finale (80-99%)
  - protagonist faces antagonist/stakes
  - uses everything learned
  - demonstrates growth
  - villain/obstacle defeated

final image (100%)
  - mirrors opening image
  - shows transformation
  - proves theme


create your beat sheet

for each beat, identify:
  [ ] what happens (event)
  [ ] why it matters (emotional impact)
  [ ] how it moves story (plot consequence)
  [ ] word count allocation

example beat sheet entry:

midpoint (word 40,000 / 50%)
  what: protagonist discovers antagonist is her missing brother
  why: recontextualizes entire quest - not just defeat him, but save
       or destroy family. raises personal stakes.
  consequence: every previous encounter gains new meaning.
         must choose between mission and family.


PHASE 3: CHARACTER ARC STRUCTURE

plot structure and character arc are inseparable. the structure exists
to force character change.


the external arc (what happens)

setup:
  protagonist has a flaw / wound / lie they believe

inciting incident:
  something challenges the lie/flaw

journey (act two):
  protagonist tries to solve problems using the flaw
  - it doesn't work
  - things get worse
  - reaches low point

dark night:
  protagonist realizes the flaw is the problem

climax:
  protagonist confronts flaw, tries new approach

resolution:
  protagonist lives without the flaw


the internal arc (how they change)

before (normal world):
  - flaw protects from wound
  - lie feels true
  - coping mechanisms work

journey:
  - coping mechanisms fail
  - wound is exposed
  - lie is challenged

dark night:
  - must confront wound
  - must let go of lie
  - must find new truth

after (new normal):
  - lives with new truth
  - wound may remain but response changed
  - no longer protected by flaw


example: pride and prejudice

external arc:
  elizabeth meets darcy, dislikes him, events force interaction,
  eventually realizes she misjudged him, they marry

internal arc:
  flaw: hasty judgment
  lie: first impressions are reliable
  wound: father's cynical view of marriage
  journey: wickham seems good (reinforces lie), darcy seems bad
          (reinforces lie), darcy's letter reveals truth
  dark night: realizing she misjudged both men
  new truth: requires investigation, not assumption
  new normal: married to darcy, sees clearly


structural sync

key structural points MUST correspond to character arc:

break into two:
  protagonist leaves comfort zone AND comfort zone of old belief

midpoint:
  external: stakes raised
  internal: old belief challenged

all is lost:
  external: defeat
  internal: old coping strategies fail completely

climax:
  external: final confrontation
  internal: must overcome flaw to succeed

resolution:
  external: world changed
  internal: character changed


PHASE 4: SUBPLOT WEAVING

subplots follow their own arcs but connect to main plot.


subplot types

relationship subplot:
  - often the "b story"
  - provides emotional counterweight
  - often carries theme
  - connects to main plot at key structural points

mirror subplot:
  - secondary character faces similar challenge
  - their choices highlight protagonist's choices
  - serves as warning or inspiration

contrasting subplot:
  - character faces opposite challenge
  - shows alternative path
  - highlights stakes by showing what could happen

thematic subplot:
  - explores story theme from different angle
  - may not involve protagonist directly
  - enriches story's emotional resonance


subplot connection points

subplots must connect to main plot at key structural moments:

break into two:
  - subplot introduced or activated

midpoint:
  - subplot complicates main plot
  - or main plot complicates subplot

all is lost:
  - subplot hits its own low point
  - adds to protagonist's despair

climax:
  - subplot resolution aids or complicates final confrontation
  - secondary character's arc resolves

resolution:
  - subplot's new status shows theme in another context


example subplot weaving

main plot: detective hunting serial killer
subplot: detective's failing marriage

connections:
  - act one: marriage already strained (setup)
  - break into two: case demands more time, creates marital tension
  - midpoint: killer attacks someone close to detective; spouse says
               "you're never here, i can't do this anymore"
  - all is lost: spouse leaves; detective is alone, realizes the cost
  - dark night: detective chooses to save spouse instead of pursuing
                 killer immediately
  - climax: spouse's support provides key to catching killer
  - resolution: marriage repaired through shared trauma

subplot serves:
  [ ] shows stakes of main plot (cost to personal life)
  [ ] creates additional tension
  [ ] provides emotional motivation
  [ ] demonstrates theme (justice requires sacrifice)


subplot pacing rules

  [1] introduce subplot early or not at all
      act one is best. later introductions feel forced.

  [2] give subplot its own arc
      beginning, middle, end - not just random complications

  [3] don't let subplot overshadow main plot
      subplot serves main plot, not vice versa

  [4] resolve subplot before or with main plot
      resolution during climax or immediately after

  [5] cut subplot if it doesn't connect
      if subplot can be removed without affecting main plot,
      it doesn't belong


PHASE 5: SCENE STRUCTURE

every scene is a mini-story with its own structure.


scene components

entry:
  [ ] where are we?
  [ ] who's present?
  [ ] what's the situation?
  [ ] what does scene POV character want?

action:
  [ ] conflict arises
  [ ] character pursues goal
  [ ] obstacles arise
  [ ] character tries, fails, adjusts
  [ ] scene builds to turning point

exit:
  [ ] how has situation changed?
  [ ] what does character do now?
  [ ] what's the immediate consequence?

every scene must:
  [ ] advance plot
  [ ] reveal character
  [ ] create/change tension
  [ ] answer previous question AND raise new one


scene types

action scene:
  - physical conflict
  - clear goal and obstacle
  - escalation of stakes
  - turning point changes situation

revelation scene:
  - character learns something
  - information is revealed
  - context changes
  - decisions must be made

emotion scene:
  - character processes experience
  - internal state is explored
  - decision or realization
  - character is changed

relationship scene:
  - two or more characters interact
  - relationship dynamics shift
  - power changes hands
  - alliances form/break


scene structure template

before writing scene, identify:

scene goal:
  [ ] what does POV character want in this scene?

scene conflict:
  [ ] what opposes the goal?
  [ ] who or what is the obstacle?

scene outcome:
  [ ] does character get what they want? (usually: no, or yes but...)
  [ ] how is situation different at scene end?

scene consequence:
  [ ] what must character do now?
  [ ] what's changed that can't be undone?

scene value charge:
  [ ] entry value: positive/negative for POV character
  [ ] exit value: opposite or intensified
  - if scene starts and ends with same value, cut it


PHASE 6: PACING AND TENSION

pacing is manipulation of story time and information.


controlling pacing

tools to slow pacing:
  [ ] longer sentences
  [ ] more description
  [ ] internal reflection
  [ ] dialogue exchanges
  [ ] detailed action sequences

tools to accelerate pacing:
  [ ] shorter sentences
  [ ] fragments
  [ ] less description
  [ ] more external action
  [ ] chapter cliffhangers
  [ ] time cuts (skip the transitions)


pacing by location

act one:
  - establish world at measured pace
  - reader needs grounding
  - speed up toward inciting incident

act two first half:
  - moderate pace
  - room for exploration
  - accelerate toward midpoint

act two second half:
  - complications come faster
  - less breathing room
  - building pressure

act three:
  - fastest pace
  - high tension
  - resolution can breathe again


tension building

tension comes from:
  [ ] reader knowing more than character
  [ ] character wanting something they can't have
  [ ] time pressure
  [ ] limited resources
  [ ] uncertain outcomes
  [ ] competing desires

tension creates questions:
  - will they succeed?
  - what will it cost?
  - who can be trusted?
  - what's really happening?

every scene should raise questions.
every scene answer should raise new questions.


structural tension

the story shape creates macro-tension:

act one:
  question: will protagonist leave comfort?

act two:
  question: can protagonist survive the journey?

act three:
  question: will protagonist triumph?

within these macro-questions, scene-level questions keep reader
moving forward.


time pressure

time clocks create urgency:

hard time clock:
  - bomb will explode in 24 hours
  - wedding is in three days
  - must catch train at midnight

soft time clock:
  - antagonist is getting closer
  - opportunity is slipping away
  - resources are running out

psychological time clock:
  - character believes something will happen
  - self-imposed deadline
  - internal urgency

time clocks work best when multiple clocks converge:

external clock: villain will strike in 48 hours
internal clock: protagonist's own doubt grows
relationship clock: spouse will leave if protagonist misses one more
                 family event


PHASE 7: STORY ARCHETYPES


the hero's journey (joseph campbell / christopher vogler)

useful for: adventure, fantasy, science fiction, mythic stories

structure:
  [ ] ordinary world - normal life before change
  [ ] call to adventure - something breaks normal
  [ ] refusal - protagonist hesitates
  [ ] mentor - guide appears with wisdom/tools
  [ ] crossing threshold - leaves ordinary world
  [ ] tests, allies, enemies - learns rules of new world
  [ ] approach to inmost cave - prepares for central challenge
  [ ] ordeal - faces death/greatest fear
  [ ] reward - survives, gains something
  [ ] road back - returns to ordinary world with consequences
  [ ] resurrection - final test, transformed
  [ ] return with elixir - brings something back to help ordinary world

not all stories fit this pattern.
use it only if it serves YOUR story.


the mystery structure

useful for: detective stories, thrillers, mysteries

structure:
  [ ] crime/discovery - body found, mystery begins
  [ ] investigation - detective follows leads
  [ ] false solution - seems solved, but isn't
  [ ] reversal - false solution falls apart
  [ ] true investigation - with new understanding
  [ ] final revelation - true solution discovered
  [ ] confrontation - detective faces killer

key: reader should have same information as detective.
fair play means reader could solve it too (in theory).


the romance structure

useful for: romance novels, romantic subplots

structure:
  [ ] meet-cute - protagonists meet in memorable way
  [ ] attraction - initial interest despite obstacles
  [ ] first kiss / intimacy - relationship begins
  [ ] midpoint complication - secret revealed, external obstacle
  [ ] black moment - relationship seems doomed
  [ ] grand gesture - one character makes sacrifice
  [ ] reconciliation - relationship restored
  [ ] commitment - happily ever after

key: external obstacles should mirror internal fears.
overcoming external obstacles forces internal growth.


PHASE 8: STRUCTURAL PROBLEMS AND SOLUTIONS


problem: saggy middle

symptoms:
  - act two drags
  - story loses momentum
  - reader gets bored
  - nothing seems to be happening

solutions:
  [ ] raise stakes - what's at risk must matter more
  [ ] add time pressure - create urgency
  [ ] introduce complications - make protagonist's plan fail
  [ ] deepen character conflict - internal should match external
  [ ] tighten scene structure - cut scenes that don't change situation
  [ ] add midpoint twist - recontextualize everything


problem: protagonist is passive

symptoms:
  - things happen TO protagonist
  - protagonist doesn't make decisions
  - other characters drive plot
  - protagonist is reactive, not active

solutions:
  [ ] give protagonist clear goal - they must want something
  [ ] make protagonist make hard choices - choices have consequences
  [ ] tie external conflict to internal - solving problem requires growth
  [ ] remove easy solutions - character must struggle
  [ ] give protagonist agency - their decisions matter


problem: predictable plot

symptoms:
  - reader sees what's coming
  - no surprises
  - story feels formulaic
  - tension never builds

solutions:
  [ ] subvert expectations - set up one thing, deliver another
  [ ] deepen characters - complex people aren't predictable
  [ ] raise emotional stakes - outcome matters more to reader
  [ ] add twist that recontextualizes - change what we think we know
  [ ] focus on HOW not WHAT - how happens matters more than what
  [ ] character reveals truth through action under pressure - they
       surprise even themselves


problem: climax doesn't earn

symptoms:
  - resolution feels unearned
  - solution comes from nowhere
  - character doesn't seem to change
  - climax is deus ex machina

solutions:
  [ ] plant seeds early - solution must be established
  [ ] tie climax to character arc - victory requires growth
  [ ] make it cost something - real sacrifice
  [ ] test everything learned - every skill/knowledge piece used
  [ ] raise stakes to maximum - protagonist must give everything


PHASE 9: REVISION CHECKLIST


act one check

  [ ] is normal world established?
  [ ] is protagonist clear and sympathetic?
  [ ] is flaw/wound/lie established?
  [ ] does inciting incident disrupt normal?
  [ ] does protagonist choose to engage? (not forced)
  [ ] is point of no return clear?
  [ ] are stakes established?


act two check

  [ ] is new world distinct from normal?
  [ ] are there tests and challenges?
  [ ] is there a midpoint shift?
  [ ] do stakes escalate?
  [ ] does protagonist try and fail?
  [ ] is there a clear all-is-lost moment?
  [ ] does protagonist realize their flaw?
  [ ] does dark night lead to new understanding?


act three check

  [ ] does protagonist choose to face final challenge?
  [ ] is climax the hardest test yet?
  [ ] does protagonist use everything learned?
  [ ] is growth demonstrated through action?
  [ ] does victory cost something?
  [ ] is transformation clear?
  [ ] does world show change?
  [ ] does ending feel earned?


pacing check

  [ ] does act one establish before accelerating?
  [ ] does act two maintain momentum?
  [ ] does act two accelerate toward climax?
  [ ] does act three move fast?
  [ ] do tension levels rise overall?
  [ ] are there slow moments for relief?
  [ ] does ending land with right weight?


subplot check

for each subplot:
  [ ] when is it introduced?
  [ ] what is its arc?
  [ ] how does it connect to main plot?
  [ ] how does it complicate protagonist's journey?
  [ ] when/how is it resolved?
  [ ] what does it add to theme?


character arc check

  [ ] what is the flaw?
  [ ] what is the wound?
  [ ] what is the lie they believe?
  [ ] what is the truth they must learn?
  [ ] what challenges the flaw?
  [ ] when does flaw fail them completely?
  [ ] how do they change?
  [ ] is change demonstrated through action?


PHASE 10: STRUCTURAL EXERCISES


exercise 1: reverse outline

take a story you admire and create its beat sheet.

  [ ] identify each structural beat
  [ ] note word count percentages
  [ ] examine how subplots weave
  [ ] trace character arc through structure
  [ ] identify pacing techniques

goal: internalize structural patterns.


exercise 2: the 50-word outline

condense your story into exactly 50 words.

protagonist wants [goal] but [obstacle] stands in way. if they fail,
[stakes]. they try [plan a] but [complication]. then they try [plan b]
but [worse complication]. at [low point], all seems lost. finally,
they [solution] and [resolution].

if you can't do it, you don't know your story.


exercise 3: scene cards

create one card per scene.

for each scene:
  [ ] pov character
  [ ] scene goal
  [ ] scene conflict
  [ ] scene outcome
  [ ] scene consequence
  [ ] word count

arrange scenes. can you see the structure?


exercise 4: structural stress test

take your outline and identify:

what could you cut without breaking the story?
  - if nothing, your story is too thin
  - if everything, your story has no core

what's essential?
  - these are your structural pillars
  - everything else serves them

find the minimum viable story. then build from there.


exercise 5: the five-minute version

tell your story in five minutes.

  [ ] setup
  [ ] inciting incident
  [ ] act two journey
  [ ] midpoint shift
  [ ] all is lost
  [ ] climax
  [ ] resolution

record yourself. where do you get lost? where does it feel thin?

those are your structural problems.


PHASE 11: PLOT RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER force your story into a structure that doesn't fit
      structure serves story, not vice versa
      if it's not working, question the structure, not the story

  [2] ACT ONE must end when protagonist leaves comfort
      not before, not after
      crossing the threshold is the door into act two

  [3] MIDPOINT must change the context
      before: problem seems like X
      after: problem is actually Y
      if nothing shifts, it's not a midpoint

  [4] ALL IS LOST must feel hopeless
      protagonist's usual strategies must fail completely
      this is the bottom
      only then can transformation occur

  [5] CLIMAX must be earned by everything that came before
      no sudden powers
      no convenient coincidences
      no unexpected solutions
      the solution must be planted early

  [6] CHARACTER ARC must be tied to plot structure
      external obstacles force internal change
      internal change enables external victory
      they are the same story

  [7] EVERY SCENE must change the situation
      entry value != exit value
      if nothing changes, cut the scene

  [8] SUBPLOTS must serve the main plot
      if subplot can be removed, remove it
      if subplot doesn't connect, make it connect

  [9] PACING must accelerate overall
      act one: establish
      act two: build
      act three: sprint
      variations within, but overall curve rises

  [10] STAKES must be personal
       if protagonist can walk away, they will
       make it impossible to walk away
       make it matter to the character


FINAL REMINDERS


structure is invisible

when it works, reader doesn't notice it.
they just feel the story working.
when it fails, everyone notices.

know the rules so you can break them.

templates are tools, not laws.
learn them, use them, abandon them when necessary.
the only rule is: does it work?


tension is everything

plot is just a machine to create tension.
everything serves the tension.

if tension drops, fix it or cut it.
if tension never builds, structure is wrong.


character is the engine

plot happens to character.
character's choices drive plot.
no character, no plot worth reading.

structure exists to force character to confront their flaw.
everything else is decoration.


the goal

a story that:
  [ ] grabs reader and won't let go
  [ ] makes reader feel
  [ ] feels inevitable and surprising
  [ ] delivers on promises
  [ ] leaves reader changed

structure is how you get there.

now go build something that matters.
