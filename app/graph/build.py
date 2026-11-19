"""Connects the nodes into the interviewer graph (the diagram is in plan.md and HOW_IT_WORKS.md).

- add_node registers a step; add_edge is a fixed arrow (ask -> answer);
  add_conditional_edges is a fork: a routing function from nodes.py returns the next node's name.
- The loop ask -> answer -> evaluate_answer -> ask repeats until every question is done, then
  goes to final_feedback and END.

build_graph() returns the graph before it is compiled. app/main.py compiles it with the Postgres
checkpointer, so progress is saved. The module-level `graph` has no checkpointer: it is what
LangGraph Studio loads through langgraph.json (Studio brings its own).
"""

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import InterviewState


def build_graph() -> StateGraph:
    g = StateGraph(InterviewState)
    g.add_node("prepare_brief", nodes.prepare_brief)
    g.add_node("review_brief", nodes.review_brief)
    g.add_node("plan_questions", nodes.plan_questions)
    g.add_node("ask", nodes.ask)
    g.add_node("answer", nodes.answer)
    g.add_node("evaluate_answer", nodes.evaluate_answer)
    g.add_node("final_feedback", nodes.final_feedback)

    g.add_conditional_edges(START, nodes.route_start, ["prepare_brief", "plan_questions"])
    g.add_edge("prepare_brief", "review_brief")
    g.add_conditional_edges("review_brief", nodes.route_after_review, ["plan_questions", "prepare_brief"])
    g.add_edge("plan_questions", "ask")
    g.add_edge("ask", "answer")
    g.add_edge("answer", "evaluate_answer")
    g.add_conditional_edges("evaluate_answer", nodes.route_after_evaluate, ["ask", "final_feedback"])
    g.add_edge("final_feedback", END)
    return g


graph = build_graph().compile()
