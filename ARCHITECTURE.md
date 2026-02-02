# Cost Agent AWS -- Architecture & Agent Flow

## Executive Summary

Cost Agent AWS is an AI-powered system that autonomously analyzes AWS EMR transient cluster utilization, recommends cheaper instance configurations, and executes a controlled test deployment to validate the recommendations. The system uses a **LangGraph state machine** orchestrated by **Azure OpenAI GPT-4o** to move through a multi-phase workflow with a single human approval gate. The operator clicks one button; the agent handles everything else.

---

## 1. System Architecture

```
                                   OPERATOR
                                      |
                                      | Browser (chat UI)
                                      v
                    +-------------------------------------+
                    |        FLASK WEB SERVER              |
                    |  app.py                              |
                    |                                      |
                    |  POST /api/agent/chat  ----+         |
                    |  POST /api/agent/approve   |         |
                    |  GET  /api/agent/status/  <-+- SSE   |
                    +---------------+---------------------+
                                    |
                     Background Thread (per session)
                                    |
                                    v
                    +-------------------------------------+
                    |        LANGGRAPH STATE MACHINE       |
                    |  agent/graph.py                      |
                    |                                      |
                    |  StateGraph(AgentState)               |
                    |  + MemorySaver checkpointer          |
                    |  + interrupt_before=["backup"]       |
                    |                                      |
                    |  9 nodes, 11 edges                   |
                    +---------------+---------------------+
                                    |
                       +------------+------------+
                       |                         |
                       v                         v
          +---------------------+   +-------------------------+
          |   AZURE OPENAI      |   |   AWS SERVICES          |
          |   (GPT-4o)          |   |                         |
          |                     |   |   EMR (read)            |
          |   - Reasoning       |   |   CloudWatch (metrics)  |
          |   - Tool selection  |   |   SSM (config r/w)      |
          |   - Report gen      |   |   Lambda (create)       |
          +---------------------+   +-------------------------+
```

### How the Web Layer Connects to the Agent

1. The Flask server holds an in-memory `sessions` dict keyed by `thread_id`.
2. When the user sends a chat message, Flask spawns a **background thread** that runs the compiled LangGraph graph via `graph.stream()`.
3. Each graph event (phase change, AI message) is pushed to a `queue.Queue` in the session.
4. The browser connects to an **SSE (Server-Sent Events)** endpoint that reads from the queue and streams updates in real time.
5. When the graph hits the `interrupt_before=["backup"]` checkpoint, the SSE stream sends an `approval_required` event, and the UI shows Approve/Cancel buttons.
6. On approval, Flask calls `graph.update_state()` to set `human_approved=True`, then resumes the graph with `graph.stream(None, config)`.

---

## 2. Software Layers

The codebase is organized into 5 dependency layers. Each layer depends only on layers below it.

```
Layer 5  +-------------------------------------------------+
         |  WEB APPLICATION                                |
         |  app.py, templates/*, static/*                  |
         +-------------------------------------------------+
                |
Layer 4  +-------------------------------------------------+
         |  AGENT ORCHESTRATION                            |
         |  agent/graph.py        (StateGraph definition)  |
         |  agent/nodes.py        (node functions)         |
         +-------------------------------------------------+
                |
Layer 3  +-------------------------------------------------+
         |  LANGGRAPH TOOLS                                |
         |  tools/analyze.py      tools/param_store.py     |
         |  tools/emr_operations.py  tools/lambda_ops.py   |
         |  tools/metrics.py      tools/cost_calculator.py |
         +-------------------------------------------------+
                |
Layer 2  +-------------------------------------------------+
         |  SERVICES                                       |
         |  services/emr_service.py     (EMR API)          |
         |  services/cloudwatch_service.py (metrics)       |
         |  services/pricing_service.py (89 instance types)|
         |  services/analyzer_service.py (sizing engine)   |
         |  services/retry.py     (backoff + boto3 cache)  |
         +-------------------------------------------------+
                |
Layer 1  +-------------------------------------------------+
         |  FOUNDATION                                     |
         |  config.py             (env, thresholds, logging)|
         |  agent/state.py        (AgentState TypedDict)   |
         |  agent/prompts.py      (system prompts)         |
         +-------------------------------------------------+
```

**Layer 1 -- Foundation.** Configuration constants loaded from `.env`, sizing thresholds, backoff parameters, Graviton family detection set, and a JSON-structured audit logger. `AgentState` is a `TypedDict` with 19 fields that serves as the single source of truth flowing through every graph node.

**Layer 2 -- Services.** Pure Python modules that wrap AWS APIs (EMR, CloudWatch, SSM) behind functions with automatic exponential backoff and jitter. The pricing service contains a static catalog of 89 instance types across 16 families. The analyzer service implements the sizing classification, workload profiling, and recommendation engine.

**Layer 3 -- Tools.** Nine functions decorated with `@tool` from `langchain_core.tools`. These are the actions the LLM can invoke during the conversational phase. Each tool wraps one or more Layer 2 service calls and returns a formatted string or dict.

**Layer 4 -- Agent Orchestration.** The `StateGraph` definition with 9 nodes and conditional routing. The LLM node uses `AzureChatOpenAI` with all 9 tools bound. The execution pipeline (backup through report) runs deterministically after approval.

**Layer 5 -- Web Application.** Flask server with 3 API endpoints and SSE streaming. The frontend is vanilla HTML/CSS/JS with a dark monochrome chat interface.

---

## 3. LangGraph Methodology

### What Is LangGraph

LangGraph is a framework for building stateful, multi-step agent workflows as **directed graphs**. Unlike simple prompt chaining, LangGraph provides:

- **Typed state** that flows through every node and accumulates data across steps
- **Conditional edges** that route execution based on state values
- **Checkpointing** via `MemorySaver` that enables interrupt/resume (human-in-the-loop)
- **Tool integration** via `ToolNode` that automatically executes LLM-selected tool calls

### How We Use It

Our graph has two distinct phases separated by a **checkpoint interrupt**:

```
Phase A: CONVERSATIONAL (LLM-driven, tool-calling loop)
Phase B: EXECUTION (deterministic pipeline, no LLM involvement)
```

**Phase A** is an agentic loop where the LLM reasons, selects tools, receives tool results, and reasons again. The LLM decides when analysis is complete and presents recommendations to the user.

**Phase B** is a fixed pipeline that runs after approval. No LLM calls are made. Each node performs one infrastructure action (backup, modify, create, wait, revert) and writes results to state. Error handling routes to the revert node to guarantee config safety.

The `interrupt_before=["backup"]` directive pauses the graph right before Phase B begins. The graph state is persisted by `MemorySaver`. When the user approves, the Flask server calls `graph.update_state()` to set `human_approved=True` and resumes execution.

---

## 4. Complete Node & Edge Diagram

```
                              START
                                |
                                v
                        +---------------+
                        |  initialize   |  Assign correlation_id (UUID)
                        +---------------+  Set current_phase = "initialized"
                                |
                                v
                    +-------------------+
              +---->|      agent        |  LLM invoked with system prompt + tools
              |     +-------------------+  Reads messages, decides next action
              |             |
              |             | route_agent()
              |             |
              |     +-------+--------+-------------------+
              |     |                |                    |
              |     v                v                    v
              | "tools"          "backup"               "end"
              |     |                |                    |
              |     v                |                    v
              | +---------+         |                  (END)
              | |  tools  |         |
              | +---------+         |     INTERRUPT
              |     |               |     (approval gate)
              +-----+               |
                                    v
                            +---------------+
                            |    backup     |  Read Parameter Store config
                            +---------------+  Store original_config_backup
                                    |
                                    v
                            +---------------+     error
                            |    modify     |  ---------> +
                            +---------------+             |
                                    |                     |
                                    | success             |
                                    v                     |
                            +---------------+     error   |
                            |    create     |  ---------> +
                            +---------------+             |
                                    |                     |
                                    | success             |
                                    v                     |
                            +---------------+             |
                            |     wait      |  ---------> +
                            +---------------+             |
                                    |                     |
                                    | always              |
                                    v                     |
                            +---------------+ <-----------+
                            |    revert     |  ALWAYS runs (error or success)
                            +---------------+  Writes original config back to SSM
                                    |
                                    v
                            +---------------+
                            |    report     |  Generate optimization summary
                            +---------------+
                                    |
                                    v
                                  (END)
```

### Node Reference

| Node | Type | What It Does | State Written |
|------|------|-------------|---------------|
| **initialize** | Deterministic | Generates UUID for audit correlation | `correlation_id`, `current_phase` |
| **agent** | LLM | Invokes GPT-4o with system prompt and 9 bound tools. Decides whether to call a tool, present results, or end conversation. | `messages` (AI response) |
| **tools** | ToolNode | Automatically executes whichever tool(s) the LLM selected in its response. Returns tool output as a `ToolMessage`. | `messages` (tool results) |
| **backup** | Deterministic | Reads current Parameter Store config via SSM `GetParameter`. Stores the raw string for exact byte-level revert. | `original_config_backup`, `param_store_config` |
| **modify** | Deterministic | Parses the nested JSON config, replaces `InstanceType` values in CORE/TASK fleets, updates `GravitonAmi` flag if architecture changes, writes back via SSM `PutParameter`. | `current_phase` |
| **create** | Deterministic | Invokes `app-job-submit-lambda-prod` with the cluster name. The Lambda reads the (now modified) Parameter Store config and calls EMR `RunJobFlow`. | `new_cluster_id` |
| **wait** | Deterministic | Polls `DescribeCluster` every 30 seconds for up to 30 minutes. Exits when cluster reaches WAITING/RUNNING or fails. | `new_cluster_status` |
| **revert** | Deterministic | Writes the original config string back to Parameter Store. Runs regardless of error state in prior nodes. | `config_reverted` |
| **report** | Deterministic | Assembles a summary of the entire run: cluster IDs, recommendations applied, revert status, any errors. | `final_report` |

### Edge Reference

| From | To | Condition |
|------|----|-----------|
| START | initialize | Always |
| initialize | agent | Always |
| agent | tools | LLM response contains `tool_calls` |
| agent | backup | `core_analysis` exists AND `human_approved` is True |
| agent | END | No tool calls, no approval (conversation ended) |
| tools | agent | Always (loop back for LLM to process tool results) |
| backup | modify | Always |
| modify | create | No error in state |
| modify | revert | Error in state |
| create | wait | No error in state |
| create | revert | Error in state |
| wait | revert | Always (revert regardless of outcome) |
| revert | report | Always |
| report | END | Always |

---

## 5. Data Flow: End-to-End

### Phase A: Analysis & Recommendation

```
User: "Optimize STRESS-TEMPLATE-S"
         |
         v
    [agent node]  LLM reads message, decides to call analyze_cluster tool
         |
         v
    [tools node]  Executes analyze_cluster("STRESS-TEMPLATE-S")
         |
         +---> emr_service.get_transient_clusters()
         |         |
         |         +---> EMR ListClusters (paginated, last 24h)
         |         +---> Filter: TERMINATED/COMPLETED, runtime < 6h
         |         +---> Match cluster by name -> cluster_id
         |
         +---> collect_node_metrics(cluster_id, "CORE")
         |         |
         |         +---> EMR ListInstanceFleets -> find CORE fleet
         |         +---> EMR ListInstances -> get EC2 instance IDs
         |         +---> CloudWatch GetMetricStatistics
         |         |       Namespace: AWS/EC2 -> CPUUtilization
         |         |       Namespace: CWAgent -> mem_used_percent
         |         +---> Calculate: avg, P95 for CPU and Memory
         |
         +---> collect_node_metrics(cluster_id, "TASK")
         |         |
         |         +---> (same flow as CORE, for TASK fleet)
         |
         +---> analyzer_service.analyze_node_type("CORE", ...)
         |         |
         |         +---> classify_sizing()
         |         |       Uses max(CPU, Memory) for both avg and peak
         |         |       Thresholds:
         |         |         avg < 25% AND peak < 35%  -> HEAVILY OVERSIZED
         |         |         avg < 50% AND peak < 60%  -> MODERATELY OVERSIZED
         |         |         avg < 70% AND peak < 80%  -> RIGHT-SIZED
         |         |         above thresholds           -> UNDERSIZED
         |         |
         |         +---> detect_workload_profile()
         |         |       CPU/Mem ratio > 1.5 -> cpu_heavy
         |         |       Mem/CPU ratio > 1.5 -> memory_heavy
         |         |       Otherwise           -> balanced
         |         |
         |         +---> calculate_required_resources()
         |         |       required_vcpu = current_vcpu * (peak_cpu/100) * 1.2
         |         |       required_mem  = current_mem  * (peak_mem/100) * 1.2
         |         |
         |         +---> recommend_instance()
         |                 1. Try same-family downsize first
         |                    (e.g., r7g.4xlarge -> r7g.2xlarge)
         |                 2. If none fits, try cross-family
         |                    (cheapest across all 16 families that
         |                     meets vCPU + memory requirements)
         |                 3. Detect architecture change
         |                    (Graviton <-> Intel/AMD)
         |
         +---> analyzer_service.analyze_node_type("TASK", ...)
         |         |
         |         +---> (same pipeline as CORE)
         |
         v
    [agent node]  LLM receives analysis results, formats presentation
         |
         v
    [agent node]  LLM calls calculate_cost tool
         |
         v
    [tools node]  Executes calculate_cost(...)
         |
         +---> pricing_service.get_instance_spec() for each type
         +---> Per-run cost  = sum(price * count * hours) for each fleet
         +---> Monthly cost  = per-run * 30 (estimated runs)
         +---> Savings       = current - recommended (per-run and monthly)
         +---> Test cost     = single run with recommended config
         |
         v
    [agent node]  LLM presents complete analysis + cost comparison
                  Graph pauses at interrupt_before=["backup"]
                  UI shows [Proceed with Optimization] [Cancel]
```

### Decision Gate: Human Approval

```
    GRAPH PAUSED (MemorySaver checkpoint)
         |
         |  User clicks "Proceed with Optimization"
         |  Flask calls graph.update_state({"human_approved": True})
         |  Flask calls graph.stream(None, config) to resume
         |
         v
    Phase B begins
```

### Phase B: Execution Pipeline

```
    [backup node]
         |
         +---> SSM GetParameter("/application/ecdp-config/prod/EMR-BASE/<NAME>")
         +---> Store raw string as original_config_backup
         +---> Parse JSON config into param_store_config
         |
         v
    [modify node]
         |
         +---> Parse config["Instances"] (JSON string within JSON)
         +---> For each InstanceFleet in InstanceFleets:
         |       If CORE fleet -> replace InstanceType with core_recommendation
         |       If TASK fleet -> replace InstanceType with task_recommendation
         |       MASTER fleet  -> no changes
         +---> If architecture changed (Graviton <-> Intel):
         |       Update config["GravitonAmi"] flag
         +---> Re-serialize Instances dict as JSON string
         +---> SSM PutParameter with modified config
         +---> Audit log: changes (before/after instance types)
         |
         v
    [create node]
         |
         +---> Lambda Invoke("app-job-submit-lambda-prod")
         |       Payload:
         |       {
         |         "resource": "/executions/clusters",
         |         "path": "/executions/clusters",
         |         "body": "{\"cluster_name\":\"<NAME>\",
         |                   \"job_type\":\"CLUSTER\",
         |                   \"request_type\":\"CREATE\",
         |                   \"fifo_key\":\"<NAME>\"}",
         |         "httpMethod": "POST"
         |       }
         +---> Lambda reads Parameter Store (now has modified config)
         +---> Lambda calls EMR RunJobFlow -> returns cluster_id
         +---> Store new_cluster_id in state
         +---> Audit log: lambda invocation + cluster_id
         |
         v
    [wait node]
         |
         +---> Loop (every 30s, max 30 minutes):
         |       EMR DescribeCluster(new_cluster_id)
         |       If WAITING or RUNNING -> success, exit loop
         |       If TERMINATED/TERMINATED_WITH_ERRORS -> error, exit loop
         |       Else -> continue polling
         +---> Audit log: each status transition
         |
         v
    [revert node]  *** ALWAYS RUNS ***
         |
         +---> SSM PutParameter(original_config_backup)
         +---> Audit log: revert success/failure
         |
         v
    [report node]
         |
         +---> Compile summary:
         |       - Cluster name and test cluster ID
         |       - Recommendations applied (CORE and TASK)
         |       - Test cluster status
         |       - Config revert status
         |       - Any errors encountered
         +---> Write to final_report in state
         |
         v
       (END)
```

---

## 6. State Schema

The `AgentState` TypedDict is the single data structure that flows through every node. LangGraph manages state persistence across the interrupt/resume boundary via `MemorySaver`.

```
AgentState
├── messages              list (Annotated with add_messages reducer)
│                         Full conversation history: user, AI, tool messages
│
├── Cluster Identification
│   ├── cluster_name      str   "STRESS-TEMPLATE-S"
│   └── cluster_id        str   "j-3ABC123DEF"
│
├── Parameter Store
│   ├── param_store_config       dict   Parsed JSON config
│   └── original_config_backup   str    Raw string for exact revert
│
├── Analysis Results
│   ├── core_analysis     dict   {sizing_status, workload_profile, metrics, recommendation}
│   └── task_analysis     dict   (same structure)
│
├── Recommendations
│   ├── core_recommendation   dict   {recommended_type, savings_percent, arch_change, ...}
│   └── task_recommendation   dict   (same structure)
│
├── Cost
│   └── estimated_savings     dict   {per_run, monthly, test_cost}
│
├── Approval
│   └── human_approved        bool   Set to True when user clicks Approve
│
├── Execution
│   ├── modified_config       dict   Config after instance type changes
│   ├── new_cluster_id        str    "j-4XYZ789GHI"
│   ├── new_cluster_status    str    WAITING | RUNNING | TERMINATED | TIMEOUT
│   └── config_reverted       bool   True if revert succeeded
│
├── Output
│   └── final_report          str    Compiled optimization summary
│
└── Tracking
    ├── correlation_id        str    UUID tying all audit entries together
    ├── current_phase         str    Latest phase for UI progress indicator
    └── error                 str    Error message (triggers revert routing)
```

---

## 7. Decision Flow: Recommendation Engine

The analyzer service makes three sequential decisions for each node type:

```
                    Metrics In
                  (cpu_avg, cpu_p95,
                   mem_avg, mem_p95)
                        |
                        v
              +-------------------+
              |  classify_sizing  |
              |  max(CPU, Mem)    |
              +-------------------+
                   |    |    |    |
          Heavily  | Mod. | Right| Under-
          Over     | Over | Sized| sized
                   |    |    |    |
                   v    v    |    |
              Recommend      |    |
              downsizing     |    |
                   |         v    v
                   |       Return None
                   |       (no change)
                   v
          +--------------------+
          | detect_workload    |
          | profile            |
          +--------------------+
          cpu_heavy | balanced | memory_heavy
                   |
                   v
          +--------------------+
          | calculate_required |
          | resources          |
          +--------------------+
          required_vcpu = vcpu * (peak/100) * 1.2
          required_mem  = mem  * (peak/100) * 1.2
                   |
                   v
          +--------------------+
          | recommend_instance |
          +--------------------+
                   |
            +------+------+
            |             |
            v             v
       Same Family    Cross Family
       (preferred)    (fallback)
            |             |
            v             v
       Smaller in     Cheapest across
       current family  all 16 families
       that meets      that meets
       requirements    requirements
       and costs less  and costs less
            |             |
            +------+------+
                   |
                   v
            Recommendation
            {
              recommended_type
              recommendation_kind
              arch_change
              savings_percent
              current_price
              recommended_price
            }
```

### Instance Family Coverage

The pricing catalog covers 16 instance families in sizes xlarge through 16xlarge:

| Category | Intel/AMD (x86_64) | Graviton (arm64) |
|----------|-------------------|-----------------|
| General Purpose | m5, m6i, m7i | m6g, m7g |
| Memory Optimized | r5, r6i, r7i | r6g, r7g |
| Compute Optimized | c5, c6i, c7i | c6g, c7g |

Cross-family recommendations can switch between any of these, including changing CPU architecture (Graviton to Intel or vice versa), in which case the `GravitonAmi` flag is updated.

---

## 8. Cost Comparison Model

```
INPUTS:
  Current config:     CORE = 10x r7g.4xlarge ($0.857/hr each)
                      TASK =  5x r7g.4xlarge ($0.857/hr each)
                      Runtime = 2.3 hours

  Recommended:        CORE = 10x r7g.2xlarge ($0.428/hr each)
                      TASK =  5x r6g.4xlarge ($0.806/hr each)

CALCULATION:
  Current per-run     = (10 * 0.857 * 2.3) + (5 * 0.857 * 2.3)
                      = $19.71 + $9.86 = $29.57

  Recommended per-run = (10 * 0.428 * 2.3) + (5 * 0.806 * 2.3)
                      = $9.84 + $9.27 = $19.11

  Savings per run     = $10.46 (35.4%)

  Monthly (30 runs)   = $313.80 savings

  Test cluster cost   = $19.11 (one run with recommended config)
```

---

## 9. Safety & Error Recovery

### Config Protection

```
Timeline:
  T0  Backup     SSM GetParameter -> store original string
  T1  Modify     SSM PutParameter with new instance types
  T2  Create     Lambda invoked (reads modified config)
  T3  Revert     SSM PutParameter with original string
      ^^^^^^^^^
      Danger window: T1 to T3 (typically < 60 seconds)
      During this window, other processes reading the same
      Parameter Store path will see modified config.
```

### Error Recovery Routing

Every execution node (modify, create, wait) catches exceptions and sets `state["error"]`. Conditional edges check for errors:

```
modify --error--> revert  (config was changed, must revert)
create --error--> revert  (config was changed, must revert)
wait   --always-> revert  (always revert, success or failure)
```

The revert node has no error routing -- if revert itself fails, it sets `config_reverted=False` and the report node flags it as requiring manual intervention.

### Audit Trail

Every infrastructure-modifying action is logged as structured JSON with a shared `correlation_id`:

```json
{"timestamp":"2025-01-15T14:30:00Z","event":"param_store_modify",
 "cluster_name":"STRESS-TEMPLATE-S","changes":["CORE: r7g.4xlarge -> r7g.2xlarge"],
 "correlation_id":"a1b2c3d4-..."}
```

Events logged: `param_store_read`, `param_store_modify`, `param_store_revert`, `lambda_invoke`, `lambda_invoke_success`, `lambda_invoke_failure`, `cluster_status_change`.

---

## 10. Phase 2 Roadmap (Future)

Phase 1 validates that the test cluster starts successfully with the recommended config. Phase 2 extends the pipeline to run actual workloads and compare:

```
Phase 1 (Current)                    Phase 2 (Future)

backup -> modify -> create -> wait   ... -> copy_steps -> submit_steps -> wait_steps
       -> revert -> report                -> compare_runtime -> compare_cost
                                          -> generate_report -> cleanup -> END
```

| Comparison Metric | Source |
|------------------|--------|
| Step runtime | EMR ListSteps on old vs new cluster |
| Total cluster runtime | DescribeCluster Timeline |
| Per-run cost | Instance hours * on-demand price |
| Monthly projected cost | Per-run * estimated monthly frequency |
| Resource utilization | CloudWatch metrics on new cluster |

This enables the agent to produce a side-by-side report showing whether the cheaper configuration runs the same workload in comparable time, giving confidence to apply the recommendation to production parameter store configs permanently.
