import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", os.getenv("MONGODB_URL", "mongodb://localhost:27017"))
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "legal_ai")
JWT_SECRET = os.getenv("JWT_SECRET", os.getenv("JWT_SECRET_KEY", "change-me-in-production"))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_DAYS = 7
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
CHAT_EXTERNAL_STREAM_URL = os.getenv("CHAT_EXTERNAL_STREAM_URL", "")
CHAT_EXTERNAL_MODEL = os.getenv("CHAT_EXTERNAL_MODEL", "default")
CHAT_EXTERNAL_CHANNEL = os.getenv("CHAT_EXTERNAL_CHANNEL", "web")
CHAT_EXTERNAL_TIMEOUT = float(os.getenv("CHAT_EXTERNAL_TIMEOUT", "120"))