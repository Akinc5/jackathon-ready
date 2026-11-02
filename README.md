# Jackathon

A team of AI agents that helps you win hackathons. Give it a hackathon name (and a track, if you have one). It researches the event, ranks the problem statements most likely to win, and, in later phases, builds the frontend and backend, writes the pitch deck, records the demo video, critiques the result like a judge, and commits everything to GitHub.

It's a **co-pilot, not autopilot**: you approve the key decisions, starting with which problem to build.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Orchestrator, multi-provider LLM layer, **Research** and **Strategist** agents, CLI | ✅ done |
| 2 | **Architect**, **Backend** and **Frontend** agents, code sandbox | next |
| 3 | **Compiler**, **Evaluator** loop, **Committer** | |
| 4 | **PPT** and **Video** agents | |
| 5 | Premium web UI with live agent streaming | |

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
copy .env.example .env          # then add at least one API key
jackathon doctor                # checks your models and keys
jackathon run "HackMIT 2026" --track "Healthcare" --skills "python, react" --hours 24
```

Other commands: `jackathon runs` (list runs), `jackathon show <run_id>`, `jackathon resume <run_id>`.
Run the tests (offline, free): `pytest`.

## Choosing models

Each agent's model is set in `.env` as `provider:model`:

```ini
JACK_MODEL=anthropic:claude-opus-5-5          # default for every agent
JACK_MODEL_RESEARCH=gemini:gemini-2.5-flash   # override just one agent
```

Supported providers: `anthropic`, `openai`, `gemini`, `groq`, `openrouter`, `ollama` (local and free). Use a cheaper model for the Research agent, which makes lots of calls, and a stronger one for the Strategist.

The Research agent searches with DuckDuckGo by default (no key needed). Add a `TAVILY_API_KEY` for better results.

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
│   └── web.py                web_search, fetch_page
├── core/
│   ├── events.py          4. event bus: how the UI watches agents work
│   ├── models.py          5. the data passed between agents
│   ├── store.py              save/load runs as JSON
│   └── pipeline.py        7. the orchestrator: runs agents in order
├── agents/
│   ├── base.py            6. THE AGENT LOOP (the most important file)
│   ├── research.py           Research agent
│   └── strategist.py         Strategist agent
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
