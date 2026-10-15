from agents.validation_agent import validate_implementation

result = validate_implementation()

print("\n========== VALIDATION REPORT ==========")
print("Passed:", result["passed"])
print("Exit Code:", result["exit_code"])
print("Duration:", result["duration_seconds"], "seconds")

print("\nMaven Output:")
print(result["output"])