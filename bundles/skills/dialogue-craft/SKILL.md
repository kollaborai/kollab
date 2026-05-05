---
name: dialogue-craft
description: "Dialogue Craft skill - writing natural, purposeful dialogue"
---

dialogue craft mode: SUBTEXT OVER STATEMENT

when this skill is active, you follow dialogue-first discipline.
this is a comprehensive guide to crafting compelling, natural dialogue.


PHASE 0: CONTEXT VERIFICATION

before writing ANY dialogue, verify the writing context is understood.


check for existing dialogue samples

  <read><file>context.md</file></read>

if no context file exists:
  <create>
  <file>context.md</file>
  <content>
  # Dialogue Context

  ## Project Overview
  [brief description of story]

  ## Character Voices
  [character name]: [speech pattern notes]
  [character name]: [speech pattern notes]

  ## Dialogue Samples
  [existing dialogue to match voice]
  </content>
  </create>


check character voice consistency

  <terminal>find . -name "*.md" -type f | xargs grep -l "dialogue\|voice" 2>/dev/null | head -5</terminal>

  <terminal>find . -name "*dialogue*" -o -name "*voice*" -type f 2>/dev/null | head -5</terminal>

review existing character dialogue before writing new lines.
match the established voice patterns.


identify dialogue purpose

ask yourself BEFORE writing:
  [ ] what is this scene trying to accomplish?
  [ ] what information MUST be conveyed?
  [ ] what information SHOULD be hidden (subtext)?
  [ ] what is each character's agenda?
  [ ] what power dynamic exists between speakers?

never write dialogue without knowing its purpose.


verify scene context

  <read><file>scene_outline.md</file></read>

if no scene outline exists, create minimal context:
  [ ] setting (where are we?)
  [ ] characters present
  [ ] scene goal (what must happen?)
  [ ] emotional state of each character entering


PHASE 1: THE FUNDAMENTAL TRUTH OF DIALOGUE

dialogue is NOT conversation.

real conversation:
  [ ] full of filler words, repetition, dead ends
  [ ] boring, meandering, pointless
  [ ] 90% small talk and logistics

good dialogue:
  [ ] distilled to its essence
  [ ] every line advances plot, character, or theme
  [ ] sounds natural without being realistic

the dialogue paradox:
  to make dialogue feel real, remove everything that makes it real


example: real vs good dialogue

real conversation:
  "hey, how are you doing?"
  "pretty good, just got here, you know?"
  "yeah, traffic was bad on the way over too"
  "tell me about it, i was stuck on the highway for like"
  "an hour, people driving crazy, you know how it is"
  "oh totally, same here, but i made it, so"
  "right, yeah, good to see you"
  "you too"

good dialogue:
  "you're late."
  "traffic on 95. again."
  "we talked about this."
  "i know. won't happen again."
  "it can't. the meeting started ten minutes ago."

the second version:
  [ ] conveys conflict immediately
  [ ] establishes history without exposition
  [ ] shows power dynamic
  [ ] implies ongoing problem
  [ ] creates tension


PHASE 2: SAID VS OTHER SPEECH TAGS

the golden rule: said is invisible

readers' eyes glide over "said." they pause on other tags.

use said 90% of the time.

  [ok] "i can't believe you did that," she said.
  [ok] "what do you mean?" he said.
  [ok] "fine," she said.

these tags call attention to themselves:
  [x] "i can't believe you did that," she exclaimed.
  [x] "what do you mean?" he questioned.
  [x] "fine," she snapped.

every time you use a fancy tag, you pull reader out of story.


when to use alternative tags

use only when the manner of speech is NOT clear from context:
  [ ] whispered - when volume matters
  [ ] shouted / yelled - when escalation is needed
  [ ] murmured / mumbled - when clarity is intentionally reduced
  [ ] grunted - when character is refusing full speech

even then, consider if action beat works better.


speech tag frequency

not every line needs a tag.

once established, readers know who's talking.

  "i can't do this."
  "yes you can."
  "you don't understand."
  "try me."

short exchanges without tags create rhythm and tension.


PHASE 3: ACTION BEATS

action beats replace speech tags while adding movement and character.

  [ok] "i don't know." she picked at her cuticles. "maybe i left it at home."

  [ok] "you think this is funny?" he slammed his hand on the table. the
       silverware rattled. "because i don't."

  [ok] "sure." he wouldn't meet her eyes. "whatever you say."

beats do triple duty:
  [ ] identify speaker
  [ ] add physical action
  [ ] reveal emotional state


effective beats vs ineffective beats

effective beats:
  [ ] character-specific gestures
  [ ] meaningful interactions with environment
  [ ] actions that reveal subtext
  [ ] pacing changes (long beat = pause)

ineffective beats:
  [x] generic body parts (he nodded, she shrugged)
  [x] meaningless movement (he walked, she turned)
  [x] repetitive actions (he smiled for the tenth time)

combine beat with dialogue purpose:
  "i'm not angry." she smiled, teeth gritted. "why would you think that?"

the smile contradicts the words. that's good dialogue.


beat placement

beats can come before, during, or after dialogue.

before:
  she poured herself another drink. "you going to answer me?"

during (for pacing):
  "i told you" - she slammed the drawer - "i wasn't going to do it."

after:
  "whatever." he turned back to the tv.


PHASE 4: SUBTEXT - WHAT'S NOT SAID

subtext is the meaning beneath the words.

characters rarely say what they mean. they:
  [ ] protect themselves
  [ ] protect others
  [ ] maintain social masks
  [ ] pursue hidden agendas
  [ ] avoid vulnerability

on-the-nose dialogue (bad):
  "i am angry at you because you betrayed my trust and now i don't know
   if i can ever forgive you."

subtext-heavy dialogue (good):
  "pass the salt."
  "here."
  "thanks."
  "anytime."
  "that's it?"
  "what did you want, a speech?"

the second version says everything through:
  [ ] mundane request (salt)
  [ ] minimal response
  [ ] expectation of something more
  [ ] deflection


creating subtext: the gap technique

gap between words and meaning = subtext

example: character wants to leave but doesn't want to say it

on-the-nose:
  "i want to go home now."

subtext version:
  "what time is it?"
  "almost ten."
  "huh. didn't realize it was so late."
  "we can stay longer if you want."
  "no, i should probably..."

the real message is conveyed through:
  [ ] checking time (looking for exit)
  [ ] noticing lateness (seeking justification)
  [ ] trailing off (leaving door open for other to decide)


emotional subtext through misdirection

characters deflect emotion with:
  [ ] logistics ("did you lock the door?")
  [ ] humor ("that's one way to look at it")
  [ ] deflection ("i'm fine, just tired")
  [ ] anger (easier than sadness)
  [ ] silence


exercise: subtext conversion

take these direct statements and rewrite with subtext:

direct: "i love you but i'm scared you'll hurt me."

subtext version:
  "you're leaving again?"
  "just for the weekend."
  "right. the weekend."
  "i'll call."
  "you always say that."

direct: "i stole the money and i feel guilty."

subtext version:
  "you hear about the break-in?"
  "yeah. sad."
  "people these days, right?"
  "yeah. desperate times."
  "guess so."

notice how neither character admits guilt outright.


PHASE 5: VOICE DIFFERENTIATION

every character needs a distinct voice.

voice elements:
  [ ] sentence length
  [ ] vocabulary level
  [ ] use of slang or jargon
  [ ] speech patterns (pauses, repetitions)
  [ ] metaphors they use
  [ ] what they DON'T say
  [ ] formality level


voice differentiation example

same situation, three characters:

character A (educated, formal, precise):
  "i believe there's been a misunderstanding. i never agreed to those
   terms. perhaps we should review the original agreement."

character B (casual, colloquial, direct):
  "nah, that ain't right. we never said nothing about that. you're
   mixing stuff up."

character C (guarded, minimal, reactive):
  "that's not what i remember."
  "you remember wrong."
  "do i?"

three distinct voices in the same scene.


voice consistency exercises

before writing a character, create voice profile:

  [ ] education level: [affects vocabulary, sentence complexity]
  [ ] regional background: [affects idioms, pronunciation]
  [ ] profession: [affects jargon, worldview]
  [ ] age: [affects slang references, formality]
  [ ] secret: [affects what they avoid saying]
  [ ] default emotion: [affects tone]
  [ ] speech habit: [affects pattern]

example voice profile:

  character: marcus
  education: high school dropout, well-read autodidact
  background: south side chicago, former mechanic
  age: 42
  secret: feels inadequate around educated people
  default emotion: defensive skepticism
  speech habit: repeats questions when processing, uses "look" as
                transition, short sentences, mechanical metaphors

sample dialogue based on profile:
  "look, i'm saying it doesn't fit. the pieces, they don't match.
   you follow? like an engine with wrong-size pistons. looks okay on
   the outside, but inside? grinding. you hear what i'm saying?"


PHASE 6: DIALOGUE PACING AND RHYTHM

dialogue has musicality. vary the rhythm.

fast-paced dialogue:
  [ ] short sentences
  [ ] interruptions
  [ ] overlaps (--)
  [ ] minimal tags
  [ ] action beats only for emphasis

slow-paced dialogue:
  [ ] longer sentences
  [ ] pauses (indicated by beats)
  [ ] internal thoughts
  [ ] more detailed action beats
  [ ] reflection


example: pacing shift

fast (argument):
  "you said--"
  "i know what i said."
  "then explain--"
  "there's nothing to explain."
  "bullshit."
  "watch your mouth."

slow (aftermath):
  he stood by the window, not turning. the room felt very quiet.
  "i didn't mean to," she said.
  "you never do."
  "that's unfair."
  he turned finally. "is it?"

the same scene, different pacing for different effect.


interrupting and cutting off

use em dashes for interruption:
  "i was trying to tell--"
  "i don't care what you were trying."

use ellipses for trailing off:
  "i don't know if i can..."
  "can what?"
  "never mind."

how characters interrupt reveals:
  [ ] power dynamics (who gets to interrupt?)
  [ ] emotional state (desperation interrupts more)
  [ ] relationship (intimacy allows interruption)

intimacy and interruption:
  close characters can finish each other's thoughts.
  distant characters interrupt to dominate.


PHASE 7: EXPOSITION THROUGH DIALOGUE

rule: never have characters tell each other what they both know.

bad exposition (the "as you know, bob" conversation):
  "as you know, steve, our father died three years ago in that tragic
   car accident on highway 10, leaving us the family business that's
   been in our family for four generations since 1923."

this is not dialogue. this is a data dump disguised as dialogue.


good exposition: characters arguing about what they both know

  "we're not selling."
  "it's not worth keeping anymore."
  "it's all we have left of him."
  "dad's dead, janie. the shop died with him."

both know the context. the argument reveals:
  [ ] there's a family business
  [ ] their father died
  [ ] one wants to sell, one doesn't
  [ ] it's about memory, not money

all conveyed without stating facts outright.


exposition through conflict

characters reveal background by fighting about it.

"you always take his side."
  "i do not."
  "you've been doing it since we were kids. he breaks the window,
   you blame me."
  "that was twenty years ago."
  "some things don't change."

reveals:
  [ ] sibling relationship
  [ ] family dynamic (one parented differently)
  [ ] ongoing resentment
  [ ] specific past incident

shown through conflict, not explained.


exposition through voice

background leaks through how characters speak.

"the customer is always--" he made air quotes "right. that what they
 taught you in business school?"

in six words:
  [ ] character's attitude (skeptical)
  [ ] other character's education (business school)
  [ ] class tension (blue collar vs white collar)
  [ ] relationship dynamic (one looking down on the other)


PHASE 8: GENRE-SPECIFIC DIALOGUE PATTERNS

different genres have different dialogue conventions.


thriller/suspense dialogue

characteristics:
  [ ] information withholding
  [ ] coded language
  [ ] misdirection
  [ ] tension in subtext
  [ ] questions as power moves

  "do you have it?"
  "i might."
  "might isn't good enough."
  "then maybe you should've been clearer about what you needed."

nothing is stated outright. everything is negotiation.


romance dialogue

characteristics:
  [ ] vulnerability is the goal
  [ ] miscommunication creates tension
  [ ] emotional progress is tracked
  [ ] subtext gradually becomes text

early stage:
  "you're still here?"
  "looks like."
  "why?"
  "haven't figured that out yet."

late stage (after breakthrough):
  "you're still here."
  "i'm not going anywhere."
  "you promise?"
  "i promise."

the same words, different meaning through established intimacy.


science fiction/fantasy dialogue

characteristics:
  [ ] worldbuilding through speech
  [ ] created slang/jargon
  [ ] cultural values revealed
  [ ] avoid exposition dumps

"void take it, you ran the hyperdrive hot?"
  "we needed the speed."
  "at the cost of my engine? spacer's luck, you're lucky it didn't
   blow us into quantum."

reveals:
  [ ] hyperdrive technology
  [ ] spacer culture ("spacer's luck")
  [ ] religious element ("void take it")
  [ ] physics concept ("quantum" as bad outcome)

all shown through character voice, not explained.


literary/contemporary dialogue

characteristics:
  [ ] heightened realism
  [ ] philosophical undertones
  [ ] character depth through speech patterns
  [ ] subtext is primary

"do you think people change?"
  "depends."
  "on what?"
  "on whether they want to."
  "and if they don't?"
  "then they don't. they just get better at hiding."

conversations about ideas, revealed through concrete moments.


PHASE 9: DIALOGUE BLOCKS AND WHITE SPACE

how dialogue looks on page matters.

short exchanges:
  create white space
  increase reading speed
  feel lighter, faster

long speeches:
  create dense blocks
  slow reading down
  feel heavier, more significant

use formatting to control pacing.


when characters monologue

monologues are rare in real life. use them sparingly.

valid reasons for monologue:
  [ ] teaching (someone needs to learn)
  [ ] confession (emotional release)
  [ ] interrogation (power position)
  [ ] performance (character is literally performing)

invalid reasons:
  [x] author wants to explain something
  [x] character "just needs to say this"
  [x] filling space

break long speeches with:
  [ ] listener reactions
  [ ] action beats
  [ ] paragraph breaks
  [ ] internal thoughts (if pov allows)


white space as emotional indicator

sparse, sparse = tension, withholding

  "well?"
  "i can't."
  "why?"
  "i just can't."

dense, flowing = intimacy, openness

  "it's not that i don't want to, it's just that i've tried before and
   it never works out the way you expect it to, you know? you plan and
   you hope and then something happens and it's all gone, and i'm not
   sure i can go through that again, not after last time."

use page layout as emotional tool.


PHASE 10: DIALOGUE REVISION CHECKLIST

first draft dialogue is rarely good dialogue. revise systematically.


read dialogue aloud

  <terminal>say "your dialogue here" -v 200</terminal> (macos)
  <terminal>espeak "your dialogue here"</terminal> (linux)

if you stumble, reader will too.

mark:
  [ ] awkward phrasing
  [ ] unnatural rhythms
  [ ] repeated words
  [ ] tongue twisters
  [ ] too-long sentences


check every speech tag

  [ ] can tag be removed? (speaker is clear)
  [ ] can tag be "said"? (usually yes)
  [ ] can tag be replaced with action beat? (often yes)
  [ ] is tag doing necessary work? (if no, remove)


check for said-bookisms

these are almost always wrong:
  [x] ejaculated (has different meaning now)
  [x] pontificated (too academic)
  [x] articulated (too clinical)
  [x] expostulated (who talks like this?)
  [x] retorted (unless it's actually a retort)
  [x] queried (use "asked" or nothing)

rule: if you can't imagine a real person using this verb to describe
speech, don't use it.


check for adverb dependence

  "i hate you," she said angrily.
  "i hate you," she said.
  "i hate you." she slammed the door.

adverbs indicate weak dialogue. strengthen the words instead.

weak adverbs to avoid:
  angrily, sadly, happily, excitedly, nervously, quickly, slowly,
  loudly, softly, bitterly, sweetly

if you need an adverb, rewrite the dialogue.


check for on-the-nose disease

characters saying exactly what they feel:
  [x] "i am so angry right now!"
  [x] "i'm really sad about this."
  [x] "i love you so much."

real people deflect:
  [ ] "i can't talk about this."
  [ ] "whatever. it's fine."
  [ ] "you too."

find direct emotion statements and convert to subtext.


check for information dump

characters explaining things they both know:
  [x] "as we discussed earlier..."
  [x] "remember when we..."
  [x] "as you know..."

if both characters know it, they wouldn't say it.

either:
  [ ] cut the exposition
  [ ] make it conflict
  [ ] find someone who DOESN'T know


check for speech consistency

read one character's dialogue in isolation.
does it sound like the same person throughout?

common consistency breaks:
  [x] formal character suddenly uses slang
  [x] uneducated character uses academic words
  [x] quiet character delivers long speech
  [x] funny character becomes dead serious without reason

characters can change, but reader needs to see why.


PHASE 11: DIALOGUE EXERCISES

practice these to improve dialogue craft.


exercise 1: the yes/no game

two characters. one wants something, one won't give it.
write dialogue where neither says yes or no directly.

aim: 200-400 words
constraint: no direct yes or no
goal: maintain tension while refusing direct answer


exercise 2: the double conversation

two characters talking about two things at once.
what they're discussing vs. what they're actually discussing.

surface: planning a party
subtext: their failing relationship

aim: 300-500 words
goal: make subtext clear without stating it


exercise 3: voice differentiation

write a scene with three characters discussing the same event.
each character should have distinct voice.

aim: 400-600 words
challenge: remove all speech tags
goal: reader knows who's speaking from voice alone


exercise 4: the argument with no words

write an argument using only:
  [ ] sentence fragments
  [ ] interruptions
  [ ] single words
  [ ] actions

no complete sentences. no explanations.

aim: 200-300 words
goal: conflict completely clear


exercise 5: exposition through conflict

convey a complex backstory through an argument.
both characters know the full history.
neither states it outright.

aim: 400-600 words
challenge: write for someone who knows nothing about the story
goal: reader understands the backstory without it being explained


exercise 6: genre shift

take the same basic situation (one character wants to leave, one wants
them to stay) and write it in three genres:
  [ ] thriller
  [ ] romance
  [ ] comedy

aim: 200-400 words each
goal: see how genre changes speech patterns


exercise 7: subtext-only rewrite

take a passage of direct dialogue and rewrite with maximum subtext.
characters should talk about anything BUT what they actually mean.

direct: "i'm pregnant and i'm scared you'll leave me."

rewrite: conversation about dinner plans that's actually about the
pregnancy and fear.


exercise 8: the silent scene

write a scene where two characters have a complete conversation without
either saying what they really mean. every line is deflection.

aim: 300-500 words
goal: reader understands the real conversation


PHASE 12: DIALOGUE MISTAKES TO AVOID

these are the most common dialogue problems.


mistake 1: everyone sounds the same

symptom: you could swap character names and nothing would change.

fix: create voice profiles for each character. give each:
  [ ] distinct sentence length preference
  [ ] vocabulary level
  [ ] speech habit
  [ ] default deflection


mistake 2: too-formal speech

symptom: characters speak in complete, grammatically perfect sentences.

real speech:
  [ ] fragments
  [ ] contractions
  [ ] restarts
  [ ] filler words (used sparingly)
  [ ] colloquialisms

  "i am going to the store to purchase milk." (robot)
  "i'm gonna run out for milk." (human)


mistake 3: overuse of names

symptom: characters constantly address each other by name.

  "john, i don't think that's a good idea."
  "but mary, we have to try."
  "john, listen to me."

real people only use names for:
  [ ] getting attention
  [ ] emphasis/anger
  [ ] intimacy
  [ ] confusion (multiple people present)

use names sparingly.


mistake 4: excessive phonetic spelling

symptom: writing accents phonetically.

  "i'm gonna go to the store, bawss."
  "y'all come back now, ya hear?"

this is:
  [ ] hard to read
  [ ] often offensive
  [ ] rarely necessary

better: indicate accent through:
  [ ] word choice
  [ ] sentence structure
  [ ] cultural references
  [ ] occasional dropped g or shortened word


mistake 5: starting with hello

symptom: scenes start with greeting.

  "hello, john."
  "hi, mary. how are you?"
  "i'm fine. how are you?"
  "also fine."

start at the real beginning:
  [ ] in the middle of action
  [ ] with the important line
  [ ] at the moment of change

skip the social niceties unless they're doing work.


mistake 6: speech tag pile-up

symptom: every line has a tag.

  "hello," he said.
  "hi," she said.
  "how are you?" he said.
  "fine," she said.

trust the reader. once established, they know who's talking.


mistake 7: emotional monologue

symptom: character delivers full emotional speech.

  "i've been so sad since my mother died. i feel empty inside, like
   there's a hole where my heart used to be. i cry myself to sleep
   every night."

real people deflect:
  [ ] change the subject
  [ ] make a joke
  [ ] leave the room
  [ ] get angry

emotion leaks out. it doesn't pour.


PHASE 13: ADVANCED TECHNIQUES


unspoken dialogue

what characters choose not to say is as important as what they say.

  "do you still--"
  "no."
  "i wasn't going to ask about that."
  "what were you going to ask?"
  "doesn't matter."

the unspoken question hangs over the entire exchange.


dialogue as action

sometimes the dialogue IS the action.

  "don't"
  "i have to"
  "then i can't let you"
  "you're not going to stop me"
  "watch me"

each line is a move in the conflict. no physical action needed.


echo dialogue

characters repeat words, showing fixation.

  "he's gone"
  "i know he's gone"
  "but he's really gone"
  "i know"
  "like, actually gone gone"
  "i know"

repetition shows:
  [ ] denial
  [ ] processing
  [ ] trauma
  [ ] obsession


the non-answer answer

characters respond to the question beneath the question.

  "are you seeing anyone?"
  "why do you ask?"
  "just curious."
  "curious about what?"

the first question isn't "are you seeing someone."
the real question is "do i still have a chance."

the answer ("why do you ask?") responds to the real question.


dialogue callbacks

earlier dialogue gains new meaning later.

earlier:
  "if you ever need me, you know where to find me."
  "i won't need you."

later:
  she knocked on his door at 3 am. "you said."
  "i know what i said."

the callback carries the weight of earlier context.


PHASE 14: DIALOGUE RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER let characters say exactly what they mean
      find the deflection
      find the subtext
      find the way to say it indirectly

  [2] SAID is your default speech tag
      use "said" unless another tag does necessary work
      if you must use another tag, ask yourself:
        - is this the ONLY way to convey this information?
        - would an action beat work better?

  [3] READ every dialogue passage aloud
      if you stumble, rewrite
      if it sounds stiff, rewrite
      if it sounds like writing, rewrite

  [4] CHECK every information dump
      if both characters know it, why are they saying it?
      either cut it or make it conflict

  [5] GIVE every character a distinct voice
      if you can remove character names and not tell who's speaking,
      your voices aren't distinct enough

  [6] USE action beats instead of speech tags when possible
      beats do double duty: identify speaker AND reveal character

  [7] NEVER use phonetic spelling for accents
      indicate accent through word choice, sentence structure, rhythm

  [8] CUT greetings, goodbyes, and other social filler
      start at the important moment
      end at the important moment

  [9] VARY sentence length for rhythm
      fast dialogue = short sentences
      slow dialogue = longer sentences
      mix them to create musicality

  [10] REMEMBER that conflict is the engine of dialogue
       characters want different things
       let those wants collide through speech


FINAL REMINDERS


dialogue is character

good dialogue reveals:
  [ ] what character wants
  [ ] what character fears
  [ ] what character believes
  [ ] how character sees themselves
  [ ] how character sees others

if dialogue doesn't reveal character, cut it.


dialogue is conflict

every conversation is a negotiation.
characters want things.
sometimes those wants align.
sometimes they clash.
let them clash.


dialogue is subtext

what's said is never the whole story.
what's not said is often the real story.
the gap between words and meaning is where story lives.


listen

listen to how people actually talk.
not in movies.
not in books.
in real life.

  [ ] restaurants
  [ ] public transit
  [ ] line at the grocery store
  [ ] your own conversations

notice the:
  [ ] interruptions
  [ ] unfinished thoughts
  [ ] changes of subject
  [ ] things left unsaid

use it. distill it. make it art.


the goal

dialogue that:
  [ ] reveals character
  [ ] advances plot
  [ ] creates tension
  [ ] sounds natural
  [ ] carries subtext

every line should do at least two of these.

now go write something people actually want to read.
