import uuid
from governance.workspace_manager import initialize_workspace

workspace = initialize_workspace(str(uuid.uuid4()))
from agents.validation_agent import validate_implementation

result = validate_implementation(workspace)

print("\n========== VALIDATION REPORT ==========")
print("Passed:", result["passed"])
print("Exit Code:", result["exit_code"])
print("Duration:", result["duration_seconds"], "seconds")

print("\nMaven Output:")
print(result["output"])