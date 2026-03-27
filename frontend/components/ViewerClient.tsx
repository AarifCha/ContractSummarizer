"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { pdfViewUrl } from "@/lib/api";
import { getToken } from "@/lib/session";

export default function ViewerClient({ id }: { id: number }) {
  const token = useMemo(() => getToken(), []);
  const src = useMemo(() => pdfViewUrl(id, token), [id, token]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className={`appLayout${sidebarCollapsed ? " appLayoutCollapsed" : ""}`}>
      <aside className={`sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
        <button
          type="button"
          className="btnGhost sidebarToggle"
          onClick={() => setSidebarCollapsed((prev) => !prev)}
          aria-label={sidebarCollapsed ? "Expand panel" : "Collapse panel"}
        >
          {sidebarCollapsed ? ">" : "<"}
        </button>
        <div className="brand">Legal Operations</div>
        <div className="brandSub">Workspace</div>
        <nav>
          <a className="navLink active" href="#">AI Summary</a>
          <a className="navLink" href="#">QA Chat</a>
          <a className="navLink" href="#">Archive</a>
          <Link className="navLink" href="/api-key">API Key</Link>
        </nav>
      </aside>

      <main className="mainArea">
        <div className="container">
          <div className="topHeader">
            <div>
              <h1 className="title" style={{ fontSize: 30 }}>Contract Workspace</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>
                Summary and citations synchronized to source document
              </p>
            </div>
            <Link href="/library">
              <button type="button" className="btnGhost">Back to Library</button>
            </Link>
          </div>

          <div className="workspaceGrid" style={{ gridTemplateColumns: "minmax(300px, 460px) minmax(540px, 1fr)" }}>
            <aside className="card" style={{ padding: 14 }}>
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <button type="button" className="btnPrimary" style={{ fontSize: 12, padding: "7px 10px" }}>AI Summary</button>
                <button type="button" style={{ fontSize: 12, padding: "7px 10px" }}>QA Chat</button>
              </div>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>Executive Summary</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                This panel is ready for generated summaries with citation anchors.
              </p>

              <div className="card" style={{ padding: 12, marginBottom: 10, background: "#f8fbff" }}>
                <strong style={{ display: "block", marginBottom: 6 }}>Citation [1] - Liability</strong>
                <p className="muted" style={{ margin: 0 }}>
                  "...total aggregate liability shall be capped to fees paid over 12 months..."
                </p>
              </div>

              <div className="card" style={{ padding: 12, background: "#f8fbff" }}>
                <strong style={{ display: "block", marginBottom: 6 }}>Citation [2] - Termination</strong>
                <p className="muted" style={{ margin: 0 }}>
                  "...either party may terminate with 30 days written notice..."
                </p>
              </div>
            </aside>

            <section className="card" style={{ height: "calc(100vh - 130px)", padding: 10 }}>
              <iframe
                title="pdf-viewer"
                src={src}
                style={{ width: "100%", height: "100%", border: "none", borderRadius: 10 }}
              />
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
