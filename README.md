# PDF Library (Phase 1)

Proper split architecture for future growth:

- `frontend/` -> Next.js app (UI only)
- `backend/` -> FastAPI service (auth, PDF storage, API)

## Phase 1 Features

- User register/login/logout
- Upload PDFs per authenticated user
- List only the logged-in user's PDFs
- Open a PDF in a viewer
- Delete PDFs

## Run Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Run Frontend

```bash
cd frontend
copy .env.example .env.local
npm install
npm run dev
```

Frontend runs on `http://localhost:3000`, backend on `http://localhost:8000`.
