from dotenv import load_dotenv
from agents.codebase_agent import analyze_codebase

load_dotenv()

requirement = (
    "Add URL expiration support so links expire "
    "24 hours after creation."
)

result = analyze_codebase(requirement)

print("\n========== CODEBASE ANALYSIS ==========")
print(result)