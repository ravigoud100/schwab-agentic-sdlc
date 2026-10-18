import uuid
from governance.workspace_manager import initialize_workspace

workspace = initialize_workspace(str(uuid.uuid4()))
from dotenv import load_dotenv
from agents.codebase_agent import analyze_codebase

load_dotenv()

requirement = (
    "Add URL expiration support so links expire "
    "24 hours after creation."
)

result = analyze_codebase(requirement, workspace)

print("\n========== CODEBASE ANALYSIS ==========")
print(result)