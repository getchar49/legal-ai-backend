# Legal AI Backend

## 1. Cách chạy ứng dụng

Cài đặt các gói phụ thuộc (dùng uv):
```bash
uv sync
```

Tạo file `.env` ở thư mục gốc:
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=legal_ai
JWT_SECRET=your_jwt_secret
LLM_API_KEY=your_openai_api_key
LLM_BASE_URL=[https://api.openai.com/v1](https://api.openai.com/v1)
LLM_MODEL=gpt-4o-mini
```

Khởi chạy server:
```bash
uv run uvicorn app.main:app --reload
```
Server sẽ chạy tại `http://127.0.0.1:8000`. Bạn có thể xem tài liệu API chi tiết tại `http://127.0.0.1:8000/docs`.

---

## 2. Danh sách API Endpoints

### Auth
* `POST /api/register`: Đăng ký người dùng mới (email, password).
* `POST /api/login`: Đăng nhập và nhận Access Token.

### Chat
* `POST /api/chat`: Gửi tin nhắn cho AI (yêu cầu token). Hỗ trợ trả về stream hoặc text thường, có thể truyền kèm `conversation_id` để tiếp tục hội thoại.

### History
*(Các API dưới đây đều yêu cầu Access Token trong header)*
* `GET /api/history`: Lấy danh sách lịch sử hội thoại của user.
* `POST /api/history`: Tạo một phiên hội thoại mới rỗng.
* `GET /api/history/{conversation_id}`: Lấy chi tiết lịch sử tin nhắn của một hội thoại cụ thể.
* `DELETE /api/history/{conversation_id}`: Xóa một phiên hội thoại.