from openai import AsyncOpenAI
from app.core.config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

llm_client = (
    AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY) if LLM_API_KEY else None
)