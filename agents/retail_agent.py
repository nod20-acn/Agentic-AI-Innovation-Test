import json
import os
from datetime import datetime
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient

from .utilities import create_azure_credential, parse_retail_state, strip_retail_metadata, extract_requirements


load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=False)


def initialize_customer_facing_agent():
    """Initialize retail_agent customer-facing agent."""
    endpoint = os.getenv("AZURE_AIPROJECT_ENDPOINT")
    tenant_id = os.getenv("AZURE_TENANT_ID")

    if not endpoint:
        raise ValueError("Missing required environment variable: AZURE_AIPROJECT_ENDPOINT")

    credential = create_azure_credential(tenant_id)
    project_client = AIProjectClient(
        endpoint=endpoint,
        credential=credential,
    )

    # Use environment-configured agent name (fallback aligns with `.env.example`)
    agent_name = os.getenv("AGENT_RETAIL", "retail-agent")
    try:
        agent = project_client.agents.get(agent_name=agent_name)
    except Exception:
        agent = None

    openai_client = project_client.get_openai_client()
    return agent, openai_client, project_client


def get_customer_facing_response(user_input, agent, openai_client, conversation_history=None):
    """Get response from customer-facing retail_agent."""
    if not agent:
        return "retail_agent: Hello! I'm your MediaMarktSaturn sales assistant. How can I help you find the perfect product today?"

    try:
        messages = []
        if conversation_history:
            for msg in conversation_history[-10:]:
                role = "user" if msg.get("role") == "user" else "assistant"
                messages.append({"role": role, "content": msg.get("content", "")})

        messages.append({"role": "user", "content": user_input})

        response = openai_client.responses.create(
            input=messages,
            extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
        )
        return response.output_text
    except Exception as exc:
        return f"retail_agent Error: {str(exc)}"


def _build_recent_history_excerpt(conversation_history, limit=6):
    history = []
    if not conversation_history:
        return history

    for msg in conversation_history[-limit:]:
        history.append(
            {
                "role": msg.get("role", "agent"),
                "sender": msg.get("sender", "Unknown"),
                "content": msg.get("content", ""),
            }
        )

    return history


def collect_customer_input_packet(
    user_input,
    customer_agent,
    customer_client,
    conversation_history=None,
    iteration_counts=None,
):
    """
    Customer-facing retail_agent gathers customer input and emits a JSON packet
    for backend orchestrator processing.
    """
    raw_response = get_customer_facing_response(
        user_input,
        customer_agent,
        customer_client,
        conversation_history,
    )

    state = parse_retail_state(raw_response)
    customer_message = strip_retail_metadata(raw_response)

    synthetic_history = list(conversation_history or []) + [{"content": user_input}]
    requirements = extract_requirements(synthetic_history)

    packet = {
        "schema_version": "1.0",
        "message_type": "customer_intake_packet",
        "source_agent": "retail_agent",
        "target_agent": "retail_orchestrator_agent",
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "conversation": {
            "latest_user_input": user_input,
            "recent_history": _build_recent_history_excerpt(conversation_history),
        },
        "intake": {
            "customer_visible_draft": customer_message,
            "extracted_requirements": requirements,
        },
        "routing_context": {
            "state": state,
            "routing_hint": state.get("routing", "none"),
            "iteration_counts": iteration_counts or {},
        },
    }

    return packet


