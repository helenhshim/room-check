# Room check: Product Spec (v2.2)

Status: locked 10/17/2026, re-scoped 10/20 (v2.2). New ideas go to "After the MVP," not into the build. v2.1 (10/17): the MVP became Phase 1, the Room check connector; the Notion product became the Phase 2 follow-up. v2.2 (10/20): Phase 1.5 added, a Notion template anyone can try in about 2 minutes (section 4c). It pulls the "lands in Notion" part of the loop forward, because a chat-only check is something a general chatbot already does well. Technical design: [architecture.md](architecture.md).

This is the public copy of the spec. Personal household details are removed; the product decisions are unchanged.

**The loop this product is built around:**
```
external signals -> an agent processes them -> lands in a Notion workspace -> people review -> action
```

**Two phases, one engine**
| | **Phase 1 = the MVP** | **Phase 2 = the follow-up** |
|---|---|---|
| What | **Room check connector:** a hosted MCP server anyone adds to Claude by link | **Notion product:** a shared workspace where the agent does the work and people review it together |
| Who | Two people furnishing a shared living room first, then 20-50 people | The same two people deciding the living room |
| The loop | signals -> agent -> people (in a Claude chat) -> action | the full loop, landing in Notion, with memory and learning |
| Why this order | Ships something real people can use, fast | Collaboration, memory and taste learning on the proven engine |

**Phase 1.5 (10/20): Try it in Notion.** A public Notion template plus the Room check connector plus Notion's own connector in Claude. The room lives in the person's Notion, Claude reads it there, Room check judges, and the cards land in their Notion gallery where someone else can vote. No new runner and no Notion access from the server. Details in section 4c.

**Positioning:** Pinterest finds what looks like your pin. This decides what works in your room, together, and remembers why. (Phase 1 delivers "what works in your room"; Phase 1.5 delivers "together" and "remembers why" in the person's own Notion; Phase 2 adds the full loop with learning.)

**Why not just a chatbot (10/20):** a general chatbot judges a piece from a photo well. What it can't do: keep the room as a shared, editable record outside one chat, let a second person vote, or change a verdict because someone edited the room. Phase 1.5 demos exactly those three. The workspace is the shared context any agent reads over MCP.

---

## 0. Decision checklist (used for every decision)
1. **The loop:** does it serve signals -> agent -> Notion -> people -> action? (Phase 1 runs it in a Claude chat; nothing in Phase 1 may block Phase 2 landing it in Notion.)
2. **Real use first:** does it help the first two users decide their living room, and can other people use it?
3. **People decide:** the agent advises; votes, buying and rule changes need a person; edits win.
4. **Honest over impressive:** verified facts and links only; "not found" over a guess.
5. **Context over isolation:** judge against the room and confirmed, context-dependent rules.
6. **One engine, many surfaces:** logic lives in the shared engine, never in a single surface.
7. **Smallest thing that proves it:** the spec is locked; new ideas go to After the MVP.
8. **Measure it:** every feature has a metric or eval; cost is logged.
9. **Safe by default:** compliance, privacy, secrets, cost caps.
10. **Calm and image-first:** four things per card; restraint over options.
11. **Built for others:** no hardcoded people, rooms, budgets or stores.
12. **Save the work:** log the decision, commit and push.

## 1. User and problem

**Users:** two people (me and a roommate) furnishing a shared living room. Phase 1 also serves anyone with a room and a Claude account (first 20-50 people by invite).

**Problem:** ideas live in scattered texts, Pinterest screenshots and a Notes list. Feedback gets lost, the room has no agreed direction, and nobody checks a piece against what is already in the room before buying.

**What's painful today**
1. **No single place.** Links in texts, pins as screenshots, a Notes list with no context.
2. **Feedback doesn't happen.** The second person rarely reacts to links or opens the Pinterest board.
3. **Feedback is hard to act on.** "We don't like X about it" means starting a new search from scratch.
4. **No shared direction.** Hard to judge whether a piece fits when the room's look isn't decided.
5. **Costly mistakes.** See the coffee table.

Phase 1 addresses 3 and 5 for anyone (check a piece against the room; find better options). Phase 2 addresses 1, 2 and 4 (one shared place, easy votes, a shared direction).

**Evidence: the walnut coffee table.** After three months without one and no settled direction, I bought a dark walnut coffee table that looked good on its own. In the room it blends into the dark wood floor and dark TV stand, and the quality was only so-so. Lesson: the miss was about context, the pieces already in the room, not the item.

**Baseline (captured 10/16, before using the tool)**
| Measure | Before |
|---|---|
| Links texted to the second person | 10-15 |
| Links they reacted to | about 2 (roughly 15%) |
| Items in Notes with no context | 10-15 |
| Coffee table | about 3 months without one, then a regretted purchase |

---

## 2. Vision and principles

> **Decide on every piece with confidence, together.** An agent keeps your finds, your taste, and your room in one shared Notion workspace, and checks each piece against the space and what's already in it.

Phase 1 ships the core of this, the check against the room, inside Claude where people already are. Phase 2 ships the shared workspace around it.

**Feel:** calm and curated to look at (image-first, large photos, only a few details per card); quick and practical to use (shortest path from "found something" to "decided").

**Principles**
1. **Context over isolation.** Every piece is judged against the room and what's already in it.
2. **Taste you can read.** The agent's understanding of your taste is plain language you can edit; it never changes silently. Rules start as the agent's proposals and only count once the person confirms them; overrides update rules directly.
3. **Fast to a decision.** Every step removes work: capture, check, compare, decide.
4. **Input is easier than a text.** In Phase 1, paste a link or a photo into Claude. In Phase 2, vote by dragging a card.
5. **The agent advises, people decide.** AI-filled fields are marked and editable, your edits win, nothing is bought or sent without you.
6. **Honest over impressive.** "Not found" instead of a guess; never a link it didn't verify.

**Taste is context-dependent.** The same tone, color or material can be right in one setting and wrong in another. Each taste rule is written as: what (tone, color, material) / works when / doesn't work when, judged on placement, size, what it sits next to, and how much of it the room already has.

Example confirmed rules from the first test room:
- **Wood tones: mix freely.** Any wood tone works as long as the piece reads apart from the dark floor, from the piece itself (lighter wood, raised on legs) or from what's around it (a light rug underneath, white or cream pieces next to it). Doesn't work when a dark wood piece sits low on the floor in the main area and would still blend in. If only the rug is missing, it's workable: add one (so the walnut coffee table with no rug yet is a Watch, not a block).
- **Warm accent (orange, red):** one dominant warm accent per room, and it can be large if it's the only one.
- **Pale tones (ivory, cream, white, oatmeal):** work when small, vertical, or away from the sofa; don't work for large pieces next to an oatmeal sofa, where they blend into it.
- **Boucle:** never.

Agent proposals that aren't confirmed yet (for example "chrome only as accents") are never used to filter.

---

## 3. Goals and non-goals

**User goals**
1. Make the next living room decisions with confidence: each piece matches the direction, the facts are there (dimensions, return window, reviews and quality, price), and both people agree.
2. Nothing bought without a check against the owned pieces. (Phase 1)
3. The second person votes on items moved to Considering within 1 day. (Phase 2)

**MVP goals (Phase 1)**
1. Something real people can use: the first two users, then 20-50 people by invite.
2. Every check and find returns verified facts and a context judgment, the same card every time.
3. Cost stays bounded: per-person and global daily caps, cost logged per call.

**Non-goals (MVP, Phase 1):** memory across chats, votes, collaboration or taste learning (Phase 2); any Notion access from the server; room photos reaching the server; buying anything or messaging anyone on a person's behalf; crawling stores; getting around bot checks; Pinterest API import; 3D placement; billing or open sign-up (invite links only); Notion Custom Agents (free plan).

---

## 4. The MVP: Phase 1, Room check connector

### 4.1 Flows: inputs and tools
The user talks to their own Claude. Claude is the conversation layer: it looks at room photos itself and turns them into a text room profile, then calls the connector's tools. The agent's work (fetching, verifying, judging, searching) runs on the server.

```
INPUT (any, in any order, in a Claude chat)      TOOL               RETURNS
- "help me with my living room", room photos  -> start_room_check  -> what to ask next, what's unknown
- one or more product links                   -> check_pieces      -> one card per link
- a pin, a product link, or words + a note    -> find_similar      -> best 3 cards + what was left out
  ("cream lounge chair under $400")
- "I'm about to buy this one, in oak"         -> ready_to_buy      -> warnings (never blocks)
```

| Flow | Trigger | Agent does | Person does |
|---|---|---|---|
| **Setup** (once) | Gets a personal connector link | Issues the link | Adds it in Claude as a custom connector |
| **Room profile** (each chat) | Shares room photos or describes the room | Claude drafts the profile (floor, walls, owned pieces, budget, likes, confirmed rules); `start_room_check` says what's still unknown | Confirms or corrects |
| **Check** | Pastes one or more links | Verified facts, contrast against owned pieces, budget, Fits / Watch / Try, flags | Reads the cards |
| **Find similar** | A pin, a link, or words, plus an optional note | Search logic in 4.4; returns the best 3 and a left-out line, in about 20-30 seconds | Reads, asks for changes |
| **Ready to buy** | Names the piece and version | Lists what's missing and any concerns | Decides |

### 4.2 End to end
```
 EVERY CALL
 User (Claude app)        User's Claude                Server (Vercel): api/mcp.py + engine/       Outside
 photos, links,     ->    turns photos into a   ->    1. GATE: key valid? under per-person
 a pin, or words          TEXT room profile           and global daily caps? else "limit hit"
                          calls a tool with           2. ENGINE: fetch (guardrails) ---------->  store pages
                          profile + links             extract facts, search ------------------>  Tavily
                          (photos never leave         judge ---------------------------------->  Claude API
                          the user's Claude)          3. LOG: run ID, cost, counts
 reads the cards,   <-    shows the cards,     <-     returns JSON
 decides                  asks follow-ups             Stateless: the server keeps nothing between calls.
```
**Same room across a chat:** Claude remembers the chat, so the room is never forgotten within it. To stop drift, `start_room_check` returns one cleaned-up room profile, and Claude sends that exact profile on every later call, changed only when the person corrects or adds something. If evals show drift anyway, the fallback is a room ID kept 24 hours on the server.

### 4.3 What comes back to the user
| Tool | Server returns |
|---|---|
| `start_room_check` | What to ask next and what's still unknown in the room profile (floor, walls, owned pieces, budget) |
| `check_pieces` | Per link: **facts** (name, store, photo, price and was-price, lowest price with "price varies by size", W/D/H, material, in stock) and a **verdict** (score 1-5, Fits line, Watch / Try line, flags, unknowns) |
| `find_similar` | What it looked for, the **best 3** (each with facts and verdict), a **left-out summary** ("2 over $300, 3 couldn't read the page"), and a message if fewer than 3 qualify |
| `ready_to_buy` | Ready or not, **missing** (dimensions, price, chosen version, return window) and **concerns** (low contrast, conflicts with a rule, over budget, sold out). Warns, never blocks |
| Every tool | A run ID; a plain message when a page couldn't be read ("Share the price and size, or a screenshot") or a limit was hit |

**The card (the same shape every time; four things up front):** photo; name, store and price; Fits; Watch with a Try fix. Below them, small: flags and anything "not found."

### 4.4 Find similar: search logic (shared engine, used by both phases)
1. The description of what to look for comes from the source: a pin, a product link, several (what they have in common), or words.
2. Search for options under the budget ($500 default; a note such as "under $300" wins; items between the two get a Watch line).
3. At most 2 searches: one limited to stores the agent can read, one on the open web. Drop any link not in the search results; skip Amazon; dedupe by product link.
4. Verify each page loads with a real price and photo (same guardrails as Check).
5. **Budget filter:** drop anything over budget using the verified price. Unknown prices only if nothing else qualifies, labeled "price not found."
6. **Taste filter:** one batched judgment scores each candidate's photo against the reference, the confirmed rules, the built-in contrast principle (general, deferring to confirmed rules), and the owned pieces; drop low scores. A judgment made without the room profile is a bug.
7. Return the best 3. If fewer qualify, say so. One line says how many were left out and why.
8. Daily cap on searches; cost and time logged per call.

### 4.5 What the agent decides vs what needs a person
**Agent on its own:** fetches and verifies facts, checks, filters, writes Fits and Watch lines, suggests the best 3.
**Needs a person:** confirming the room profile and any taste rule, every buying decision. The server keeps nothing between calls.

---

## 4c. Phase 1.5: Try it in Notion (10/20)

**Goal:** a visitor sees a working loop in their own Claude and Notion within about 2 minutes, typing nothing but one paste.

**Visitor flow**
```
"Try it in 2 minutes"
  1. Duplicate the template .............................. Notion, 10 sec
  2. Add the public Room check URL + turn on Notion ...... Claude, 1 min
  3. Copy the starter prompt from the template, paste .... Claude, 10 sec
     -> Claude reads the room from their Notion, Room check judges,
        cards with photos and verdicts land in their Items gallery
  4. "Try this next" on the page:
     - add a rug under Owned, ask again: the verdict changes
     - share with someone; they vote on the board
     - tell Claude about your real room in one sentence
```

**Template contents:** a sample room (light oak floor, white walls, gray sofa, likes filled in, since checks need likes), Owned pieces, 3 sample product links in Inbox from stores that read reliably from Vercel (IKEA through JSON-LD, Shopify stores), the Items gallery and voting views, a "How it works" section (which database each card goes in, which fields to fill), the starter prompt, and a "Try this next" checklist. No personal details.

**The moment only Notion gives:** an edit in Notion changes the next verdict. v1 uses an owned piece (add a jute rug and the walnut coffee table goes from Watch to Fits). Rules stay read-only text on the page in v1; a Rules database people can edit is the first item after.

**Access:** one public demo URL, plus personal URLs for specific people. Caps are per key (`engine/caps.py`), so the public key is registered as a tester with a raised `CAP_TESTER_CHECKS_PER_DAY`; the global daily spend cap still applies. Rotate the key if it's abused.

**Not in 1.5:** a Notion AI or Custom Agent version (needs a Business or Enterprise plan and an owner enabling custom MCP servers), the vote reminder agent, a Rules database, one-click setup with Notion sign-in, ChatGPT, judge tuning, find a set.

**Done when**
- A fresh duplicate of the template runs the whole flow 3 times, each logged; cards land in the right fields in at least 2 of 3 runs.
- The rug edit changes the verdict on camera.
- A 90-second recording.

**Cut order:** view polish first, then reading the builder's own workspace, never the rug edit.

---

## 4b. Phase 2 (follow-up): Notion product

### 4b.1 Flows: inputs and actions, not steps
Anything dropped on the board becomes a card. The agent looks at what each card contains and what's ticked, and does what it needs. Nothing is a required step; setup only makes checks sharper.

```
INPUT (any, in any order)                 ACTION
- one link, or several in one card    ->  Check          automatic for every product card
- a pin image (one per card)          ->  Find similar   opt-in tick on any card, or on several
- words ("cream lounge chair <$400)   ->                 cards ticked together ("more like these")
- existing cards you like             ->  Review         whenever the decider opens Claude
```

| Flow | Trigger | Agent does | People do |
|---|---|---|---|
| **Room setup** (optional, once) | Photos on the room page | Reads the room and proposes owned pieces | Confirm or correct |
| **Calibration** (optional, anytime) | About 15 varied style cards (Type: Calibration) | Drafts or updates the taste page | Each drags Love or No (about 2 min); the decider confirms rules |
| **Add** | A card with a link, several links (split into one card each), a pin, or words | Fills photo, price, size, return window, reviews for products | none |
| **Check** | Every product card | Judges against confirmed taste rules, owned pieces (contrast) and budget; one Fits line and one Watch line with a Try fix; flags issues. Without a taste page it says "taste not set yet, judged on room and budget only" | Read on the card |
| **Find similar** | Find similar ticked on a pin, a product card, several cards ticked together, or a words card; optional "what to change" note | Search logic in 4.4; adds the best 3 as Suggested, linked to what prompted them | Wait about a minute |
| **Triage** | Suggested cards appear | none | The decider moves Suggested to Considering or Pass. Only Considering reaches the second person |
| **Decide together** | Considering | none | Both drag Love / Maybe / No; winner to Shortlist, then Buy. At Shortlist the decider picks the exact version (size, color); from then on every check uses it |
| **Review session** | The decider opens Claude | Suggests what to decide next (anchors first); surfaces one insight grounded in past decisions, labeled as an observation; records votes with reasons; proposes taste updates. If the second person hasn't voted, asks "decide now or wait?"; on "wait," posts one reminder in Notion | Answer in plain words; approve or reject updates |
| **Act** | Shortlist or Buy | Re-checks price and stock daily. Ready-to-buy **warns** (does not block) when dimensions, return window, chosen version or price are unknown or a confirmed rule fails | Buy |

### 4b.2 What the agent decides vs what needs a person
**Agent on its own:** fills details, checks, filters, writes Fits and Watch lines, adds Suggested cards, proposes taste updates, re-checks prices.
**Needs a person:** moving Suggested to Considering, every vote, Shortlist and Buy, approving taste-page changes. Edits to agent fields always win.

---

## 5. Architecture
One engine, many surfaces: all fetching, judgment, search and rules live in `engine/`; each surface is a thin adapter. Detail: [architecture.md](architecture.md).

**Phase 1 (MVP): hosted connector**
```
User's Claude ──MCP──> api/mcp.py on Vercel (gate: personal link, daily caps) ──> engine/
                                                                                   |        |          |
                                                                            Claude API  store pages  Tavily
                                                       Upstash Redis: usage counts, product cache (24 h), cost log
```
- **Stateless:** the room profile comes in with each call; no Notion access; photos never reach the server.
- **Models:** a small, cheap model (Haiku 5.5) writes the search brief and makes one batched judgment per call; it beat the stronger model on the judgment evals. `MODEL_JUDGE` switches back.

**Phase 2 (follow-up): Notion runner**
```
Two people ── Notion (the whole interface and the data)
                 ^
                 | reads new cards, writes results
           Agent runner (Python, checks every minute) ──> engine/ (same as Phase 1)

Review session: Claude Desktop -> the agent's tools (list pending, record vote + reason,
approve taste updates) -> Notion.
```

### Cost
First search test: about 60,000 input tokens per pin, mostly search results fed back into a mid-size model across about 4 searches.
**Built into the engine (Phase 1):** the small model for both the brief and the judgment (it passed the judgment evals); at most 2 searches; a search API that returns only links and titles, so the model never reads raw results; one batched judgment; downscaled images; prompt caching of instructions; product facts cached 24 hours. First live run: a check of 4 links about $0.01, find similar about $0.004.
**Caps (Phase 1):** about 10 checks and 3 finds per person per day, plus a hard global daily spend cap.
**Later:** search trusted Shopify stores directly (no model tokens), cache repeat pins.
**Eval:** report tokens per call before vs after, with result quality held steady.

---

## 6. Data model

**Phase 1 (MVP)**
- **Room profile (per call, never stored):** name, floor, walls, owned pieces (name, material, tone, placement), budget per piece, likes (not rules), taste rules with status (only confirmed ones count), notes.
- **Product and verdict:** the engine types (product facts with sources; verdict with score, Fits, Watch / Try, flags, unknowns).
- **Redis (machine state):** per-link and global usage counts, product cache, run log with cost and time. No personal content; kept 30 days.

**Phase 2 (follow-up): Notion**
- **Rooms:** owners, budget per item, spot measurements, photos and renders.
- **Items:** one database for everything on the board. **Type** (what the card is): Product, Pin, Request, Calibration. **Status** (where it stands): Suggested, Considering, Shortlist, Buy, Bought, Owned, Pass. Boards are columns of Status; Type is a small tag. Cards show photo, name, price, Fits, Watch (with "Try"), votes, flags; everything else stays inside the page. Key fields: URL, store, price and was-price, W/D/H with source, return window with source, rating with source, one vote and one reason per member, Decider, flags, agent status, found-from (link to the pin).
- **Taste page:** sections per member and shared. Each rule is "what / works when / doesn't work when" with a status (proposed or confirmed). Only confirmed rules drive filtering and evals. Dated change log of approved updates and overrides.
- **Local state (never shown on the board):** fetch log, price history, the agent's last written value per field (to detect your edits), search runs with cost and time.

---

## 7. Data sourcing and compliance
Source ladder: official API, then public feed (Shopify `.js`), then structured data on the product page, then a screenshot the person adds. Only pages tied to a request; stop on 403, 429 or a bot check and never work around it; robots.txt checked; at most one price check per item per day; honest user-agent; store only product fields, link images rather than copying them. Secrets live in `.env` locally and in Vercel environment variables, never committed.
**Public server (Phase 1):** only public https pages on the standard port; private and internal addresses blocked, re-checked on every redirect; size and time limits; only structured fields reach the model, never raw page text.

---

## 8. Metrics and evals

**Outcomes**
- **Phase 1 (MVP):** the first two users use it on their next living room pieces; 20-50 invited people try it; share who come back for a second check; 0 purchases that fail the context check; cost per call within caps.
- **Phase 2:** decisions made on the next living room pieces; the second person votes within 1 day (unprompted).

**Evals** (Phase 1 evals gate opening the connector to people beyond the first two users)
| Eval | Phase | Pass bar |
|---|---|---|
| Search quality: share of returned links that are real, verified product pages | 1 | Track; first test 8 of 14 |
| Budget compliance: suggestions over budget shown | 1 | 0 |
| Grounding: links shown that weren't in search results; facts not on the product page | 1 | 0 |
| Contrast test across rooms beyond the first one (light floor, tonal, maximalist, empty, a non-wood rule): a piece that truly merges with its surround is flagged, a near-match gets a Watch with a fix, matching that texture or form separates is allowed, and a confirmed rule wins over the built-in principle in both directions | 1 | Rule-override cases right in 3 of 3 runs; at most 1 over-block per run in no-rule rooms, none concentrated in one style; 0 leaks of the first room's details |
| Context accuracy (paired cases): the same tone in two settings, built only from confirmed rules (named pair: walnut coffee table on the floor fails, walnut bookshelf by the door passes) | 1 | Both halves right on every pair |
| Rule violations: says yes where a confirmed rule says no | 1 | 0 |
| Over-blocking: says no to what confirmed rules allow | 1 | Low, tracked |
| "Not found" honesty: invented values | 1 | 0 |
| Cost and time per call | 1 | Track; see Cost |
| Pin match: the user rates each suggestion as a match | 1 | Track the share |
| Ready-to-buy: items reaching Buy with a must-have missing | 1, 2 | 0 |
| Learning: predicted vs actual vote, first 10 votes vs next 10 | 2 | Directional improvement |
| Trust: how often each agent field is edited | 2 | Track per field |
| Insight usefulness: each review-session insight rated useful or not | 2 | Track the share |

Report counts and stories; the sample is small and says so.

---

## 8b. Usable by others
**Phase 1 (MVP):** usable by anyone with an invite link and a Claude account, no setup beyond pasting the link. Free to them; the server pays, within caps.

**Phase 1.5 (10/20):** usable by anyone with Claude and Notion accounts, in about 2 minutes, through the public demo URL and the template (section 4c). Free plan users get one custom connector in Claude, so anyone already using one is blocked; the setup notes say so.

**Phase 2 (Notion product)**
| Level | Meaning | When |
|---|---|---|
| **A. Designed for others** | No hardcoded people, rooms, budgets or stores: members (any number) each get a vote and reason field at setup; rooms are rows with their own owned pieces and photos; budget, stores and decider are room settings; each workspace's taste page starts empty | Phase 2, done while building |
| **B. A friend can set it up** | Notion template to duplicate, README setup, 2-3 test households | After Phase 2 |
| **C. Anyone can use it** | Hosted runner, Notion sign-in instead of tokens, usage limits, billing, privacy policy, stricter compliance (official store APIs or partnerships) | Not planned; "what it would take at scale" |

Until level B has outside users, the claim is "designed so any household can configure it," never "used by other households."

---

## 9. Risks
| Risk | Phase | Mitigation |
|---|---|---|
| Cost runs away (abuse or a bug) | 1 | Invite-only personal links, per-person and global daily caps, a monthly spend limit on the Anthropic account, cost logged per call |
| Public demo URL is shared widely | 1.5 | Raised cap on that one key, global daily spend cap, personal URLs for specific people, rotate the key |
| Claude writes cards to the wrong fields in a stranger's Notion | 1.5 | "How it works" on the template page; 3 fresh-duplicate runs before recording; a flow-hint line if needed |
| Sample links break (sold out, blocked) and the first card says "Not found" | 1.5 | Only reliably readable stores; checked in the fresh-duplicate runs; 24 h product cache |
| Gallery cards have no photos | 1.5 | Test cover images first; fallback: image in the page body, gallery previews page content |
| Many stores can't be read | 1 | Honest "couldn't read this page" with a way to share facts; grow the list of readable stores; never bypass blocks |
| Search returns weak results | 1, 2 | Budget and taste filters, best 3 only, honest "fewer than 3" message |
| Judgment gets a fact about the room wrong (e.g. called white walls dark) | 1, 2 | Grounding and context evals before opening up |
| People add it once and don't come back | 1 | Setup in one paste; the same card every time; track second-check rate |
| The second person doesn't engage | 2 | Only picked items reach them; one drag to vote |
| Runs only while the laptop is open | 2 | Accept for Phase 2; host the runner later |

---

## 10. Build order (changed 10/17: usable by people first)
**Phase 1 = the MVP: Room check connector.**
1. Shared engine: fetch (with guardrails), extract, judge (check, contrast, ready-to-buy), search (find similar), models (router, structured output, cost log), types. Unit tests for extract and guardrails.
2. Remote MCP server: `start_room_check`, `check_pieces`, `find_similar`, `ready_to_buy`; per-user and daily caps; public-server guardrails; run IDs and cost per call.
3. Host it; the first two users try it, then 20-50 people.
4. Evals on the engine (contrast pairs, grounding, budget compliance, "not found" honesty, cost and time per call).

**Phase 1.5 (10/20): Try it in Notion.** Section 4c. Comes before judge tuning.

**Phase 2 = the follow-up: Notion product (collaboration, memory, learning).** Starts after Phase 1 ships.
5. Workspace changes (Type, statuses, fields, per-member votes, taste page, level A settings).
6. Notion runner (Check and Find similar routes) on the same engine.
7. Calibration and the taste page.
8. Review session (local MCP over the Notion board).

**Cut order if short on time:** in Phase 1, `ready_to_buy` before anything else; never cut verified facts, the contrast check, or find similar.

## 11. After the MVP
1. **Phase 2, the Notion product** (sections 4b and 10).
2. **After Phase 1.5, in order:** a Rules database people edit (What, Works when, Fails when, Status, Added by; flipping a rule changes the verdict); the vote reminder (the agent leaves a Notion comment that @mentions the voter, so Notion sends the notification); one-click setup (a hosted page with Notion sign-in that duplicates the template and issues a key); a Notion Custom Agent version (Business or Enterprise); the same room from ChatGPT.
3. Level B: template, README setup, 2-3 test households.
4. **Find a set.** When the person wants several pieces that go together (3-4 mixed dining chairs), the agent shows every option found, then proposes 1-2 combinations that match their reference pins, with one line on why each set works together, and asks the person to choose or swap. It never buys or locks in a set.
5. Hosting the runner; a second domain on the same engine; Pinterest API import.
