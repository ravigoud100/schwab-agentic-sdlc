import uuid
from governance.workspace_manager import initialize_workspace

workspace = initialize_workspace(str(uuid.uuid4()))
from agents.implementation_agent import implement_expiration

print("\nTEST 1: Verify unauthorized execution is blocked")

try:
    implement_expiration(approved=False, workspace=workspace)
except PermissionError as error:
    print("PASS:", error)


print("\nTEST 2: Generate an approved code change")

modified_file = implement_expiration(approved=True, workspace=workspace)

print("\nGenerated file:")
print(modified_file)
x