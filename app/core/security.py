import jwt
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from fastapi.security import HTTPBearer
from app.core.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_DAYS

pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

def create_access_token(user_id: str) -> str:
    expire_at = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"user_id": user_id, "exp": expire_at}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)