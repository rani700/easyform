"""LangGraph state machine wiring all nodes together.

Flow:
    classify -> extract -> validate -> merge -> detect_missing -> format
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.nodes.classify import classify_documents
from agent.nodes.extract import extract_per_doc
from agent.nodes.format_response import format_response
from agent.nodes.merge import merge_with_manual
from agent.nodes.missing import detect_missing
from agent.nodes.validate import cross_validate
from agent.schemas import AgentState, ProcessRequest, ProcessResponse


def _build_graph():
    g = StateGraph(AgentState)

    g.add_node("classify", classify_documents)
    g.add_node("extract", extract_per_doc)
    g.add_node("validate", cross_validate)
    g.add_node("merge", merge_with_manual)
    g.add_node("missing", detect_missing)

    g.add_edge(START, "classify")
    g.add_edge("classify", "extract")
    g.add_edge("extract", "validate")
    g.add_edge("validate", "merge")
    g.add_edge("merge", "missing")
    g.add_edge("missing", END)

    return g.compile()


_GRAPH = _build_graph()


async def run_graph(request: ProcessRequest) -> ProcessResponse:
    state = AgentState(request=request)
    final_raw = await _GRAPH.ainvoke(state)

    # LangGraph returns the final state as a dict-like; coerce back to our Pydantic model.
    if isinstance(final_raw, AgentState):
        final = final_raw
    else:
        final = AgentState(**final_raw)

    return format_response(final)
