# COST_AGENT_AWS - Agentic EMR Cost Optimization

## Overview

An AI-powered agent that autonomously analyzes AWS EMR cluster utilization, recommends optimized configurations, creates test clusters with recommended specs, reruns workloads, compares performance, and generates a comprehensive cost optimization report.

The agent uses **Azure OpenAI** as the LLM backend and **LangGraph** for multi-step orchestration with human-in-the-loop approval.

## What the Agent Does

```
User: "Analyze cluster j-ABC123 and optimize it"

  1. ANALYZE      → Fetch cluster config + CloudWatch metrics (CPU, Memory)
  2. ASSESS       → Determine sizing status (oversized / right-sized / undersized)
  3. RECOMMEND    → Propose right-sized instance types with cost estimates
  4. *** HUMAN APPROVAL *** → User approves or rejects cluster creation
  5. CREATE       → Spin up new EMR cluster with recommended specs
  6. WAIT         → Poll until cluster is ready (WAITING state)
  7. REPLICATE    → Copy steps from original cluster to new cluster
  8. EXECUTE      → Submit steps and wait for completion
  9. COMPARE      → Compare runtimes and costs between old and new clusters
 10. REPORT       → Generate detailed optimization report
 11. CLEANUP      → Terminate test cluster
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COST_AGENT_AWS                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     FLASK WEB APPLICATION                            │   │
│  │  /                  → Landing page                                   │   │
│  │  /agent             → Agent chat interface                           │   │
│  │  /api/agent/chat    → Send message to agent                          │   │
│  │  /api/agent/approve → Approve/reject agent actions                   │   │
│  │  /api/agent/status  → Poll agent progress                            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     LANGGRAPH AGENT                                   │   │
│  │                                                                      │   │
│  │  ┌──────────┐   ┌───────────┐   ┌─────────────┐   ┌──────────────┐  │   │
│  │  │ Analyze  │──▶│ Recommend │──▶│   Human     │──▶│   Create     │  │   │
│  │  │ Cluster  │   │ New Specs │   │  Approval   │   │   Cluster    │  │   │
│  │  └──────────┘   └───────────┘   └─────────────┘   └──────┬───────┘  │   │
│  │                                                           │          │   │
│  │  ┌──────────┐   ┌───────────┐   ┌─────────────┐          │          │   │
│  │  │ Generate │◀──│  Compare  │◀──│  Run Steps  │◀─────────┘          │   │
│  │  │  Report  │   │  Results  │   │  & Wait     │                     │   │
│  │  └──────────┘   └───────────┘   └─────────────┘                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     TOOLS (Agent Actions)                            │   │
│  │                                                                      │   │
│  │  analyze_cluster       → Run utilization analysis (existing logic)   │   │
│  │  get_cluster_details   → Fetch cluster configuration                 │   │
│  │  get_cluster_steps     → List steps from original cluster            │   │
│  │  create_emr_cluster    → Create new cluster via EMR API              │   │
│  │  check_cluster_status  → Poll cluster readiness                      │   │
│  │  submit_emr_steps      → Submit steps to new cluster                 │   │
│  │  check_step_status     → Poll step completion                        │   │
│  │  terminate_cluster     → Cleanup test cluster                        │   │
│  │  calculate_cost        → Compute cost comparison                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      │ boto3 + Azure OpenAI SDK             │
│                                      ▼                                      │
│  ┌──────────────────────────────┐  ┌───────────────────────────────────┐   │
│  │         AWS SERVICES          │  │        AZURE OPENAI               │   │
│  │  EMR, EC2, CloudWatch         │  │  LLM for reasoning & reporting   │   │
│  └──────────────────────────────┘  └───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| LLM | Azure OpenAI (GPT-4o) | Agent reasoning, report generation |
| Agent Framework | LangGraph | Multi-step orchestration, state management, human-in-the-loop |
| Backend | Python 3.10+ / Flask | Web server, API routes |
| AWS SDK | boto3 | EMR, EC2, CloudWatch interactions |
| Frontend | Bootstrap 5 / Vanilla JS | Chat-based agent interface |

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
│   ├── emr_operations.py           # Create/terminate cluster tools
│   ├── step_operations.py          # Copy/submit/monitor step tools
│   ├── metrics.py                  # CloudWatch metrics collection
│   └── cost_calculator.py          # Cost comparison tool
│
├── services/
│   ├── __init__.py
│   ├── emr_service.py              # EMR API wrapper
│   ├── cloudwatch_service.py       # CloudWatch API wrapper
│   ├── pricing_service.py          # Instance pricing data
│   └── analyzer_service.py         # Analysis and recommendation engine
│
├── static/
│   ├── css/
│   │   └── style.css               # UI styles
│   └── js/
│       └── agent.js                # Agent chat interface JS
│
└── templates/
    ├── base.html                   # Base template
    └── agent.html                  # Agent chat interface
```

## Agent Workflow (LangGraph)

### State Schema

The agent maintains the following state throughout its lifecycle:

| Field | Type | Description |
|-------|------|-------------|
| `messages` | list | Conversation history (user + agent) |
| `cluster_id` | str | Target cluster ID |
| `analysis_result` | dict | Utilization analysis output |
| `sizing_status` | str | oversized / right-sized / undersized |
| `recommendation` | dict | Recommended instance type, count, cost |
| `human_approved` | bool | Whether user approved cluster creation |
| `new_cluster_id` | str | ID of the test cluster |
| `new_cluster_status` | str | STARTING / WAITING / RUNNING / COMPLETED |
| `old_steps` | list | Steps copied from original cluster |
| `new_step_results` | list | Step results from test cluster |
| `cost_comparison` | dict | Side-by-side cost breakdown |
| `final_report` | str | Generated report text |

### Graph Nodes

| Node | Purpose | Updates State |
|------|---------|---------------|
| `analyze_cluster` | Run utilization analysis on target cluster | `analysis_result`, `sizing_status`, `recommendation` |
| `request_approval` | Interrupt and ask user for approval | `human_approved` |
| `create_cluster` | Create EMR cluster with recommended specs | `new_cluster_id` |
| `wait_for_cluster` | Poll until cluster reaches WAITING state | `new_cluster_status` |
| `copy_submit_steps` | Get steps from old cluster, submit to new | `old_steps`, `new_step_results` |
| `wait_for_steps` | Poll until all steps complete | `new_step_results` |
| `compare_results` | Compare runtimes and costs | `cost_comparison` |
| `generate_report` | LLM generates comprehensive report | `final_report` |
| `cleanup` | Terminate test cluster | (cleanup) |

### Graph Edges (Conditional Flow)

```
analyze_cluster
    │
    ├── sizing_status == "right_sized" or "undersized"
    │       → generate_report (no optimization needed)
    │
    └── sizing_status == "oversized"
            → request_approval
                │
                ├── human_approved == True
                │       → create_cluster → wait_for_cluster
                │         → copy_submit_steps → wait_for_steps
                │         → compare_results → generate_report → cleanup
                │
                └── human_approved == False
                        → generate_report (analysis only, user declined)
```

### Human-in-the-Loop

The agent uses LangGraph's `interrupt()` at the approval step:

1. Agent completes analysis and presents recommendation
2. Graph execution **pauses** and state is persisted
3. User sees the recommendation in the chat UI
4. User clicks "Approve" or "Reject"
5. Graph **resumes** from the saved state with the user's decision

This ensures no AWS resources are created without explicit user consent.

## Agent Tools

### Read-Only Tools (No approval needed)

| Tool | Description | AWS API |
|------|-------------|---------|
| `analyze_emr_cluster` | Run full utilization analysis | EMR + CloudWatch |
| `get_cluster_details` | Get cluster configuration | `emr:DescribeCluster` |
| `get_cluster_steps` | List steps from a cluster | `emr:ListSteps` |
| `check_cluster_status` | Check cluster state | `emr:DescribeCluster` |
| `check_step_status` | Check step completion | `emr:DescribeStep` |
| `calculate_cost` | Compute cost comparison | Pricing data (local) |

### Write Tools (Require human approval)

| Tool | Description | AWS API |
|------|-------------|---------|
| `create_emr_cluster` | Create new cluster with recommended specs | `emr:RunJobFlow` |
| `submit_emr_steps` | Submit steps to cluster | `emr:AddJobFlowSteps` |
| `terminate_cluster` | Terminate test cluster | `emr:TerminateJobFlows` |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Yes | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | Yes | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT` | Yes | Model deployment name (e.g., gpt-4o) |
| `AZURE_OPENAI_API_VERSION` | Yes | API version (e.g., 2024-10-21) |
| `AWS_REGION` | Yes | AWS region (default: us-east-1) |
| `AWS_DEFAULT_REGION` | Yes | AWS region for boto3 |
| `EMR_EC2_KEY_PAIR` | No | EC2 key pair for EMR clusters |
| `EMR_SUBNET_ID` | No | Subnet for EMR clusters |
| `EMR_LOG_URI` | No | S3 path for EMR logs |
| `MAX_CLUSTER_INSTANCES` | No | Safety limit for instance count (default: 20) |
| `AUTO_TERMINATE_MINUTES` | No | Auto-terminate test cluster after N minutes (default: 120) |

## AWS IAM Permissions

### Read Permissions (Analysis)

| Permission | Purpose |
|------------|---------|
| `elasticmapreduce:ListClusters` | List EMR clusters |
| `elasticmapreduce:DescribeCluster` | Get cluster details |
| `elasticmapreduce:ListInstanceGroups` | Get instance configuration |
| `elasticmapreduce:ListInstances` | Get EC2 instance IDs |
| `elasticmapreduce:ListSteps` | Get cluster steps |
| `elasticmapreduce:DescribeStep` | Get step details |
| `ec2:DescribeInstances` | Get instance details |
| `cloudwatch:GetMetricStatistics` | Fetch utilization metrics |

### Write Permissions (Cluster Operations)

| Permission | Purpose |
|------------|---------|
| `elasticmapreduce:RunJobFlow` | Create new EMR cluster |
| `elasticmapreduce:AddJobFlowSteps` | Submit steps to cluster |
| `elasticmapreduce:TerminateJobFlows` | Terminate test cluster |
| `iam:PassRole` | Assign EMR service/instance roles |

## Safety Guardrails

| Guardrail | Description | Default |
|-----------|-------------|---------|
| Human approval required | Agent cannot create clusters without explicit user approval | Always on |
| Max instance count | Upper limit on instances in test cluster | 20 |
| Auto-terminate | Test cluster auto-terminates after timeout | 120 minutes |
| Cost ceiling | Agent warns if estimated test cost exceeds threshold | $50 |
| Step validation | Agent validates steps before submission | On |
| Cleanup on failure | If any step fails, terminate test cluster | On |

## Analysis Thresholds

Same thresholds as COST_UI (EMR Cost Optimizer):

| Status | Average Utilization | Peak (P95) | Agent Action |
|--------|---------------------|------------|-------------|
| Heavily Oversized | < 25% | < 35% | Recommend aggressive downsizing |
| Moderately Oversized | < 50% | < 60% | Recommend moderate downsizing |
| Right-Sized | < 70% | < 80% | Report no action needed |
| Undersized | ≥ 70% | ≥ 80% | Report potential upsizing |

## Running the Application

### Prerequisites

- Python 3.10+
- AWS credentials configured (`~/.aws/credentials`)
- Azure OpenAI access (endpoint, key, deployment)
- AWS IAM permissions (read + write as listed above)
- EMR-compatible VPC/subnet/security group in target region

### Installation

```bash
git clone https://github.com/GA3773/COST_AGENT_AWS.git
cd COST_AGENT_AWS
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Azure OpenAI and AWS settings
```

### Start Server

```bash
python app.py
```

Access at: http://localhost:5001

## Relationship to COST_UI

This project builds upon the analysis logic from [COST_UI](https://github.com/GA3773/COST_UI):

| Component | COST_UI | COST_AGENT_AWS |
|-----------|---------|----------------|
| Analysis | Manual trigger via UI | Agent-driven |
| Recommendations | Display only | Agent acts on them |
| Cluster creation | Not supported | Agent creates test cluster |
| Step replication | Not supported | Agent copies and reruns steps |
| Comparison | Not supported | Agent compares runtimes and costs |
| Reporting | Modal in UI | LLM-generated comprehensive report |

The `services/` directory reuses the analysis patterns from COST_UI (EMR service, CloudWatch service, pricing service, analyzer service).

## Contributing

1. Follow existing code patterns from COST_UI
2. All write operations (cluster creation, step submission) must go through the approval flow
3. Add safety guardrails for any new tools that modify AWS resources
4. Test with small clusters first (2-3 nodes) before production workloads
