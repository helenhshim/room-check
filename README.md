# Room check

Room check is a hosted MCP connector that judges furniture against a real room. You give Claude a room (floor, walls, what's already in it, what you like, your budget) and a few product links. Room check reads each store page, verifies price, size and material, and returns a short card per piece: **Fits**, **Watch** or **Try**, with the reason in one line.

It started as a fix for a problem I kept having: furniture that looks great on a product page and wrong in the room, because nobody checked it against the walnut coffee table that was already there.

## Try it in 2 minutes

The room lives in Notion, so you can edit it, share it, and watch the verdicts change. Start with the sample room, then make it yours.

1. Duplicate the [Notion template](https://topaz-stone-8c9.notion.site/Room-check-try-it-in-Notion-3ff4e3e36c2f81a4a0f8d456bc9259d9) (top right).
2. In Claude, open Settings, Connectors and add Room check as a custom connector, using the Room check URL on the template page.
3. On the same page, connect Notion and pick the workspace with your copy.
4. Paste the starter prompt from the template into a new Claude chat.

## How it works

```
             you, in Claude
                   |
                   v
   +----------- Claude -----------+
   |                              |
   v                              v
Notion's connector          Room check MCP (this repo, on Vercel)
reads the room page,        start_room_check, check_pieces,
writes verdicts back        find_similar, ready_to_buy
                                  |
                                  v
                    store pages, web search, Claude API
```

- **Room check never touches Notion.** Claude does the reading and writing through Notion's own connector. Room check takes a room profile and links as plain inputs and returns verdicts. That keeps the connector small, keeps your workspace permissions with Notion, and means the same connector works from any MCP client.
- **Four tools.** `start_room_check` cleans up the room profile and says what's still unknown. `check_pieces` judges product links against the room. `find_similar` searches the web for pieces like a reference and returns the best 3 plus what it left out and why. `ready_to_buy` is a last look before checkout (dimensions, return window, which version).
- **Facts come from the page, not the model.** `engine/fetch.py` and `engine/extract.py` read structured data (JSON-LD, microdata) from each store page. If a price or size isn't on the page, the card says "not found" instead of guessing.
- **Code decides what code can decide.** Budget, missing dimensions and broken taste rules are flagged in code (`engine/judge.py`). The model judges taste and contrast against the room, in one batched call.
- **Guardrails.** Only public https URLs are fetched (DNS is resolved so private addresses can't sneak in). Each key has daily check and search limits, and a global daily spend cap stops everything once reached (`engine/caps.py`). The run log keeps cost fields only, never room or user text.

### Layout

| Path | What it is |
|---|---|
| `api/mcp.py` | The MCP server (streamable HTTP) and its four tools |
| `api/_auth.py`, `api/keys.py` | Personal connector URLs and rate-limited key issuing |
| `engine/` | Fetching, extraction, search, judgment, caps and shared state |
| `tests/` | Offline tests (no network, no API keys) |
| `docs/spec.md` | Product spec: problem, users, phases, flows, evals, risks |
| `docs/architecture.md` | Technical design: system overview, decisions, data flow |
| `GLOSSARY.md` | The words the product and the judge use (confirmed rule, Watch, block, over-block) |

## Holding judgment to evals

Taste is the part that's easy to get confidently wrong, so judgment is held to evals rather than vibes. Each change to the judge prompt or model is rerun against a fixed set of cases before it ships.

- **Contrast pairs.** Pairs of pieces where one should fit the room and one shouldn't (for example, a walnut coffee table next to an existing walnut piece fails, a walnut bookshelf passes). Bar: 5 of 5 every run.
- **Model choice came from the evals.** Haiku 5.5 passed 5 of 5 contrast pairs in 3 of 3 runs with 0 over-blocking. Sonnet 5.5 scored 3, 4 and 3 of 5 and over-blocked warm accents. Judgment runs on Haiku at about $0.001 per call.
- **Grounding.** 0 of 60 cards made claims that didn't trace back to the page facts or the room. Offline, all 27 facts on 4 recorded pages traced to the page source.
- **Cost.** Reworking the find step took a search from about 60,600 tokens to about 6,000, and from $0.17 to $0.015.
- **Shipped fixes.** Every bug fixed after a live test gets a live eval case against production. 6 of 8 pass today; the other 2 are tracked as open work.

The eval suites run against recorded store pages and a private test room, so they aren't included in this snapshot. The offline tests are:

```bash
pip install -r requirements-dev.txt
pytest
```

## What's next

- **What you ask for outranks the room's likes.** A request for an ivory rug should not come back jute just because the room likes natural textures.
- **Orchestration in the connector, not the prompt.** Move "read the room page, write verdicts back" into the connector's own guidance, so it works without the starter prompt and in other MCP clients.

## Run it yourself

Copy `.env.example` to `.env` and fill in an Anthropic key and a Tavily key. Upstash Redis is optional locally (counts live in memory without it). Deploys to Vercel as a Python function (`vercel.json`).

This repo is a code snapshot of a private working repo, published without its history, test fixtures or eval data.
