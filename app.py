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

# In-memory session storage: thread_id -> session dict
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
    old_thread_id = data.get("thread_id")

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Always create a fresh thread for each conversation turn.
    # This avoids LLM message history corruption (dangling tool_calls)
    # from previous graph runs on the same thread.
    # The only exception is when resuming an interrupted graph (approval flow),
    # which goes through /api/agent/approve instead.
    thread_id = str(uuid.uuid4())

    sessions[thread_id] = {
        "status_queue": queue.Queue(),
        "msg_count": 0,  # Track how many AI messages we've sent via SSE
        "completed": False,
        "awaiting_approval": False,
    }

    session = sessions[thread_id]

    # Run graph in background thread
    config = {"configurable": {"thread_id": thread_id}}

    def run_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Agent is thinking...")

            inputs = {"messages": [("user", user_message)]}

            # Use stream_mode="updates" to get only the NEW state changes
            # from each node, not the full accumulated state.
            for event in g.stream(inputs, config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    # Send phase updates
                    phase = node_output.get("current_phase", "")
                    if phase:
                        _push_status(thread_id, "phase", phase)

                    # Send only the NEW messages produced by this node
                    new_messages = node_output.get("messages", [])
                    for msg in new_messages:
                        if hasattr(msg, "content") and msg.content:
                            if hasattr(msg, "type") and msg.type == "ai":
                                # Skip AI messages that only contain tool_calls
                                # (no human-readable content)
                                if hasattr(msg, "tool_calls") and msg.tool_calls:
                                    if not msg.content.strip():
                                        continue
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
            session["completed"] = True

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

    # Resume the interrupted graph on the SAME thread
    def resume_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Executing optimization...")

            # Update state with approval
            g.update_state(config, {"human_approved": True})

            for event in g.stream(None, config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    phase = node_output.get("current_phase", "")
                    if phase:
                        _push_status(thread_id, "phase", phase)

                    new_messages = node_output.get("messages", [])
                    for msg in new_messages:
                        if hasattr(msg, "content") and msg.content:
                            if hasattr(msg, "type") and msg.type == "ai":
                                _push_status(thread_id, "message", msg.content)

            session["completed"] = True
            _push_status(thread_id, "complete", "Optimization complete.")

        except Exception as e:
            logger.error(f"Resume execution error: {e}", exc_info=True)
            _push_status(thread_id, "error", str(e))
            session["completed"] = True

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
                    # Drain any remaining events
                    while not q.empty():
                        try:
                            event = q.get_nowait()
                            yield f"data: {json.dumps(event)}\n\n"
                        except queue.Empty:
                            break
                    break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
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
