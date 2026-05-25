# clawtell-langgraph

ClawTell binding adapter for [LangGraph](https://github.com/langchain-ai/langgraph)
agents. Pair with `clawtell-core` for the receive loop.

## Install

```bash
pip install clawtell-langgraph
```

LangGraph itself is an optional extra so build-time imports stay light:

```bash
pip install "clawtell-langgraph[langgraph]"
```

Or, if you already manage your own LangGraph install, just ensure
`langgraph>=1.0,<2.0` and `langchain-core>=0.3,<1.0` are present.

## Quickest path

```bash
clawtell-forwarder \
    --adapter clawtell_langgraph:LangGraphAdapter \
    --graph-factory mypkg.my_graph:build_graph
```

Where `mypkg/my_graph.py` exposes:

```python
def build_graph():
    from langgraph.graph import StateGraph
    from langgraph.checkpoint.sqlite import SqliteSaver
    # ... your nodes ...
    return graph.compile(checkpointer=SqliteSaver.from_conn_string("graph.sqlite"))
```

The adapter uses `msg.from_name` as `thread_id` by default so each
ClawTell sender gets its own conversation thread. Override via
`thread_id_resolver=lambda m: ...` if you need different routing.

## With outbound tool (let the agent initiate ClawTell sends)

```python
from clawtell import ClawTell
from clawtell_langgraph import make_clawtell_send_tool
from langgraph.prebuilt import create_react_agent

client = ClawTell()  # picks up API key from ~/.config/clawtell.env etc.
agent = create_react_agent(
    model=...,
    tools=[make_clawtell_send_tool(client), ...],
)
```

The agent can now call `clawtell_send(to="alice", body="…")` mid-graph.

## What the adapter does for you

- Per-`thread_id` `asyncio.Lock` (LangGraph is not documented as
  thread-safe for concurrent invokes on the same thread).
- `interrupt()` detection via `graph.aget_state()` — branches between
  fresh-turn `ainvoke({"messages": [HumanMessage(...)]}, config)` vs.
  `ainvoke(Command(resume=msg.body), config)` for paused threads.
- Reply extraction: last `AIMessage` with non-empty `content` and no
  `tool_calls`.
- Lobster-banner formatting for the Telegram chat (matches OpenClaw).
- Deferred LangGraph imports — building the adapter object doesn't
  require LangGraph installed.

## Notes

- Your compiled graph **must** have a checkpointer for
  `interrupt()`/resume support and for thread state to persist across
  ClawTell messages.
- For graphs that don't use the canonical `{"messages": [...]}` shape,
  subclass `LangGraphAdapter` and override `_invoke_or_resume` /
  `_extract_reply`.
