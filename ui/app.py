"""
Web UI backend (Phase 1c).
==========================
A thin Flask wrapper over the existing engine: two-pane chat + chart canvas with
persistent conversation history. It reuses `route_question` (trend->chart,
relational->graph, else->SQL) and `ChatStore`, so the UI adds no new analytics and
keeps the grounding guarantee.

Run:  pip install flask && python ui/app.py    ->  http://localhost:5000

Single local user assumed: one server-side Conversation, single-threaded (pyodbc
connections aren't thread-safe).
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, request, jsonify, send_from_directory   # noqa: E402
from agent.lbs_agent import Conversation, route_question          # noqa: E402
from agent.store import ChatStore                                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

store = ChatStore()
convo = Conversation(store=store)
convo.new_conversation()

app = Flask(__name__, static_folder=HERE, static_url_path="")


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.post("/api/ask")
def ask():
    q = (request.json or {}).get("question", "").strip()
    if not q:
        return jsonify(error="empty question"), 400
    r = route_question(convo, q)
    return jsonify(text=r["text"], source=r["source"], spec=r["spec"],
                   conversation_id=convo.conversation_id)


@app.get("/api/conversations")
def list_conversations():
    return jsonify([
        {"id": c.id, "title": c.title, "turns": c.turn_count, "updated_at": c.updated_at}
        for c in store.list()
    ])


@app.post("/api/conversations")
def new_conversation():
    title = (request.json or {}).get("title", "Untitled") or "Untitled"
    cid = convo.new_conversation(title)
    return jsonify(id=cid)


@app.post("/api/conversations/<int:cid>/open")
def open_conversation(cid: int):
    if not store.exists(cid):
        return jsonify(error="not found"), 404
    convo.load_conversation(cid)
    return jsonify(id=cid, turns=[
        {"question": t.question, "answer": t.answer} for t in convo.turns
    ])


if __name__ == "__main__":
    print("LBS UI -> http://localhost:5000  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=5000, threaded=False, debug=False)
