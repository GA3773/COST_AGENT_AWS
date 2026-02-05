/**
 * Agent interface for EMR Cost Optimization.
 * Split-panel layout with sidebar cluster list and main agent view.
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
    var clusters = [];
    var lastRecommendation = null;
    var workflowSteps = {};

    // DOM Elements - Sidebar
    var btnRefresh = document.getElementById("btnRefresh");
    var clusterList = document.getElementById("clusterList");
    var clusterCount = document.getElementById("clusterCount");
    var paginationInfo = document.getElementById("paginationInfo");
    var btnPrev = document.getElementById("btnPrev");
    var btnNext = document.getElementById("btnNext");

    // DOM Elements - Main Panel
    var emptyState = document.getElementById("emptyState");
    var agentView = document.getElementById("agentView");
    var selectedClusterName = document.getElementById("selectedClusterName");
    var btnClosePanel = document.getElementById("btnClosePanel");
    var subwayGraph = document.getElementById("subwayGraph");

    // DOM Elements - Recommendation Popup
    var recommendNode = document.getElementById("recommendNode");
    var recommendPopup = document.getElementById("recommendPopup");
    var recommendContent = document.getElementById("recommendContent");
    var closeRecommendPopup = document.getElementById("closeRecommendPopup");
    var approvalLabel = document.getElementById("approvalLabel");

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

        // Recommendation popup
        recommendNode.addEventListener("click", function () {
            if (lastRecommendation) {
                recommendPopup.style.display = "block";
            }
        });
        closeRecommendPopup.addEventListener("click", function () {
            recommendPopup.style.display = "none";
        });
    }

    // ========== Cluster List ==========

    function loadClusters() {
        clusterList.innerHTML = '<div class="cluster-list__loading">Loading...</div>';

        fetch("/api/clusters?page=" + currentPage + "&per_page=20")
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.error) {
                    clusterList.innerHTML = '<div class="cluster-list__loading">Error loading clusters</div>';
                    return;
                }
                clusters = data.clusters;
                renderClusters(data.clusters);
                totalPages = data.pages;
                clusterCount.textContent = data.total;
                paginationInfo.textContent = data.page + "/" + data.pages;
                btnPrev.disabled = data.page <= 1;
                btnNext.disabled = data.page >= data.pages;
            })
            .catch(function (err) {
                clusterList.innerHTML = '<div class="cluster-list__loading">Failed to load</div>';
                console.error("Load clusters error:", err);
            });
    }

    function renderClusters(clusterData) {
        if (!clusterData || clusterData.length === 0) {
            clusterList.innerHTML = '<div class="cluster-list__loading">No clusters found</div>';
            return;
        }

        var html = clusterData.map(function (c) {
            var statusClass = c.state === "TERMINATED" ? "terminated" : "completed";
            var isSelected = selectedCluster === c.name;
            var selectedClass = isSelected ? " cluster-item--selected" : "";
            return '<div class="cluster-item' + selectedClass + '" data-cluster="' + escapeAttr(c.name) + '">' +
                '<div class="cluster-item__indicator cluster-item__indicator--' + statusClass + '"></div>' +
                '<div class="cluster-item__info">' +
                '<div class="cluster-item__name">' + escapeHtml(c.name) + '</div>' +
                '<div class="cluster-item__meta">' + c.runtime_hours.toFixed(1) + 'h · ' + formatTime(c.ended) + '</div>' +
                '</div>' +
                '</div>';
        }).join("");

        clusterList.innerHTML = html;

        // Bind click handlers
        var items = clusterList.querySelectorAll(".cluster-item");
        items.forEach(function (item) {
            item.addEventListener("click", function () {
                var name = item.getAttribute("data-cluster");
                selectCluster(name);
            });
        });
    }

    function selectCluster(clusterName) {
        selectedCluster = clusterName;

        // Update selection in list
        var items = clusterList.querySelectorAll(".cluster-item");
        items.forEach(function (item) {
            if (item.getAttribute("data-cluster") === clusterName) {
                item.classList.add("cluster-item--selected");
            } else {
                item.classList.remove("cluster-item--selected");
            }
        });

        openAgentPanel(clusterName);
    }

    // ========== Agent Panel ==========

    function openAgentPanel(clusterName) {
        selectedClusterName.textContent = clusterName;
        emptyState.style.display = "none";
        agentView.style.display = "flex";
        resetSubwayGraph();
        clearMessages();
        lastRecommendation = null;
        recommendPopup.style.display = "none";
        approvalLabel.textContent = "Approval";
        appendMessage("agent", "Ready to analyze **" + clusterName + "**.\n\nType `analyze` to see utilization metrics and recommendations, or ask any question about this cluster.");
        inputEl.focus();

        // Start a new session for this cluster
        sessionId = null;
        workflowSteps = {
            analyze: "pending",
            recommend: "pending",
            approval: "pending",
            modify: "pending",
            create: "pending",
            monitor: "pending",
            clone: "phase2",
            compare: "phase2",
            finalize: "phase2"
        };
        startStatusPolling();
    }

    function closeAgentPanel() {
        emptyState.style.display = "flex";
        agentView.style.display = "none";
        selectedCluster = null;
        stopStatusPolling();
        closeSSE();
        recommendPopup.style.display = "none";

        // Clear selection in list
        var items = clusterList.querySelectorAll(".cluster-item");
        items.forEach(function (item) {
            item.classList.remove("cluster-item--selected");
        });
    }

    function clearMessages() {
        messagesEl.innerHTML = "";
    }

    // ========== Subway Graph ==========

    function resetSubwayGraph() {
        var nodes = subwayGraph.querySelectorAll(".subway-node");
        nodes.forEach(function (node) {
            node.classList.remove("subway-node--completed", "subway-node--in_progress", "subway-node--error", "subway-node--approved", "subway-node--denied");
        });
        var connectors = subwayGraph.querySelectorAll(".subway-connector");
        connectors.forEach(function (conn) {
            conn.classList.remove("subway-connector--completed");
        });
        approvalLabel.textContent = "Approval";
    }

    function updateSubwayGraph(steps) {
        if (!steps) return;
        workflowSteps = Object.assign({}, workflowSteps, steps);

        var stepOrder = ["analyze", "recommend", "approval", "modify", "create", "monitor", "clone", "compare", "finalize"];
        var lastCompletedIndex = -1;

        stepOrder.forEach(function (step, index) {
            var status = workflowSteps[step];
            var node = subwayGraph.querySelector('[data-step="' + step + '"]');
            if (!node) return;

            node.classList.remove("subway-node--completed", "subway-node--in_progress", "subway-node--error", "subway-node--approved", "subway-node--denied");

            if (status === "completed" || status === "approved") {
                node.classList.add("subway-node--completed");
                if (status === "approved" && step === "approval") {
                    node.classList.add("subway-node--approved");
                }
                lastCompletedIndex = index;
            } else if (status === "denied") {
                node.classList.add("subway-node--denied");
            } else if (status === "in_progress") {
                node.classList.add("subway-node--in_progress");
            } else if (status === "error") {
                node.classList.add("subway-node--error");
            }
        });

        // Update connectors
        var connectors = subwayGraph.querySelectorAll(".subway-connector:not(.subway-connector--phase2)");
        connectors.forEach(function (conn, index) {
            conn.classList.remove("subway-connector--completed");
            if (index < lastCompletedIndex) {
                conn.classList.add("subway-connector--completed");
            }
        });
    }

    function updateStepFromMessage(content) {
        // Detect workflow steps from message content
        var lowerContent = content.toLowerCase();

        // Analyze step - agent is fetching/analyzing
        if (lowerContent.includes("analyzing") || lowerContent.includes("fetching metrics") || lowerContent.includes("analysis for cluster")) {
            updateSubwayGraph({ analyze: "in_progress" });
        }

        // Recommendation step - agent presents options
        if (lowerContent.includes("recommendation") || lowerContent.includes("options:") || lowerContent.includes("best fit")) {
            updateSubwayGraph({ analyze: "completed", recommend: "completed" });
            // Store recommendation for popup
            lastRecommendation = content;
            recommendContent.innerHTML = renderMarkdown(extractRecommendationSummary(content));
        }

        // Approval waiting - agent asks user to confirm/choose
        if (lowerContent.includes("please confirm") || lowerContent.includes("would you like to proceed") ||
            (lowerContent.includes("let me know") && lowerContent.includes("option"))) {
            updateSubwayGraph({ analyze: "completed", recommend: "completed", approval: "in_progress" });
        }

        // Approval granted - agent confirms it will proceed OR user approved
        // Detect: "have been updated", "updating the", "proceeding with", "backed up"
        if (lowerContent.includes("have been updated") ||
            lowerContent.includes("updating the") ||
            lowerContent.includes("proceeding with") ||
            (lowerContent.includes("backup") && lowerContent.includes("config"))) {
            updateSubwayGraph({ approval: "approved" });
            approvalLabel.textContent = "Approved";
        }

        // Modify step - Parameter Store updated
        // Detect: "updated to ... in the parameter store", "modified parameter store", "parameter store for"
        if ((lowerContent.includes("parameter store") && (lowerContent.includes("updated") || lowerContent.includes("modified"))) ||
            (lowerContent.includes("have been updated") && lowerContent.includes("parameter store"))) {
            updateSubwayGraph({ approval: "approved", modify: "completed" });
            approvalLabel.textContent = "Approved";
        }

        // Create step - Lambda invocation
        if ((lowerContent.includes("invoke") && lowerContent.includes("lambda")) ||
            (lowerContent.includes("creating") && lowerContent.includes("cluster")) ||
            lowerContent.includes("lambda invocation")) {
            updateSubwayGraph({ modify: "completed", create: "in_progress" });
        }
        if (lowerContent.includes("lambda") && (lowerContent.includes("success") || lowerContent.includes("successfully"))) {
            updateSubwayGraph({ create: "completed", monitor: "in_progress" });
        }

        // Monitor step - waiting for cluster
        if (lowerContent.includes("monitoring") ||
            lowerContent.includes("background") ||
            lowerContent.includes("10-12 minutes") ||
            lowerContent.includes("being created in the background") ||
            lowerContent.includes("automatically revert")) {
            updateSubwayGraph({ create: "completed", monitor: "in_progress" });
        }

        // Complete step - cluster ready or reverted
        if (lowerContent.includes("cluster is ready") || lowerContent.includes("cluster is running") ||
            lowerContent.includes("reverted to original")) {
            updateSubwayGraph({ monitor: "completed" });
        }
    }

    function extractRecommendationSummary(content) {
        // Extract just the recommendation part
        var lines = content.split('\n');
        var summary = [];
        var inRecommendation = false;

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.toLowerCase().includes('recommendation') || line.toLowerCase().includes('best fit')) {
                inRecommendation = true;
            }
            if (inRecommendation) {
                summary.push(line);
                if (summary.length > 10) break;
            }
        }

        return summary.length > 0 ? summary.join('\n') : 'See chat for full recommendation.';
    }

    // ========== Status Polling ==========

    function startStatusPolling() {
        stopStatusPolling();
        statusPollInterval = setInterval(pollStatus, 30000);
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
                if (data.monitor_details && data.active) {
                    showProgress(data.monitor_details.message || "Monitoring cluster...");
                    updateSubwayGraph({ monitor: "in_progress" });
                }
                if (data.monitor_details && data.monitor_details.status === "reverted") {
                    updateSubwayGraph({ monitor: "completed" });
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

        // Smart commands
        if (selectedCluster) {
            if (text.toLowerCase() === "analyze") {
                text = "Analyze cluster " + selectedCluster;
                updateSubwayGraph({ analyze: "in_progress" });
            } else if (text.toLowerCase() === "optimize") {
                text = "Optimize cluster " + selectedCluster;
            }
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

        if (approved) {
            showProgress("Executing optimization...");
            updateSubwayGraph({ approval: "approved", modify: "in_progress" });
            approvalLabel.textContent = "Approved";
        } else {
            showProgress("Cancelling...");
            updateSubwayGraph({ approval: "denied" });
            approvalLabel.textContent = "Denied";
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
                    updateStepFromMessage(data.content);
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
                        var stepUpdate = {};
                        stepUpdate[stepData.step] = stepData.status;
                        updateSubwayGraph(stepUpdate);
                    } catch (e) {}
                    break;
                case "approval_required":
                    hideProgress();
                    showApprovalButtons();
                    updateSubwayGraph({ analyze: "completed", recommend: "completed", approval: "in_progress" });
                    break;
                case "complete":
                    hideProgress();
                    setInputEnabled(true);
                    closeSSE();
                    pollStatus();
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

        // Map phases to new step names
        var phaseToStep = {
            backed_up: { approval: "approved", modify: "completed" },
            modified: { modify: "completed", create: "in_progress" },
            cluster_creation_submitted: { create: "completed", monitor: "in_progress" },
            monitoring: { create: "completed", monitor: "in_progress" },
            cluster_ready: { monitor: "completed" },
            reverted: { monitor: "completed" },
        };

        var stepUpdate = phaseToStep[phase];
        if (stepUpdate) {
            updateSubwayGraph(stepUpdate);
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

    function escapeAttr(text) {
        return text.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function formatTime(isoString) {
        if (!isoString) return "-";
        try {
            var date = new Date(isoString);
            return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        } catch (e) {
            return "-";
        }
    }
})();
