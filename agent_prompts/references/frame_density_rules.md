# Narrative Atomization & Frame Density Rules

## NARRATIVE ATOMIZATION — THE GOVERNING FRAMEWORK

Narrative Atomization is the process of decomposing a natural language narrative passage into its smallest independently renderable units — called **story atoms** — where each atom represents exactly one visual state change, action, or spatial relationship that can be mapped to a discrete frame, animation, or scene instruction.

### Story Atom Requirements

A story atom MUST contain exactly three components:
1. **One subject** — who or what
2. **One action or state** — what happens or what exists
3. **One context** — where, how, or in relation to what

If an extracted unit is missing any component, it is not a valid atom. If it contains more than one of any component, it must be split further.

### Decomposition Rules

1. **One thing per atom** — each atom describes exactly one thing happening or one thing being true
2. **Compound sentence splitting** — split at every new subject, every new verb, and every causal boundary (when X causes Y, that's two atoms)
3. **Sequence preservation** — atoms preserve sequence; their order IS their timeline
4. **Implied actions become explicit** — if the prose implies an action that isn't stated, that implied action is a separate atom

### Implied Action Extraction (CRITICAL)

Prose often skips intermediate physical actions. You MUST make them explicit:

| Prose Says | Implied Atoms |
|---|---|
| "walks through the door" | 1. opens door → 2. walks through doorway |
| "sets the cup on the table" | 1. reaches for cup → 2. places cup on table |
| "reads the letter and weeps" | 1. unfolds letter → 2. reads letter → 3. weeps |
| "she's now wearing the red dress" | 1. removes previous clothing → 2. wears red dress |
| "sits across from him" | 1. crosses to chair → 2. sits down |

Not every implied action needs its own frame (see Frame Density Rules below for when to merge), but every implied action must be IDENTIFIED before frame assignment. The decision to merge is separate from the decision to extract.

### Atomization Example

**Input:** "Mei walks through the door, it swings open letting out the light from indoors spills across the snow outside"

**Atomized output:**
1. mei → opens → door (implied action made explicit)
2. door → swings open → from mei's action (causal boundary: mei's push CAUSES the swing)
3. indoor light → spills across → outdoor snow (causal boundary: open door CAUSES light spill)

**Each atom = one subject, one action, one context. No exceptions.**

---

## ATOMIC BEAT EXTRACTION (KINETIC PARSING)

Narrative Atomization produces story atoms. Kinetic Parsing classifies each atom by verb type for frame assignment.

You are an NLP model. Your default programming is to treat a "sentence" (bounded by a period) as a single complete thought. You will naturally want to group a main clause and a dependent clause together. You must override this programming.

A camera does not see punctuation; it sees VERBS. Your job is to strip away the grammar of the prose and isolate every distinct action into an Atomic Beat.

### The Anti-Compression Rule

If a sentence contains multiple physical, sensory, or vocal verbs, you MUST split it into multiple Atomic Beats. Treat commas, conjunctions ("and", "while", "as"), and participle phrases ("...her grip tightening") as hard visual cuts.

### Verb Type Classification

Classify each story atom by its primary verb type:

- **Spatial / Kinetic (Cast-Action):** A character moves, gestures, or changes posture (e.g., stepped, reached, tightened, dropped).
- **Sensory (Env-Detail):** A light shifts, a sound occurs, an object is highlighted (e.g., flickered, illuminated, cracked).
- **Vocal (Dialogue):** A line is spoken (e.g., whispered, shouted, said).

### THE TRAP vs. THE CORRECT EXECUTION

Source Prose:
"Mei stepped into the rain-slicked alley, her grip tightening on the katana. The neon sign above flickered, casting harsh red shadows."

**THE NLP TRAP (HOW YOU WILL FAIL):**

[1] Mei stepped into the rain-slicked alley, her grip tightening on the katana. (Failed: Grouped a spatial entrance and a kinetic hand-movement into one shot).

[2] The neon sign above flickered, casting harsh red shadows. (Failed: Grouped the light source and the resulting shadow cast into one shot).

**NARRATIVE ATOMIZATION → KINETIC PARSING (CORRECT):**

Atomize first:
- mei → steps into → rain-slicked alley (subject: mei, action: steps, context: alley)
- mei's grip → tightens on → katana (subject: grip, action: tightens, context: katana)
- neon sign → flickers → above (subject: sign, action: flickers, context: above alley)
- red shadows → cast across → environment (subject: shadows, action: cast, context: alley surfaces) — causal: flicker CAUSES shadows

Then classify:
[1] Mei stepped into the rain-slicked alley. (Spatial: Wide shot entrance)
[2] Her grip tightening on the katana. (Kinetic: Close up on hands)
[3] The neon sign above flickered. (Sensory: Detail on the sign)
[4] Casting harsh red shadows. (Sensory: Detail on the asphalt/environment)

### THE 1-TO-2 WRAP & VALIDATION

Once you have your numbered list of story atoms classified by verb type, you must assign every single one to a FrameNode using the source_beats array.

Wrap them using the Setup/Payoff formula:

- **Setup Frames** (F01, F02, F08): The initiation, the reaction, the environmental detail (Beats 1 and 3).
- **Payoff Frames** (F04-F06, F07, F10-F11): The consequence, the delivered dialogue, the completed action (Beats 2 and 4).

Validation: No story atoms can be orphaned. No story atoms can be merged into a single frame unless they share the same subject AND the same context AND the merge doesn't lose visual information.

---

## FRAME DENSITY RULES (MANDATORY)

The following rules OVERRIDE any compression instinct. More frames is always better than fewer frames.

**Every verb = a frame.** Every noun = a frame. Every transition between verbs or nouns = a frame.

Concrete rules:
1. **Verb->Noun:** If a time a action meets an object, that is its own frame.
   Example: "She picked up the letter" = one frame (F11 prop interaction)
2. **Verb->Verb:** If two actions happen in sequence, even in the same sentence, each action is its own frame.
   Example: "She turned (noun verb = framed) and walked to the door (verb to noun)" = two frames (F01 turn + F10 walking)
3. **Noun->Noun:** If focus shifts between two objects or characters, each gets its own frame.
   Example: "The candle flickered (noun verbed), as the ink dried (noun verb) on the page" = two frames (F08 candle + F08 ink)
4. **Environment-only frames:** Shots without cast are VALID and REQUIRED. A room settling after someone leaves, rain on a windowpane, a door closing — these are frames.
5. **Close-up companion frames:** When a character interacts with a prop or performs a significant physical action, generate BOTH:
   - The wider shot showing the action in context (F01/F02/F10)
   - A close-up detail shot of the interaction itself (F08/F11)
   This ensures the audience sees both the character doing it and what is being done.
6. **Reaction frames:** After every significant action or dialogue line, include at least one reaction frame showing the listener/observer's response.
7. **Implied action frames:** When Narrative Atomization surfaces an implied action (door opening before walking through, letter unfolding before reading), that implied action gets its own frame if it is visually distinct from the explicit action. Use judgment: "reaches for cup" before "places cup on table" may merge into one F11 frame, but "opens door" before "walks through" should be two frames (F11 door + F10 walking).

**Minimum frame density at stickiness 3:**
- Dialogue sequences: ceil(dialogue_lines * 1.5) frames minimum (speaker + listener reactions + environment cuts)
- Action sequences: 2-3 frames per described action (setup + execution + aftermath)
- Scene transitions: minimum 2 frames (leaving shot + establishing shot of new location)
- Total: expect 20-30 frames per scene for a 3-scene short (~60-90 total)

**Anti-compression validation:** After segmenting all frames for a scene, count them. If you have fewer frames than (number_of_verbs_in_prose + number_of_distinct_nouns_focused_on) * 0.7, you have compressed too aggressively. Re-read and split.

**Atomization validation:** After atomization, verify each atom has exactly one subject, one action, one context. If any atom has two subjects or two actions, split it. If any atom is missing context, infer context from the surrounding atoms and make it explicit.
