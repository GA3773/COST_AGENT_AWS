/**
 * Agent chat interface for EMR Cost Optimization.
 */
(function () {
    "use strict";

    // Persistent session ID (survives across multiple graph runs)
    var sessionId = null;
    // Current graph-run thread ID (changes with each message, persists for approval flow)
    var threadId = null;
    var eventSource = null;
    var inputLocked = false;

    var messagesEl = document.getElementById("messages");
    var inputEl = document.getElementById("userInput");
    var btnSend = document.getElementById("btnSend");
    var approvalEl = document.getElementById("approval");
    var btnApprove = document.getElementById("btnApprove");
    var btnCancel = document.getElementById("btnCancel");
    var progressEl = document.getElementById("progress");
    var progressText = document.getElementById("progressText");

    // Configure marked for safe rendering
    if (typeof marked !== "undefined") {
        marked.setOptions({
            breaks: true,
            gfm: true,
        });
    }

    // Send message
    btnSend.addEventListener("click", sendMessage);
    inputEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Approval buttons
    btnApprove.addEventListener("click", function () {
        approveOptimization(true);
    });
    btnCancel.addEventListener("click", function () {
        approveOptimization(false);
    });

    function sendMessage() {
        var text = inputEl.value.trim();
        if (!text || inputLocked) return;

        appendMessage("user", text);
        inputEl.value = "";
        setInputEnabled(false);
        showProgress("Sending...");

        // Close any existing SSE before starting a new request
        closeSSE();

        fetch("/api/agent/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: text,
                session_id: sessionId,
            }),
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.error) {
                    appendMessage("agent", "Error: " + data.error);
                    setInputEnabled(true);
                    hideProgress();
                    return;
                }
                // Persist the session ID from the server
                sessionId = data.session_id;
                // New thread_id for this graph run -- connect SSE
                threadId = data.thread_id;
                connectSSE(threadId);
            })
            .catch(function (err) {
                appendMessage("agent", "Connection error: " + err.message);
                setInputEnabled(true);
                hideProgress();
            });
    }

    function approveOptimization(approved) {
        approvalEl.style.display = "none";
        showProgress(approved ? "Executing optimization..." : "Cancelling...");

        fetch("/api/agent/approve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thread_id: threadId, approved: approved }),
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.status === "cancelled") {
                    appendMessage("agent", "Optimization cancelled.");
                    hideProgress();
                    setInputEnabled(true);
                } else {
                    // Reconnect SSE on the same thread for the resumed graph
                    connectSSE(threadId);
                }
            })
            .catch(function (err) {
                appendMessage("agent", "Error: " + err.message);
                hideProgress();
                setInputEnabled(true);
            });
    }

    function connectSSE(tid) {
        closeSSE();

        eventSource = new EventSource("/api/agent/status/" + tid);

        eventSource.onmessage = function (event) {
            var data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                return;
            }

            switch (data.type) {
                case "message":
                    appendMessage("agent", data.content);
                    showProgress("Agent is working...");
                    break;
                case "processing":
                    showProgress(data.content);
                    break;
                case "phase":
                    updateProgress(data.content);
                    break;
                case "approval_required":
                    hideProgress();
                    showApprovalButtons();
                    break;
                case "complete":
                    hideProgress();
                    setInputEnabled(true);
                    closeSSE();
                    break;
                case "error":
                    appendMessage("agent", "Error: " + data.content);
                    hideProgress();
                    setInputEnabled(true);
                    closeSSE();
                    break;
                case "cancelled":
                    hideProgress();
                    setInputEnabled(true);
                    closeSSE();
                    break;
                case "keepalive":
                    break;
                default:
                    break;
            }
        };

        eventSource.onerror = function () {
            closeSSE();
            hideProgress();
            setInputEnabled(true);
        };
    }

    function closeSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    function renderMarkdown(text) {
        if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
            return DOMPurify.sanitize(marked.parse(text));
        }
        // Fallback: escape HTML and preserve whitespace
        var escaped = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        return escaped;
    }

    function appendMessage(role, content) {
        if (!content || !content.trim()) return;

        var div = document.createElement("div");
        div.className = "message message--" + role;

        var inner = document.createElement("div");
        inner.className = "message__content";

        if (role === "agent") {
            inner.innerHTML = renderMarkdown(content);
        } else {
            inner.textContent = content;
        }

        div.appendChild(inner);
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function showApprovalButtons() {
        approvalEl.style.display = "flex";
    }

    function showProgress(text) {
        progressEl.style.display = "flex";
        progressText.textContent = text || "";
    }

    function updateProgress(phase) {
        var labels = {
            initialized: "Initializing...",
            analysis_complete: "Analysis complete",
            backed_up: "Config backed up",
            modified: "Parameter Store modified",
            cluster_created: "Cluster creation triggered",
            cluster_ready: "Cluster is ready",
            reverted: "Config reverted",
            revert_skipped: "Revert skipped",
            revert_failed: "Revert failed",
            complete: "Done",
        };
        progressText.textContent = labels[phase] || phase;
    }

    function hideProgress() {
        progressEl.style.display = "none";
    }

    function setInputEnabled(enabled) {
        inputLocked = !enabled;
        inputEl.disabled = !enabled;
        btnSend.disabled = !enabled;
        if (enabled) {
            inputEl.focus();
        }
    }
})();
