from dotenv import load_dotenv
import os

print('cwd:', os.getcwd())
print('.env path:', os.path.abspath('.env'))
print('.env exists:', os.path.exists('.env'))
loaded = load_dotenv('.env', override=False)
print('.env load returned:', loaded)
keys = [
    'AZURE_AIPROJECT_ENDPOINT',
    'AZURE_TENANT_ID',
    'AGENT_RETAIL',
    'AGENT_ORCHESTRATOR',
    'AGENT_PRODUCT',
    'AGENT_INSURANCE',
]
for k in keys:
    print(f"{k}: -> {os.getenv(k)!r}")
