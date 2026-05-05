---
name: worldbuilding
description: "Worldbuilding skill - creating consistent, immersive fictional worlds"
---

worldbuilding mode: WORLDS FIRST, DETAILS SECOND

when this skill is active, you follow systematic worldbuilding.
this is a comprehensive guide to building believable fictional worlds.


PHASE 0: EXISTING WORLD VERIFICATION

before building ANY new world element, check what already exists.


check for existing world notes

  <terminal>ls world/</terminal>

if no world directory:
  <create>
  <file>world/README.md</file>
  <content>
  Worldbuilding Database

  This directory contains all worldbuilding notes and development work.

  Structure:
  - world_bible.md - master world reference
  - geography.md - maps, locations, physical world
  - history.md - timeline, past events, background
  - cultures/ - cultural notes for each society
  - magic_system.md (if applicable) - rules of magic/tech
  - politics.md - governments, laws, conflicts
  - economy.md - trade, resources, currency
  - creatures.md (if applicable) - fauna and beings
  - language.md - linguistic notes, naming conventions
  </content>
  </create>


check for world bible

  <read><file>world/world_bible.md</file></read>

if no world bible exists, create one:
  <create>
  <file>world/world_bible.md</file>
  <content>
  World Bible

  Story Title: [YOUR STORY HERE]

  Last Updated: [DATE]

  World Overview:
  [One-paragraph description of the world]

  World Type: [primary/secondary/constructed]
  - Primary: fictional version of real world
  - Secondary: completely invented world
  - Constructed: detailed invented world with its own rules

  Key Elements:
  [ ] Magic system: [yes/no - if yes, reference file]
  [ ] Multiple cultures: [yes/no - if yes, list]
  [ ] Non-human species: [yes/no - if yes, list]
  [ ] Special technology: [yes/no - describe]
  [ ] Unique geography: [yes/no - describe]

  Consistency Notes:
  [Track established rules that must be maintained]

  Questions for Future Development:
  [List aspects that need development]
  </content>
  </create>


check for existing story context

  <read><file>story/premise.md</file></read>
  <read><file>story/outline.md</file></read>

understand the story before building the world.
the world exists to SERVE the story, not replace it.


verify consistency tracking

  <read><file>world/consistency_log.md</file></read>

if no consistency log exists:
  <create>
  <file>world/consistency_log.md</file>
  <content>
  Worldbuilding Consistency Log

  Use this log to track world rules and maintain consistency.

  Format for each entry:
  DATE - CATEGORY - Rule established - Location

  Example:
  2024-01-15 - MAGIC - Fire spells require spoken words - Chapter 3
  2024-01-16 - GEOGRAPHY - River flows north (unusual) - Chapter 5

  Quick Reference:

  Magic Rules:
  [Establish what magic can and cannot do]

  Technology Level:
  [Define tech capabilities and limitations]

  Geography Facts:
  [Note any unusual geographical features]

  Cultural Norms:
  [Track established cultural behaviors]

  Timeline:
  [Key dates and events in order]
  </content>
  </create>


PHASE 1: THE WORLD FOUNDATION


the story-world relationship

  worldbuilding serves the story, never the reverse.

  build ONLY what the story needs:
  [ ] what settings are required for plot events?
  [ ] what cultures are needed for characters?
  [ ] what rules (magic/tech) affect the plot?
  [ ] what history created the current situation?

  extra worldbuilding is wasted effort.
  readers won't see 90% of what you create.


the iceberg principle in worldbuilding

  show 10%, know 90%.

  what readers see:
  - immediate setting of each scene
  - cultural details relevant to current action
  - rules as they affect plot/characters
  - history that explains present situations

  what you must know:
  - full geography and how it shapes cultures
  - complete history that created current situation
  - all rules and their implications
  - how everything connects and affects each other

  knowing the 90% creates consistency.
  showing only 10% keeps story moving.


determining world scope

  world scope matches story scale.

  intimate story (small scale):
  - few locations, deeply detailed
  - one culture focus
  - limited historical scope
  - example: room-based drama, small town story

  regional story (medium scale):
  - multiple connected locations
  - 2-3 cultures in contact
  - regional history relevant to plot
  - example: fantasy quest, political thriller

  epic story (large scale):
  - many diverse locations
  - multiple cultures interacting
  - world-spanning history
  - example: war stories, empire sagas

  match scope to story needs.
  bigger is not always better.


exercise: scope definition

  for your story, define the scope:

  story scale: [intimate/regional/epic]

  required locations:
    [ ] [list only what's needed for plot]

  required cultures:
    [ ] [list only what's needed for characters]

  required history:
    [ ] [list only past events relevant to current story]

  <create>
  <file>world/scope.md</file>
  <content>
  # World Scope for [STORY NAME]

  ## Story Scale
  Type: [intimate/regional/epic]
  Reason: [why this scope serves the story]

  ## Required Locations
  Primary settings:
    - [name]: [function in story]
    - [name]: [function in story]

  Secondary settings:
    - [name]: [function in story]
    - [name]: [function in story]

  ## Required Cultures
  Primary culture:
    - [name]: [connection to protagonist/plot]

  Secondary cultures:
    - [name]: [relationship to primary]
    - [name]: [relationship to primary]

  ## Required History
  Relevant past events:
    - [event]: [how it affects current story]
    - [event]: [how it affects current story]
  </content>
  </create>


PHASE 2: GEOGRAPHY AND PHYSICAL WORLD


geography as story foundation

  geography shapes everything else:
  - cultures develop based on available resources
  - politics emerge from geographical boundaries
  - history flows from geographical conflict

  build geography FIRST, then let it shape the rest.


essential geographical elements

  [1] terrain type
    - mountains, plains, deserts, forests, islands, etc.
    - each terrain type creates specific cultural adaptations

  [2] water features
    - oceans, seas, rivers, lakes
    - water = civilization (trade, food, travel)

  [3] climate
    - temperature, rainfall, seasons
    - determines agriculture, architecture, clothing

  [4] resources
    - what's available? what's scarce?
    - scarcity drives trade and conflict

  [5] natural barriers
    - mountains, deserts, oceans
    - create isolation, cultural difference, political boundaries


realism in geography

  even fantasy worlds should feel geographically plausible.

  principles:

  [1] rivers flow downhill (toward oceans/lakes)
  [2] mountains form in ranges, not randomly
  [3] climate varies by latitude and altitude
  [4] deserts often exist on one side of mountain ranges
  [5] civilizations cluster near water
  [6] borders follow natural features

  fantasy can break these rules, but should have reasons.
  "magic did it" is a weak reason.
  "the gods created this mountain as a barrier" is better.


exercise: map sketching

  sketch your world's geography:

  [ ] draw rough outline of landmasses
  [ ] mark mountain ranges
  [ ] draw major rivers
  [ ] mark seas/oceans
  [ ] note climate zones
  [ ] mark resource locations
  [ ] identify where civilizations would naturally form

  you don't need artistic skill.
  stick figures and labels work fine.
  the goal is understanding, not presentation.


the map narrative

  write a paragraph describing travel from point A to point B.

  this forces you to think about:
  - distance and travel time
  - terrain difficulties
  - what travelers would see
  - how geography connects locations

  <create>
  <file>world/geography/travel_[a]_to_[b].md</file>
  <content>
  # Travel Narrative: [LOCATION A] to [LOCATION B]

  Distance: [approximately how far]
  Method of travel: [how people make this journey]
  Typical travel time: [how long it takes]

  Route Description:
  [write a paragraph describing what a traveler sees and experiences
   along the way - terrain, landmarks, dangers, stops]

  Seasonal Variations:
  [how does this journey change in different seasons?]

  Dangers:
  [what makes this journey risky?]
  </content>
  </create>


PHASE 3: HISTORY AND TIMELINE


history as current story foundation

  history explains WHY the world is as it is.

  current situations have historical causes:
  - a war created current political boundaries
  - a past atrocity fuels current hatred
  - an old agreement created current alliances
  - a past disaster shaped current culture

  don't invent history for its own sake.
  invent history to explain the present.


building a timeline

  create a timeline that serves the story.

  structure:

  [1] deep past - creation myths, ancient history
      [what do people believe about the beginning?]

  [2] major events - empire rises/falls, great wars, discoveries
      [what large events shaped the world?]

  [3] recent past - living memory, current generation
      [what happened that people currently remember?]

  [4] immediate past - what led to current situation
      [what directly created the story's starting point?]

  focus most attention on [3] and [4].
  these affect characters directly.


timeline depth guide

  how much history do you need?

  deep past (1000+ years ago):
  - creation myths
  - founding of major institutions
  - ancient wars that are legendary
  - only what affects current beliefs

  major events (100-1000 years ago):
  - political boundaries established
  - current cultures emerged
  - major conflicts that still resonate
  - only what explains current tensions

  recent past (0-100 years ago):
  - characters' childhoods
  - events parents experienced
  - forming of current alliances
  - anything that affects character motivations

  immediate past (0-10 years ago):
  - directly caused current situation
  - relevant to protagonist's backstory
  - created current conflicts
  - detailed and specific


exercise: timeline creation

  create a timeline for your world:

  <create>
  <file>world/history/timeline.md</file>
  <content>
  # World Timeline: [WORLD NAME]

  ## Deep Past (Myth/Ancient)
  [Year/ Era] - [Event]: [Significance to current world]

  ## Major Events
  [Year] - [Event]: [Significance to current world]
  [Year] - [Event]: [Significance to current world]
  [Year] - [Event]: [Significance to current world]

  ## Recent Past (Living Memory)
  [Year] - [Event]: [Who remembers this? How does it affect them?]
  [Year] - [Event]: [Who remembers this? How does it affect them?]

  ## Immediate Past (Pre-Story)
  [Year] - [Event]: [Direct connection to story beginning]
  [Year] - [Event]: [Direct connection to story beginning]

  ## The Event That Started Everything
  [Year] - [Event]: [This is what kicks off your story]
  </content>
  </create>


history through objects

  show history through physical things:

  [1] ruined monuments - what happened here?
  [2] old battlefield - what was this conflict?
  [3] abandoned buildings - why did people leave?
  [4] cultural artifacts - what does this object mean?
  [5] place names - who was this person/place?
  [6] scars on the land - what caused this?

  exercise: historical artifact

  pick a location in your world. create an artifact
  that implies history without exposition.

  artifact: [describe the object]
  location: [where is it found?]
  condition: [what state is it in?]
  implied history: [what does this suggest about the past?]

  write a scene where a character encounters this artifact.
  show their reaction. reveal history through interaction.


PHASE 4: CULTURE CREATION


culture as character writ large

  cultures are collective characters.
  they have personalities, flaws, virtues, conflicts.

  culture components:

  [1] values - what matters most?
  [2] norms - what behaviors are expected?
  [3] taboos - what's forbidden?
  [4] rituals - what behaviors are performed?
  [5] institutions - what structures organize society?
  [6] arts - what do they create?
  [7] beliefs - what do they hold true?


values foundation

  every culture is built on values.

  value questions:

  [ ] what is this culture's highest good?
      (honor? freedom? order? harmony? strength?)

  [ ] what is this culture's greatest fear?
      (chaos? weakness? shame? impurity? death?)

  [ ] what do they admire in others?
      (bravery? wisdom? beauty? wealth? piety?)

  [ ] what do they despise?
      (cowardice? stupidity? ugliness? poverty? impiety?)

  [ ] what makes someone a good member of this culture?
      (what virtues do they teach their children?)

  values create behaviors.
  behaviors reveal values.


norms and behaviors

  norms are how values show up in daily life.

  norm categories:

  [1] social norms
      - greetings and farewells
      - personal space
      - eye contact
      - touching behavior
      - queuing/waiting
      - gift giving

  [2] communication norms
      - direct vs indirect speech
      - interrupting or listening
      - emotion display
      - volume and tone
      - silence meaning

  [3] work norms
      - punctuality expectations
      - effort vs result
      - hierarchy respect
      - collaboration style

  [4] gender norms (if applicable)
      - roles and expectations
      - dress and behavior
      - power dynamics
      - flexibility or rigidity


taboos

  taboos are strong prohibitions.

  every culture has lines you don't cross.

  taboo levels:

  [1] mild taboos - social disapproval
      "you don't talk about money at dinner"

  [2] strong taboos - social punishment
      "adultery destroys your marriage prospects"

  [3] absolute taboos - expulsion or death
      "murder of a guest is punishable by execution"

  taboos reveal what a culture sacredly protects.
  they're excellent sources of conflict.


exercise: culture profile

  create a profile for one culture:

  <create>
  <file>world/cultures/[culture_name].md</file>
  <content>
  # Culture Profile: [NAME]

  ## Basics
  Location: [where do they live?]
  Population: [approximate size]
  Neighbors: [who do they interact with?]

  ## Values
  Highest good: [what matters most]
  Greatest fear: [what they most fear]
  Admired traits: [what they value in people]
  Despised traits: [what they hate]

  ## Norms
  Greeting: [how do they greet each other?]
  Personal space: [close or distant?]
  Eye contact: [direct or averted?]
  Communication: [direct or indirect?]
  Time: [precise or flexible?]

  ## Taboos
  Mild: [what causes social disapproval?]
  Strong: [what causes real punishment?]
  Absolute: [what is absolutely forbidden?]

  ## Institutions
  Government: [how are they ruled?]
  Family structure: [how are families organized?]
  Education: [how do they learn?]
  Economy: [how do they get resources?]

  ## Arts and Expression
  What they create: [art, music, literature, etc.]
  Aesthetic preferences: [what do they find beautiful?]
  Storytelling traditions: [what stories do they tell?]
  </content>
  </create>


PHASE 5: POLITICAL SYSTEMS


politics as conflict engine

  political systems create story conflicts.

  [1] power struggles - who rules? who wants to?
  [2] ideological conflicts - different visions of society
  [3] resource conflicts - who gets what?
  [4] succession crises - what happens when power changes?
  [5] foreign relations - war, alliances, trade


government types

  consider how your society is governed:

  [1] monarchy - rule by one (hereditary)
      - strength: stability, clarity
      - weakness: depends on single ruler's quality
      - conflict source: succession, bad rulers

  [2] aristocracy - rule by few (elite)
      - strength: expertise, continuity
      - weakness: protects elite interests
      - conflict source: exclusion, resentment

  [3] democracy - rule by many (citizens)
      - strength: representation, adaptability
      - weakness: slow, factional
      - conflict source: polarization, demagoguery

  [4] theocracy - rule by religious authority
      - strength: unified values
      - weakness: inflexibility, persecution
      - conflict source: heresy, interpretation disputes

  [5] magocracy/technocracy - rule by magic/tech users
      - strength: competence in domain
      - weakness: power inequality
      - conflict source: resource access, resentment


power distribution

  how is power actually held and exercised?

  [1] centralized - power concentrated at top
      - efficient, coordinated
      - vulnerable to single point failure
      - creates distance between rulers and ruled

  [2] decentralized - power distributed
      - resilient, adaptable
      - can be inefficient
      - multiple power centers create competition

  [3] federated - regional autonomy with central authority
      - balance of local and central
      - constant tension between levels

  [4] anarchic - no formal government
      - freedom, chaos
      - power goes to those who can take it


law and justice

  how does the society maintain order?

  [ ] what behaviors are illegal?
  [ ] who makes the laws?
  [ ] who enforces the laws?
  [ ] how are crimes punished?
  [ ] how are disputes resolved?
  [ ] is justice equal or stratified?

  exercise: crime and punishment

  for your culture, define:

  crime: [specific act]
  punishment: [what happens?]
  reasoning: [why this punishment?]

  example:
    crime: theft of food
    punishment: repayment + public shaming
    reasoning: theft shows need, not malice; shame maintains order

  create three such examples showing the culture's values.


PHASE 6: ECONOMICS AND RESOURCES


economics as world foundation

  economics shapes:
  - what cultures can afford (war, art, technology)
  - where power lies (who controls resources)
  - how people live (poverty, luxury, work)
  - what conflicts emerge (resource competition)


resource mapping

  what does your world have?

  [1] abundant resources
      - what's plentiful?
      - who controls it?
      - how does this create wealth/power?

  [2] scarce resources
      - what's rare?
      - who needs it?
      - how does this create conflict/innovation?

  [3] unique resources
      - what exists only in one place?
      - why is it valuable?
      - who fights to control it?

  resources aren't just material.
  knowledge, magic, technology can be resources too.


trade and exchange

  how do goods and services move?

  [1] trade routes
      - what paths connect cultures?
      - what goods travel these routes?
      - what dangers do traders face?

  [2] trade centers
      - where do cultures meet and exchange?
      - what makes these locations ideal?
      - who controls these hubs?

  [3] currencies
      - what do people use for exchange?
      - coins? barter? favors? reputation?
      - what gives it value?

  [4] trade relationships
      - who trades with whom?
      - who's dependent on whom?
      - what creates leverage?


wealth distribution

  how is wealth shared?

  [1] equal - minimal inequality
      - often small, homogeneous cultures
      - may limit individual achievement
      - requires strong social cohesion

  [2] stratified - significant inequality
      - common in large, complex societies
      - creates social tension
      - justifications vary (divine right, merit, etc.)

  [3] highly unequal - extreme wealth gap
      - small elite, large underclass
      - unstable without force
      - source of conflict and revolution

  exercise: economic snapshot

  create an economic profile:

  <create>
  <file>world/economy/[culture_name]_economy.md</file>
  <content>
  # Economic Profile: [CULTURE NAME]

  ## Resources
  Abundant:
    - [resource]: [who benefits?]
  Scarce:
    - [resource]: [who needs it?]
  Unique:
    - [resource]: [why is it special?]

  ## Trade
  Major routes: [where do goods flow?]
  Key goods: [what's traded?]
  Trade partners: [who do they exchange with?]
  Currency: [what do they use for exchange?]

  ## Wealth Distribution
  Level: [equal/stratified/highly unequal]
  Justification: [how is this explained/defended?]
  Tension: [is this stable? what conflicts exist?]

  ## Economic Activities
  Primary: [how do they get resources?]
  Secondary: [how do they process them?]
  Tertiary: [what services exist?]
  </content>
  </create>


PHASE 7: MAGIC OR TECHNOLOGY SYSTEMS


magic/tech as rules-based systems

  whether magic or advanced technology, create clear rules.

  systems need:
  [1] source - where does it come from?
  [2] cost - what does using it require?
  [3] limits - what can't it do?
  [4] consequences - what happens when used?
  [5] availability - who can access it?


magic system design

  hard magic (explicit rules):
  - defined capabilities and limits
  - consistent costs
  - can be used strategically by characters
  - example: brandon sanderson's systems

  soft magic (mysterious):
  - undefined capabilities
  - unpredictable effects
  - serves as wonder/problem, not solution
  - example: tolkien's magic

  most stories use both:
  hard magic for protagonists (solve problems)
  soft magic for antagonists (create mystery)


magic system questionnaire

  [ ] source
      - where does magic come from?
      - is it inherent, learned, granted, found?

  [ ] access
      - who can use magic?
      - is it common, rare, unique?
      - how do people gain access?

  [ ] cost
      - what does using magic cost?
      - energy, health, sanity, materials?
      - can the cost kill you?

  [ ] limits
      - what can magic NOT do?
      - are there immutable rules?
      - what happens when rules are broken?

  [ ] types
      - are there different kinds of magic?
      - do they interact or conflict?
      - can they be combined?

  [ ] social position
      - how does society treat magic users?
      - honored? feared? hunted? controlled?
      - how does this shape the world?


technology levels

  define what technology exists in your world.

  tech continuum:
  [1] pre-technical - stone tools, hunter-gatherer
  [2] agricultural - farming, basic settlements
  [3] pre-industrial - craft specialization, simple machines
  [4] industrial - mass production, mechanization
  [5] modern - information age, digital technology
  [6] advanced - AI, space travel, post-scarcity
  [7] futuristic - beyond current understanding

  fantasy worlds often have mixed tech:
  - medieval weapons + advanced medicine
  - pre-industrial transport + magical communication
  - ancient society + lost high-tech past

  whatever you choose, be consistent.


exercise: magic/tech system profile

  define your system:

  <create>
  <file>world/[magic_tech]_system.md</file>
  <content>
  # [Magic/Technology] System: [WORLD NAME]

  ## Type
  Category: [hard/soft system]
  Source: [where does it come from?]

  ## Access
  Who can use it: [anyone/some/few/unique]
  How gained: [born with/learned/granted/etc]
  Training required: [years of study/days/none]

  ## Capabilities
  What it CAN do:
    - [capability 1]: [specific example]
    - [capability 2]: [specific example]
    - [capability 3]: [specific example]

  What it CANNOT do (hard limits):
    - [limitation 1]: [why?]
    - [limitation 2]: [why?]
    - [limitation 3]: [why?]

  ## Cost
  Price of use: [energy/material/sanity/etc]
  Short-term consequence: [what happens immediately?]
  Long-term consequence: [what happens with repeated use?]
  Can it kill you? [how?]

  ## Society
  Public attitude: [how do people feel about it?]
  Legal status: [is it regulated/banned/etc?]
  Social position of users: [honored/fear/hunted/etc]
  Institutions: [what organizations control it?]

  ## Plot Integration
  How it creates conflict: [ ]
  How it solves problems: [ ]
  How it shapes the world: [ ]
  </content>
  </create>


PHASE 8: CULTURAL DIVERSITY AND CONTACT


multiple cultures

  most stories involve more than one culture.

  create diversity through:
  [1] different values - what matters varies
  [2] different norms - behaviors differ
  [3] different resources - environments shape cultures
  [4] different histories - past events created differences
  [5] different adaptations - similar problems, different solutions


cultural contact zones

  cultures interact in predictable ways:

  [1] trade cities - where goods and ideas exchange
      - cultural mixing
      - hybrid cultures emerge
      - tolerance and tension

  [2] borderlands - where cultures meet and clash
      - military presence
      - cultural blending or conflict
      - identity complexity

  [3] conquest zones - one culture dominates another
      - power inequality
      - resistance and adaptation
      - cultural trauma

  [4] diaspora communities - dispersed cultures
      - preserving identity
      - assimilation pressures
      - cultural evolution


cultural differences as conflict

  culture clash creates story conflict:

  [1] value conflicts
      - what's good in one culture is bad in another
      - example: individualism vs collectivism

  [2] norm conflicts
      - polite in one culture is rude in another
      - example: direct vs indirect communication

  [3] resource conflicts
      - cultures want the same things
      - example: competing claims to land

  [4] religious conflicts
      - different beliefs about ultimate questions
      - example: different gods, different rules

  [5] historical conflicts
      - past wrongs, remembered grievances
      - example: old wars, old atrocities


avoiding monocultures

  no culture is uniform.

  within any culture, you find:
  [1] subcultures - regional, class,职业 variations
  [2] counter-cultures - groups rejecting mainstream
  [3] individual variation - not everyone conforms
  [4] change over time - cultures aren't static
  [5] internal conflict - cultures argue with themselves

  exercise: cultural diversity

  pick your main culture. create internal variation:

  subculture 1:
    - how do they differ from mainstream?
    - where are they located?
    - how are they viewed?

  subculture 2:
    - how do they differ from mainstream?
    - where are they located?
    - how are they viewed?

  counter-culture:
    - what do they reject?
    - what do they offer instead?
    - how does the mainstream view them?


PHASE 9: LANGUAGE AND NAMES


language basics

  you don't need to create a full language.
  you need naming consistency and linguistic flavor.

  language components to consider:

  [1] phonemes - what sounds exist?
      - harsh consonants vs flowing vowels
      - click sounds, tones, etc.
      - creates cultural "feel"

  [2] syntax - how are sentences built?
      - word order patterns
      - simple or complex structure
      - influences translation style

  [3] vocabulary - what concepts exist?
      - words for things important to the culture
      - no words for concepts they don't have
      - reveals cultural priorities

  [4] idioms - figurative expressions
      - reveal cultural worldview
      - add authenticity
      - create immersion


naming conventions

  consistent naming creates believable worlds.

  consider:

  [1] name structure
      - given name + family name?
      - given name + clan name + given name?
      - name changes over life?
      - religious/secular names?

  [2] name meanings
      - do names have meanings?
      - are meanings significant?
      - how are names chosen?

  [3] name sources
      - nature? ancestors? virtues?
      - random? divined? purchased?

  [4] foreign names
      - how do they adapt names from other cultures?
      - translate? transliterate? avoid?


exercise: naming guide

  create a naming guide for one culture:

  <create>
  <file>world/language/[culture]_naming.md</file>
  <content>
  # Naming Guide: [CULTURE NAME]

  ## Structure
  Full name format: [explain how names are structured]
  Example: [give an example name breakdown]

  ## Given Names
  Source: [where do given names come from?]
  Meanings: [do names have meanings?]
  Gender: [are names gendered?]
  Examples:
    - Male: [list 5-10 examples]
    - Female: [list 5-10 examples]
    - Neutral: [list 5-10 examples]

  ## Family Names
  Source: [where do family names come from?]
  Inheritance: [how are they passed down?]
  Examples: [list 10 common family names]

  ## Special Names
  Honorifics: [titles and respect forms]
  Religious names: [if applicable]
  Outsider names: [how are foreign names handled?]

  ## Sound Patterns
  Common sounds: [what phonemes are frequent?]
  Forbidden sounds: [what sounds don't occur?]
  Rhythm: [stressed/unstressed patterns]
  Length: [typical name length]
  </content>
  </create>


creating foreign flavor

  add linguistic flavor without full conlangs:

  [1] recurring foreign words
      - concepts that don't translate
      - terms of address
      - cultural-specific ideas

  [2] names and titles
      - use character names from the culture
      - show respect forms
      - reveal hierarchy

  [3] translated idioms
      - "may your road be smooth"
      - "the wolf at the door"
      - reveal cultural worldview

  [4] speech patterns in translation
      - formal vs casual grammar
      - direct vs indirect communication
      - shows cultural approach


PHASE 10: RELIGION AND BELIEF SYSTEMS


religion as culture foundation

  beliefs shape:
  - what people value (morality)
  - how they behave (norms, taboos)
  - how they explain the world (cosmology)
  - how they organize (institutions)
  - what they hope for (eschatology)


belief system components

  [1] cosmology - what is the universe?
      - creation story
      - structure of reality
      - place of humans in the universe

  [2] theology - what are the gods/powers?
      - number and nature of deities
      - their powers and domains
      - their relationships to each other and humans

  [3] eschatology - what happens after death?
      - afterlife (or not)
      - judgment, reward, punishment
      - ultimate fate of the world

  [4] praxis - what do believers do?
      - rituals, prayers, practices
      - holy days, festivals
      - life cycle events (birth, marriage, death)

  [5] morality - how should believers live?
      - commandments, virtues, sins
      - guidance for daily life
      - resolution of moral dilemmas


religious diversity

  worlds rarely have single beliefs.

  within-culture diversity:
  - different interpretations
  - different sects/denominations
  - orthodox vs heterodox
  - believers vs non-believers

  between-culture diversity:
  - different religions
  - syncretic blending
  - proselytizing and conversion
  - religious conflict


religion and story

  religion can:
  [1] motivate characters - true believers act on faith
  [2] create conflict - religious disagreements
  [3] explain world - beliefs shape behavior
  [4] provide symbolism - ritual, metaphor
  [5] offer resolution - spiritual answers

  exercise: belief system profile

  <create>
  <file>world/religions/[belief_name].md</file>
  <content>
  # Belief System: [NAME]

  ## Basics
  Type: [monotheism/polytheism/animism/philosophy/etc]
  Followers: [who believes this?]
  Distribution: [where is it believed?]

  ## Cosmology
  Creation: [how did the universe begin?]
  Structure: [what is the universe like?]
  Humanity's place: [where do humans fit?]

  ## Theology
  Deities/Forces: [what powers exist?]
  Domains: [what do they control?]
  Relationships: [how do they relate to each other? to humans?]
  Intervention: [do they act in the world? how?]

  ## Eschatology
  Afterlife: [what happens after death?]
  Judgment: [is there judgment? criteria?]
  Ultimate fate: [where is everything going?]

  ## Praxis
  Daily practices: [what do believers do daily?]
  Weekly rituals: [what happens regularly?]
  Annual festivals: [what special days exist?]
  Life events: [birth, marriage, death rituals]

  ## Morality
  Virtues: [what is good?]
  Sins: [what is evil?]
  Guidance: [how do believers know what to do?]

  ## Institutions
  Organization: [how is belief organized?]
  Leadership: [who leads? how are they chosen?]
  Spaces: [where do believers gather?]
  Relationship to state: [how does power interact with belief?]
  </content>
  </create>


PHASE 11: DAILY LIFE AND MATERIAL CULTURE


daily life creates immersion

  readers connect with daily details:
  - what people eat
  - how they dress
  - where they live
  - how they travel
  - what they do for fun

  these details make the world feel real.


food and drink

  food culture reveals:
  - geography (what's available)
  - wealth (what can they afford?)
  - values (hospitality, status, etc.)
  - technology (cooking methods, preservation)

  consider:
  [ ] staples - what do people eat daily?
  [ ] delicacies - what's special or rare?
  [ ] taboos - what won't they eat?
  [ ] meals - how many and when?
  [ ] dining - who eats together? how?
  [ ] drink - what do they drink? alcohol? water?


clothing and appearance

  clothing communicates:
  - status (wealth, rank)
  - identity (culture, profession)
  - values (modesty, display, etc.)
  - practicality (climate, activity)

  consider:
  [ ] materials - what fabrics are available?
  [ ] styles - what do people wear?
  [ ] variation - by class, gender, profession?
  [ ] symbolism - do clothes have meaning?
  [ ] taboo - what's not worn?


housing and architecture

  buildings show:
  - technology (what can they build?)
  - wealth (what can they afford?)
  - values (privacy, community, etc.)
  - environment (adaptation to climate)

  consider:
  [ ] materials - stone, wood, brick, etc.
  [ ] layout - how are spaces arranged?
  [ ] density - cities or villages?
  [ ] public spaces - what do they build together?
  [ ] defensive features - walls, forts, etc?


transport and communication

  how do people and information move?

  transport:
  [ ] what methods exist? (walking, riding, vehicles, etc.)
  [ ] how long does travel take?
  [ ] who can travel? (everyone, elite, merchants?)
  [ ] what infrastructure exists? (roads, ports, etc.)

  communication:
  [ ] how do messages travel?
  [ ] how fast is communication?
  [ ] who has access to information?
  [ ] how is information controlled?


exercise: daily life scene

  write a scene showing daily life in your world.

  follow one character through:
  [ ] waking up - what's their home like?
  [ ] breakfast - what do they eat?
  [ ] work - what do they do?
  [ ] lunch - break routine
  [ ] evening - what do they do for fun?
  [ ] sleep - how does the day end?

  focus on sensory details:
  - smells, tastes, sounds
  - textures, temperatures
  - what's seen, heard, felt


PHASE 12: REVEALING WORLD THROUGH STORY


the iceberg in practice

  you know 90% of the world.
  show only 10%.

  principles of revelation:

  [1] relevance first
      - show what's relevant to current scene
      - explain only when necessary for understanding
      - trust readers to infer

  [2] action over exposition
      - show the world in motion
      - let behavior reveal culture
      - demonstrate rules through consequences

  [3] character perspective
      - reveal what the character notices
      - what's strange to them is strange to us
      - what's normal to them becomes normal to us

  [4] conflict-driven
      - reveal world through problems
      - cultural clashes reveal differences
      - rule violations show what's forbidden


avoiding info-dumps

  info-dump: large block of worldbuilding explanation.

  problems:
  - stops story momentum
  - feels like lecture
  - readers skim and forget

  alternatives:

  [1] weave into action
      instead of explaining the political system,
      show a character navigating it

  [2] dialogue
      have characters explain what they already know
      to someone who doesn't (fish out of water)

  [3] triggered memory
      current event reminds character of past
      reveal through association

  [4] demonstration
      show the rule/concept in action
      let readers infer the explanation


the stranger guide

  classic technique: stranger arrives in new world.

  why it works:
  - stranger needs explanations (justifies exposition)
  - stranger notices what locals take for granted
  - reader learns with stranger
  - culture shock creates natural conflict

  pitfalls:
  - don't overuse
  - stranger needs own arc (not just tour guide)
  - locals shouldn't be stupid
  - balance curiosity with urgency


exercise: world introduction

  write a scene introducing your world.

  requirements:
  [ ] establish setting through action
  [ ] reveal one cultural norm through behavior
  [ ] hint at one rule (magic/tech/social)
  [ ] create a question in the reader's mind
  [ ] no direct exposition longer than 2 sentences

  <create>
  <file>world/world_introduction_scene.md</file>
  <content>
  # World Introduction Scene

  Draft:
  [write your scene here]

  Checklist:
  [ ] setting established through action
  [ ] cultural norm revealed through behavior
  [ ] world rule hinted at
  [ ] reader questions created
  [ ] exposition limited

  Revision Notes:
  [what works? what needs improvement?]
  </content>
  </create>


PHASE 12: WORLD CONSISTENCY SYSTEMS


consistency is immersion

  inconsistency breaks the world spell.
  readers stop believing when rules contradict themselves.

  consistency systems:

  [1] rule documentation
      - write down every rule you establish
      - update when rules are revealed
      - check before contradicting

  [2] consequence tracking
      - actions have consistent effects
      - if X happens, Y always results
      - readers learn the world's logic

  [3] cultural coherence
      - values connect to behaviors
      - geography influences culture
      - history explains present

  [4] cause and effect
      - everything has a reason
      - nothing happens "just because"
      - world operates on logic


the world bible

  maintain a master reference document.

  sections:
  [ ] physical rules - geography, physics
  [ ] magic/tech rules - systems and limits
  [ ] cultural rules - norms, taboos, values
  [ ] political rules - government, laws
  [ ] economic rules - resources, trade
  [ ] linguistic rules - names, language patterns
  [ ] historical timeline - events and their consequences

  update as you write.
  refer to when planning scenes.


tracking systems

  for ongoing consistency:

  [ ] rule log
      date | category | rule established | location
      2024-01-15 | magic | fire requires gesture | ch3
      2024-01-16 | culture | shoes removed indoors | ch5

  [ ] location register
      name | description | connections | first appearance
      tavern | meeting place, neutral ground | connected to thieves | ch1

  [ ] character register
      name | culture | role | relationships
      kira | northern | protagonist | sister to joren

  [ ] terminology guide
      term | meaning | first use | notes
      flow | magical energy | ch1 | can be dangerous


PHASE 13: WORLDBUILDING CHECKLISTS


comprehensive world checklist

  for complete worlds, verify:

  geography:
    [ ] map created (even if rough)
    [ ] climate zones established
    [ ] resources identified
    [ ] travel times calculated
    [ ] natural barriers noted

  history:
    [ ] timeline created
    [ ] major events established
    [ ] recent past detailed
    [ ] connection to current story clear

  cultures:
    [ ] values defined for each culture
    [ ] norms established
    [ ] taboos identified
    [ ] institutions described
    [ ] variation within cultures noted

  politics:
    [ ] government type defined
    [ ] power structure clear
    [ ] laws and consequences established
    [ ] international relationships defined

  economics:
    [ ] resources mapped
    [ ] trade routes established
    [ ] wealth distribution defined
    [ ] currency/exchange system clear

  magic/tech (if applicable):
    [ ] rules established
    [ ] costs defined
    [ ] limits identified
    [ ] social position determined

  language:
    [ ] naming conventions created
    [ ] foreign words documented
    [ ] speech patterns defined


scene-level world checklist

  before writing each scene, verify:

  [ ] where is this scene? (specific location)
  [ ] what's the environment like? (sensory details)
  [ ] what cultural norms apply? (behavior expectations)
  [ ] what world rules affect this scene? (magic/tech/law)
  [ ] what history is relevant? (context)

  after writing each scene, verify:

  [ ] is the setting clear?
  [ ] are cultural behaviors consistent?
  [ ] are world rules followed?
  [ ] is the world revealed through action?
  [ ] are there any contradictions to established material?


consistency checking

  after drafting, do a world pass:

  read for world consistency:
  [ ] are all rules followed?
  [ ] are all locations consistent?
  [ ] are all cultural behaviors consistent?
  [ ] are there any contradictions?

  check causality:
  [ ] does everything have a reason?
  [ ] do consequences follow actions?
  [ ] does the world make logical sense?

  check revelation:
  [ ] is the world revealed gradually?
  [ ] is info-dumping avoided?
  [ ] is exposition balanced with action?


PHASE 14: AVOIDING COMMON WORLDBUILDING PITFALLS


pitfall: overbuilding

  problem: creating more world than the story needs.

  symptoms:
  - encyclopedic detail no reader sees
  - worldbuilding replaces actual story
  - scenes become tours, not narratives

  solution:
  - build only what the story requires
  - add details only when relevant to scene
  - remember: world serves story, not vice versa


pitfall: underbuilding

  problem: discovering contradictions mid-draft.

  symptoms:
  - realizing a location doesn't make sense
  - forgetting previously established rules
  - cultures changing arbitrarily

  solution:
  - build foundation before drafting
  - document rules as established
  - maintain consistency log


pitfall: monocultural worlds

  problem: entire fantasy world speaks with one voice.

  symptoms:
  - every culture thinks alike
  - no cultural conflict or difference
  - foreign cultures are just costume changes

  solution:
  - give each culture distinct values
  - create meaningful cultural differences
  - show variation within cultures


pitfall: exoticism

  problem: treating cultures as curiosities.

  symptoms:
  - describing cultures as "strange" or "exotic"
  - reducing real cultures to stereotypes
  - treating cultural difference as spectacle

  solution:
  - present cultures from within, not from outside
  - show the reasons behind cultural differences
  - treat all cultures with equal respect


pitfall: static worlds

  problem: worlds don't change or have history.

  symptoms:
  - cultures feel like museum exhibits
  - no sense of historical development
  - no current tensions or changes

  solution:
  - show culture in flux
  - include reform movements, generational change
  - let world evolve during story


pitfall: arbitrary rules

  problem: rules that exist only because author said so.

  symptoms:
  - magic/tech rules feel random
  - cultural behaviors have no rationale
  - readers can't predict consequences

  solution:
  - give every rule a reason
  - connect rules to each other
  - ensure internal logic


PHASE 15: WORLD EXERCISES AND PROMPTS


world development exercises

  exercise 1: the travelogue

  write about a journey across your world.

  from: [starting location]
  to: [destination]

  describe:
    - what landscapes are crossed?
    - what cultures are encountered?
    - what dangers exist?
    - how long does it take?
    - what does a traveler need?

  this forces you to think about connections between locations.


exercise 2: the artifact

  create an object that implies world history.

  object: [describe it]
  location: [where is it found?]
  condition: [what state is it in?]

  what does it imply about:
    - who made it?
    - when was it made?
    - what was it used for?
    - why is it here now?

  write a scene where someone discovers this artifact
  and what they learn from it.


exercise 3: the menu

  create a meal from your world.

  culture: [who's eating?]
  occasion: [why is this meal special?]

  list:
    - main dish: [what and why?]
    - side dishes: [what and why?]
    - drink: [what and why?]
    - dessert: [what and why?]

  explain:
    - what does this meal reveal about the culture?
    - what ingredients show geography/wealth?
    - what preparations show technology?
    - what customs show values?


exercise 4: the conflict

  create a conflict based on worldbuilding.

  type: [resource/ideological/territorial/cultural/etc]

  sides:
    - culture A: [what do they want? why?]
    - culture B: [what do they want? why?]

  history:
    - when did this start?
    - what previous events led to this?

  current situation:
    - what's happening now?
    - what could resolve this?

  this exercise connects worldbuilding to story conflict.


exercise 5: the news

  write a news report from your world.

  format: [written/broadcast/sung/etc]

  event: [what happened?]

  perspective: [who's reporting? what's their bias?]

  what does this report reveal about:
    - the world's technology?
    - the world's politics?
    - the world's values?
    - the world's problems?


exercise 6: the conversation

  write a conversation between two people from different
  cultures in your world.

  characters:
    - person from culture A: [who are they?]
    - person from culture B: [who are they?]

  topic: [what are they discussing?]

  show through dialogue:
    - different values
    - different assumptions
    - different communication styles
    - different references/knowledge

  avoid explanation.
  let differences emerge naturally.


PHASE 16: WORLDBUILDING RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] BUILD only what the story needs
      extra worldbuilding is wasted effort
      if it doesn't serve plot/character/theme, cut it

  [2] ESTABLISH rules and FOLLOW them
      readers learn your world's logic
      breaking logic breaks trust
      if you must break a rule, show the breaking

  [3] REVEAL world through ACTION, not exposition
      show cultures behaving, not descriptions of behavior
      show rules in use, not explanations of rules
      show history's effects, not summaries of events

  [4] CREATE cultures with INTERNAL VARIATION
      no culture is uniform
      show subcultures, counter-cultures, individual variation
      cultures should argue with themselves

  [5] MAKE geography SHAPE culture
      environment affects resources, economy, values
      cultures don't exist in vacuum
      show the connection between place and people

  [6] GIVE every cultural element a REASON
      why do they believe this?
      why do they do this?
      why is this taboo?
      if you can't explain why, revise

  [7] MAINTAIN CONSISTENCY once established
      document rules as you create them
      check before changing anything
      contradictions must be explained

  [8] TREAT all cultures with RESPECT
      no "good" or "bad" cultures
      everyone thinks they're right
      show the reasons behind differences

  [9] REMEMBER that WORLD serves STORY
      the world is the stage, not the play
      readers connect to characters, not maps
      don't let worldbuilding overwhelm narrative

  [10] SHOW 10%, KNOW 90%
        reveal gradually through story
        trust readers to infer
        maintain depth without overwhelming


PHASE 17: WORLDBUILDING SESSION CHECKLIST


before starting a story:

  [ ] world scope defined
  [ ] world bible created
  [ ] geography mapped (at minimum)
  [ ] timeline established
  [ ] primary culture(s) developed
  [ ] political system defined
  [ ] economic system outlined
  [ ] magic/tech rules established (if applicable)
  [ ] naming conventions created
  [ ] consistency log ready


for each location created:

  [ ] position on map established
  [ ] climate defined
  [ ] resources identified
  [ ] culture adapted to environment
  [ ] connections to other locations mapped
  [ ] travel times calculated


for each culture created:

  [ ] values identified
  [ ] norms established
  [ ] taboos defined
  [ ] institutions described
  [ ] history connected to present
  [ ] language/naming patterns created
  [ ] variation within culture noted


for each rule established:

  [ ] documented in world bible
  [ ] consequences defined
  [ ] limits identified
  [ ] logged for consistency
  [ ] integrated with story


after completing a draft:

  [ ] do a world consistency pass
  [ ] verify all rules are followed
  [ ] check for contradictions
  [ ] reduce info-dumping
  [ ] ensure world serves story
  [ ] document any new rules established


FINAL REMINDERS


worldbuilding is stage design

  your world is where the story happens.
  it's not the story itself.

  readers don't fall in love with worlds.
  readers fall in love with characters.
  the world gives those characters somewhere to stand.


specificity creates authenticity

  the more specific your world, the more real it feels.

  not "they had a feudal government"
  but "the high king sat on the throne of spears,
   each spear taken from a defeated enemy,
   the oldest black with age, the newest still
   stained with the blood of its owner."

  specific details. universal emotions.


consistency is magic

  readers will believe impossible things.
  they won't believe inconsistent things.

  once you establish a rule, follow it.
  once you create a culture, keep it consistent.
  once you define a place, don't move it.

  consistency creates the illusion of reality.


less is more

  you can create a world that feels vast
  while showing very little.

  a few carefully chosen details
  imply a world beyond the page.

  trust readers to imagine.
  their imagination is more powerful
  than any description you could write.


when in doubt

go deeper, not wider.

better to know one culture deeply
than ten superficially.

better to understand one location completely
than to sketch a hundred.

depth creates believability.
breadth creates tourism.

focus on what matters to the story.
let the rest remain shadow.

now go build a world worth visiting.
