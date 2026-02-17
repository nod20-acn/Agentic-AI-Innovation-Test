import streamlit as st
import os
from dotenv import load_dotenv

# Load environment variables early so module-level `os.getenv` (and imported agent modules)
# see the values immediately when they are imported.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)

import json
import re
from datetime import datetime
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER

from agents.retail_agent import initialize_customer_facing_agent, collect_customer_input_packet
from agents.retail_orchestrator_agent import initialize_orchestrator_agent, orchestrate_customer_packet
from agents.utilities import map_state_to_phase
from agents.product_agent import initialize_product_agent
from agents.insurance_agent import initialize_insurance_agent

IS_APP_SERVICE = bool(os.getenv("WEBSITE_SITE_NAME") or os.getenv("WEBSITE_INSTANCE_ID"))

REQUIRED_ENV_VARS = [
    "AZURE_AIPROJECT_ENDPOINT",
    "AZURE_TENANT_ID",
    "AGENT_RETAIL",
    "AGENT_ORCHESTRATOR",
    "AGENT_PRODUCT",
    "AGENT_INSURANCE",
]


def get_missing_required_env_vars():
    return [key for key in REQUIRED_ENV_VARS if not os.getenv(key)]

# Helper function to get icon (image or emoji fallback)
def get_agent_icon(agent_name):
    """Get base64 encoded image for agent icon or return emoji fallback"""
    import base64
    icon_mapping = {
        'retail_agent': ('buybuddy.png', '🛒'),
        'fridgebuddy': ('fridgebuddy.png', '📦'),
        'insurancebuddy': ('insurancebuddy.png', '🛡️'),
        'customer': (None, '👤')
    }
    
    icon_file, fallback_emoji = icon_mapping.get(agent_name.lower(), (None, '💬'))
    
    if icon_file:
        icon_path = os.path.join(os.path.dirname(__file__), 'assets', icon_file)
        if os.path.exists(icon_path):
            try:
                with open(icon_path, 'rb') as f:
                    img_bytes = f.read()
                img_base64 = base64.b64encode(img_bytes).decode()
                return f'<img src="data:image/png;base64,{img_base64}" class="chat-icon-img" alt="{agent_name}">'
            except:
                return fallback_emoji
    return fallback_emoji

def format_insurance_response_for_ui(raw_response):
    """Convert InsuranceBuddy JSON output into user-friendly plain text for UI display."""
    if not raw_response:
        return ""

    response_text = str(raw_response).strip()
    parsed = None

    # Handle fenced json blocks
    fenced_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text, re.IGNORECASE)
    if fenced_match:
        response_text = fenced_match.group(1).strip()

    try:
        parsed = json.loads(response_text)
    except Exception:
        return raw_response

    if not isinstance(parsed, dict):
        return raw_response

    status = str(parsed.get("status", "")).lower()

    if status == "incomplete":
        missing = parsed.get("missing_fields", [])
        missing_text = ", ".join(missing) if isinstance(missing, list) and missing else "some details"
        return (
            f"I need a few more details to prepare your insurance quote: {missing_text}. "
            "Please share these and I’ll continue right away."
        )

    if status == "declined":
        reason = parsed.get("risk_assessment", {}).get("justification") if isinstance(parsed.get("risk_assessment"), dict) else None
        if reason:
            return f"Insurance is currently not available for this configuration. Reason: {reason}"
        return "Insurance is currently not available for this configuration."

    if status == "approved":
        options = parsed.get("coverage_options", [])
        recommendations = parsed.get("recommendations")
        next_steps = parsed.get("next_steps")

        option_summaries = []
        if isinstance(options, list):
            for option in options[:2]:
                if not isinstance(option, dict):
                    continue
                bundle = option.get("bundle_name", "Coverage")
                monthly = option.get("monthly_premium")
                duration = option.get("duration")
                parts = [bundle]
                if monthly:
                    parts.append(f"{monthly}/month")
                if duration:
                    parts.append(str(duration))
                option_summaries.append(" - ".join(parts))

        summary_lines = ["Great news — your product is eligible for insurance."]
        if option_summaries:
            summary_lines.append("Available options: " + "; ".join(option_summaries) + ".")
        if recommendations:
            summary_lines.append(f"Recommendation: {recommendations}")
        if next_steps:
            summary_lines.append(f"Next step: {next_steps}")

        return " ".join(summary_lines)

    return raw_response

def format_product_response_for_ui(raw_response):
    """Convert FridgeBuddy JSON output into user-friendly plain text for UI display."""
    if not raw_response:
        return ""

    response_text = str(raw_response).strip()
    parsed = None

    fenced_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text, re.IGNORECASE)
    if fenced_match:
        response_text = fenced_match.group(1).strip()

    try:
        parsed = json.loads(response_text)
    except Exception:
        return raw_response

    if not isinstance(parsed, dict):
        return raw_response

    recommendations = parsed.get("recommended_models", [])
    reason = parsed.get("reasoning") or parsed.get("summary") or parsed.get("notes")

    if not isinstance(recommendations, list) or not recommendations:
        if reason:
            return f"I checked the Liebherr catalog. {reason}"
        return raw_response

    top_models = []
    for model in recommendations[:3]:
        if not isinstance(model, dict):
            continue

        name = (
            model.get("model_name")
            or model.get("model_number")
            or model.get("name")
            or "Liebherr model"
        )
        price = model.get("price") or model.get("price_range")
        features = model.get("features") if isinstance(model.get("features"), list) else []

        line = name
        if price:
            line += f" ({price})"
        if features:
            line += f" — {', '.join([str(f) for f in features[:3]])}"
        top_models.append(line)

    if not top_models:
        return raw_response

    summary_lines = ["Here are the top Liebherr options from FridgeBuddy:"]
    summary_lines.extend([f"• {item}" for item in top_models])
    if reason:
        summary_lines.append(f"Why these: {reason}")

    return "<br/>".join(summary_lines)

# Webpage configurations
st.set_page_config(page_title="Agentic AI System Interface", page_icon="🛒")

# Define custom CSS for styling
custom_css = """
<style>
/* Title with icon styling */
.title-with-icon {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 1rem;
}

.title-with-icon h1 {
    margin: 0;
    font-size: 2.5rem;
    font-weight: 600;
}

.title-icon {
    width: 60px;
    height: 60px;
    animation: float 3s ease-in-out infinite;
}

.title-icon img {
    width: 100%;
    height: 100%;
    object-fit: contain;
}

.title-icon-emoji {
    font-size: 60px;
    line-height: 60px;
}

@keyframes float {
    0%, 100% { transform: translateY(0px); }
    50% { transform: translateY(-10px); }
}

/* Chat message styling */
.chat-message {
    padding: 12px;
    margin: 8px 0;
    border-radius: 10px;
    border-left: 4px solid #ddd;
    color: #FFFFFF;
}

.user-message {
    border-left: none;
    border-right: 4px solid #2196F3;
    margin-left: auto;
    margin-right: 0;
    max-width: 80%;
    text-align: right;
}

.user-message .sender-name {
    justify-content: flex-end;
}

.user-message .timestamp {
    margin-left: 8px;
}

.retail-message {
    border-left-color: #FF6B35;
}

.product-message {
    border-left-color: #FFC107;
}

.insurance-message {
    border-left-color: #F44336;
}

.sender-name {
    font-weight: bold;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    color: #FFFFFF;
}

.timestamp {
    font-size: 0.75em;
    color: #BDBDBD;
    margin-left: auto;
}

.message-content {
    margin-top: 4px;
    line-height: 1.6;
    color: #E0E0E0;
}

.specialist-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.8em;
    margin-left: 8px;
    background-color: #FFF9C4;
    color: #F57F17;
}

/* Chat icon image styling */
.chat-icon-img {
    width: 24px;
    height: 24px;
    object-fit: contain;
    vertical-align: middle;
    margin-right: 4px;
}
</style>
"""

# Apply the custom CSS
st.markdown(custom_css, unsafe_allow_html=True)

# Display title with icon
icon_path = os.path.join(os.path.dirname(__file__), 'assets', 'buybuddy.png')
if os.path.exists(icon_path):
    # Read and encode the image for display
    import base64
    with open(icon_path, 'rb') as f:
        img_bytes = f.read()
    img_base64 = base64.b64encode(img_bytes).decode()
    icon_html = f'<div class="title-icon"><img src="data:image/png;base64,{img_base64}" alt="BuyBuddy"></div>'
else:
    # Fallback to emoji if image not found
    icon_html = '<div class="title-icon title-icon-emoji">🛒</div>'

st.markdown(f'''
<div class="title-with-icon">
    {icon_html}
    <h1>BuyBuddy - Customer Service</h1>
</div>
''', unsafe_allow_html=True)

st.write("Welcome! I'm your BuyBuddy. Ask me anything, and I'll coordinate with specialized teams when needed.")

# Initialize all agents
@st.cache_resource
def initialize_all_agents():
    """Initialize customer-facing retail_agent and specialized agents"""
    agents = {}
    clients = {}
    errors = {}
    
    try:
        # Initialize Customer-facing retail_agent
        try:
            customer_agent, customer_client, project_client = initialize_customer_facing_agent()
            agents['customer'] = customer_agent
            clients['customer'] = customer_client
            clients['project'] = project_client
        except Exception as e:
            agents['customer'] = None
            errors['customer'] = str(e)

        # Initialize Backend Orchestrator retail_agent
        try:
            orchestrator_agent, orchestrator_client = initialize_orchestrator_agent(clients.get('project'))
            agents['orchestrator'] = orchestrator_agent
            clients['orchestrator'] = orchestrator_client
        except Exception as e:
            agents['orchestrator'] = None
            errors['orchestrator'] = str(e)
        
        # Initialize Product Agent (specialist)
        try:
            mfg_agent, mfg_client = initialize_product_agent()
            agents['product'] = mfg_agent
            clients['product'] = mfg_client
        except Exception as e:
            agents['product'] = None
            errors['product'] = str(e)
        
        # Initialize Insurance Agent (specialist)
        try:
            insurance_agent, insurance_client = initialize_insurance_agent()
            agents['insurance'] = insurance_agent
            clients['insurance'] = insurance_client
        except Exception as e:
            agents['insurance'] = None
            errors['insurance'] = str(e)
            
    except Exception as e:
        errors['main'] = str(e)
    
    return agents, clients, errors

def get_agent_runtime(force_initialize=False):
    if "agent_runtime" not in st.session_state:
        st.session_state.agent_runtime = {
            "agents": {},
            "clients": {},
            "errors": {},
            "initialized": False,
        }

    runtime = st.session_state.agent_runtime
    missing_env_vars = get_missing_required_env_vars()

    if missing_env_vars:
        runtime["errors"] = {
            "env": "Missing required environment variables: " + ", ".join(missing_env_vars)
        }
        runtime["initialized"] = False
        return runtime["agents"], runtime["clients"], runtime["errors"], runtime["initialized"]

    should_initialize = force_initialize or (not runtime["initialized"] and not IS_APP_SERVICE)

    if should_initialize:
        agents_local, clients_local, errors_local = initialize_all_agents()
        runtime["agents"] = agents_local
        runtime["clients"] = clients_local
        runtime["errors"] = errors_local
        runtime["initialized"] = True

    return runtime["agents"], runtime["clients"], runtime["errors"], runtime["initialized"]


agents, clients, init_errors, agents_initialized = get_agent_runtime()

def determine_current_phase(conversation_history, last_state=None):
    """
    Determine the current phase based on retail_agent state or conversation history.
    
    PHASES:
    1. Customer Intake - Gathering requirements
    2. Inventory Decision - Checking internal stock
    3. Product Agreement - Validating product selection
    4. Insurance - Offering protection plans
    5. Final Consolidation - Generating quotation
    
    Args:
        conversation_history: Previous messages
        last_state: Parsed state from retail_agent last response
    
    Returns: int (1-5)
    """
    # Prefer using retail_agent's actual state if available
    if last_state:
        return map_state_to_phase(last_state)
    
    # Fallback to keyword-based detection
    if not conversation_history or len(conversation_history) <= 1:
        return 1  # Starting phase
    
    # Analyze last few messages for phase indicators
    recent_messages = conversation_history[-5:] if len(conversation_history) >= 5 else conversation_history
    conversation_text = " ".join([msg.get("content", "").lower() for msg in recent_messages])
    
    # Phase 5: Final consolidation keywords
    if any(kw in conversation_text for kw in ['quotation', 'final price', 'ready to checkout', 'confirm order', 'total cost']):
        return 5
    
    # Phase 4: Insurance keywords
    if any(kw in conversation_text for kw in ['insurance', 'warranty', 'protection plan', 'coverage', 'ergo']):
        return 4
    
    # Phase 3: Product agreement keywords
    if any(kw in conversation_text for kw in ['confirm', 'agreed', 'accept', 'this model', 'go with']):
        return 3
    
    # Phase 2: Inventory/product search keywords
    if any(kw in conversation_text for kw in ['product', 'fridge', 'looking for', 'need', 'stock', 'available', 'liebherr']):
        return 2
    
    # Default: Phase 1 (intake)
    return 1

# Initialize phase tracking and iteration counters in session state
if "current_phase" not in st.session_state:
    st.session_state.current_phase = 1

if "retail_state" not in st.session_state:
    st.session_state.retail_state = None

if "iteration_counts" not in st.session_state:
    st.session_state.iteration_counts = {
        'customer_clarifications': 0,
        'product_agent_calls': 0,
        'insurance_agent_calls': 0
    }

if "inventory_checked_once" not in st.session_state:
    st.session_state.inventory_checked_once = False

# Display initialization status in sidebar
with st.sidebar:
    # Phase Tracker - Compact view
    st.subheader("📋 Progress")
    
    # Update current phase based on retail_agent state or conversation
    current_phase = determine_current_phase(
        st.session_state.get('messages', []),
        st.session_state.get('retail_state')
    )
    st.session_state.current_phase = current_phase
    
    phases = [
        (1, "Intake"),
        (2, "Inventory"),
        (3, "Agreement"),
        (4, "Insurance"),
        (5, "Quotation")
    ]
    
    # Compact phase display
    phase_status = []
    for num, title in phases:
        if num < current_phase:
            phase_status.append(f"✅ {title}")
        elif num == current_phase:
            phase_status.append(f"▶️ **{title}**")
        else:
            phase_status.append(f"⏸️ {title}")
    
    st.markdown(" → ".join(phase_status))
    
    st.markdown("---")
    
    # Agent Status - Compact
    st.subheader("🤖 Agents")
    # Diagnostic: show whether a local .env file exists (helps root-cause "not loaded" cases)
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    st.caption(f".env present: {os.path.exists(_env_path)}")

    # Show credential selection/diagnostic (helps validate local vs webapp auth flows)
    cred_type = os.getenv("AZURE_CREDENTIAL_TYPE")
    if not cred_type:
        # reflect configuration that will be used (best-effort)
        if os.getenv("AZURE_MANAGED_IDENTITY_CLIENT_ID"):
            cred_type = f"user-assigned managed identity configured ({os.getenv('AZURE_MANAGED_IDENTITY_CLIENT_ID')})"
        else:
            cred_type = "DefaultAzureCredential (local or platform-managed)"
    st.caption(f"Credential: {cred_type}")

    missing_env_vars = get_missing_required_env_vars()
    if missing_env_vars:
        st.error("Missing App Settings: " + ", ".join(missing_env_vars))

    if not agents_initialized:
        st.info("Agents will initialize on first request.")
    elif 'main' not in init_errors:
        agent_icons = []
        if agents.get('customer'):
            agent_icons.append("🛒 BuyBuddy (Customer)")
        if agents.get('orchestrator'):
            agent_icons.append("⚙️ BuyBuddy (Orchestrator)")
        if agents.get('product'):
            agent_icons.append("📦 FridgeBuddy")
        if agents.get('insurance'):
            agent_icons.append("🛡️ InsuranceBuddy")
        
        if agent_icons:
            st.success(" | ".join(agent_icons))
        else:
            # More helpful diagnostics when agents exist as env vars but agent objects are None
            st.warning("No agents configured — agent names appear set but were not found in the Azure AI Project")
            st.markdown("**Configured agent names (from environment):**")
            st.write(f"- AGENT_RETAIL = `{os.getenv('AGENT_RETAIL')}` — {'found' if agents.get('customer') else 'NOT FOUND'}")
            st.write(f"- AGENT_ORCHESTRATOR = `{os.getenv('AGENT_ORCHESTRATOR')}` — {'found' if agents.get('orchestrator') else 'NOT FOUND'}")
            st.write(f"- AGENT_PRODUCT = `{os.getenv('AGENT_PRODUCT')}` — {'found' if agents.get('product') else 'NOT FOUND'}")
            st.write(f"- AGENT_INSURANCE = `{os.getenv('AGENT_INSURANCE')}` — {'found' if agents.get('insurance') else 'NOT FOUND'}")
            if init_errors:
                st.markdown("**Initialization errors (agent-level):**")
                for k, v in init_errors.items():
                    st.write(f"- {k}: {v}")
    else:
        st.error("Initialization failed")

def generate_quotation():
    """Generate a product quotation after all agents collaborate to finalize the offer"""
    global agents, clients, init_errors, agents_initialized

    missing_env_vars = get_missing_required_env_vars()
    if missing_env_vars:
        return None, "Missing required App Settings: " + ", ".join(missing_env_vars)

    if not agents_initialized:
        agents, clients, init_errors, agents_initialized = get_agent_runtime(force_initialize=True)

    if len(st.session_state.messages) <= 1:
        return None, "No conversation to generate quotation from. Start chatting with BuyBuddy first!"
    
    # Build conversation context
    conversation_text = ""
    for msg in st.session_state.messages:
        sender = msg.get("sender", "Unknown")
        content = msg.get("content", "")
        conversation_text += f"{sender}: {content}\n\n"
    
    # Create prompt for customer-facing retail_agent to generate quotation after collaboration
    quotation_prompt = f"""Based on the collaboration between BuyBuddy, FridgeBuddy, and InsuranceBuddy agents in the conversation below, create a formal product quotation for the customer.

The quotation should include:
1. Quotation Summary
2. Customer Requirements
3. Product Specifications & Offerings
4. Product Details (production timeline, capacity, delivery)
5. Pricing & Financial Terms
6. Terms & Conditions
7. Validity Period

Conversation History:
{conversation_text}

Generate a comprehensive, professional product quotation document that consolidates inputs from all three agents."""
    
    try:
        if agents.get('customer') and clients.get('customer'):
            response = clients['customer'].responses.create(
                input=[{"role": "user", "content": quotation_prompt}],
                extra_body={"agent": {"name": agents['customer'].name, "type": "agent_reference"}},
            )
            quotation_text = response.output_text
            return quotation_text, None
        else:
            return None, "BuyBuddy customer-facing agent is not available to generate quotation."
    except Exception as e:
        return None, f"Error generating quotation: {str(e)}"

def get_pdf_buffer(quotation_text):
    """Generate a PDF document from the quotation text"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                          rightMargin=72, leftMargin=72,
                          topMargin=72, bottomMargin=18)
    
    # Container for the 'Flowable' objects
    elements = []
    
    # Define styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Justify', alignment=TA_JUSTIFY))
    
    # Title style
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor='#1E88E5',
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    # Add title
    title = Paragraph("Product Quotation", title_style)
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Add date
    date_text = f"Generated on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
    elements.append(Paragraph(date_text, styles['Normal']))
    elements.append(Spacer(1, 12))
    
    # Process the quotation text
    paragraphs = quotation_text.split('\n\n')
    
    for para in paragraphs:
        if para.strip():
            # Check if it's a heading
            if any(keyword in para for keyword in ['Quotation Summary', 'Customer Requirements', 
                                                    'Product Specifications', 'Product Details',
                                                    'Pricing', 'Financial Terms', 'Terms & Conditions', 'Validity']):
                elements.append(Spacer(1, 12))
                elements.append(Paragraph(para.strip(), styles['Heading2']))
                elements.append(Spacer(1, 6))
            else:
                # Regular paragraph
                elements.append(Paragraph(para.strip(), styles['Justify']))
                elements.append(Spacer(1, 12))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer

def handle_customer_query(user_input, thinking_container):
    """
    Handle customer query through retail_agent
    retail_agent coordinates with specialists when needed
    """
    thinking_steps = []
    
    def add_thinking_step(step):
        thinking_steps.append(step)
        with thinking_container:
            st.markdown("\n\n".join(thinking_steps))
    
    add_thinking_step("🛒 BuyBuddy is analyzing your query...")
    
    # Check iteration limits
    if st.session_state.iteration_counts['customer_clarifications'] >= 10:
        add_thinking_step("⚠️ Maximum BuyBuddy conversation iterations reached (10/10)")
        add_thinking_step("💡 Please reset chat to start a new conversation.")
        return {
            'thinking': "\n\n".join(thinking_steps),
            'main_response': "I have reached the maximum of 10 conversation iterations for this session. Please click 'Reset Chat' to continue with a new request.",
            'inventory_check': None,
            'specialist_responses': []
        }
    
    try:
        global agents, clients, init_errors, agents_initialized

        missing_env_vars = get_missing_required_env_vars()
        if missing_env_vars:
            add_thinking_step("⚠️ Missing required App Settings")
            return {
                'thinking': "\n\n".join(thinking_steps),
                'main_response': "Configuration incomplete. Missing App Settings: " + ", ".join(missing_env_vars),
                'inventory_check': None,
                'specialist_responses': []
            }

        if not agents_initialized:
            add_thinking_step("🔄 Initializing agents...")
            agents, clients, init_errors, agents_initialized = get_agent_runtime(force_initialize=True)

        if agents.get('customer'):
            add_thinking_step("🧾 BuyBuddy is collecting your requirements...")

            customer_packet = collect_customer_input_packet(
                user_input,
                agents['customer'],
                clients['customer'],
                st.session_state.messages,
                st.session_state.iteration_counts,
            )

            add_thinking_step("⚙️ Orchestrator is coordinating specialist requests...")

            orchestrator_result = orchestrate_customer_packet(
                customer_packet,
                agents.get('orchestrator'),
                clients.get('orchestrator'),
                (agents.get('product'), clients.get('product')),
                (agents.get('insurance'), clients.get('insurance')),
                st.session_state.messages,
                st.session_state.iteration_counts,
            )

            result = {
                'main_response': orchestrator_result.get('customer_response', ''),
                'state': orchestrator_result.get('state'),
                'inventory_check': orchestrator_result.get('inventory_check'),
                'specialist_responses': orchestrator_result.get('specialist_responses', []),
            }
            
            # Store retail_agent state
            st.session_state.retail_state = result.get('state')
            
            # Increment customer clarification counter
            st.session_state.iteration_counts['customer_clarifications'] += 1
            
            # Show thinking steps based on what retail agent decided
            if result.get('inventory_check'):
                add_thinking_step("📦 Checked internal MediaMarktSaturn inventory")
            
            if result.get('specialist_responses'):
                specialists = [s['agent'] for s in result['specialist_responses']]
                add_thinking_step(f"🔍 Consulted with: {', '.join(specialists)}")
            
            add_thinking_step("💬 Response ready!")
            
            return {
                'thinking': "\n\n".join(thinking_steps),
                'main_response': result['main_response'],
                'inventory_check': result.get('inventory_check'),
                'specialist_responses': result['specialist_responses']
            }
        else:
            add_thinking_step("⚠️ BuyBuddy not fully configured, using demo mode")
            return {
                'thinking': "\n\n".join(thinking_steps),
                'main_response': f"BuyBuddy (Demo): Thank you for your query about '{user_input}'. I can help you with information, specifications, and coordinate with our product and insurance teams as needed.",
                'inventory_check': None,
                'specialist_responses': []
            }
    except Exception as e:
        add_thinking_step(f"❌ Error: {str(e)}")
        return {
            'thinking': "\n\n".join(thinking_steps),
            'main_response': f"I apologize, but I encountered an error: {str(e)}",
            'inventory_check': None,
            'specialist_responses': []
        }

# Initialize session state for chat messages
if "messages" not in st.session_state:
    st.session_state.messages = [{
        "role": "agent",
        "sender": "BuyBuddy",
        "content": "Hello! I'm your BuyBuddy. I can help you with information, specifications, features, and more. I can also coordinate with our FridgeBuddy and InsuranceBuddy teams when needed. How can I assist you today?",
        "timestamp": datetime.now().strftime("%I:%M %p"),
        "icon": get_agent_icon('retail_agent')
    }]

# Reset chat history button
if st.sidebar.button("🔄 Reset Chat"):
    st.session_state.messages = [{
        "role": "agent",
        "sender": "BuyBuddy",
        "content": "Hello! I'm your BuyBuddy. I can help you with information, specifications, features, and more. I can also coordinate with our FridgeBuddy and InsuranceBuddy teams when needed. How can I assist you today?",
        "timestamp": datetime.now().strftime("%I:%M %p"),
        "icon": get_agent_icon('retail_agent')
    }]
    # Reset all tracking flags
    st.session_state.inventory_checked_once = False
    st.session_state.iteration_counts = {
        'customer_clarifications': 0,
        'product_agent_calls': 0,
        'insurance_agent_calls': 0
    }
    st.session_state.retail_state = None
    st.session_state.current_phase = 1
    st.rerun()

# Show quotation section only in final phase (Phase 5)
if st.session_state.current_phase >= 5:
    st.sidebar.markdown("---")
    st.sidebar.subheader("💰 Final Proposal")
    if st.sidebar.button("📄 Generate Proposal", type="primary", use_container_width=True):
        if len(st.session_state.messages) <= 1:
            st.sidebar.warning("Start a conversation first!")
        else:
            with st.sidebar:
                with st.spinner("Finalizing proposal..."):
                    quotation_text, error = generate_quotation()
                    
                    if error:
                        st.error(error)
                    elif quotation_text:
                        # Generate PDF
                        pdf_buffer = get_pdf_buffer(quotation_text)
                        
                        # Offer download
                        st.success("✅ Ready!")
                        st.download_button(
                            label="📥 Download PDF",
                            data=pdf_buffer,
                            file_name=f"proposal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )
                        
                        # Show preview
                        with st.expander("Preview"):
                            st.markdown(quotation_text)

# Display chat messages
for msg in st.session_state.messages:
    sender = msg.get("sender", "Unknown")
    icon = msg.get("icon", "💬")
    timestamp = msg.get("timestamp", "")
    content = msg.get("content", "")
    
    # Determine CSS class based on sender
    if msg["role"] == "user":
        css_class = "user-message"
    elif "BuyBuddy" in sender:
        css_class = "retail-message"
    elif "FridgeBuddy" in sender:
        css_class = "product-message"
    elif "InsuranceBuddy" in sender:
        css_class = "insurance-message"
    else:
        css_class = "chat-message"
    
    # Display message with custom styling
    st.markdown(f"""
    <div class="chat-message {css_class}">
        <div class="sender-name">
            <span>{icon} <strong>{sender}</strong></span>
            <span class="timestamp">{timestamp}</span>
        </div>
        <div class="message-content">{content}</div>
    </div>
    """, unsafe_allow_html=True)
    
    # Show thinking process for agent messages if available
    if msg["role"] == "agent" and "thinking" in msg:
        with st.expander("🧠 Agent Coordination Process", expanded=False):
            st.markdown(msg["thinking"])
    
    # Show internal inventory check results if available (only once per session)
    if msg.get("inventory_check") and msg["inventory_check"].get("checked"):
        # Only show if we haven't shown it before
        if not st.session_state.inventory_checked_once:
            st.session_state.inventory_checked_once = True
            inventory = msg["inventory_check"]
            st.markdown(f"""
        <div class="chat-message retail-message" style="border-left-color: #FF9800; background-color: rgba(255, 152, 0, 0.1);">
            <div class="sender-name">
                <span>📦 <strong>Internal Inventory Check</strong></span>
                <span class="specialist-badge" style="background-color: #FFE0B2; color: #E65100;">MediaMarktSaturn</span>
            </div>
            <div class="message-content">
                <strong>{inventory.get('summary', 'Inventory check completed')}</strong><br/>
                {inventory.get('details', 'Checked our internal database for available products.')}
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # Specialist responses are intentionally not rendered directly in UI.
    # Orchestrator summarizes specialist outputs into BuyBuddy's main response.

# Receive user input and generate response
if prompt := st.chat_input(placeholder="Ask me anything..."):
    current_time = datetime.now().strftime("%I:%M %p")
    
    # Add user message
    user_message = {
        "role": "user",
        "sender": "Customer",
        "content": prompt,
        "timestamp": current_time,
        "icon": get_agent_icon('customer')
    }
    st.session_state.messages.append(user_message)
    
    # Display user message immediately
    st.markdown(f"""
    <div class="chat-message user-message">
        <div class="sender-name">
            <span>{user_message['icon']} <strong>Customer</strong></span>
            <span class="timestamp">{current_time}</span>
        </div>
        <div class="message-content">{prompt}</div>
    </div>
    """, unsafe_allow_html=True)
    
    # Get response from BuyBuddy
    with st.status("🛒 BuyBuddy is working on your request...", expanded=True) as status:
        thinking_container = st.empty()
        
        # Handle the customer query
        result = handle_customer_query(prompt, thinking_container)
        
        status.update(label="✅ Response ready!", state="complete", expanded=False)
    
    # Add BuyBuddy response to message history
    response_time = datetime.now().strftime("%I:%M %p")
    agent_message = {
        "role": "agent",
        "sender": "BuyBuddy",
        "content": result['main_response'],
        "timestamp": response_time,
        "icon": get_agent_icon('retail_agent'),
        "thinking": result['thinking'],
        "inventory_check": result.get('inventory_check'),
        "specialist_responses": result.get('specialist_responses', [])
    }
    st.session_state.messages.append(agent_message)
    
    st.rerun()
