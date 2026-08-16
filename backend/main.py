"""
main.py
-------
This is the entry point for the DocQuery backend, modified to support
Neon DB / SQLite database connection, User Registration, Login, and
authenticated file upload/chat actions.
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
import os
import uuid
from sqlalchemy.orm import Session

# Import DB setup and models
from database import engine, get_db
import models
from auth import hash_password, verify_password, create_access_token, get_current_user

# Import RAG pipeline functions
from rag import (
    extract_text_from_pdf,
    chunk_text,
    embed_and_store,
    retrieve_relevant_chunks,
    ask_llm,
)

# Initialize database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="DocQuery API")

# Allow CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploaded_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --- Request schemas ---

class ChatRequest(BaseModel):
    question: str
    doc_id: str


class UserAuthRequest(BaseModel):
    username: str
    password: str


# --- Public / Helper Endpoints ---

@app.get("/")
def health_check():
    """Simple endpoint to confirm the API is alive."""
    return {"status": "ok", "service": "DocQuery API"}


# --- Authentication Endpoints ---

@app.post("/auth/register")
def register_user(request: UserAuthRequest, db: Session = Depends(get_db)):
    """Registers a new user by hashing their password and storing it in Neon/SQLite."""
    if not request.username.strip() or not request.password.strip():
        raise HTTPException(status_code=400, detail="Username and password cannot be empty")
        
    existing = db.query(models.User).filter(models.User.username == request.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username is already taken")
    
    hashed = hash_password(request.password)
    new_user = models.User(username=request.username, hashed_password=hashed)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {"message": "User registered successfully"}


@app.post("/auth/login")
def login_user(request: UserAuthRequest, db: Session = Depends(get_db)):
    """Validates login credentials and returns a JWT access token."""
    user = db.query(models.User).filter(models.User.username == request.username).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    token = create_access_token(data={"sub": user.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username
    }


# --- Secured Document & Chat Endpoints ---

@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Step 1 of the pipeline: take an uploaded PDF, save it, extract its text,
    split it into chunks, and store embeddings for each chunk in FAISS.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported right now.")

    doc_id = str(uuid.uuid4())
    save_path = os.path.join(UPLOAD_DIR, f"{doc_id}.pdf")

    # Save the uploaded file to disk
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Extract raw text from the PDF
    raw_text = extract_text_from_pdf(save_path)
    if not raw_text.strip():
        # Clean up files if processing failed
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=400, detail="Could not extract any text from this PDF.")

    try:
        # Split the text into overlapping chunks
        chunks = chunk_text(raw_text)

        # Embed each chunk and store the vectors in FAISS and DB
        num_chunks = embed_and_store(doc_id=doc_id, chunks=chunks, db=db)

        # Save metadata record to DB for the specific user
        new_doc = models.Document(
            id=doc_id,
            filename=file.filename,
            user_id=current_user.id,
            chunks_created=num_chunks
        )
        db.add(new_doc)
        db.commit()
    except Exception as e:
        # Clean up file on disk if insertion/embedding failed
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=500, detail=f"Failed to process and index PDF: {str(e)}")

    return {
        "doc_id": doc_id,
        "filename": file.filename,
        "chunks_created": num_chunks,
        "message": "Document uploaded and indexed successfully.",
    }


@app.get("/documents")
def list_documents(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lists all documents uploaded so far by the authenticated user."""
    docs = db.query(models.Document).filter(models.Document.user_id == current_user.id).all()
    return {
        "documents": [
            {"doc_id": d.id, "filename": d.filename, "chunks": d.chunks_created}
            for d in docs
        ]
    }


@app.post("/chat")
async def chat_with_document(
    request: ChatRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Step 2 of the pipeline: given a question and a doc_id, retrieve the most
    relevant chunks from that document, build a grounded prompt, and ask the LLM.
    """
    # Verify the document belongs to the authenticated user
    doc = db.query(models.Document).filter(
        models.Document.id == request.doc_id,
        models.Document.user_id == current_user.id
    ).first()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or access denied.")

    # Retrieve relevant chunks using the DB session
    relevant_chunks = retrieve_relevant_chunks(
        doc_id=request.doc_id,
        query=request.question,
        db=db,
        top_k=8
    )

    if not relevant_chunks:
        raise HTTPException(status_code=404, detail="No indexed content found for this document.")

    # Save user message to database
    user_msg = models.ChatMessage(
        user_id=current_user.id,
        document_id=request.doc_id,
        role="user",
        content=request.question
    )
    db.add(user_msg)

    # Call LLM
    answer = ask_llm(question=request.question, context_chunks=relevant_chunks)

    # Save assistant message (with sources) to database
    assistant_msg = models.ChatMessage(
        user_id=current_user.id,
        document_id=request.doc_id,
        role="assistant",
        content=answer,
        sources=relevant_chunks
    )
    db.add(assistant_msg)
    db.commit()

    return {
        "answer": answer,
        "sources": relevant_chunks,
    }


@app.get("/chat/history/{doc_id}")
def get_chat_history(
    doc_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetches chronological chat history for a specific document belonging to the user."""
    # Verify the document belongs to the user
    doc = db.query(models.Document).filter(
        models.Document.id == doc_id,
        models.Document.user_id == current_user.id
    ).first()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or access denied.")
        
    messages = db.query(models.ChatMessage).filter(
        models.ChatMessage.document_id == doc_id,
        models.ChatMessage.user_id == current_user.id
    ).order_by(models.ChatMessage.created_at.asc()).all()
    
    return {
        "history": [
            {
                "role": m.role,
                "content": m.content,
                "sources": m.sources
            }
            for m in messages
        ]
    }


@app.delete("/chat/history/{doc_id}")
def clear_chat_history(
    doc_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Deletes all chat messages for a specific document belonging to the user."""
    # Verify the document belongs to the user
    doc = db.query(models.Document).filter(
        models.Document.id == doc_id,
        models.Document.user_id == current_user.id
    ).first()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or access denied.")
        
    db.query(models.ChatMessage).filter(
        models.ChatMessage.document_id == doc_id,
        models.ChatMessage.user_id == current_user.id
    ).delete()
    
    db.commit()
    return {"message": "Chat history cleared successfully"}

