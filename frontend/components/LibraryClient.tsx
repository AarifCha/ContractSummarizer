"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { deletePdf, listPdfs, PdfFile, uploadPdf } from "@/lib/api";
import { clearSession, getEmail, getToken } from "@/lib/session";
import { useRouter } from "next/navigation";

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function LibraryClient() {
  const router = useRouter();
  const token = useMemo(() => getToken(), []);
  const [email, setEmail] = useState<string | null>(null);
  const [files, setFiles] = useState<PdfFile[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);

  async function refresh() {
    if (!token) {
      router.push("/login");
      return;
    }
    try {
      const nextFiles = await listPdfs(token);
      setFiles(nextFiles);
    } catch {
      setError("Could not load PDF list.");
    }
  }

  useEffect(() => {
    setEmail(getEmail());
    refresh();
  }, []);

  async function onUpload() {
    if (!token) return;
    if (!selectedFile) {
      setError("Please choose a PDF file first.");
      return;
    }

    setBusy(true);
    const response = await uploadPdf(token, selectedFile);
    setBusy(false);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail ?? "Upload failed.");
      return;
    }
    setSelectedFile(null);
    setUploadModalOpen(false);
    await refresh();
  }

  async function onDelete(id: number) {
    if (!token) return;
    setBusy(true);
    await deletePdf(token, id);
    setBusy(false);
    await refresh();
  }

  return (
    <div className="appLayout">
      <aside className="sidebar">
        <div className="brand">Legal Operations</div>
        <div className="brandSub">Premier Tier</div>
        <nav>
          <Link className="navLink active" href="/library">All Contracts</Link>
          <a className="navLink" href="#">Archive</a>
          <Link className="navLink" href="/api-key">API Key</Link>
        </nav>
        <div style={{ marginTop: "auto", fontSize: 12 }} className="muted">
          Secure document workspace
        </div>
      </aside>

      <main className="mainArea">
        <div className="container">
          <header className="topHeader">
            <div>
              <h1 className="title">Manage Contracts</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>
                Centralized repository for your legal documents
              </p>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span className="muted" style={{ fontSize: 13 }}>{email ?? "Signed in"}</span>
              <button
                onClick={() => {
                  clearSession();
                  router.push("/login");
                }}
              >
                Sign out
              </button>
            </div>
          </header>

          <div className="workspaceGrid">
            <aside className="card uploadPanel" style={{ alignSelf: "start" }}>
              <h3>Upload Contract</h3>
              <p className="muted uploadHint">
                Each PDF remains isolated by user and prepared for later AI analysis.
              </p>
              <button className="btnPrimary" type="button" onClick={() => setUploadModalOpen(true)}>
                Upload Contract
              </button>
              <div className="roadmapCard">
                <strong style={{ fontSize: 13 }}>Roadmap</strong>
                <p className="muted" style={{ marginBottom: 0, marginTop: 6 }}>
                  This left panel will also show PDF processing progress and indexing states.
                </p>
              </div>
            </aside>

            <section className="card" style={{ overflow: "hidden" }}>
              <div className="repositoryCardHeader">
                <h2 style={{ margin: 0 }}>Active Repository</h2>
              </div>
              <div className="fileRow fileHeader" style={{ borderTop: "1px solid var(--border)" }}>
                <div>Document Name</div>
                <div>Uploaded</div>
                <div>Actions</div>
              </div>
              {error && <p style={{ color: "#b4233f", margin: "10px 14px" }}>{error}</p>}
              {files.length === 0 ? (
                <p className="muted" style={{ margin: "10px 14px 16px" }}>No files uploaded yet.</p>
              ) : (
                files.map((file) => (
                  <article key={file.id} className="fileRow">
                    <div style={{ minWidth: 0 }}>
                      <strong style={{ display: "block", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
                        {file.original_name}
                      </strong>
                      <span className="muted" style={{ fontSize: 12 }}>{formatBytes(file.size_bytes)}</span>
                    </div>
                    <div className="muted" style={{ fontSize: 13 }}>
                      {new Date(file.created_at).toLocaleDateString()}
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <Link href={`/library/${file.id}`}><button className="btnGhost">Open</button></Link>
                      <button className="btnDanger" onClick={() => onDelete(file.id)}>Delete</button>
                    </div>
                  </article>
                ))
              )}
            </section>
          </div>
        </div>
      </main>

      {uploadModalOpen && (
        <div className="modalBackdrop" onClick={() => setUploadModalOpen(false)}>
          <div className="modalCard" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0, marginBottom: 8 }}>Upload Contract</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Drag and drop a PDF here, or click the box to browse files.
            </p>

            <label
              className={`dropZone${dragActive ? " active" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                setDragActive(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setDragActive(false);
                const file = e.dataTransfer.files?.[0];
                if (file) {
                  setSelectedFile(file);
                }
              }}
            >
              <input
                type="file"
                accept=".pdf,application/pdf"
                onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
                style={{ display: "none" }}
              />
              <strong>{selectedFile ? selectedFile.name : "Drop PDF here or click to choose"}</strong>
              <span className="muted" style={{ fontSize: 13 }}>
                PDF only
              </span>
            </label>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
              <button type="button" className="btnGhost" onClick={() => setUploadModalOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btnPrimary" disabled={busy} onClick={onUpload}>
                {busy ? "Uploading..." : "Upload PDF"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
