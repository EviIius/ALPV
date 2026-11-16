# Prompt: apply Lindsay's review to the data strategy workshop deck (v9 → v10)

## Role and inputs
You are editing a PowerPoint deck for a two-day corporate risk data strategy workshop. Attached is the current version (v9). Produce v10, following the instructions below exactly.

## Ground rules
1. **Keep the deck's look.** Use the deck's existing template, master slides, fonts, colors and spacing. Follow the format of the Risk Data Sourcing Deck (v6.2) where the deck already does. Don't introduce a new visual style.
2. **No bank name.** Never write the bank's name or use its logo anywhere.
3. **Find slides by content, not number.** Slide numbers below refer to v9, and numbering shifts as slides are merged or deleted. Identify each slide by its v9 number and its content before changing it. Apply deletions last, or work from the back of the deck forward.
4. **Leave placeholders alone.** Don't change the content of slides 6–8 or 12–13; they're placeholders for technology's slides. Only make sure each has a clear label: **"PLACEHOLDER – to be replaced with slide from Technology"**.
5. **Don't fill in other groups' content.** Slides 14–19 are the priority spotlight templates that each group (financial crimes / FCRM, eGRC, credit risk, and others) fills in with its own initiatives. Don't invent any of that content. Only update the timing to 15 minutes per group, and keep the template structure.
6. **No speaker notes.** Remove all speaker notes, and don't add any.
7. **Don't invent data.** Use only the numbers given here. Where something isn't known, put a clearly marked [TBD] placeholder.
8. **Short text.** Write on-slide text as short phrases: no more than about 6 bullets per box and no more than about 12 words per bullet.

## Changes, slide by slide

### Opening (v9 slides 2, 3, 4 and 7)
- Combine the stage-setting content (agenda, objectives, ground rules) into **one opening slide per day**: two slides in total.
- Each slide has the agenda on the left and objectives plus ground rules on the right.
- Ground rules can differ slightly by day. The last ground rule applies to day 1 only.

### "The 8 words you'll hear most"
- Delete this slide.

### Day 1 agenda timing
- Priority spotlights drop from 30 to **15 minutes per group**. The spotlight block shortens to about one hour (roughly 3:00–4:00 instead of 3:00–5:00), e.g. a 3:15 slot for FCRM.
- Our team's priorities follow straight after.
- Add a day-1 item at the end: **"Overview of phases 1 and 2, and the phase 1 timeline"**, which has moved up from day 2.
- Keep exact times as [TBD] where they aren't listed here.

### Slides 6–8
- Placeholders. Label them only.

### Slides 9 and 10: migration status (merge into one slide)
Merge 9 and 10. The message is a grounding point: technology says "everything is on DPC by Nov 19", but that isn't fully true for corporate risk. A plan is still needed for the rest.

**Headline:** "Not all corporate risk data is on DPC after Nov 19: what's left and what happens next".

**Three action boxes across the top:**
- **Migrated on Nov 19:** via the three tenants (ORCAP for non-financial risk, BUSCRT for credit risk, FCTDS for financial crimes). Applications those tenants served now get their data from DPC.
- **Unknown, will it move?:** not DPC-ready today. Next step: decide whether it moves.
- **Moves when re-platformed:** expected on DPC but with no dates yet, e.g. GSMOS to be replaced by a homegrown customer name screening app on DPC (FCTDS), and FCP replaced by FCCP built on DPC. Next step: set a timeline.

**Bottom strip:** move the "Not migrating to DPC" bucket from the bottom-right box into a low-emphasis informational strip across the bottom. These items stay where they are and integrate with DPC (e.g. DERA, the FCRAM tool).

**Callout box:** "Workshops to be set up: decide whether each item moves to DPC, and the timeline to move". It applies to the Unknown box and the Re-platform box.

Add a small footnote: "Pending confirmation from Technology".

### Slides 12–13
- Placeholders. These summarize the four tooling capabilities (e.g. the context engine) mentioned in early calls. Technology will replace them with its own slides.
- Label each as a placeholder. Don't otherwise edit them.

### Slides 14–19: priority spotlight templates
- Leave the content for the groups to fill in.
- Update any time references to 15 minutes each.

### Slide 21: "Where we're headed / 5 changes"
- Keep this slide (Lindsay likes it). It's the best explanation of where we are today, where we're going, and the shift.

### Slides 22 and 23: target-state data plane
- Slide 22 is a simplified version of slide 23; slide 23 is the detailed version shown before.
- Jacqueline will pick one level of detail.
- **Default for now:** put slide 21's content side by side with slide 22's simplified target state, as one slide.
- Move slide 23 to an appendix, titled "Backup – detailed target state". Add the note "[Decision: Jacqueline to choose simplified vs detailed]".

### Slide 24: sourcing facts (three columns)
Rebuild as **three columns**, each with a one-line "so what":

**Column 1 – Today's use cases:**
- 142 tier 1 and tier 2 use cases (reports, models, BSA/AML)
- 110 data subjects
- 184 source applications
- So what: "184 places for 110 things".

**Column 2 – Overlap already in RDS:**
- Placeholder for Vic's analysis from the Ryan file, as a pie chart of sources landed by 1, 2, or 3+ tenants (ORCAP, FCTDS, BUSCRT, other RDS tenants).
- Example: FNDD is sourced three times (credit risk, FCTDS, another RDS tenant).
- Note: ORCAP has little overlap.
- So what: "We already duplicate sourcing today".
- Mark the numbers [TBD – Vic].

**Column 3 – New 2027 demand:**
- 15 sources needed by 4–5 groups (9 already in RDS, 6 not started), "candidates to tackle first, subject to prioritization"
- 110 sources needed by 2–3 groups
- So what: "Centralized intake avoids sourcing the same data again".

**Simplify:**
- Change the word "platforms" to "groups".
- Remove the "partially complete / not complete" detail.
- Keep "as of 8/31" (no number refresh).

### Slide 25
- Delete. It repeats slide 21.

### Moved to the end of day 1: phases overview (v9 slide 33)
- Place the phases 1 and 2 overview right after the sourcing facts slide:
  - Phase 1: get the house in order (existing data).
  - Phase 2: central intake ("the front door") for new data.
- Follow it with the phase 1 timeline (v9 slide 37):
  - Organize RCSA data first, done by Feb 5.
  - Issues next, overlapping.
  - Then the remaining non-financial risk data.
  - Financial crimes start date [TBD].
- Add a divider slide "Day 1 – Our priorities and the path forward" before slide 21.

### Day 2 opening (v9 slides 29 and 30)
- Merge them into one slide: a short refresher ("where we are vs where we're going") plus the day-2 agenda.
- Delete the standalone agenda slide (30).

### Slide 31
- Make sure its concepts appear in day 1 (on the 21+22 slide). Then delete it.

### Slide 32: accountability (our team, business groups, technology)
- Keep it. It's the most important slide for getting accountability.
- Add the note "[Confirm: cover roles and responsibilities on day 1 or day 2]".

### Operating model slide ("we own all data until it's consumed")
- Delete. It needs more operating model discussion before being shown.

### Access slide
- Delete. Access is a technology conversation, partly covered in day 1.

### Slides 34–36: how we get the plan in order, and sequencing
- Combine into one or two slides.
- Slide 34 is the start of the phase 1 deep dive on day 2.

### Divider slides
- Add simple section dividers between the major sections: Opening; DPC and tooling; Priority spotlights; Our priorities and path forward; Day 2 opening; Phase 1 deep dive; Next steps.

## Output
1. The updated v10 .pptx.
2. A change log listing, for each v9 slide: kept / merged into X / moved / deleted / placeholder, with a one-line reason.
3. A list of open decisions:
   - 22 vs 23
   - Whether roles and responsibilities are covered on day 1
   - Agenda times
   - Extra breaks, or shortening to one day or half a day (Jacqueline has conflicts)
   - Technology's confirmation of the migration status slide
   - Vic's overlap numbers
