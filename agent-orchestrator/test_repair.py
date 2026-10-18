import uuid
from governance.workspace_manager import initialize_workspace

workspace = initialize_workspace(str(uuid.uuid4()))
from agents.repair_agent import propose_repair


simulated_error = """[ERROR] COMPILATION ERROR:
[ERROR] ShortUrl.java:[42,17] cannot find symbol
[ERROR] symbol: method getExpiresAt()
[ERROR] location: variable shortUrl of type ShortUrl
[INFO] BUILD FAILURE"""

proposal = propose_repair(simulated_error, workspace)

print("\n========== REPAIR PROPOSAL ==========")
print(proposal)

assert proposal.strip()

print("\nPASS: Repair Agent generated a proposal.")
