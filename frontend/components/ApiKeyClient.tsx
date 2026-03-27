"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const STORAGE_KEY = "pdf_library_api_key";

function maskKey(value: string) {
  if (value.length <= 8) return "********";
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

export default function ApiKeyClient() {
  const [apiKey, setApiKey] = useState("");
  const [savedKey, setSavedKey] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const existing = localStorage.getItem(STORAGE_KEY) ?? "";
    setSavedKey(existing);
  }, []);

  function onSave(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = apiKey.trim();
    if (!trimmed) {
      setMessage("Enter an API key first.");
      return;
    }
    localStorage.setItem(STORAGE_KEY, trimmed);
    setSavedKey(trimmed);
    setApiKey("");
    setMessage("API key saved.");
  }

  function onClear() {
    localStorage.removeItem(STORAGE_KEY);
    setSavedKey("");
    setMessage("API key removed.");
  }

  return (
    <div className="appLayout">
      <aside className="sidebar">
        <div className="brand">Legal Operations</div>
        <div className="brandSub">Settings</div>
        <nav>
          <Link className="navLink" href="/library">All Contracts</Link>
          <a className="navLink" href="#">Archive</a>
          <Link className="navLink active" href="/api-key">API Key</Link>
        </nav>
      </aside>

      <main className="mainArea">
        <div className="container">
          <header className="topHeader">
            <div>
              <h1 className="title">API Key</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>
                Add your model provider key for AI features.
              </p>
            </div>
          </header>

          <section className="card" style={{ padding: 16, maxWidth: 720 }}>
            <h3 style={{ marginTop: 0 }}>Key Management</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Stored locally in your browser for now. We can migrate this to backend secrets in the next step.
            </p>
            <form onSubmit={onSave} style={{ display: "grid", gap: 10 }}>
              <input
                type="password"
                placeholder="Paste API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btnPrimary" type="submit">Save Key</button>
                <button className="btnDanger" type="button" onClick={onClear}>Remove Key</button>
              </div>
            </form>

            <div className="card" style={{ marginTop: 12, padding: 12, background: "#f9fbff" }}>
              <strong>Current key</strong>
              <p className="muted" style={{ marginBottom: 0 }}>
                {savedKey ? maskKey(savedKey) : "No key saved yet."}
              </p>
            </div>

            {message && <p style={{ marginBottom: 0 }}>{message}</p>}
          </section>
        </div>
      </main>
    </div>
  );
}
