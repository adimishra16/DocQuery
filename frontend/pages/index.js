import { useState, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

function renderMarkdown(text) {
  if (!text) return "";
  
  // Pre-process: if list items are compressed inline, insert newlines
  let processedText = text;
  processedText = processedText.replace(/([^\n])\s*\*\s+\*\*/g, "$1\n* **");

  const lines = processedText.split("\n");
  const elements = [];
  
  let inList = false;
  let listItems = [];
  
  const parseInline = (str) => {
    const parts = str.split(/\*\*(.*?)\*\*/g);
    return parts.map((part, idx) => {
      if (idx % 2 === 1) {
        return <strong key={idx} style={{ color: "#fff", fontWeight: 700 }}>{part}</strong>;
      }
      return part;
    });
  };

  lines.forEach((line, lineIdx) => {
    const trimmed = line.trim();
    const isBullet = trimmed.startsWith("* ") || trimmed.startsWith("- ") || trimmed.startsWith("• ");
    
    if (isBullet) {
      if (!inList) {
        inList = true;
        listItems = [];
      }
      const itemText = trimmed.substring(2);
      listItems.push(<li key={lineIdx} style={{ marginBottom: "6px" }}>{parseInline(itemText)}</li>);
    } else {
      if (inList) {
        elements.push(<ul key={`list-${lineIdx}`} style={{ margin: "10px 0 14px 20px", paddingLeft: "15px", listStyleType: "disc" }}>{listItems}</ul>);
        inList = false;
      }
      
      if (trimmed === "") {
        elements.push(<div key={`br-${lineIdx}`} style={{ height: "10px" }} />);
      } else {
        elements.push(<p key={lineIdx} style={{ marginBottom: "8px" }}>{parseInline(line)}</p>);
      }
    }
  });
  
  if (inList) {
    elements.push(<ul key={`list-end`} style={{ margin: "10px 0 14px 20px", paddingLeft: "15px", listStyleType: "disc" }}>{listItems}</ul>);
  }
  
  return elements;
}


export default function Home({ isAdmin = false }) {
  // Auth state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [token, setToken] = useState(null);
  const [username, setUsername] = useState("");
  
  // Auth Form inputs
  const [authMode, setAuthMode] = useState("login"); // "login" or "register"
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [authSuccess, setAuthSuccess] = useState("");
  const [authLoading, setAuthLoading] = useState(false);

  // Application state
  const [documents, setDocuments] = useState([]);
  const [activeDocId, setActiveDocId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);

  // Check for stored credentials on mount
  useEffect(() => {
    const savedToken = localStorage.getItem("token");
    const savedUsername = localStorage.getItem("username");
    if (savedToken && savedUsername) {
      setToken(savedToken);
      setUsername(savedUsername);
      setIsAuthenticated(true);
    }
  }, []);

  // Load the list of documents when authentication changes.
  useEffect(() => {
    if (isAuthenticated && token) {
      fetchDocuments();
    }
  }, [isAuthenticated, token]);

  // Load chat history when selected document changes.
  useEffect(() => {
    if (activeDocId && token) {
      fetchChatHistory(activeDocId);
    } else {
      setMessages([]);
    }
  }, [activeDocId, token]);

  async function fetchDocuments() {
    try {
      const res = await fetch(`${API_BASE}/documents`, {
        headers: {
          "Authorization": `Bearer ${token}`
        }
      });
      if (res.status === 401) {
        handleLogout();
        return;
      }
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch (err) {
      console.error("Failed to load documents:", err);
    }
  }

  async function fetchChatHistory(docId) {
    try {
      const res = await fetch(`${API_BASE}/chat/history/${docId}`, {
        headers: {
          "Authorization": `Bearer ${token}`
        }
      });
      if (res.status === 401) {
        handleLogout();
        return;
      }
      const data = await res.json();
      setMessages(data.history || []);
    } catch (err) {
      console.error("Failed to load chat history:", err);
    }
  }


  async function handleAuthSubmit(e) {
    e.preventDefault();
    if (!authUsername.trim() || !authPassword.trim()) {
      setAuthError("Please fill in all fields.");
      return;
    }

    setAuthLoading(true);
    setAuthError("");
    setAuthSuccess("");

    try {
      const endpoint = authMode === "login" ? "/auth/login" : "/auth/register";
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: authUsername, password: authPassword })
      });
      const data = await res.json();

      if (!res.ok) throw new Error(data.detail || "Authentication failed");

      if (authMode === "login") {
        localStorage.setItem("token", data.access_token);
        localStorage.setItem("username", data.username);
        setToken(data.access_token);
        setUsername(data.username);
        setIsAuthenticated(true);
        setAuthUsername("");
        setAuthPassword("");
      } else {
        setAuthSuccess("Registration successful! You can now sign in.");
        setAuthMode("login");
        setAuthPassword("");
      }
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setAuthLoading(false);
    }
  }

  function handleLogout() {
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    setToken(null);
    setUsername("");
    setIsAuthenticated(false);
    setDocuments([]);
    setActiveDocId(null);
    setMessages([]);
  }

  async function handleUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    setUploading(true);
    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/upload`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`
        },
        body: formData,
      });
      const data = await res.json();

      if (!res.ok) throw new Error(data.detail || "Upload failed");

      await fetchDocuments();
      setActiveDocId(data.doc_id);
      setMessages([]); // fresh chat for a fresh document
    } catch (err) {
      alert(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleSend() {
    if (!question.trim() || !activeDocId) return;

    const userMessage = { role: "user", content: question };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion("");
    setAsking(true);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ question: userMessage.content, doc_id: activeDocId }),
      });
      const data = await res.json();

      if (!res.ok) throw new Error(data.detail || "Chat failed");

      const assistantMessage = {
        role: "assistant",
        content: data.answer,
        sources: data.sources,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${err.message}` }]);
    } finally {
      setAsking(false);
    }
  }

  async function handleClearChat() {
    if (!activeDocId || !token) return;
    if (!confirm("Are you sure you want to delete all chat history for this document?")) return;

    try {
      const res = await fetch(`${API_BASE}/chat/history/${activeDocId}`, {
        method: "DELETE",
        headers: {
          "Authorization": `Bearer ${token}`
        }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to clear chat");
      
      setMessages([]);
    } catch (err) {
      alert(err.message);
    }
  }

  // --- Auth View ---
  if (!isAuthenticated) {
    return (
      <div className="auth-wrapper">
        <div className="auth-card">
          <div className="auth-header">
            <h1 className="auth-title">
              {authMode === "login" ? "Welcome to DocQuery" : "Create Account"}
            </h1>
            <p className="auth-subtitle">
              {authMode === "login"
                ? "Sign in to upload and chat with your documents"
                : "Register a new account to get started"}
            </p>
          </div>

          {authError && <div className="error-banner">{authError}</div>}
          {authSuccess && <div className="success-banner">{authSuccess}</div>}

          <form onSubmit={handleAuthSubmit} className="auth-form">
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                type="text"
                className="form-input"
                placeholder="Enter your username"
                value={authUsername}
                onChange={(e) => setAuthUsername(e.target.value)}
                disabled={authLoading}
                required
              />
            </div>
            
            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                type="password"
                className="form-input"
                placeholder="Enter your password"
                value={authPassword}
                onChange={(e) => setAuthPassword(e.target.value)}
                disabled={authLoading}
                required
              />
            </div>

            <button type="submit" className="auth-btn" disabled={authLoading}>
              {authLoading ? (
                <svg className="animate-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <circle cx="12" cy="12" r="10" stroke="rgba(255,255,255,0.1)" strokeDasharray="32" />
                  <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                </svg>
              ) : (
                authMode === "login" ? "Sign In" : "Sign Up"
              )}
            </button>
          </form>

          <div className="auth-footer">
            {authMode === "login" ? (
              <>
                Don't have an account?
                <button className="auth-toggle-btn" onClick={() => { setAuthMode("register"); setAuthError(""); setAuthSuccess(""); }}>
                  Register
                </button>
              </>
            ) : (
              <>
                Already have an account?
                <button className="auth-toggle-btn" onClick={() => { setAuthMode("login"); setAuthError(""); setAuthSuccess(""); }}>
                  Sign In
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  // --- Main Dashboard ---
  return (
    <div className="container" style={{ position: "relative" }}>
      <div className="header-right">
        <div className="user-badge">
          <svg className="user-badge-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
          <span>{username}</span>
        </div>
        <button className="logout-link-btn" onClick={handleLogout}>
          Logout
        </button>
      </div>

      <header className="header">
        <h1 className="title">DocQuery</h1>
        <p className="subtitle">
          Upload any PDF document, then chat with it instantly. Answers are grounded in the document text via Gemini RAG.
        </p>
      </header>

      {/* Upload Card */}
      <div className="card">
        <label className={`upload-zone ${uploading ? 'disabled' : ''}`}>
          <input
            type="file"
            accept="application/pdf"
            onChange={handleUpload}
            disabled={uploading}
            style={{ display: "none" }}
          />
          <div className="upload-icon">
            {uploading ? (
              <svg className="animate-spin" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <circle cx="12" cy="12" r="10" stroke="rgba(255,255,255,0.1)" strokeDasharray="32" />
                <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            )}
          </div>
          <div className="upload-text">
            {uploading ? (
              <span>Analyzing and Indexing Document...</span>
            ) : (
              <>
                Drag & drop or <span>click to upload a PDF</span>
              </>
            )}
          </div>
          <p className="upload-text-sub">Supports PDFs up to 25MB</p>
        </label>

        {documents.length > 0 && (
          <div>
            <h3 className="doc-section-title">Your Documents</h3>
            <div className="doc-list">
              {documents.map((doc) => (
                <div
                  key={doc.doc_id}
                  className={`doc-chip ${doc.doc_id === activeDocId ? "active" : ""}`}
                  onClick={() => {
                    setActiveDocId(doc.doc_id);
                    setMessages([]);
                  }}
                >
                  <span className="doc-chip-icon">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <polyline points="14 2 14 8 20 8" />
                      <line x1="16" y1="13" x2="8" y2="13" />
                      <line x1="16" y1="17" x2="8" y2="17" />
                    </svg>
                  </span>
                  {doc.filename}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Chat Card */}
      {activeDocId && (
        <div className="card">
          <div className="chat-header">
            <h3 className="chat-header-title">
              Chatting with: <span className="chat-header-filename">{documents.find(d => d.doc_id === activeDocId)?.filename}</span>
            </h3>
            <button className="clear-chat-btn" onClick={handleClearChat}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
              Clear Chat
            </button>
          </div>
          <div className="chat-window">
            {messages.length === 0 && (
              <div className="welcome-screen">
                <div className="welcome-icon">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                </div>
                <div>
                  <p style={{ fontWeight: 600, color: '#f1f5f9' }}>Ready to Chat</p>
                  <p style={{ fontSize: '13px', marginTop: '4px' }}>Ask any question to retrieve answers grounded in the uploaded document.</p>
                </div>
              </div>
            )}
            
            {messages.map((msg, i) => (
              <div key={i} className={`message ${msg.role}`}>
                <div>{renderMarkdown(msg.content)}</div>

              </div>
            ))}
            {asking && (
              <div className="message assistant">
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span>Thinking</span>
                  <svg className="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <circle cx="12" cy="12" r="10" stroke="rgba(255,255,255,0.1)" strokeDasharray="32" />
                    <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                  </svg>
                </div>
              </div>
            )}
          </div>

          <div className="chat-input-row">
            <input
              type="text"
              placeholder="Ask a question about this document..."
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={asking}
            />
            <button className="send-btn" onClick={handleSend} disabled={asking || !question.trim()}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
