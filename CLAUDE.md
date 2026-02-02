# COST_AGENT_AWS - Agentic EMR Cost Optimization

## Overview

An AI-powered agent that autonomously analyzes AWS EMR transient cluster utilization, recommends optimized instance configurations, modifies cluster configs in AWS Parameter Store, creates test clusters via Lambda, and generates optimization reports.

The agent uses **Azure OpenAI** as the LLM backend and **LangGraph** for multi-step orchestration.

## Scope

- **Transient clusters only** - clusters with runtime < 6 hours
- **Terminated/Completed clusters only** - only clusters with full metric history are analyzed (not RUNNING/WAITING)
- **Core and Task nodes analyzed separately** - each node type gets its own recommendation
- **Master nodes are not modified** - only Core and Task fleet instance types are changed
- **UAT environment** - all modifications run in UAT, not production

## How It Works

### Cluster Configuration Flow

```
AWS Parameter Store                    Lambda                         EMR
/application/ecdp-config/    →    app-job-submit-lambda-prod    →    Cluster
prod/EMR-BASE/<CLUSTER_NAME>          (creates cluster)             (runs)
```

The agent does NOT call EMR RunJobFlow directly. Instead:
1. Reads cluster config from Parameter Store
2. Modifies the instance types in the config
3. Writes modified config back to Parameter Store
4. Invokes the Lambda to create the cluster from that config
5. After test completes, **reverts Parameter Store to original config**

### Parameter Store Config Structure

Path: `/application/ecdp-config/prod/EMR-BASE/<CLUSTER_NAME>`

The `Instances` field contains a JSON string with `InstanceFleets` array. Each fleet has:
- `InstanceFleetType`: MASTER, CORE, or TASK
- `InstanceTypeConfigs`: Array of instance type configurations
- `TargetOnDemandCapacity` / `TargetSpotCapacity`: Node counts

When modifying, we ONLY change the `InstanceType` value within `InstanceTypeConfigs` for CORE and TASK fleets. Everything else remains unchanged:
- EBS configuration: unchanged
- Weighted capacity: unchanged
- Bid price: unchanged
- Spot/OnDemand targets: unchanged
- Subnet IDs: unchanged

### Lambda Invocation

Function: `app-job-submit-lambda-prod`

Payload:
```json
{
    "resource": "/executions/clusters",
    "path": "/executions/clusters",
    "body": "{\"cluster_name\": \"<CLUSTER_NAME>\", \"job_type\": \"CLUSTER\", \"request_type\": \"CREATE\", \"fifo_key\": \"<CLUSTER_NAME>\"}",
    "httpMethod": "POST"
}
```

The Lambda reads the Parameter Store config using the `cluster_name` from the payload. We cannot pass arbitrary cluster names - the name must match an existing Parameter Store entry.

## Agent Workflow

### Phase 1 (Current Implementation)

```
User: "Optimize STRESS-TEMPLATE-S"

  1. ANALYZE       → Fetch cluster metrics from CloudWatch
                     Analyze CORE nodes separately
                     Analyze TASK nodes separately

  2. RECOMMEND     → Propose instance types per node type
                     Show estimated cost savings

  3. APPROVE       → Single approval gate
                     [Proceed with Optimization] or [Cancel]
                     "Fire and forget" - one click, agent does the rest

  4. BACKUP        → Save original Parameter Store config

  5. MODIFY        → Update Parameter Store with recommended instance types
                     Change InstanceType in CORE/TASK fleets
                     Update GravitonAmi flag if architecture changes

  6. CREATE        → Invoke Lambda to create cluster with new config

  7. WAIT          → Poll until cluster is WAITING/RUNNING state

  8. REVERT        → Revert Parameter Store to original config
                     (regardless of success or failure)
```

### Phase 2 (Future)

```
  9. COPY STEPS    → Get steps from old cluster (all or user-selected)
 10. SUBMIT STEPS  → Run steps on new cluster
 11. COMPARE       → Compare runtimes between old and new
 12. COST REPORT   → Compare costs and generate report
 13. CLEANUP       → Terminate test cluster
```

### Interaction Model: "Fire and Forget"

Management requires minimal human-in-the-loop interference:

1. User provides cluster name or selects from list
2. Agent analyzes and presents recommendation with cost estimate
3. User clicks ONE button to approve
4. Agent executes everything autonomously:
   - Backs up config
   - Modifies Parameter Store
   - Creates cluster
   - Monitors progress
   - Reverts config
   - Reports results
5. User sees live progress feed and final results

Only ONE approval point - before Parameter Store modification and cluster creation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COST_AGENT_AWS                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     FLASK WEB APPLICATION                            │   │
│  │  /              → Agent chat interface (single page)                 │   │
│  │  /api/agent/chat    → Send message to agent                          │   │
│  │  /api/agent/approve → Approve optimization                           │   │
│  │  /api/agent/status  → Poll agent progress (SSE or polling)           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     LANGGRAPH AGENT                                   │   │
│  │                                                                      │   │
│  │  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────────┐    │   │
│  │  │ Analyze  │──▶│ Recommend │──▶│ Approve  │──▶│   Backup     │    │   │
│  │  │ Cluster  │   │ Per Node  │   │ (single) │   │  Param Store │    │   │
│  │  └──────────┘   └───────────┘   └──────────┘   └──────┬───────┘    │   │
│  │                                                        │            │   │
│  │  ┌──────────┐   ┌───────────┐   ┌──────────┐          │            │   │
│  │  │  Revert  │◀──│   Wait    │◀──│  Create  │◀─────────┘            │   │
│  │  │  Config  │   │ for Ready │   │ Cluster  │                       │   │
│  │  └──────────┘   └───────────┘   └──────────┘                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     TOOLS                                            │   │
│  │                                                                      │   │
│  │  list_transient_clusters  → List clusters with runtime < 6 hours     │   │
│  │  analyze_cluster          → CloudWatch metrics + sizing assessment   │   │
│  │  get_param_store_config   → Read Parameter Store config              │   │
│  │  modify_param_store       → Update instance types in config          │   │
│  │  revert_param_store       → Restore original config                  │   │
│  │  invoke_cluster_lambda    → Trigger cluster creation Lambda          │   │
│  │  check_cluster_status     → Poll cluster state                       │   │
│  │  calculate_cost           → Compute cost comparison                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      │ boto3 + Azure OpenAI SDK             │
│                                      ▼                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                          AWS SERVICES                                 │  │
│  │  EMR          CloudWatch       Parameter Store      Lambda            │  │
│  │  (read only)  (metrics)        (read/write config)  (create cluster)  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                          AZURE OPENAI                                 │  │
│  │  LLM for reasoning, analysis interpretation, report generation        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| LLM | Azure OpenAI (GPT-4o) | Agent reasoning, report generation |
| Agent Framework | LangGraph | Multi-step orchestration, state management |
| Backend | Python 3.10+ / Flask | Web server, API routes |
| AWS SDK | boto3 | EMR, CloudWatch, SSM, Lambda |
| Frontend | Vanilla JS | Chat interface with bold typography theme |

## Project Structure

```
COST_AGENT_AWS/
├── CLAUDE.md                       # This documentation
├── requirements.txt                # Python dependencies
├── .env.example                    # Required environment variables
├── config.py                       # Configuration and thresholds
├── app.py                          # Flask application entry point
│
├── agent/
│   ├── __init__.py
│   ├── graph.py                    # LangGraph workflow definition
│   ├── state.py                    # Agent state schema
│   ├── nodes.py                    # Graph node functions
│   └── prompts.py                  # System prompts for the agent
│
├── tools/
│   ├── __init__.py
│   ├── analyze.py                  # Cluster analysis tool
│   ├── emr_operations.py           # List clusters, check status
│   ├── param_store.py              # Read/modify/revert Parameter Store
│   ├── lambda_operations.py        # Invoke cluster creation Lambda
│   ├── metrics.py                  # CloudWatch metrics collection
│   └── cost_calculator.py          # Cost comparison tool
│
├── services/
│   ├── __init__.py
│   ├── emr_service.py              # EMR API wrapper (with backoff)
│   ├── cloudwatch_service.py       # CloudWatch API wrapper
│   ├── pricing_service.py          # Instance pricing data
│   └── analyzer_service.py         # Analysis and recommendation engine
│
├── static/
│   ├── css/
│   │   └── style.css               # Bold typography theme
│   └── js/
│       └── agent.js                # Agent chat interface JS
│
└── templates/
    ├── base.html                   # Base template
    └── agent.html                  # Agent chat interface
```

## Agent State Schema

| Field | Type | Description |
|-------|------|-------------|
| `messages` | list | Conversation history (user + agent) |
| `cluster_name` | str | Target cluster name (Parameter Store key) |
| `cluster_id` | str | EMR cluster ID (for metrics lookup) |
| `param_store_config` | dict | Full config from Parameter Store |
| `original_config_backup` | dict | Backup of original config before modification |
| `core_analysis` | dict | CORE node utilization analysis |
| `task_analysis` | dict | TASK node utilization analysis |
| `core_recommendation` | dict | Recommended instance type for CORE |
| `task_recommendation` | dict | Recommended instance type for TASK |
| `estimated_savings` | dict | Projected cost savings |
| `human_approved` | bool | Whether user approved the optimization |
| `modified_config` | dict | Config with recommended instance types |
| `new_cluster_id` | str | ID of the newly created test cluster |
| `new_cluster_status` | str | Current state of test cluster |
| `config_reverted` | bool | Whether Parameter Store has been reverted |
| `final_report` | str | Generated report |

## Analysis Logic

### Cluster Discovery

- Fetch clusters from EMR API using `ListClusters`
- Include states: TERMINATED, COMPLETED (only clusters with full metric history)
- Filter client-side: runtime < 6 hours
- Use **exponential backoff** for API throttling (100s of clusters in 24h window)
- Fetch clusters from last 24 hours

### Per-Node Analysis

CORE and TASK nodes are analyzed independently:

1. Get EC2 instance IDs for each node type
2. Fetch CPU metrics from `AWS/EC2` namespace
3. Fetch Memory metrics from `CWAgent` namespace
4. Calculate average and P95 (peak) utilization
5. Apply sustained peak analysis (distinguish spikes from sustained usage)
6. Determine sizing status per node type

### Sizing Thresholds

Uses the HIGHER of CPU and Memory utilization:

| Status | Average | Peak (P95) | Action |
|--------|---------|------------|--------|
| Heavily Oversized | < 25% | < 35% | Suggest 2 sizes down |
| Moderately Oversized | < 50% | < 60% | Suggest 1 size down |
| Right-Sized | < 70% | < 80% | No change needed |
| Undersized | >= 70% | >= 80% | Consider upsizing |

### Workload Profile Detection

- **CPU Heavy**: CPU utilization > 1.5x Memory utilization
- **Memory Heavy**: Memory utilization > 1.5x CPU utilization
- **Balanced**: Both within 1.5x of each other

### Recommendation Types

| Type | Description |
|------|-------------|
| Same Family | Smaller instance in current family (e.g., r7g.4xlarge to r7g.2xlarge) |
| Cross Family | Cheapest instance meeting requirements across all families (including non-Graviton) |
| Category Optimized | Best instance for workload profile (compute/memory/general) |

**Important**: Recommendations can cross architecture boundaries (Graviton to Intel/AMD and vice versa). The `GravitonAmi` flag in Parameter Store config may need to be updated if switching architectures.

### Headroom Calculation

```
Required resources = (Current specs x Peak utilization) x 1.2 (20% headroom)
```

## Parameter Store Modification

### What Changes

1. `InstanceType` value within `InstanceTypeConfigs` for CORE and TASK fleets
2. `GravitonAmi` flag - **must be updated** when a recommendation switches CPU architecture (e.g., Graviton `r7g` to Intel `r7i` or vice versa). The recommendation engine detects architecture changes and flips this flag accordingly.

### What Does NOT Change

- Master fleet configuration
- EBS volumes (type, size, count)
- Weighted capacity
- Bid price as percentage of on-demand
- Spot/OnDemand capacity targets
- Subnet IDs
- Bootstrap actions
- Configurations (Spark, YARN, etc.)
- Applications
- Tags
- Step concurrency level

### Safety: Backup and Revert

1. Before modification: read and store the complete original Parameter Store value
2. After modification and cluster creation: revert Parameter Store to original value
3. Revert happens regardless of success or failure
4. Revert is the FIRST cleanup action (before any other cleanup)

## Pricing Strategy

Hybrid approach for instance pricing data:

1. **Static pricing table** - A hardcoded dictionary of common EMR instance types and their on-demand hourly rates, stored in `services/pricing_service.py`. This is the default source and requires no API calls.
2. **Optional AWS Pricing API refresh** - When available, the agent can query the AWS Pricing API (`pricing:GetProducts`) to refresh the static table with current prices. This is not required for basic operation.
3. **Fallback behavior** - If an instance type is not in the static table and the Pricing API is unavailable, the agent logs a warning and uses the closest known instance type's price as an estimate.

The static table should cover all instance families commonly used in EMR: `m5`, `m6g`, `m6i`, `m7g`, `m7i`, `r5`, `r6g`, `r6i`, `r7g`, `r7i`, `c5`, `c6g`, `c6i`, `c7g`, `c7i` in sizes `xlarge` through `16xlarge`.

## API Throttling Strategy

EMR API can throttle with 100s of clusters. Strategy:

- **Exponential backoff** with jitter on all EMR API calls
- **Retry config**: Initial delay 1s, max delay 30s, max retries 5
- **Pagination**: Use `Marker` for `ListClusters` to page through results
- **Client-side filtering**: Filter by runtime < 6 hours after fetching
- **Caching**: Cache cluster list for the session (user can refresh)

## UI Design

### Design Language

- **Bold Typography** - Large, confident type hierarchy
- **Monochrome base** - Black, white, grays as primary palette
- **Accent color** - Single accent for actions and status
- **No emojis** - Professional, clean interface
- **Chat-based** - Conversation with the agent, live progress feed
- **Minimal chrome** - No unnecessary UI elements

### Interaction Flow

```
┌─────────────────────────────────────────────────────────┐
│  COST AGENT                                              │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Agent: Which cluster would you like to optimize?        │
│         Here are the recent transient clusters:          │
│                                                          │
│         STRESS-TEMPLATE-S     2.3 hours   terminated     │
│         STRESS-TEMPLATE-L     3.1 hours   terminated     │
│         ETL-DAILY-LOAD        1.8 hours   running        │
│                                                          │
│  ─────────────────────────────────────────────────────   │
│                                                          │
│  User: Optimize STRESS-TEMPLATE-S                        │
│                                                          │
│  ─────────────────────────────────────────────────────   │
│                                                          │
│  Agent: Analyzing STRESS-TEMPLATE-S...                   │
│                                                          │
│         CORE NODES (10x r7g.4xlarge)                     │
│         CPU: 18.3% avg | 32.1% peak                     │
│         Memory: 24.5% avg | 38.7% peak                  │
│         Status: HEAVILY OVERSIZED                        │
│         Recommendation: r7g.2xlarge                      │
│                                                          │
│         TASK NODES (5x r7g.4xlarge)                      │
│         CPU: 45.2% avg | 58.3% peak                     │
│         Memory: 38.1% avg | 51.2% peak                  │
│         Status: MODERATELY OVERSIZED                     │
│         Recommendation: r7g.xlarge                       │
│                                                          │
│         Estimated savings: $1,247/month                  │
│         Test cluster cost: ~$8                           │
│                                                          │
│         [ Proceed with Optimization ]    [ Cancel ]      │
│                                                          │
│  ─────────────────────────────────────────────────────   │
│                                                          │
│  Agent: Optimization in progress...                      │
│         > Backed up parameter store config               │
│         > Modified CORE: r7g.4xlarge -> r7g.2xlarge      │
│         > Modified TASK: r7g.4xlarge -> r7g.xlarge       │
│         > Triggering cluster creation...                 │
│         > Cluster j-3KF82HD created                      │
│         > Waiting for cluster ready... (2m 34s)          │
│         > Cluster is WAITING                             │
│         > Reverted parameter store to original config    │
│         > Done.                                          │
│                                                          │
│  [Type a message...]                                     │
└─────────────────────────────────────────────────────────┘
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Yes | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | Yes | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT` | Yes | Model deployment name (e.g., gpt-4o) |
| `AZURE_OPENAI_API_VERSION` | Yes | API version (e.g., 2024-10-21) |
| `AWS_REGION` | Yes | AWS region (default: us-east-1) |
| `AWS_DEFAULT_REGION` | Yes | AWS region for boto3 |
| `PARAM_STORE_PREFIX` | Yes | Parameter Store path prefix (default: /application/ecdp-config/prod/EMR-BASE/) |
| `LAMBDA_FUNCTION_NAME` | Yes | Lambda function name (default: app-job-submit-lambda-prod) |
| `MAX_CLUSTER_INSTANCES` | No | Safety limit for instance count (default: 20) |
| `AUTO_TERMINATE_MINUTES` | No | Auto-terminate test cluster after N minutes (default: 120) |
| `MAX_TEST_COST_DOLLARS` | No | Cost ceiling warning threshold (default: 50) |

## AWS IAM Permissions

### Read Permissions

| Permission | Purpose |
|------------|---------|
| `elasticmapreduce:ListClusters` | List EMR clusters |
| `elasticmapreduce:DescribeCluster` | Get cluster details |
| `elasticmapreduce:ListInstanceFleets` | Get fleet configuration |
| `elasticmapreduce:ListInstances` | Get EC2 instance IDs |
| `elasticmapreduce:ListSteps` | Get cluster steps (Phase 2) |
| `ec2:DescribeInstances` | Get instance details |
| `cloudwatch:GetMetricStatistics` | Fetch utilization metrics |
| `ssm:GetParameter` | Read Parameter Store config |

### Write Permissions

| Permission | Purpose |
|------------|---------|
| `ssm:PutParameter` | Modify Parameter Store config |
| `lambda:InvokeFunction` | Trigger cluster creation Lambda |

## Safety Guardrails

| Guardrail | Description | Default |
|-----------|-------------|---------|
| Single approval gate | User approves once, agent handles the rest | Always on |
| Config backup + auto-revert | Parameter Store always reverted after test | Always on |
| Max instance count | Upper limit on instances in test cluster | 20 |
| Auto-terminate | Test cluster auto-terminates after timeout | 120 minutes |
| Cost ceiling warning | Agent warns if estimated test cost exceeds threshold | $50 |
| Exponential backoff | All AWS API calls use backoff for throttling | Always on |

## Logging and Audit Trail

Every infrastructure-modifying action must be logged with structured output. The agent uses Python's `logging` module with a dedicated `audit` logger.

### Required audit log entries

| Event | Fields Logged |
|-------|---------------|
| Parameter Store read | timestamp, cluster_name, param_path |
| Parameter Store modify | timestamp, cluster_name, param_path, changes (before/after instance types, GravitonAmi flag) |
| Parameter Store revert | timestamp, cluster_name, param_path, success/failure |
| Lambda invocation | timestamp, cluster_name, lambda_function, payload |
| Cluster status change | timestamp, cluster_name, new_cluster_id, old_status, new_status |

Log format: JSON lines to stdout (Flask default). Each entry includes a `correlation_id` that ties all actions within a single optimization run together.

## Known Risks and Manual Recovery

### Parameter Store Race Condition

The agent modifies a shared Parameter Store config, creating a window where other processes could read the modified (non-original) config. This risk is accepted for Phase 1 because:
- The agent is used by a single operator in UAT
- The modification window is short (seconds between write and Lambda invocation)
- The revert happens immediately after cluster creation is triggered

**Mitigation**: Do not run multiple optimization workflows concurrently against clusters that share the same Parameter Store config path.

### Crash Recovery

If the agent process crashes after modifying Parameter Store but before reverting:

1. Check the agent's audit logs for the last `Parameter Store modify` entry
2. Read the original config from the log's `before` field
3. Manually write it back to the Parameter Store path using the AWS console or CLI:
   ```
   aws ssm put-parameter --name "<param_path>" --value '<original_value>' --type String --overwrite
   ```

There is no automatic crash recovery in Phase 1. The operator is responsible for manual revert if the agent fails mid-workflow.

## Implementation Phases

### Phase 1 (Current)
1. User selects or names a cluster
2. Agent analyzes CORE and TASK nodes separately
3. Agent recommends instance types per node type
4. User approves with single click
5. Agent backs up Parameter Store config
6. Agent modifies Parameter Store with recommendations
7. Agent invokes Lambda to create test cluster
8. Agent monitors cluster creation
9. Agent reverts Parameter Store to original config

### Phase 2 (Future)
1. Get steps from original cluster (all or user-selected)
2. Submit steps to new test cluster
3. Wait for step completion
4. Compare runtimes (old vs new)
5. Compare costs (old vs new)
6. Generate comprehensive report
7. Terminate test cluster

## Relationship to COST_UI

This project builds upon analysis patterns from [COST_UI](https://github.com/GA3773/COST_UI):

| Component | COST_UI | COST_AGENT_AWS |
|-----------|---------|----------------|
| Scope | All clusters | Transient only (< 6 hours) |
| Analysis | Manual trigger via UI button | Agent-driven via chat |
| Configuration | Read-only | Reads and modifies Parameter Store |
| Cluster creation | Not supported | Via Lambda invocation |
| Recommendations | Display only | Agent acts on them |
| Interaction | Dashboard with modals | Chat-based agent interface |
| UI Theme | Bootstrap default | Bold typography, minimal |
