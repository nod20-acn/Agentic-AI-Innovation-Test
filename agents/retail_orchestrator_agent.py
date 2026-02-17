import json
import os
import re

from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient

from .product_agent import get_product_response
from .insurance_agent import get_insurance_response
from .utilities import (
    create_azure_credential,
    map_state_to_phase,
    validate_product_context,
    validate_insurance_context,
    extract_requirements,
    extract_product_details,
    get_agent_icon,
)


load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=False)


def initialize_orchestrator_agent(project_client=None):
    """Initialize retail backend orchestrator agent."""
    tenant_id = os.getenv("AZURE_TENANT_ID")
    # Default fallback matches the example/project naming used in deployment
    orchestrator_name = os.getenv("AGENT_ORCHESTRATOR", "buybuddy-orchestrator")

    local_project_client = project_client
    if local_project_client is None:
        endpoint = os.getenv("AZURE_AIPROJECT_ENDPOINT")
        if not endpoint:
            raise ValueError("Missing required environment variable: AZURE_AIPROJECT_ENDPOINT")
        credential = create_azure_credential(tenant_id)
        local_project_client = AIProjectClient(endpoint=endpoint, credential=credential)

    try:
        orchestrator_agent = local_project_client.agents.get(agent_name=orchestrator_name)
    except Exception:
        orchestrator_agent = None

    orchestrator_client = local_project_client.get_openai_client()
    return orchestrator_agent, orchestrator_client


def get_orchestrator_response(customer_packet, orchestrator_agent, orchestrator_client):
    """Call orchestrator agent with JSON packet and return raw response text."""
    if not orchestrator_agent:
        return None

    payload = json.dumps(customer_packet, ensure_ascii=False)
    response = orchestrator_client.responses.create(
        input=[
            {
                "role": "user",
                "content": payload,
            }
        ],
        extra_body={"agent": {"name": orchestrator_agent.name, "type": "agent_reference"}},
    )
    return response.output_text


def _extract_json_dict(raw_response):
    if not raw_response:
        return None

    response_text = str(raw_response).strip()

    fenced_start = response_text.lower().find("```json")
    if fenced_start != -1:
        fenced_end = response_text.find("```", fenced_start + 7)
        if fenced_end != -1:
            response_text = response_text[fenced_start + 7:fenced_end].strip()

    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    first_brace = response_text.find("{")
    last_brace = response_text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = response_text[first_brace:last_brace + 1].strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def _sanitize_customer_response(text):
    message = str(text or "").strip()
    if not message:
        return ""

    message = re.sub(r"\s+\d+\s*$", "", message).strip()

    has_large_json_blob = (
        ("{" in message and "}" in message and len(message) > 250)
        or "recommended_models" in message
        or "coverage_options" in message
    )

    if has_large_json_blob:
        prefix = message.split("Product update:")[0].strip()
        if prefix:
            return prefix
        return "I reviewed your request and coordinated with our specialists."

    return message


def _normalize_specialist_entries(entries):
    normalized = []
    if not isinstance(entries, list):
        return normalized

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        agent_name = entry.get("agent", "System")
        response_text = entry.get("response", "")

        if "FridgeBuddy" in agent_name:
            css_class = "product-message"
            icon = get_agent_icon("fridgebuddy")
            response_text = _simplify_fridge_response(response_text)
        elif "InsuranceBuddy" in agent_name:
            css_class = "insurance-message"
            icon = get_agent_icon("insurancebuddy")
            response_text = _simplify_insurance_response(response_text)
        elif agent_name == "System":
            css_class = "system-message"
            icon = "⚠️"
        else:
            css_class = entry.get("css_class", "chat-message")
            icon = entry.get("icon", "💬")

        normalized.append(
            {
                "agent": agent_name,
                "response": response_text,
                "raw_response": str(entry.get("raw_response", entry.get("response", ""))),
                "icon": entry.get("icon", icon),
                "css_class": entry.get("css_class", css_class),
                "exchange_format": entry.get("exchange_format", "json"),
            }
        )

    return normalized


def _is_valid_orchestrator_result(payload):
    return (
        isinstance(payload, dict)
        and payload.get("message_type") == "orchestrator_result"
        and isinstance(payload.get("state"), dict)
    )


def _build_agent_result_payload(parsed_result, fallback_state):
    state = parsed_result.get("state") if isinstance(parsed_result.get("state"), dict) else (fallback_state or {})

    specialist_responses = _normalize_specialist_entries(parsed_result.get("specialist_responses", []))

    inventory_check = parsed_result.get("inventory_check")
    if not isinstance(inventory_check, dict):
        inventory_check = None

    customer_response = _sanitize_customer_response(parsed_result.get("customer_response"))
    if not customer_response:
        customer_response = "I’ve reviewed your request and coordinated with specialists."
    customer_response = _build_user_summary(customer_response, specialist_responses)

    return {
        "schema_version": "1.0",
        "message_type": "orchestrator_result",
        "source_agent": "retail_orchestrator_agent",
        "target_agent": "retail_agent",
        "state": state,
        "routing": parsed_result.get("routing", state.get("routing", "none")),
        "inventory_check": inventory_check,
        "specialist_responses": specialist_responses,
        "customer_response": customer_response,
        "exchange_format": "json",
    }


def _simplify_fridge_response(raw_response):
    parsed = _extract_json_dict(raw_response)
    if not parsed:
        response_text = str(raw_response or "").strip()
        if (
            "recommended_models" in response_text
            or ("{" in response_text and "}" in response_text and len(response_text) > 180)
        ):
            return "I reviewed FridgeBuddy results and shortlisted suitable models. I can narrow this to one best option based on your preferred style."
        return response_text

    recommendations = parsed.get("recommended_models", [])
    reason = parsed.get("reasoning") or parsed.get("summary") or parsed.get("notes")

    if not isinstance(recommendations, list) or not recommendations:
        if reason:
            return f"I reviewed FridgeBuddy recommendations. {reason}"
        return "FridgeBuddy completed analysis, but no clear recommendation was returned."

    top_models = []
    for model in recommendations[:2]:
        if not isinstance(model, dict):
            continue
        name = model.get("model_name") or model.get("model_number") or model.get("name") or "Liebherr model"
        price = model.get("price") or model.get("price_range")
        features = model.get("features") if isinstance(model.get("features"), list) else []

        line = name
        if price:
            line += f" ({price})"
        if features:
            line += f" - {', '.join([str(item) for item in features[:3]])}"
        top_models.append(line)

    if not top_models:
        return "FridgeBuddy analysis is complete. Please share if you want another recommendation run."

    summary = "Top options: " + "; ".join(top_models) + "."
    if reason:
        summary += f" Why these: {reason}"
    return summary


def _simplify_insurance_response(raw_response):
    parsed = _extract_json_dict(raw_response)
    if not parsed:
        response_text = str(raw_response or "").strip()
        if (
            "coverage_options" in response_text
            or ("{" in response_text and "}" in response_text and len(response_text) > 180)
        ):
            return "InsuranceBuddy completed evaluation. I can share the best coverage option and next step."
        return response_text

    status = str(parsed.get("status", "")).lower()
    if status == "incomplete":
        missing = parsed.get("missing_fields", [])
        missing_text = ", ".join(missing) if isinstance(missing, list) and missing else "some details"
        return f"InsuranceBuddy needs a few more details: {missing_text}."

    if status == "declined":
        risk = parsed.get("risk_assessment", {}) if isinstance(parsed.get("risk_assessment"), dict) else {}
        reason = risk.get("justification")
        if reason:
            return f"Insurance is not available for this configuration. Reason: {reason}"
        return "Insurance is not available for this configuration."

    if status == "approved":
        options = parsed.get("coverage_options", [])
        option_parts = []
        if isinstance(options, list):
            for option in options[:2]:
                if not isinstance(option, dict):
                    continue
                name = option.get("bundle_name", "Coverage")
                monthly = option.get("monthly_premium")
                duration = option.get("duration")
                text = name
                if monthly:
                    text += f" ({monthly}/month)"
                if duration:
                    text += f" for {duration}"
                option_parts.append(text)

        summary = "Insurance is available"
        if option_parts:
            summary += ": " + "; ".join(option_parts) + "."
        else:
            summary += "."
        return summary

    return str(raw_response)


def _build_product_payload(customer_packet):
    state = customer_packet.get("routing_context", {}).get("state", {})
    requirements = customer_packet.get("intake", {}).get("extracted_requirements", {})

    return {
        "schema_version": "1.0",
        "message_type": "specialist_request",
        "source_agent": "retail_orchestrator_agent",
        "target_agent": "fridgebuddy",
        "requested_action": "provide_liebherr_recommendations",
        "customer_context": {
            "latest_user_input": customer_packet.get("conversation", {}).get("latest_user_input"),
            "requirements": requirements,
        },
        "state_context": state,
        "constraints": {
            "max_recommendations": 3,
            "response_format": "json",
        },
    }


def _build_insurance_payload(customer_packet, conversation_history):
    requirements = extract_requirements(conversation_history)
    product_details = extract_product_details(conversation_history)

    return {
        "schema_version": "1.0",
        "message_type": "specialist_request",
        "source_agent": "retail_orchestrator_agent",
        "target_agent": "insurancebuddy",
        "requested_action": "provide_insurance_quote",
        "product_context": {
            "manufacturer": "Liebherr",
            "product_type": "Refrigerator",
            "product_model": product_details.get("product_model"),
            "key_features": product_details.get("key_features") or requirements.get("features") or ["standard configuration"],
            "configuration_class": "Standard",
        },
        "pricing_context": {
            "purchase_price": requirements.get("budget", "TBD"),
        },
        "constraints": {
            "response_format": "json",
        },
    }


def _build_user_summary(base_text, specialist_responses):
    base_message = str(base_text or "").strip()
    if not specialist_responses:
        return base_message

    summary_parts = []
    for item in specialist_responses:
        if not isinstance(item, dict):
            continue

        agent_name = str(item.get("agent", "")).strip()
        response_text = str(item.get("response", "")).strip()
        if not response_text:
            continue

        if "FridgeBuddy" in agent_name:
            summary_parts.append(f"Product update: {response_text}")
        elif "InsuranceBuddy" in agent_name:
            summary_parts.append(f"Insurance update: {response_text}")
        elif agent_name == "System":
            summary_parts.append(response_text)

    if summary_parts:
        return " ".join(summary_parts)

    return "I consulted our specialists and prepared an updated recommendation."


def orchestrate_customer_packet(
    customer_packet,
    orchestrator_agent=None,
    orchestrator_client=None,
    product_agent=None,
    insurance_agent=None,
    conversation_history=None,
    iteration_counts=None,
):
    """
    Backend retail orchestrator consumes customer-facing JSON packet,
    coordinates with downstream agents via JSON payloads, and returns a
    JSON response package for customer-facing output.
    """
    conversation_history = conversation_history or []
    iteration_counts = iteration_counts or {}

    state_from_packet = customer_packet.get("routing_context", {}).get("state", {})

    if orchestrator_agent and orchestrator_client:
        try:
            raw_orchestrator_response = get_orchestrator_response(
                customer_packet,
                orchestrator_agent,
                orchestrator_client,
            )
            parsed_orchestrator_response = _extract_json_dict(raw_orchestrator_response)
            if _is_valid_orchestrator_result(parsed_orchestrator_response):
                return _build_agent_result_payload(parsed_orchestrator_response, state_from_packet)
        except Exception:
            pass

    state = state_from_packet
    routing = customer_packet.get("routing_context", {}).get("routing_hint", "none")
    user_input = customer_packet.get("conversation", {}).get("latest_user_input", "")
    base_text = customer_packet.get("intake", {}).get("customer_visible_draft", "")

    response_lower = base_text.lower()
    user_input_lower = user_input.lower()

    if routing == "none":
        explicit_product_request = any(
            term in user_input_lower
            for term in [
                "fridgebuddy",
                "product_agent",
                "product agent",
                "liebherr specialist",
                "refer to product",
                "refer to fridgebuddy",
            ]
        )
        if explicit_product_request:
            routing = "product_agent"

        product_status = state.get("product_status", "collecting")
        overall_status = state.get("overall_status", "intake")

        if (
            routing == "none"
            and overall_status != "intake"
            and product_status in ["searching", "proposed"]
            and validate_product_context(conversation_history)
            and any(kw in response_lower for kw in ["liebherr", "fridgebuddy", "product specialist", "external catalog"])
        ):
            routing = "product_agent"
        elif (
            routing == "none"
            and product_status == "agreed"
            and any(kw in response_lower for kw in ["ergo", "insurancebuddy", "insurance offer"])
        ):
            routing = "ergo_agent"

    inventory_check_result = None
    if state.get("inventory_checked"):
        inventory_check_result = {
            "checked": True,
            "phase": map_state_to_phase(state),
            "summary": "Internal inventory check performed",
            "details": "Checked MediaMarktSaturn internal systems for product availability.",
            "first_check": True,
        }

    specialist_responses = []

    if routing == "product_agent" and product_agent:
        if iteration_counts.get("product_agent_calls", 0) >= 3:
            specialist_responses.append(
                {
                    "agent": "System",
                    "response": "⚠️ Maximum FridgeBuddy iterations reached. Using best available information.",
                    "icon": "⚠️",
                    "css_class": "system-message",
                    "exchange_format": "json",
                }
            )
        else:
            try:
                payload = _build_product_payload(customer_packet)
                prod_response = get_product_response(
                    json.dumps(payload, ensure_ascii=False),
                    product_agent[0],
                    product_agent[1],
                )
                specialist_responses.append(
                    {
                        "agent": "FridgeBuddy (Liebherr Specialist)",
                        "response": _simplify_fridge_response(prod_response),
                        "raw_response": str(prod_response),
                        "icon": get_agent_icon("fridgebuddy"),
                        "css_class": "product-message",
                        "exchange_format": "json",
                    }
                )
                iteration_counts["product_agent_calls"] = iteration_counts.get("product_agent_calls", 0) + 1
            except Exception as ex:
                specialist_responses.append(
                    {
                        "agent": "System",
                        "response": f"⚠️ FridgeBuddy unavailable: {str(ex)}",
                        "icon": "⚠️",
                        "css_class": "system-message",
                        "exchange_format": "json",
                    }
                )

    if routing == "ergo_agent" and insurance_agent:
        is_valid, error_msg = validate_insurance_context(state, conversation_history)
        if not is_valid:
            specialist_responses.append(
                {
                    "agent": "System",
                    "response": f"⚠️ Cannot route to InsuranceBuddy: {error_msg}",
                    "icon": "⚠️",
                    "css_class": "system-message",
                    "exchange_format": "json",
                }
            )
        elif iteration_counts.get("insurance_agent_calls", 0) >= 3:
            specialist_responses.append(
                {
                    "agent": "System",
                    "response": "⚠️ Maximum InsuranceBuddy iterations reached.",
                    "icon": "⚠️",
                    "css_class": "system-message",
                    "exchange_format": "json",
                }
            )
        else:
            try:
                payload = _build_insurance_payload(customer_packet, conversation_history)
                insurance_response = get_insurance_response(
                    payload,
                    insurance_agent[0],
                    insurance_agent[1],
                )
                specialist_responses.append(
                    {
                        "agent": "InsuranceBuddy (ERGO Specialist)",
                        "response": _simplify_insurance_response(insurance_response),
                        "raw_response": str(insurance_response),
                        "icon": get_agent_icon("insurancebuddy"),
                        "css_class": "insurance-message",
                        "exchange_format": "json",
                    }
                )
                iteration_counts["insurance_agent_calls"] = iteration_counts.get("insurance_agent_calls", 0) + 1
            except Exception as ex:
                specialist_responses.append(
                    {
                        "agent": "System",
                        "response": f"⚠️ InsuranceBuddy unavailable: {str(ex)}",
                        "icon": "⚠️",
                        "css_class": "system-message",
                        "exchange_format": "json",
                    }
                )

    result_payload = {
        "schema_version": "1.0",
        "message_type": "orchestrator_result",
        "source_agent": "retail_orchestrator_agent",
        "target_agent": "retail_agent",
        "state": state,
        "routing": routing,
        "inventory_check": inventory_check_result,
        "specialist_responses": specialist_responses,
        "customer_response": _build_user_summary(base_text, specialist_responses),
        "exchange_format": "json",
    }

    return result_payload
