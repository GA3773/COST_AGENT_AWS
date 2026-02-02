/**
 * Agent chat interface for EMR Cost Optimization.
 */
(function () {
    "use strict";

    let threadId = null;
    let eventSource = null;

    const messagesEl = document.getElementById("messages");
    const inputEl = document.getElementById("userInput");
    const btnSend = document.getElementById("btnSend");
    const approvalEl = document.getElementById("approval");
    const btnApprove = document.getElementById("btnApprove");
    const btnCancel = document.getElementById("btnCancel");
    const progressEl = document.getElementById("progress");
    const progressText = document.getElementById("progressText");

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
        if (!text) return;

        appendMessage("user", text);
        inputEl.value = "";
        setInputEnabled(false);
        showProgress("Sending...");

        fetch("/api/agent/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, thread_id: threadId }),
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.error) {
                    appendMessage("agent", "Error: " + data.error);
                    setInputEnabled(true);
                    hideProgress();
                    return;
                }
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
                    // SSE will continue to stream updates
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
        if (eventSource) {
            eventSource.close();
        }

        eventSource = new EventSource("/api/agent/status/" + tid);

        eventSource.onmessage = function (event) {
            var data = JSON.parse(event.data);

            switch (data.type) {
                case "message":
                    appendMessage("agent", data.content);
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

    function appendMessage(role, content) {
        var div = document.createElement("div");
        div.className = "message message--" + role;

        var inner = document.createElement("div");
        inner.className = "message__content";
        inner.textContent = content;

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
        inputEl.disabled = !enabled;
        btnSend.disabled = !enabled;
        if (enabled) {
            inputEl.focus();
        }
    }
})();
