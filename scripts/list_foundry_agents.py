"""List agents from the Azure AI Foundry project using the endpoint in .env.

This script will:
 - load .env
 - try the configured AZURE_AIPROJECT_ENDPOINT
 - if that fails or contains a project path, also try the base account endpoint
 - print agent names found or errors for diagnosis

Run: python scripts/list_foundry_agents.py
"""
from dotenv import load_dotenv
import os
import traceback

from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
from azure.ai.projects import AIProjectClient

# Load .env relative to this script so the tool works no matter the current working directory
_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
print(f"Using .env path: {os.path.abspath(_env_path)}")
load_dotenv(_env_path, override=False)

def make_credential(tenant_id=None):
    # Prefer DefaultAzureCredential for local dev; fall back to interactive browser
    cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    try:
        # quick token check
        cred.get_token("https://management.azure.com/.default")
        return cred
    except Exception:
        if tenant_id:
            return InteractiveBrowserCredential(additionally_allowed_tenants=["*"], tenant_id=tenant_id)
        return InteractiveBrowserCredential(additionally_allowed_tenants=["*"])


def try_list(endpoint, cred):
    print(f"\nTrying endpoint: {endpoint}")
    try:
        client = AIProjectClient(endpoint=endpoint, credential=cred)
        print("Client created. Listing agents...")
        agents = list(client.agents.list())
        if not agents:
            print("No agents returned by this endpoint.")
            return False
        print(f"Found {len(agents)} agent(s):")
        for a in agents:
            print(" -", a.name)
        return True
    except Exception as e:
        print("Error while listing agents:")
        traceback.print_exc()
        return False


if __name__ == '__main__':
    endpoint = os.getenv('AZURE_AIPROJECT_ENDPOINT')
    tenant = os.getenv('AZURE_TENANT_ID')

    if not endpoint:
        print('AZURE_AIPROJECT_ENDPOINT is not set in .env')
        raise SystemExit(1)

    cred = make_credential(tenant)

    ok = try_list(endpoint, cred)

    # If endpoint contains a project path, also try the base account endpoint
    if not ok and '/api/' in endpoint:
        base = endpoint.split('/api/')[0]
        print('\nRetrying with base account endpoint (without /api/...):')
        try_list(base, cred)

    print('\nConfigured AGENT_* values from .env:')
    for k in ['AGENT_RETAIL','AGENT_ORCHESTRATOR','AGENT_PRODUCT','AGENT_INSURANCE']:
        print(f" - {k} = {os.getenv(k)!r}")
