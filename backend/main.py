"""
main.py
-------
This is the entry point for the DocQuery backend.

WHAT THIS FILE DOES (in interview terms):
- Exposes 2 real endpoints: /upload (add a document) and /chat (ask a question).
- Does NOT contain any RAG logic itself — that lives in rag.py.
  Keeping main.py "dumb" (just routing + request/response handling) and rag.py
  "smart" (the actual AI logic) is a clean separation you can point to if asked
  "how did you structure this?"

FLOW:
  1. User uploads a PDF -> we extract text -> chunk it -> embed each chunk ->
     store the vectors in a FAISS index (in rag.py).
  2. User asks a question -> we embed the question -> find the most similar
     chunks in FAISS -> stuff those chunks into a prompt -> send to the LLM ->
     return the answer.
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
import os
import uuid

from rag import (
    extract_text_from_pdf,
    chunk_text,
    embed_and_store,
    retrieve_relevant_chunks,
    ask_llm,
)

app = FastAPI(title="DocQuery API")

# Allow the Next.js frontend (running on a different port/domain) to call this API.
# In a real production app you'd lock allow_origins down to your actual frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploaded_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Very simple in-memory "database" of which documents have been uploaded.
# In a real version you'd persist this in Postgres/Supabase instead of a
# Python list that resets every time the server restarts.
DOCUMENT_REGISTRY = []


class ChatRequest(BaseModel):
    question: str
    doc_id: str  # which document to chat with


@app.get("/")
def health_check():
    """Simple endpoint to confirm the API is alive. Useful for deployment checks."""
    return {"status": "ok", "service": "DocQuery API"}


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
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
        raise HTTPException(status_code=400, detail="Could not extract any text from this PDF.")

    # Split the text into overlapping chunks (see rag.py for why overlap matters)
    chunks = chunk_text(raw_text)

    # Embed each chunk and store the vectors in FAISS under this doc_id
    num_chunks = embed_and_store(doc_id=doc_id, chunks=chunks)

    DOCUMENT_REGISTRY.append({"doc_id": doc_id, "filename": file.filename, "chunks": num_chunks})

    return {
        "doc_id": doc_id,
        "filename": file.filename,
        "chunks_created": num_chunks,
        "message": "Document uploaded and indexed successfully.",
    }


@app.get("/documents")
def list_documents():
    """Lists all documents uploaded so far, so the frontend can show a picker."""
    return {"documents": DOCUMENT_REGISTRY}


@app.post("/chat")
async def chat_with_document(request: ChatRequest):
    """
    Step 2 of the pipeline: given a question and a doc_id, retrieve the most
    relevant chunks from that document, build a grounded prompt, and ask the LLM.
    """
    relevant_chunks = retrieve_relevant_chunks(doc_id=request.doc_id, query=request.question, top_k=8)

    if not relevant_chunks:
        raise HTTPException(status_code=404, detail="No indexed content found for this document.")

    answer = ask_llm(question=request.question, context_chunks=relevant_chunks)

    return {
        "answer": answer,
        "sources": relevant_chunks,  # returning the actual chunks used lets the UI show "citations"
    }
