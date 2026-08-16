"""
rag.py
------
This file contains the actual "AI" logic. If an interviewer asks you to
walk through your RAG pipeline, this is the file you're describing.

THE 4 STAGES OF RAG, MAPPED TO FUNCTIONS BELOW:
  1. extract_text_from_pdf   -> get raw text out of the uploaded file
  2. chunk_text               -> split text into small overlapping pieces
  3. embed_and_store          -> turn each chunk into a vector, store in FAISS
  4. retrieve_relevant_chunks -> given a question, find the closest chunks
  5. ask_llm                  -> build a grounded prompt and call the LLM

WHY EACH STEP EXISTS (say this out loud in an interview):
  - Chunking: LLMs have limited context windows, and smaller, focused chunks
    retrieve more accurately than one giant blob of text.
  - Embeddings: a vector (list of numbers) that captures the MEANING of text,
    so "revenue growth" and "sales increased" end up close together in vector
    space even though the words are different.
  - FAISS: a fast library for searching millions of vectors for the ones most
    similar to a query vector. Facebook AI Similarity Search.
  - Retrieval before generation: instead of asking the LLM to "remember" the
    whole document, we hand it only the 3-4 most relevant chunks as context.
    This is what makes answers grounded instead of hallucinated.
"""

import os
import faiss
import numpy as np
from pypdf import PdfReader
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# ---------------------------------------------------------------------------
# CLIENT SETUP
# ---------------------------------------------------------------------------
# Using Google Gemini API here for both embeddings and chat completion.
# Make sure GEMINI_API_KEY is set in your backend/.env file.
EMBEDDING_MODEL = "models/gemini-embedding-001"  # 3072 dimensions
CHAT_MODEL = "models/gemini-3.7-flash"               # highly intelligent latest model



def get_genai_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError(
            "GEMINI_API_KEY is missing or set to placeholder in backend/.env. "
            "Please paste your real key in backend/.env"
        )
    return genai.Client(api_key=api_key)

# ---------------------------------------------------------------------------
# IN-MEMORY VECTOR STORE
# ---------------------------------------------------------------------------
# For a demo project, we keep one FAISS index + chunk list PER document,
# stored in a plain Python dict. In production this would be a persistent
# vector DB (Supabase Vector / Pinecone) so it survives a server restart and
# scales beyond what fits in RAM.
FAISS_INDEXES = {}   # doc_id -> faiss.IndexFlatL2
DOC_CHUNKS = {}       # doc_id -> list of chunk strings (same order as the index)


def extract_text_from_pdf(pdf_path: str) -> str:
    """Reads a PDF file and returns all its text concatenated together."""
    reader = PdfReader(pdf_path)
    text_parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(text_parts)


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """
    Splits text into overlapping chunks.

    chunk_size: roughly how many characters per chunk (kept simple -- a more
                advanced version would chunk by tokens or by sentence boundaries).
    overlap:    how many characters each chunk shares with the previous one.
                Overlap prevents a sentence that answers the question from being
                cut in half right at a chunk boundary.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return [c.strip() for c in chunks if c.strip()]


import time

def _embed(texts: list[str]) -> np.ndarray:
    """
    Calls the Gemini embedding API with batching, rate limiting, and retries.
    This protects your free tier quota from hitting Rate Limit (429) errors.
    """
    client = get_genai_client()
    vectors = []
    
    # Batch inputs to minimize API requests (Gemini supports up to 100 texts per call)
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        
        # Implement retry logic with exponential backoff for free-tier rate limits
        retries = 3
        delay = 2.0
        for attempt in range(retries):
            try:
                response = client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=batch,
                )
                for emb in response.embeddings:
                    vectors.append(emb.values)
                break
            except Exception as e:
                # If rate limited (HTTP 429) or other API transient error, sleep and retry
                if "429" in str(e) or "quota" in str(e).lower() or attempt == retries - 1:
                    if attempt < retries - 1:
                        time.sleep(delay)
                        delay *= 2
                        continue
                raise e
        
        # Add a tiny delay between batches to respect the 15 RPM limit
        if i + batch_size < len(texts):
            time.sleep(1.0)
            
    return np.array(vectors, dtype="float32")



def embed_and_store(doc_id: str, chunks: list[str]) -> int:
    """
    Embeds every chunk of a document and stores the vectors in a new FAISS
    index keyed by doc_id. Returns the number of chunks stored.
    """
    vectors = _embed(chunks)
    dimension = vectors.shape[1]  # 3072 for models/gemini-embedding-001

    # IndexFlatL2 = brute-force nearest neighbour search using Euclidean distance.
    # It's the simplest FAISS index -- exact (not approximate) search, which is
    # totally fine at this scale (hundreds/thousands of chunks). At millions of
    # vectors you'd switch to an approximate index like IndexIVFFlat for speed.
    index = faiss.IndexFlatL2(dimension)
    index.add(vectors)

    FAISS_INDEXES[doc_id] = index
    DOC_CHUNKS[doc_id] = chunks

    return len(chunks)


def retrieve_relevant_chunks(doc_id: str, query: str, top_k: int = 4) -> list[str]:
    """
    Embeds the user's question and searches the FAISS index for that document
    to find the top_k most similar chunks.
    """
    if doc_id not in FAISS_INDEXES:
        return []

    index = FAISS_INDEXES[doc_id]
    chunks = DOC_CHUNKS[doc_id]

    query_vector = _embed([query])
    distances, indices = index.search(query_vector, top_k)

    # indices[0] is the list of chunk positions closest to the query
    return [chunks[i] for i in indices[0] if i != -1]


def ask_llm(question: str, context_chunks: list[str]) -> str:
    """
    Builds a grounded prompt from the retrieved chunks and asks Gemini to
    answer using ONLY that context. This is the "prompt engineering" step --
    explicitly instructing the model to say "I don't know" rather than guess
    is what prevents hallucination.
    """
    client = get_genai_client()
    context = "\n\n---\n\n".join(context_chunks)

    system_prompt = (
        "You are an expert document analysis assistant. Your goal is to provide a "
        "comprehensive, accurate, and structured answer based ONLY on the context provided "
        "below. Avoid making assumptions or extrapolating beyond the text. "
        "Format your response professionally. ALWAYS use bullet points (each starting on a new line) "
        "and bold text where appropriate to make the answer highly readable. Never compress "
        "list items into a single line or row of text. If the context does not contain the answer, "
        "honestly state 'I couldn't find that in the document' instead of fabricating information."
    )

    user_prompt = f"Context:\n{context}\n\nQuestion: {question}"

    response = client.models.generate_content(
        model=CHAT_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,  # low temperature = more focused/deterministic answers, good for Q&A
        ),
    )

    return response.text



# ---------------------------------------------------------------------------
# OPTIONAL: Claude version of the same function, to show vendor flexibility.
# Swap ask_llm -> ask_llm_with_claude in main.py to switch providers.
# ---------------------------------------------------------------------------
def ask_llm_with_claude(question: str, context_chunks: list[str]) -> str:
    import anthropic

    claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    context = "\n\n---\n\n".join(context_chunks)

    message = claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=(
            "You are a helpful assistant that answers questions using ONLY the "
            "context provided. If the answer isn't in the context, say so."
        ),
        messages=[{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}],
    )
    return message.content[0].text
