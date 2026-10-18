from llm.provider import get_model

model = get_model()

response = model.invoke(
    "Explain URL expiration in Java Spring Boot in two sentences."
)

print("\nOLLAMA RESPONSE:")
print(response.text)
