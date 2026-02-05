"""Flask application for the EMR Cost Optimization Agent."""

import json
import queue
import threading
import uuid

from flask import Flask, Response, jsonify, render_template, request

from agent.graph import build_graph
from config import get_logger
from services import emr_service
from services.background_monitor import monitor

app = Flask(__name__)
logger = get_logger(__name__)

# Graph-level thread tracking: thread_id -> {status_queue, completed, awaiting_approval}
threads = {}

# Session to LangGraph thread mapping: session_id -> thread_id
# Using the same thread_id per session preserves LangGraph state (cluster_name, etc.)
session_threads = {}

# Conversation sessions: session_id -> list of {"role": "user"|"assistant", "content": "..."}
# This is the clean conversation history (no tool calls/responses) that persists
# across multiple graph runs so the LLM has full context.
conversation_sessions = {}

# Workflow step tracking: session_id -> {"cluster_name": str, "steps": {...}, "active": bool}
# Steps: backup, modify, create, monitor, revert, clone, run, compare
# Status: pending, in_progress, completed, error, phase2
workflow_sessions = {}

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
    llm_messages = []
    for msg in history:
        if msg["role"] == "user":
            llm_messages.append(("user", msg["content"]))
        elif msg["role"] == "assistant":
            llm_messages.append(("assistant", msg["content"]))

    # Use same thread_id per session to preserve LangGraph state across messages
    # This allows the agent to remember cluster_name, optimization status, etc.
    if session_id not in session_threads:
        session_threads[session_id] = str(uuid.uuid4())
    thread_id = session_threads[session_id]

    # Create or reuse thread state for SSE
    if thread_id not in threads:
        threads[thread_id] = {
            "status_queue": queue.Queue(),
            "completed": False,
            "awaiting_approval": False,
            "session_id": session_id,
        }
    else:
        # Reset for new message on same thread
        threads[thread_id]["status_queue"] = queue.Queue()
        threads[thread_id]["completed"] = False

    thread_state = threads[thread_id]

    # Run graph in background thread
    config = {"configurable": {"thread_id": thread_id}}

    def run_graph():
        try:
            g = get_graph()
            _push_status(thread_id, "processing", "Agent is thinking...")

            # Read previous state from checkpoint to preserve context
            snapshot = g.get_state(config)
            previous_state = snapshot.values if snapshot and snapshot.values else {}

            # Build inputs: messages + preserved state fields
            inputs = {
                "messages": llm_messages,
                # Preserve optimization context from previous runs
                "cluster_name": previous_state.get("cluster_name"),
                "cluster_id": previous_state.get("cluster_id"),
                "original_config_backup": previous_state.get("original_config_backup"),
                "new_cluster_id": previous_state.get("new_cluster_id"),
                "optimization_status": previous_state.get("optimization_status"),
                "optimization_request_id": previous_state.get("optimization_request_id"),
                "core_analysis": previous_state.get("core_analysis"),
                "task_analysis": previous_state.get("task_analysis"),
                "core_recommendation": previous_state.get("core_recommendation"),
                "task_recommendation": previous_state.get("task_recommendation"),
            }

            for event in g.stream(inputs, config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    phase = node_output.get("current_phase", "")
                    if phase:
                        _push_status(thread_id, "phase", phase)
                        # Update workflow step tracking
                        _update_workflow_from_phase(session_id, phase, node_output)

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
                        # Update workflow step tracking
                        _update_workflow_from_phase(session_id, phase, node_output)

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


@app.route("/api/clusters")
def list_clusters():
    """List transient clusters with pagination.

    Query params:
        page: Page number (default 1)
        per_page: Items per page (default 20, max 100)

    Returns:
        JSON with clusters array, pagination info
    """
    try:
        page = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 20)), 100)
    except ValueError:
        return jsonify({"error": "Invalid pagination params"}), 400

    clusters = emr_service.get_transient_clusters()

    # Sort by end time descending (most recent first)
    clusters.sort(key=lambda c: c.get("ended", ""), reverse=True)

    total = len(clusters)
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    start = (page - 1) * per_page
    end = start + per_page

    return jsonify({
        "clusters": clusters[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    })


@app.route("/api/optimization/status")
def optimization_status():
    """Get current optimization workflow status.

    Query params:
        session_id: The conversation session ID

    Returns:
        JSON with workflow step statuses for the subway graph
    """
    session_id = request.args.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    # Check background monitor status first
    monitor_status = monitor.get_status()

    # Get workflow session if exists
    workflow = workflow_sessions.get(session_id, {})

    # Default step statuses
    steps = workflow.get("steps", {
        "backup": "pending",
        "modify": "pending",
        "create": "pending",
        "monitor": "pending",
        "revert": "pending",
        "clone": "phase2",
        "run": "phase2",
        "compare": "phase2",
    })

    # Update monitor step based on background monitor
    if monitor_status.get("active"):
        steps["monitor"] = "in_progress"
        # Update previous steps as completed if monitoring
        if steps["backup"] == "pending":
            steps["backup"] = "completed"
        if steps["modify"] == "pending":
            steps["modify"] = "completed"
        if steps["create"] == "pending":
            steps["create"] = "completed"
    elif monitor_status.get("status") == "reverted":
        steps["monitor"] = "completed"
        steps["revert"] = "completed"
    elif monitor_status.get("status") in ("timeout", "failed"):
        steps["monitor"] = "error"
        steps["revert"] = "completed" if monitor_status.get("reverted") else "error"

    return jsonify({
        "active": workflow.get("active", False) or monitor_status.get("active", False),
        "cluster_name": workflow.get("cluster_name") or monitor_status.get("cluster_name"),
        "current_step": _get_current_step(steps),
        "steps": steps,
        "monitor_details": {
            "status": monitor_status.get("status"),
            "cluster_state": monitor_status.get("cluster_state"),
            "elapsed_seconds": monitor_status.get("elapsed_seconds"),
            "message": monitor_status.get("message"),
        } if monitor_status.get("active") or monitor_status.get("status") else None,
    })


def _get_current_step(steps: dict) -> str:
    """Determine the current active step from step statuses."""
    order = ["backup", "modify", "create", "monitor", "revert", "clone", "run", "compare"]
    for step in order:
        status = steps.get(step)
        if status == "in_progress":
            return step
        if status == "pending":
            # Find the first pending step that's not blocked
            return step
    return "complete"


def _update_workflow_from_phase(session_id: str, phase: str, node_output: dict):
    """Map graph phase changes to workflow step updates."""
    cluster_name = node_output.get("cluster_name")

    phase_to_step = {
        "initialized": None,
        "backed_up": ("backup", "completed"),
        "modified": ("modify", "completed"),
        "cluster_creation_submitted": ("create", "completed"),
        "monitoring": ("monitor", "in_progress"),
        "cluster_ready": ("monitor", "completed"),
        "reverted": ("revert", "completed"),
        "revert_skipped": ("revert", "completed"),
        "revert_failed": ("revert", "error"),
    }

    mapping = phase_to_step.get(phase)
    if mapping:
        step, status = mapping
        update_workflow_step(session_id, step, status, cluster_name)

        # Also mark previous steps as completed if needed
        order = ["backup", "modify", "create", "monitor", "revert"]
        step_idx = order.index(step) if step in order else -1
        for i in range(step_idx):
            prev_step = order[i]
            if session_id in workflow_sessions:
                current_status = workflow_sessions[session_id]["steps"].get(prev_step)
                if current_status == "pending":
                    workflow_sessions[session_id]["steps"][prev_step] = "completed"


def update_workflow_step(session_id: str, step: str, status: str, cluster_name: str = None):
    """Update a workflow step status."""
    if session_id not in workflow_sessions:
        workflow_sessions[session_id] = {
            "cluster_name": cluster_name,
            "active": True,
            "steps": {
                "backup": "pending",
                "modify": "pending",
                "create": "pending",
                "monitor": "pending",
                "revert": "pending",
                "clone": "phase2",
                "run": "phase2",
                "compare": "phase2",
            },
        }

    workflow = workflow_sessions[session_id]
    if cluster_name:
        workflow["cluster_name"] = cluster_name
    workflow["steps"][step] = status

    # Push step update through SSE if there's an active thread
    for tid, tstate in threads.items():
        if tstate.get("session_id") == session_id:
            _push_status(tid, "workflow_step", json.dumps({"step": step, "status": status}))
            break


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
