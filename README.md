# Jackathon

A team of AI agents that helps you win hackathons. Type a hackathon's name, or drop its flyer or PDF, into one input bar. Jackathon researches the event, ranks the ideas most likely to win, plans and builds a working app, tests it in a real browser, and hands you:

- **a live URL** (deployed to Hugging Face Spaces)
- **a public GitHub repo**
- **a pitch deck** (.pptx, with screenshots and speaker notes)
- **an app document** (PDF: every feature, screen and endpoint)

It's a **co-pilot, not autopilot**: you pick the idea, approve the plan, try the app and ask for changes before anything ships. You can watch every step live.

```
[flyer/PDF] -> research -> strategy -> [you pick] -> plan -> [you approve]
  -> backend + frontend (in parallel) -> browser test (+1 auto-repair)
  -> [you review: ship it, or ask for changes] -> GitHub -> deploy -> deck + PDF
```

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
playwright install chromium     # the browser used for testing and PDFs
copy .env.example .env          # then add your keys (see below)
jackathon doctor                # checks your models and keys
jackathon web                   # opens the interface at http://localhost:8080
```

You'll also need:

- **Node.js 18+**, to build the React frontends.
- **The GitHub CLI**, signed in (`gh auth login`). Jackathon creates the repo under your account.
- **`HF_TOKEN`** in `.env`: a free *Write* token from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). Without it, everything except the deploy still works.

Prefer the terminal? `jackathon run "HackMIT 2026"` or `jackathon run --file flyer.png` runs the same pipeline. Other commands: `jackathon runs`, `jackathon show <id>`, `jackathon resume <id>`, `jackathon serve <id>` (run a generated app locally). Tests (offline, free): `pytest`.

Each run lives in `runs/<run_id>/`. The generated app is in `project/`, and the deck and PDF are in `project/docs/`.

## Choosing models

Each agent's model is set in `.env` as `provider:model`:

```ini
JACK_MODEL=gemini:gemini-flash-lite-latest      # default for every agent
JACK_MODEL_STRATEGIST=gemini:gemini-3.6-flash   # override just one agent
```

Supported providers: `anthropic`, `openai`, `gemini`, `groq`, `openrouter`, `ollama` (local and free). Use a cheaper model for the Research agent, which makes lots of calls, and a stronger one for the Strategist.

**Free setup (tested):** a Gemini key from [AI Studio](https://aistudio.google.com) with the two models above. Groq's free tier caps requests at about 8K tokens, which is too small for the Research agent.

**Web search:** add a free `TAVILY_API_KEY` ([tavily.com](https://tavily.com), 1,000 searches/month, no card needed). Without one, the agent falls back to keyless search through the `ddgs` library, which often returns nothing.

---

## How the code works

Read the files in this order. Each one builds on the one before.

```
jackathon/
├── config.py              1. settings and provider list
├── llm/                   2. talking to any AI provider
│   ├── base.py               the shared vocabulary: Message, ToolCall, ToolSpec, LLM
│   ├── anthropic_llm.py      translator for Claude
│   ├── openai_llm.py         translator for OpenAI, Gemini, Groq, OpenRouter, Ollama
│   └── __init__.py           create_llm("provider:model")
├── tools/                 3. things agents can do
│   ├── base.py               Tool = Pydantic args model + async function
│   ├── web.py                web_search, fetch_page
│   ├── files.py              list/read/write/delete, locked to one folder
│   ├── runner.py             runs a fixed command, with secrets removed from its environment
│   ├── checks.py             install + test (backend) and install + build (frontend)
│   ├── smoke.py              opens every screen in headless Chromium; screenshots
│   ├── publish.py            GitHub repo via the gh CLI
│   └── deploy.py             Hugging Face Docker Space
├── core/
│   ├── events.py          4. event bus: how the UI watches agents work
│   ├── models.py          5. the data passed between agents
│   ├── store.py              save/load runs as JSON
│   └── pipeline.py        7. the orchestrator: every stage, in order
├── agents/
│   ├── base.py            6. THE AGENT LOOP (the most important file)
│   ├── intake.py             reads a flyer image or PDF
│   ├── research.py           Research agent
│   ├── strategist.py         Strategist agent
│   ├── architect.py          Architect agent: idea -> build plan + API contract
│   ├── builders.py           Backend and Frontend agents
│   └── storyteller.py        writes the pitch and the document
├── deliverables/             renders the .pptx, the PDF and the README
├── scaffold.py               starts each project from templates/
├── templates/                FastAPI app, Vite + React + Tailwind app, Dockerfile
├── web/                   9. the web interface (server.py + static/)
└── cli.py                 8. the terminal interface
```

### 1. The big idea: an agent is a loop

An "agent" is just an LLM in a loop with tools:

```
send the task to the model
loop:
    model replies, maybe asking to call tools ("web_search: HackMIT tracks")
    we run those tools and send the results back
    until the model calls submit_result with its final answer
```

That loop lives in `agents/base.py`, and every agent reuses it. A specific agent only defines three things:

```python
class ResearchAgent(Agent[ResearchReport]):
    output_model = ResearchReport          # what it must hand back
    def system_prompt(self): ...           # who it is and how to work
    def tools(self): return [WEB_SEARCH, FETCH_PAGE]   # what it can do
```

### 2. Multi-provider: one vocabulary, many translators

Each provider formats conversations differently. Anthropic puts tool results inside a *user* message, while OpenAI has a separate *tool* role. So agents never talk to a provider directly. They use our own neutral types (`Message`, `ToolCall`, `ToolSpec` in `llm/base.py`), and each adapter translates:

- `AnthropicLLM` → the Anthropic SDK. It keeps Claude's raw reply (`Message.raw`) and sends it back unchanged on the next turn, because Claude's hidden "thinking" blocks must be replayed exactly as written.
- `OpenAICompatibleLLM` → the OpenAI SDK. Gemini, Groq, OpenRouter and Ollama all accept the OpenAI format, so the same class handles them by changing `base_url`.

To add a provider, add a line to `PROVIDERS` in `config.py`. That's enough for any OpenAI-compatible API.

### 3. Getting reliable, typed output from an LLM

Agents need to return *data* (a list of tracks, scores) rather than an essay. We use the most portable trick there is:

1. Describe the output as a Pydantic model (`core/models.py`).
2. Turn that model into a JSON schema and offer it to the LLM as a tool called `submit_result`.
3. When the model calls it, validate the arguments with Pydantic.
4. If validation fails, send the errors back as the tool result. The model reads them and fixes its answer.

It works the same on every provider. Bonus: the `description=` on each `Field` becomes an instruction to the model.

### 4. Let code do what code is good at

The Strategist gives each idea five scores from 1 to 10. **The weighted total is calculated in Python** (`Scores.total`, weights in `SCORE_WEIGHTS`), and the ranking is a plain `sort()`. LLMs make arithmetic mistakes, and this way you can tune the weights without changing a prompt.

### 5. Events: agents don't know who's watching

Agents call `self._emit(...)` ("started", "calling web_search", "finished"). The `EventBus` passes each event to every subscriber. Today the only subscriber is `cli.render_event`, which prints to the terminal. In Phase 5, the web UI will subscribe and stream the same events to the browser, without changing any agent code.

### 6. The pipeline, checkpoints and resume

`core/pipeline.py` runs the stages in order and **saves state after each one** (`runs/<run_id>/state.json`). If a run crashes or you press Ctrl+C, `jackathon resume <run_id>` skips the stages that are already done.

The human checkpoint is a function passed in (`choose`). The CLI asks in the terminal, the web UI will ask in a browser, and the tests pass a fake. The pipeline doesn't care which.

### 7. Testing without spending money

`tests/fakes.py` contains `ScriptedLLM`, a pretend model that replays pre-written replies. The pipeline accepts an `llm_factory`, so tests swap in the fake and run the whole flow offline in under a second.

### 8. Phase 2: two builders, one contract

The Backend and Frontend agents run **at the same time** (`asyncio.gather`) and never see each other's code. They fit together because the Architect's `BuildPlan` is an exact **API contract**: every endpoint as `METHOD /api/path` with its request and response shapes. Both builders get the same plan.

Things that make the builds reliable:

- **Templates, not blank folders.** `scaffold.py` copies a known-good FastAPI app and a Vite + React + Tailwind app into the project first, so agents never fight config files.
- **A real feedback loop.** Each builder has one check tool: the backend's installs, compiles, checks the contract and runs pytest; the frontend's installs and runs `vite build`. The agent reads the errors, fixes them and runs the check again.
- **Trust, but verify.** When an agent says it's done, the pipeline runs the checks again *itself* and records `verified`. An agent can't claim success its code doesn't back up.
- **Contract check.** `.jackathon/contract_check.py` reads the generated app's OpenAPI schema and confirms every planned endpoint is actually served.
- **Known runtime pitfalls.** Some bugs compile fine but crash in the browser, such as a second `<BrowserRouter>`, which happened in our first live run. `runtime_pitfalls()` catches the ones we've seen. A real headless-browser smoke test arrives with the Phase 3 Evaluator.

### 9. Sandboxing: what an agent can and can't do

Agents write code that we then run, so we limit what they can do:

- **Files:** an agent's tools resolve every path and refuse anything outside its own folder (`../`, absolute paths, `node_modules`).
- **No shell:** agents can't run arbitrary commands, only their one check tool, which runs commands *we* wrote.
- **Install safety:** `npm install --ignore-scripts` and `pip install --only-binary=:all:` mean installing a package never runs that package's code. Install scripts are the most common way malicious packages attack.
- **No secrets:** generated code (pytest, the dev server) runs with every `*KEY*`, `*TOKEN*` and `*SECRET*` variable removed from its environment.
- **Package names are validated** before they go into `requirements.txt` or `package.json`, so a plan can't smuggle in flags or URLs.

This isn't a full container sandbox: generated code still runs as you, on your machine. A Docker-based runner can later replace `tools/runner.py` without changing the agents.

### 10. Shipping: test, publish, deploy, pitch

- **Browser smoke test** (`tools/smoke.py`). `vite build` and pytest can both pass while the page is blank in a browser; our first live run proved it. So we run the app exactly as it will be deployed (FastAPI serving the built React app), open every screen in headless Chromium, and fail on JavaScript errors, blank pages, or a backend route answering where a page should be. If something breaks, the builders get one automatic repair round with the exact errors.
- **One container** (`templates/root/Dockerfile`). Node builds the frontend and Python serves the API plus the built files on port 7860. The same layout runs locally in the smoke test and on Hugging Face, so what we test is what we deploy.
- **GitHub** (`tools/publish.py`) uses your signed-in `gh` CLI, so Jackathon never handles your GitHub credentials.
- **Deck and PDF** (`deliverables/`). The Storyteller agent only writes words; code does the layout. The deck is rendered with python-pptx, and the PDF is HTML printed by Chromium. Layout stays consistent, and the model can't break the design.

### 11. The web interface

`web/server.py` runs the same `Pipeline` as the CLI. Two small ideas make it work in a browser:

- **Events become a live stream.** Each run keeps a list of its events, streamed with Server-Sent Events. Refreshing the page replays the history, so nothing is lost.
- **Questions are Futures.** When the pipeline asks "which idea?", `WebHuman` creates an `asyncio.Future`, shows the question card and waits. Clicking a button calls `/answer`, which resolves the Future, and the pipeline carries on. The CLI's `TerminalHuman` answers the same three questions with terminal prompts. The pipeline can't tell the difference.

The page itself (`web/static/`) is plain HTML, CSS and JavaScript, with no build step.
