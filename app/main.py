from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import auth, history, chat, documents

app = FastAPI(title="Legal AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Content-Disposition",
        "X-Citation-Id",
        "X-Document-Category",
    ],
)

# Gắn các router vào app
app.include_router(auth.router, prefix="/api", tags=["Auth"])
app.include_router(history.router, prefix="/api/history", tags=["History"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])