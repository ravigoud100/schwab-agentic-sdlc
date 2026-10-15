from langchain_ollama import ChatOllama

model = ChatOllama(
    model="qwen2.5-coder:latest",
    temperature=0
)

response = model.invoke(
    "Explain URL expiration in Java Spring Boot in two sentences."
)

print("\nOLLAMA RESPONSE:")
print(response.text)
