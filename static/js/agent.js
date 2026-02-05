/**
 * Agent interface for EMR Cost Optimization.
 * Handles cluster listing, subway graph, and chat.
 */
(function () {
    "use strict";

    // State
    var sessionId = null;
    var threadId = null;
    var eventSource = null;
    var inputLocked = false;
    var currentPage = 1;
    var totalPages = 1;
    var selectedCluster = null;
    var statusPollInterval = null;
    var workflowActive = false;

    // DOM Elements - Clusters
    var btnRefresh = document.getElementById("btnRefresh");
    var clustersBody = document.getElementById("clustersBody");
    var paginationInfo = document.getElementById("paginationInfo");
    var btnPrev = document.getElementById("btnPrev");
    var btnNext = document.getElementById("btnNext");

    // DOM Elements - Agent Panel
    var agentPanel = document.getElementById("agentPanel");
    var selectedClusterName = document.getElementById("selectedClusterName");
    var btnClosePanel = document.getElementById("btnClosePanel");
    var subwayGraph = document.getElementById("subwayGraph");

    // DOM Elements - Chat
    var messagesEl = document.getElementById("messages");
    var inputEl = document.getElementById("userInput");
    var btnSend = document.getElementById("btnSend");
    var approvalEl = document.getElementById("approval");
    var btnApprove = document.getElementById("btnApprove");
    var btnCancel = document.getElementById("btnCancel");
    var progressEl = document.getElementById("progress");
    var progressText = document.getElementById("progressText");

    // Configure marked
    if (typeof marked !== "undefined") {
        marked.setOptions({ breaks: true, gfm: true });
    }

    // Initialize
    init();

    function init() {
        loadClusters();
        bindEvents();
    }

    function bindEvents() {
        btnRefresh.addEventListener("click", function () {
            currentPage = 1;
            loadClusters();
        });
        btnPrev.addEventListener("click", function () {
            if (currentPage > 1) {
                currentPage--;
                loadClusters();
            }
        });
        btnNext.addEventListener("click", function () {
            if (currentPage < totalPages) {
                currentPage++;
                loadClusters();
            }
        });
        btnClosePanel.addEventListener("click", closeAgentPanel);
        btnSend.addEventListener("click", sendMessage);
        inputEl.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        btnApprove.addEventListener("click", function () {
            approveOptimization(true);
        });
        btnCancel.addEventListener("click", function () {
            approveOptimization(false);
        });
    }

    // ========== Cluster List ==========

    function loadClusters() {
        clustersBody.innerHTML = '<tr><td colspan="6" class="clusters__loading">Loading clusters...</td></tr>';
        paginationInfo.textContent = "Loading...";

        fetch("/api/clusters?page=" + currentPage + "&per_page=20")
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.error) {
                    clustersBody.innerHTML = '<tr><td colspan="6" class="clusters__empty">Error: ' + data.error + '</td></tr>';
                    return;
                }
                renderClusters(data.clusters);
                totalPages = data.pages;
                paginationInfo.textContent = "Page " + data.page + " of " + data.pages + " (" + data.total + " clusters)";
                btnPrev.disabled = data.page <= 1;
                btnNext.disabled = data.page >= data.pages;
            })
            .catch(function (err) {
                clustersBody.innerHTML = '<tr><td colspan="6" class="clusters__empty">Failed to load clusters</td></tr>';
                console.error("Load clusters error:", err);
            });
    }

    function renderClusters(clusters) {
        if (!clusters || clusters.length === 0) {
            clustersBody.innerHTML = '<tr><td colspan="6" class="clusters__empty">No transient clusters found</td></tr>';
            return;
        }

        var html = clusters.map(function (c) {
            var statusClass = c.state === "TERMINATED" ? "terminated" : "completed";
            var endTime = formatDateTime(c.ended);
            return '<tr>' +
                '<td>' + escapeHtml(c.name) + '</td>' +
                '<td>' + escapeHtml(c.cluster_id) + '</td>' +
                '<td>' + c.runtime_hours.toFixed(1) + ' hrs</td>' +
                '<td><span class="cluster-status cluster-status--' + statusClass + '">' + c.state + '</span></td>' +
                '<td>' + endTime + '</td>' +
                '<td><button class="btn btn--optimize" onclick="window.selectCluster(\'' + escapeHtml(c.name) + '\')">Optimize</button></td>' +
                '</tr>';
        }).join("");

        clustersBody.innerHTML = html;
    }

    // Expose to window for onclick handler
    window.selectCluster = function (clusterName) {
        selectedCluster = clusterName;
        openAgentPanel(clusterName);
    };

    // ========== Agent Panel ==========

    function openAgentPanel(clusterName) {
        selectedClusterName.textContent = clusterName;
        agentPanel.style.display = "flex";
        resetSubwayGraph();
        clearMessages();
        appendMessage("agent", "Ready to analyze **" + clusterName + "**. Type 'analyze' to see utilization metrics and recommendations, or ask any question about this cluster.");
        inputEl.focus();

        // Start a new session for this cluster
        sessionId = null;

        // Start polling for status if workflow might be active
        startStatusPolling();
    }

    function closeAgentPanel() {
        agentPanel.style.display = "none";
        selectedCluster = null;
        stopStatusPolling();
        closeSSE();
    }

    function clearMessages() {
        messagesEl.innerHTML = "";
    }

    // ========== Subway Graph ==========

    function resetSubwayGraph() {
        var nodes = subwayGraph.querySelectorAll(".subway-node");
        nodes.forEach(function (node) {
            node.classList.remove("subway-node--completed", "subway-node--in_progress", "subway-node--error");
        });
        var connectors = subwayGraph.querySelectorAll(".subway-connector");
        connectors.forEach(function (conn) {
            conn.classList.remove("subway-connector--completed");
        });
    }

    function updateSubwayGraph(steps) {
        if (!steps) return;

        var stepOrder = ["backup", "modify", "create", "monitor", "revert", "clone", "run", "compare"];
        var lastCompletedIndex = -1;

        stepOrder.forEach(function (step, index) {
            var status = steps[step];
            var node = subwayGraph.querySelector('[data-step="' + step + '"]');
            if (!node) return;

            // Remove previous state classes
            node.classList.remove("subway-node--completed", "subway-node--in_progress", "subway-node--error");

            if (status === "completed") {
                node.classList.add("subway-node--completed");
                lastCompletedIndex = index;
            } else if (status === "in_progress") {
                node.classList.add("subway-node--in_progress");
            } else if (status === "error") {
                node.classList.add("subway-node--error");
            }
            // phase2 nodes keep their default dashed style
        });

        // Update connectors - mark completed up to last completed node
        var connectors = subwayGraph.querySelectorAll(".subway-connector:not(.subway-connector--phase2)");
        connectors.forEach(function (conn, index) {
            conn.classList.remove("subway-connector--completed");
            if (index < lastCompletedIndex) {
                conn.classList.add("subway-connector--completed");
            }
        });
    }

    // ========== Status Polling ==========

    function startStatusPolling() {
        stopStatusPolling();
        pollStatus(); // Immediate first poll
        statusPollInterval = setInterval(pollStatus, 30000); // 30 second interval
    }

    function stopStatusPolling() {
        if (statusPollInterval) {
            clearInterval(statusPollInterval);
            statusPollInterval = null;
        }
    }

    function pollStatus() {
        if (!sessionId) return;

        fetch("/api/optimization/status?session_id=" + sessionId)
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.steps) {
                    updateSubwayGraph(data.steps);
                }
                workflowActive = data.active;

                // If monitoring is active, show status in progress bar
                if (data.monitor_details && data.active) {
                    showProgress(data.monitor_details.message || "Monitoring cluster...");
                }
            })
            .catch(function (err) {
                console.error("Status poll error:", err);
            });
    }

    // ========== Chat ==========

    function sendMessage() {
        var text = inputEl.value.trim();
        if (!text || inputLocked) return;

        // If cluster is selected and user types something simple, prepend context
        if (selectedCluster && text.toLowerCase() === "analyze") {
            text = "Analyze cluster " + selectedCluster;
        } else if (selectedCluster && text.toLowerCase() === "optimize") {
            text = "Optimize cluster " + selectedCluster;
        }

        appendMessage("user", text);
        inputEl.value = "";
        setInputEnabled(false);
        showProgress("Sending...");

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
                sessionId = data.session_id;
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

        // Mark workflow as active and update subway graph
        if (approved) {
            workflowActive = true;
            updateSubwayGraph({ backup: "in_progress", modify: "pending", create: "pending", monitor: "pending", revert: "pending", clone: "phase2", run: "phase2", compare: "phase2" });
        }

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
                    resetSubwayGraph();
                } else {
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
                    updateProgressFromPhase(data.content);
                    break;
                case "workflow_step":
                    try {
                        var stepData = JSON.parse(data.content);
                        updateSingleStep(stepData.step, stepData.status);
                    } catch (e) {}
                    break;
                case "approval_required":
                    hideProgress();
                    showApprovalButtons();
                    break;
                case "complete":
                    hideProgress();
                    setInputEnabled(true);
                    closeSSE();
                    pollStatus(); // Final status update
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

    function updateSingleStep(step, status) {
        var node = subwayGraph.querySelector('[data-step="' + step + '"]');
        if (!node) return;

        node.classList.remove("subway-node--completed", "subway-node--in_progress", "subway-node--error");
        if (status === "completed") {
            node.classList.add("subway-node--completed");
        } else if (status === "in_progress") {
            node.classList.add("subway-node--in_progress");
        } else if (status === "error") {
            node.classList.add("subway-node--error");
        }
    }

    function updateProgressFromPhase(phase) {
        var labels = {
            initialized: "Initializing...",
            analysis_complete: "Analysis complete",
            backed_up: "Config backed up",
            modified: "Parameter Store modified",
            cluster_creation_submitted: "Cluster creation triggered",
            monitoring: "Monitoring cluster startup...",
            cluster_ready: "Cluster is ready",
            reverted: "Config reverted",
            revert_skipped: "Revert skipped",
            revert_failed: "Revert failed",
            complete: "Done",
        };
        progressText.textContent = labels[phase] || phase;

        // Update subway graph based on phase
        var phaseToStep = {
            backed_up: { backup: "completed" },
            modified: { backup: "completed", modify: "completed" },
            cluster_creation_submitted: { backup: "completed", modify: "completed", create: "completed", monitor: "in_progress" },
            monitoring: { backup: "completed", modify: "completed", create: "completed", monitor: "in_progress" },
            cluster_ready: { backup: "completed", modify: "completed", create: "completed", monitor: "completed" },
            reverted: { backup: "completed", modify: "completed", create: "completed", monitor: "completed", revert: "completed" },
        };

        var stepUpdate = phaseToStep[phase];
        if (stepUpdate) {
            var currentSteps = { backup: "pending", modify: "pending", create: "pending", monitor: "pending", revert: "pending", clone: "phase2", run: "phase2", compare: "phase2" };
            Object.assign(currentSteps, stepUpdate);
            updateSubwayGraph(currentSteps);
        }
    }

    // ========== UI Helpers ==========

    function renderMarkdown(text) {
        if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
            return DOMPurify.sanitize(marked.parse(text));
        }
        return escapeHtml(text);
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

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    function formatDateTime(isoString) {
        if (!isoString) return "-";
        try {
            var date = new Date(isoString);
            return date.toLocaleDateString() + " " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        } catch (e) {
            return isoString;
        }
    }
})();
