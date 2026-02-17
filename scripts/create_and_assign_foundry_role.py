#!/usr/bin/env python3
"""
Create and assign a custom "Foundry Agents Reader" role to the managed identity
configured in .env (uses Azure CLI under the hood). Run this from the repo root.

Usage: python scripts/create_and_assign_foundry_role.py

Preconditions:
 - You are logged in with `az login` to an account that can create roles and assignments
 - .env contains AZURE_AIPROJECT_RESOURCE_ID and AZURE_MANAGED_IDENTITY_CLIENT_ID
"""
import os
import subprocess
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / '.env'
ROLE_JSON = Path(__file__).resolve().parent / 'foundry-agents-reader.json'
ROLE_NAME = "Foundry Agents Reader"


def load_env(path: Path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k.strip()] = v.strip()
    return data


def run(cmd, capture_output=True, check=False):
    if isinstance(cmd, str):
        cmd = cmd.split()
    return subprocess.run(cmd, capture_output=capture_output, text=True, check=check)


def role_exists(role_name: str) -> bool:
    res = run(f"az role definition list --name \"{role_name}\" -o json")
    if res.returncode != 0:
        return False
    try:
        items = json.loads(res.stdout)
        return len(items) > 0
    except Exception:
        return False


def create_role_from_file(role_file: Path) -> bool:
    if not role_file.exists():
        print("Role definition file not found:", role_file)
        return False
    res = run(f"az role definition create --role-definition {str(role_file)}")
    if res.returncode != 0:
        print("Failed to create role:")
        print(res.stderr)
        return False
    print("Role created (or already exists):", ROLE_NAME)
    return True


def get_object_id_from_client_id(client_id: str):
    # Try to resolve a service principal / managed identity objectId from clientId
    res = run(f"az ad sp show --id {client_id} --query objectId -o tsv")
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    # Fallback: try az identity (requires resource group & name) - omitted here
    return None


def assign_role(principal_id: str, scope: str):
    print(f"Assigning role '{ROLE_NAME}' to principal {principal_id} on scope {scope} ...")
    res = run(f"az role assignment create --assignee {principal_id} --role \"{ROLE_NAME}\" --scope \"{scope}\"")
    if res.returncode != 0:
        print("Role assignment failed:")
        print(res.stderr)
        return False
    print("Role assignment created successfully.")
    return True


def verify_assignment(principal_id: str, scope: str):
    res = run(f"az role assignment list --assignee {principal_id} --scope \"{scope}\" -o json")
    if res.returncode != 0:
        print("Could not verify role assignment:")
        print(res.stderr)
        return False
    try:
        items = json.loads(res.stdout)
        if items:
            print("Verified role assignments (matching entries):")
            for it in items:
                print(f" - {it.get('roleDefinitionName')}  (scope: {it.get('scope')})")
            return True
        print("No matching role assignment found.")
        return False
    except Exception as exc:
        print("Error parsing verification response:", exc)
        return False


def main():
    env = load_env(ENV_PATH)
    subscription = env.get('AZURE_SUBSCRIPTION_ID')
    project_resource_id = env.get('AZURE_AIPROJECT_RESOURCE_ID')
    client_id = env.get('AZURE_MANAGED_IDENTITY_CLIENT_ID')

    if not project_resource_id:
        print("ERROR: AZURE_AIPROJECT_RESOURCE_ID missing from .env")
        sys.exit(1)
    if not client_id:
        print("ERROR: AZURE_MANAGED_IDENTITY_CLIENT_ID missing from .env")
        sys.exit(1)

    print(f"Using project resource id: {project_resource_id}")
    print(f"Using managed identity client id: {client_id}")

    # Ensure role exists
    if role_exists(ROLE_NAME):
        print(f"Role '{ROLE_NAME}' already exists.")
    else:
        print(f"Creating role '{ROLE_NAME}' from {ROLE_JSON} ...")
        ok = create_role_from_file(ROLE_JSON)
        if not ok:
            print("Failed to create custom role. You may need Owner/UserAccessAdministrator privileges to create roles.")
            sys.exit(1)

    # Resolve principal objectId from clientId
    principal_obj = get_object_id_from_client_id(client_id)
    if not principal_obj:
        print("ERROR: Could not resolve objectId for clientId. Ensure the managed identity/service-principal exists and you have permission to query it.")
        sys.exit(1)
    print("Resolved principal objectId:", principal_obj)

    # Assign role at project scope
    if assign_role(principal_obj, project_resource_id):
        verify_assignment(principal_obj, project_resource_id)
    else:
        print("Role assignment failed. Check your permissions and scope.")


if __name__ == '__main__':
    main()
