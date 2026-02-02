"""Flask application for the EMR Cost Optimization Agent."""

import json
import queue
import threading
import uuid

from flask import Flask, Response, jsonify, render_template, request

from agent.graph import build_graph
from config import get_logger

app = Flask(__name__)
logger = get_logger(__name__)

# In-memory session storage: thread_id -> {graph, config, status_queue, messages}
sessions = {}
graph = None


def get_graph():
    """Lazy-initialize the compiled graph."""
    global graph
    if graph is None:
        graph = build_graph()
    return graph


@app.route("/")
def index():
    """Render the agent chat interface."""
    return render_template("agent.html")


@app.route("/api/agent/chat", methods=["POST"])
def chat():
    """Send a message to the agent and start processing.

    Request body: {"message": "user message", "thread_id": "optional"}
    Returns: {"thread_id": "...", "status": "processing"}
    """
    data = request.get_json()
    user_message = data.get("message", "").strip()
    thread_id = data.get("thread_id") or str(uuid.uuid4())

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Get or create session
    if thread_id not in sessions:
        sessions[thread_id] = {
            "status_queue": queue.Queue(),
            "messages": [],
            "completed": False,
            "awaiting_approval": False,
        }

    session = sessions[thread_id]
    session["messages"].append({"role": "user", "content": user_message})

    # Run graph in background thread
    config = {"configurable": {"thread_id": thread_id}}

    def run_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Agent is thinking...")

            inputs = {"messages": [("user", user_message)]}
            for event in g.stream(inputs, config, stream_mode="values"):
                messages = event.get("messages", [])
                phase = event.get("current_phase", "")

                if phase:
                    _push_status(thread_id, "phase", phase)

                # Send new AI messages to the client
                for msg in messages:
                    if hasattr(msg, "content") and msg.content:
                        if hasattr(msg, "type") and msg.type == "ai":
                            session["messages"].append({
                                "role": "agent",
                                "content": msg.content,
                            })
                            _push_status(thread_id, "message", msg.content)

            # Check if graph is interrupted (awaiting approval)
            snapshot = g.get_state(config)
            if snapshot.next:
                session["awaiting_approval"] = True
                _push_status(thread_id, "approval_required",
                             "Review the analysis above. Proceed with optimization?")
            else:
                session["completed"] = True
                _push_status(thread_id, "complete", "Done.")

        except Exception as e:
            logger.error(f"Graph execution error: {e}", exc_info=True)
            _push_status(thread_id, "error", str(e))

    thread = threading.Thread(target=run_graph, daemon=True)
    thread.start()

    return jsonify({"thread_id": thread_id, "status": "processing"})


@app.route("/api/agent/approve", methods=["POST"])
def approve():
    """Resume the agent after user approval.

    Request body: {"thread_id": "...", "approved": true/false}
    """
    data = request.get_json()
    thread_id = data.get("thread_id")
    approved = data.get("approved", False)

    if not thread_id or thread_id not in sessions:
        return jsonify({"error": "Invalid thread_id"}), 400

    session = sessions[thread_id]
    if not session.get("awaiting_approval"):
        return jsonify({"error": "No approval pending"}), 400

    session["awaiting_approval"] = False
    config = {"configurable": {"thread_id": thread_id}}

    if not approved:
        _push_status(thread_id, "cancelled", "Optimization cancelled by user.")
        session["completed"] = True
        return jsonify({"status": "cancelled"})

    # Resume the graph
    def resume_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Executing optimization...")

            # Update state with approval
            g.update_state(config, {"human_approved": True})

            for event in g.stream(None, config, stream_mode="values"):
                messages = event.get("messages", [])
                phase = event.get("current_phase", "")

                if phase:
                    _push_status(thread_id, "phase", phase)

                for msg in messages:
                    if hasattr(msg, "content") and msg.content:
                        if hasattr(msg, "type") and msg.type == "ai":
                            session["messages"].append({
                                "role": "agent",
                                "content": msg.content,
                            })
                            _push_status(thread_id, "message", msg.content)

            session["completed"] = True
            _push_status(thread_id, "complete", "Optimization complete.")

        except Exception as e:
            logger.error(f"Resume execution error: {e}", exc_info=True)
            _push_status(thread_id, "error", str(e))

    thread = threading.Thread(target=resume_graph, daemon=True)
    thread.start()

    return jsonify({"status": "approved", "thread_id": thread_id})


@app.route("/api/agent/status/<thread_id>")
def status_stream(thread_id):
    """Server-Sent Events endpoint for live progress updates."""
    if thread_id not in sessions:
        return jsonify({"error": "Unknown thread_id"}), 404

    def generate():
        session = sessions[thread_id]
        q = session["status_queue"]

        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"

                if event.get("type") in ("complete", "error", "cancelled"):
                    break
            except queue.Empty:
                # Send keepalive
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

                if session.get("completed"):
                    break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _push_status(thread_id: str, event_type: str, content: str):
    """Push a status event to the session's queue."""
    if thread_id in sessions:
        sessions[thread_id]["status_queue"].put({
            "type": event_type,
            "content": content,
        })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
