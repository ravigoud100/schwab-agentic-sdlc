from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

model = ChatGoogleGenerativeAI(
    model="gemini-3.8-flash",
    temperature=0
)

response = model.invoke(
    "Explain what a URL shortener is in one sentence."
)

print("\nGemini Response:")
print(response.text)