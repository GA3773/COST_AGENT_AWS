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

# Graph-level thread tracking: thread_id -> {status_queue, completed, awaiting_approval}
threads = {}

# Conversation sessions: session_id -> list of {"role": "user"|"assistant", "content": "..."}
# This is the clean conversation history (no tool calls/responses) that persists
# across multiple graph runs so the LLM has full context.
conversation_sessions = {}

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

    Request body:
        message: str - the user's message
        session_id: str - persistent conversation session ID
    Returns:
        thread_id: str - this graph run's thread ID (for SSE + approval)
        session_id: str - the conversation session ID
    """
    data = request.get_json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Get or create conversation session
    if session_id not in conversation_sessions:
        conversation_sessions[session_id] = []

    history = conversation_sessions[session_id]

    # Add the new user message to conversation history
    history.append({"role": "user", "content": user_message})

    # Build the full message list for the LLM: prior conversation + new message.
    # These are clean user/assistant pairs only -- no tool calls, no tool responses.
    # This gives the LLM full conversational context while keeping each graph
    # execution isolated (fresh thread_id) to avoid tool_calls message corruption.
    llm_messages = []
    for msg in history:
        if msg["role"] == "user":
            llm_messages.append(("user", msg["content"]))
        elif msg["role"] == "assistant":
            llm_messages.append(("assistant", msg["content"]))

    # Fresh thread for each graph run (avoids dangling tool_calls in checkpoint)
    thread_id = str(uuid.uuid4())

    threads[thread_id] = {
        "status_queue": queue.Queue(),
        "completed": False,
        "awaiting_approval": False,
        "session_id": session_id,
    }

    thread_state = threads[thread_id]

    # Run graph in background thread
    config = {"configurable": {"thread_id": thread_id}}

    def run_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Agent is thinking...")

            inputs = {"messages": llm_messages}

            for event in g.stream(inputs, config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    phase = node_output.get("current_phase", "")
                    if phase:
                        _push_status(thread_id, "phase", phase)

                    new_messages = node_output.get("messages", [])
                    for msg in new_messages:
                        if hasattr(msg, "content") and msg.content:
                            if hasattr(msg, "type") and msg.type == "ai":
                                # Skip AI messages that only contain tool_calls
                                if hasattr(msg, "tool_calls") and msg.tool_calls:
                                    if not msg.content.strip():
                                        continue
                                # Send to client and save to conversation history
                                _push_status(thread_id, "message", msg.content)
                                history.append({
                                    "role": "assistant",
                                    "content": msg.content,
                                })

            # Check if graph is interrupted (awaiting approval)
            snapshot = g.get_state(config)
            if snapshot.next:
                thread_state["awaiting_approval"] = True
                _push_status(thread_id, "approval_required",
                             "Review the analysis above. Proceed with optimization?")
            else:
                thread_state["completed"] = True
                _push_status(thread_id, "complete", "Done.")

        except Exception as e:
            logger.error(f"Graph execution error: {e}", exc_info=True)
            _push_status(thread_id, "error", str(e))
            thread_state["completed"] = True

    t = threading.Thread(target=run_graph, daemon=True)
    t.start()

    return jsonify({
        "thread_id": thread_id,
        "session_id": session_id,
        "status": "processing",
    })


@app.route("/api/agent/approve", methods=["POST"])
def approve():
    """Resume the agent after user approval.

    Request body: {"thread_id": "...", "approved": true/false}
    """
    data = request.get_json()
    thread_id = data.get("thread_id")
    approved = data.get("approved", False)

    if not thread_id or thread_id not in threads:
        return jsonify({"error": "Invalid thread_id"}), 400

    thread_state = threads[thread_id]
    if not thread_state.get("awaiting_approval"):
        return jsonify({"error": "No approval pending"}), 400

    thread_state["awaiting_approval"] = False
    session_id = thread_state.get("session_id")
    history = conversation_sessions.get(session_id, [])
    config = {"configurable": {"thread_id": thread_id}}

    if not approved:
        _push_status(thread_id, "cancelled", "Optimization cancelled by user.")
        thread_state["completed"] = True
        history.append({"role": "assistant", "content": "Optimization cancelled."})
        return jsonify({"status": "cancelled"})

    # Resume the interrupted graph on the SAME thread
    def resume_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Executing optimization...")

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
                                history.append({
                                    "role": "assistant",
                                    "content": msg.content,
                                })

            thread_state["completed"] = True
            _push_status(thread_id, "complete", "Optimization complete.")

        except Exception as e:
            logger.error(f"Resume execution error: {e}", exc_info=True)
            _push_status(thread_id, "error", str(e))
            thread_state["completed"] = True

    t = threading.Thread(target=resume_graph, daemon=True)
    t.start()

    return jsonify({"status": "approved", "thread_id": thread_id})


@app.route("/api/agent/status/<thread_id>")
def status_stream(thread_id):
    """Server-Sent Events endpoint for live progress updates."""
    if thread_id not in threads:
        return jsonify({"error": "Unknown thread_id"}), 404

    def generate():
        thread_state = threads[thread_id]
        q = thread_state["status_queue"]

        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"

                if event.get("type") in ("complete", "error", "cancelled"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

                if thread_state.get("completed"):
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
    """Push a status event to the thread's queue."""
    if thread_id in threads:
        threads[thread_id]["status_queue"].put({
            "type": event_type,
            "content": content,
        })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
