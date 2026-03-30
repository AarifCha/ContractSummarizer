"use client";

import BrandLogo from "@/components/BrandLogo";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getApiKeyStatus, removeApiKey, saveApiKey } from "@/lib/api";
import { getToken } from "@/lib/session";

export default function ApiKeyClient() {
  const [apiKey, setApiKey] = useState("");
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getApiKeyStatus(token)
      .then((result) => {
        setSavedKey(result.masked_key);
      })
      .catch(() => {
        setMessage("Could not load API key status.");
      });
  }, []);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    const token = getToken();
    if (!token) {
      setMessage("Please sign in again.");
      return;
    }
    const trimmed = apiKey.trim();
    if (!trimmed) {
      setMessage("Enter an API key first.");
      return;
    }
    setBusy(true);
    try {
      const result = await saveApiKey(token, trimmed);
      setSavedKey(result.masked_key);
      setMessage("API key saved.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to save API key.");
    } finally {
      setBusy(false);
    }
    setApiKey("");
  }

  async function onClear() {
    const token = getToken();
    if (!token) {
      setMessage("Please sign in again.");
      return;
    }
    setBusy(true);
    try {
      await removeApiKey(token);
      setSavedKey(null);
      setMessage("API key removed.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to remove API key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="appLayout">
      <aside className="sidebar">
        <BrandLogo />
        <div className="brandSub">Understand every clause instantly</div>
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
              Stored in your account on the backend and used for Gemini extraction.
            </p>
            <form onSubmit={onSave} style={{ display: "grid", gap: 10 }}>
              <input
                type="password"
                placeholder="Paste API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                disabled={busy}
              />
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btnPrimary" type="submit" disabled={busy}>
                  {busy ? "Saving..." : "Save Key"}
                </button>
                <button className="btnDanger" type="button" onClick={onClear} disabled={busy}>
                  Remove Key
                </button>
              </div>
            </form>

            <div className="card" style={{ marginTop: 12, padding: 12, background: "#f9fbff" }}>
              <strong>Current key</strong>
              <p className="muted" style={{ marginBottom: 0 }}>
                {savedKey ?? "No key saved yet."}
              </p>
            </div>

            {message && <p style={{ marginBottom: 0 }}>{message}</p>}
          </section>
        </div>
      </main>
    </div>
  );
}
