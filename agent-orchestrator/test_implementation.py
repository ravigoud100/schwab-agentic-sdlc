from agents.implementation_agent import implement_expiration

print("\nTEST 1: Verify unauthorized execution is blocked")

try:
    implement_expiration(approved=False)
except PermissionError as error:
    print("PASS:", error)


print("\nTEST 2: Generate an approved code change")

modified_file = implement_expiration(approved=True)

print("\nGenerated file:")
print(modified_file)
x