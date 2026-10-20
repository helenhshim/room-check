# Room check: Technical Architecture

Matches the [spec](spec.md). Principle: **one engine, many surfaces.** All judgment, fetching, search and rules live in `engine/`; each surface (the MCP server today, a Notion runner later) is a thin adapter.

This is the public copy. It describes what shipped in Phase 1 (the hosted connector) and Phase 1.5 (Try it in Notion). The original design also planned a Notion runner on a laptop; Phase 1.5 replaced that, for now, with Notion's own connector in Claude.

## 1. System overview

```
             you, in Claude
                   |
                   v
   +----------- Claude -----------+
   |                              |
   v                              v
Notion's connector          Room check MCP (api/mcp.py on Vercel)
reads the room page,          gate: personal key, daily caps
writes verdicts back                  |
                                      v
                                   engine/
                       fetch -> extract -> judge, search
                          |                  |        |
                     store pages        Claude API   Tavily
                                      
                     Upstash Redis: usage counts, product cache (24 h), run and cost log
```

Room check never touches Notion. Claude reads and writes the workspace through Notion's connector, and sends Room check a text room profile and links. Room photos never reach the server.

## 2. Phase 1 decisions (approved 10/17)
| # | Change | Why |
|---|---|---|
| 1 | **Upstash Redis** (via the Vercel marketplace, free tier) for rate limits, daily spend caps, product cache, run logs | Serverless functions keep no files between requests, so SQLite can't hold shared state on Vercel |
| 2 | **Personal connector URL per user** (a link with a random key); limits per key plus a global daily cap | Requests come from Anthropic's servers, so IP addresses can't identify users |
| 3 | **Find similar under about 20-30 s** | Long tool calls risk timing out |
| 4 | **Dedicated search API (Tavily)** returns links and titles; the model never reads raw search results | Most of the first test's ~60k tokens per pin were search results fed to the model |
| 5 | **One batched judgment call** per find (room profile once, about 8 candidates together) | Several times cheaper and faster than one call per candidate |
| 6 | **Downscale product images server-side** before judging | Fewer image tokens, faster |
| 7 | **Parallel fetch** (one request per store at a time), **24 h product cache**, **prompt caching** of fixed instructions | Repeat checks nearly free and instant; first checks faster |

Model plan: a small model (Haiku 5.5) describes what to look for and makes the single batched judgment. It passed the judgment evals where the stronger model over-blocked. `MODEL_JUDGE` switches back. Hosting: Vercel with Fluid compute (Python functions run up to 300 s).

## 3. Repo layout
```
engine/   fetch.py (guardrails, robots.txt, parallel, cache) · extract.py (JSON-LD, microdata, Shopify)
          judge.py (batched judgment, code flags, ready to buy) · search.py (Tavily -> verify -> filter -> best 3)
          models.py (router, prompt caching, structured output, cost log) · guide.py (room profile cleanup)
          caps.py (per-key and global daily caps) · state.py (Redis or in-memory store, run log) · types.py
api/      mcp.py (MCP over streamable HTTP, the four tools) · _auth.py, keys.py (personal keys, rate-limited issuing)
tests/    offline tests, no network or API keys
```

## 4. Core types (the contract between engine and surfaces)
| Type | Fields (short) |
|---|---|
| `Room` | name, floor, walls, owned pieces (name, category, material, tone, placement), budget per piece, likes, rules, notes |
| `Product` | url, store, name, image, price (lowest verified), was-price, variants (name, price, availability, options), dims (w, d, h, source), material, availability, verified, blocked reason, chosen version |
| `Verdict` | score (1-5), fits, watch / try, flags, unknowns |
| `Rule` | what, works_when, fails_when, status (proposed or confirmed) |
| `FindResult` | what it looked for, best picks (product and verdict), left-out summary, message |

Every engine function takes and returns these types. Surfaces translate them to and from MCP tool JSON (or, later, Notion properties).

## 5. Data flow per action

**Check** (each product link)
```
url -> guard + robots -> Shopify .js | JSON-LD | microdata -> Product (verified)
     -> judge(Room, Product)   [judge model, structured output, confirmed rules only]
     -> Verdict + code flags (over budget, not found, sold out)
```
**Find similar**
```
source (pin image | product link | words) -> describe [small model] -> what to look for + 2 queries
 -> web search (max 2) -> candidate links (drop any not in results; skip Amazon; dedupe)
 -> verify each (same as Check fetch) -> budget filter on verified price
 -> judge candidates against Room + confirmed rules [judge model, product images, one batched call]
 -> best 3 + left-out summary
```
**Ready to buy:** Product (chosen version) + Room -> checklist (dims, return window, version, price, rule pass) -> warnings, never a block.

## 6. Models and prompts
- **Router:** a small model for describing pins and writing queries; a judge model for checks and candidate judgment (Haiku after the evals). Model IDs live in config (`MODEL_SMALL`, `MODEL_STRONG`, `MODEL_JUDGE`), not code.
- **Structured output:** every judgment returns JSON validated against the types.
- **Prompts are versioned by hash;** evals record which prompt version produced each result.
- **Every judgment receives the room profile and confirmed rules.** A judgment without them is a bug.
- **Product page text is never passed to the model as instructions;** only extracted fields and images.

## 7. MCP server
- HTTP server for the Room check connector with `start_room_check`, `check_pieces`, `find_similar`, `ready_to_buy`.
- No Notion access; stateless; the room profile comes in with each call, and every tool tells Claude to send the same profile back unchanged so judgments stay consistent across a chat.
- Per-key daily limits, a global daily spend cap, key issuing limited per address per day.
- Run ID and cost (tokens, searches, seconds, USD) per call. The run log keeps cost fields only, never room or user text.

## 8. Evals
- Golden sets: paired context cases (from confirmed rules only), contrast pairs across several room styles, budget cases, grounding, "not found" honesty, cost.
- The eval runner calls engine functions directly and writes a report: pass rates, violations, over-blocking, grounding, budget compliance, tokens and seconds per call, prompt version.
- Run before and after every prompt or model change. A separate live suite reruns every shipped fix against production.

## 9. Config and secrets
- `.env` locally and Vercel environment variables in production (see `.env.example`); never committed.
- Caps default in `engine/caps.py`, overridable by environment variables.
