"use client";

import { useEffect, useMemo, useState } from "react";
import BrandLogo from "@/components/BrandLogo";
import Link from "next/link";
import { deletePdf, getPdfProcessingStatus, listPdfs, PdfFile, uploadPdf } from "@/lib/api";
import { clearSession, getEmail, getToken } from "@/lib/session";
import { useRouter } from "next/navigation";

const TRACKED_UPLOAD_PDF_ID_KEY = "pdf_library_tracked_upload_pdf_id";

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type StepStatus = "running" | "done" | "failed" | "queued";

type UploadJob = {
  id: string;
  pdfId?: number;
  fileName: string;
  metadata: {
    stage?: string | null;
    status: StepStatus;
    progressPercent: number;
    completedChunks: number;
    totalChunks: number;
    error?: string;
  };
};

type StageCardState = {
  status: StepStatus;
  progressPercent: number;
  completedChunks: number;
  totalChunks: number;
  error?: string;
};

function overallJobBadge(job: UploadJob): "running" | "done" | "failed" {
  if (job.metadata.status === "failed") return "failed";
  if (job.metadata.status === "done") return "done";
  return "running";
}

function stageCardState(job: UploadJob, targetStage: "first_pass_extraction" | "chunk_embedding"): StageCardState {
  const m = job.metadata;
  const stage = m.stage ?? "first_pass_extraction";
  const base: StageCardState = {
    status: "queued",
    progressPercent: 0,
    completedChunks: 0,
    totalChunks: 1,
  };
  if (targetStage === "first_pass_extraction") {
    if (stage === "first_pass_extraction") {
      return { ...base, ...m };
    }
    if (m.status === "failed" && stage === "first_pass_extraction") {
      return { ...base, status: "failed", error: m.error };
    }
    if (stage === "chunk_embedding" || m.status === "done") {
      return { ...base, status: "done", progressPercent: 100, completedChunks: 1, totalChunks: 1 };
    }
    return base;
  }
  // targetStage === "chunk_embedding"
  if (stage === "chunk_embedding") {
    return { ...base, ...m };
  }
  if (m.status === "done" && stage === "chunk_embedding") {
    return { ...base, status: "done", progressPercent: 100, completedChunks: 1, totalChunks: 1 };
  }
  if (m.status === "failed" && stage === "chunk_embedding") {
    return { ...base, status: "failed", error: m.error };
  }
  return base;
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
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [trackedPdfId, setTrackedPdfId] = useState<number | null>(null);

  function toJobFromFile(file: PdfFile): UploadJob {
    const totalChunks = Math.max(0, Number(file.processing_total_chunks ?? 0));
    const completedChunks = Math.max(0, Number(file.processing_completed_chunks ?? 0));
    const rawStatus = file.processing_status ?? "queued";
    const clampedCompleted = totalChunks > 0 ? Math.min(completedChunks, totalChunks) : completedChunks;
    let progressPercent = totalChunks > 0 ? Math.round((clampedCompleted / totalChunks) * 100) : 0;
    if (rawStatus === "done") progressPercent = 100;

    let metaStatus: StepStatus = "queued";
    if (rawStatus === "failed") metaStatus = "failed";
    else if (rawStatus === "done") metaStatus = "done";
    else if (rawStatus === "running") metaStatus = "running";

    return {
      id: `pdf-${file.id}`,
      pdfId: file.id,
      fileName: file.original_name,
      metadata: {
        stage: file.processing_stage ?? null,
        status: metaStatus,
        progressPercent,
        completedChunks: clampedCompleted,
        totalChunks,
        error: file.processing_error ?? undefined,
      },
    };
  }

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
    const stored = localStorage.getItem(TRACKED_UPLOAD_PDF_ID_KEY);
    if (stored && !Number.isNaN(Number(stored))) {
      setTrackedPdfId(Number(stored));
    }
    refresh();
  }, []);

  useEffect(() => {
    if (trackedPdfId == null) return;
    const trackedFile = files.find((file) => file.id === trackedPdfId);
    if (!trackedFile) {
      setUploadJobs([]);
      setTrackedPdfId(null);
      localStorage.removeItem(TRACKED_UPLOAD_PDF_ID_KEY);
      return;
    }
    setUploadJobs([toJobFromFile(trackedFile)]);
  }, [files, trackedPdfId]);

  useEffect(() => {
    if (trackedPdfId != null || files.length === 0) return;
    const latestProcessable = [...files]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .find((file) => Boolean(file.processing_stage) || Number(file.processing_total_chunks ?? 0) > 0);
    if (!latestProcessable) return;
    setTrackedPdfId(latestProcessable.id);
    localStorage.setItem(TRACKED_UPLOAD_PDF_ID_KEY, String(latestProcessable.id));
  }, [files, trackedPdfId]);

  useEffect(() => {
    if (!token) return;
    const activeJobs = uploadJobs.filter(
      (job) =>
        typeof job.pdfId === "number" &&
        (job.metadata.status === "running" || job.metadata.status === "queued")
    );
    if (activeJobs.length === 0) return;

    const intervalId = setInterval(() => {
      activeJobs.forEach((job) => {
        if (typeof job.pdfId !== "number") return;
        getPdfProcessingStatus(token, job.pdfId)
          .then((status) => {
            setUploadJobs((current) =>
              current.map((existing) => {
                if (existing.id !== job.id) return existing;
                if (status.status === "failed") {
                  return {
                    ...existing,
                    metadata: {
                      ...existing.metadata,
                      stage: status.stage,
                      status: "failed" as const,
                      progressPercent: status.progress_percent,
                      completedChunks: status.completed_chunks,
                      totalChunks: status.total_chunks,
                      error: status.error ?? "Processing failed",
                    },
                  };
                }
                if (status.status === "done") {
                  return {
                    ...existing,
                    metadata: {
                      ...existing.metadata,
                      stage: status.stage,
                      status: "done" as const,
                      progressPercent: 100,
                      completedChunks: status.total_chunks,
                      totalChunks: status.total_chunks,
                      error: undefined,
                    },
                  };
                }
                return {
                  ...existing,
                  metadata: {
                    ...existing.metadata,
                    stage: status.stage,
                    status: "running" as const,
                    progressPercent: status.progress_percent,
                    completedChunks: status.completed_chunks,
                    totalChunks: status.total_chunks,
                  },
                };
              })
            );
          })
          .catch(() => {
            setUploadJobs((current) =>
              current.map((existing) =>
                existing.id === job.id
                  ? {
                      ...existing,
                      metadata: {
                        ...existing.metadata,
                        status: "failed",
                        error: "Could not fetch progress",
                      },
                    }
                  : existing
              )
            );
          });
      });
    }, 1500);

    return () => clearInterval(intervalId);
  }, [token, uploadJobs]);

  async function onUpload() {
    if (!token) return;
    if (!selectedFile) {
      setError("Please choose a PDF file first.");
      return;
    }
    setError("");
    const fileToUpload = selectedFile;
    const jobId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setUploadJobs([
      {
        id: jobId,
        fileName: fileToUpload.name,
        metadata: {
          stage: "first_pass_extraction",
          status: "queued",
          progressPercent: 0,
          completedChunks: 0,
          totalChunks: 1,
        },
      },
    ]);
    setBusy(true);
    setSelectedFile(null);
    setUploadModalOpen(false);

    const response = await uploadPdf(token, fileToUpload);
    setBusy(false);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail ?? "Upload failed.");
      setUploadJobs((current) =>
        current.map((job) =>
          job.id === jobId
            ? {
                ...job,
                metadata: { ...job.metadata, status: "failed", error: body.detail ?? "Upload did not complete." },
              }
            : job
        )
      );
      return;
    }
    const payload = (await response.json().catch(() => ({}))) as { file?: { id?: number } };
    const pdfId = payload.file?.id;
    if (typeof pdfId === "number") {
      setTrackedPdfId(pdfId);
      localStorage.setItem(TRACKED_UPLOAD_PDF_ID_KEY, String(pdfId));
    }
    setUploadJobs((current) =>
      current.map((job) =>
        job.id === jobId
          ? {
              ...job,
              pdfId,
              metadata: {
                ...job.metadata,
                stage: "first_pass_extraction",
                status: "running",
                totalChunks: 1,
              },
            }
          : job
      )
    );
    await refresh();
  }

  async function onDelete(id: number) {
    if (!token) return;
    setBusy(true);
    await deletePdf(token, id);
    setBusy(false);
    if (trackedPdfId === id) {
      setTrackedPdfId(null);
      setUploadJobs([]);
      localStorage.removeItem(TRACKED_UPLOAD_PDF_ID_KEY);
    }
    await refresh();
  }

  return (
    <div className="appLayout">
      <aside className="sidebar">
        <BrandLogo />
        <div className="brandSub">Understand every clause instantly</div>
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
            <div className="topHeaderActions">
              <span className="muted" style={{ fontSize: 13 }} title={email ?? undefined}>
                {email ?? "Signed in"}
              </span>
              <button
                type="button"
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
            <aside className="card uploadPanel">
              <h3>Upload Contract</h3>
              <p className="muted uploadHint">
                Each PDF remains isolated by user and prepared for later AI analysis.
              </p>
              <button className="btnPrimary" type="button" onClick={() => setUploadModalOpen(true)}>
                Upload Contract
              </button>
              {uploadJobs.length > 0 && (
                <div className="pipelineList">
                  {uploadJobs.map((job) => {
                    const overall = overallJobBadge(job);
                    const firstPass = stageCardState(job, "first_pass_extraction");
                    const embedding = stageCardState(job, "chunk_embedding");
                    return (
                      <article className="pipelineFileCard" key={job.id}>
                        <div className="pipelineFileCardHeader">
                          <div style={{ minWidth: 0 }}>
                            <div className="pipelineFileCardTitle" title={job.fileName}>
                              {job.fileName}
                            </div>
                            <p className="muted pipelineFileCardSubtitle" style={{ margin: "4px 0 0", fontSize: 12 }}>
                              Docling first pass on the full PDF; JSON blocks written to{" "}
                              <code className="pipelineInlineCode">first_pass_data/</code>.
                            </p>
                          </div>
                          {overall === "running" && <span className="pipelineBadge running">Processing</span>}
                          {overall === "done" && <span className="pipelineBadge done">Done</span>}
                          {overall === "failed" && <span className="pipelineBadge failed">Failed</span>}
                        </div>

                        <div className="pipelineSubSteps">
                          <div
                            className={`pipelineSubCard ${
                              firstPass.status === "failed"
                                ? "failed"
                                : firstPass.status === "done"
                                  ? "done"
                                  : firstPass.status === "queued"
                                    ? "queued"
                                    : "running"
                            }`}
                          >
                            <div className="pipelineSubCardTop">
                              <div>
                                <strong className="pipelineSubCardTitle">First pass (Docling)</strong>
                                <p className="muted pipelineSubCardDesc">
                                  Layout-aware parse of the full PDF; each block JSON includes raw text, section
                                  context, bounding boxes, and regex-detected cross-references.
                                </p>
                              </div>
                              {firstPass.status === "queued" && (
                                <span className="pipelineBadge pending">Pending</span>
                              )}
                              {firstPass.status === "running" && (
                                <span className="pipelineBadge running">Working</span>
                              )}
                              {firstPass.status === "done" && <span className="pipelineBadge done">Finished</span>}
                              {firstPass.status === "failed" && (
                                <span className="pipelineBadge failed">Failed</span>
                              )}
                            </div>
                            <div className="pipelineStepRow" style={{ marginTop: 8, marginBottom: 6 }}>
                              <span className="pipelineStepLabel">
                                {firstPass.status === "queued"
                                  ? "Queued…"
                                  : firstPass.status === "done"
                                    ? "First pass complete"
                                    : firstPass.totalChunks > 1
                                      ? `${firstPass.completedChunks}/${firstPass.totalChunks} stages`
                                      : "Running Docling first pass…"}
                              </span>
                              <span className="pipelineStepLabel">
                                {firstPass.status === "queued" ? "—" : `${firstPass.progressPercent}%`}
                              </span>
                            </div>
                            <div
                              className={`progressTrack ${
                                firstPass.status === "failed"
                                  ? "failed"
                                  : firstPass.status === "done"
                                    ? "done"
                                    : "running"
                              }`}
                            >
                              <div
                                className="progressFill"
                                style={{
                                  width:
                                    firstPass.status === "queued"
                                      ? "0%"
                                      : `${firstPass.progressPercent}%`,
                                }}
                              />
                            </div>
                            {firstPass.error && (
                              <p className="pipelineError">
                                {firstPass.error.length > 220 ? firstPass.error.slice(0, 220) + "…" : firstPass.error}
                              </p>
                            )}
                          </div>
                          <div
                            className={`pipelineSubCard ${
                              embedding.status === "failed"
                                ? "failed"
                                : embedding.status === "done"
                                  ? "done"
                                  : embedding.status === "queued"
                                    ? "queued"
                                    : "running"
                            }`}
                          >
                            <div className="pipelineSubCardTop">
                              <div>
                                <strong className="pipelineSubCardTitle">Chunk embeddings (VectorDB)</strong>
                                <p className="muted pipelineSubCardDesc">
                                  Embeds each processed chunk and writes vectors to <code className="pipelineInlineCode">VectorDB/</code>.
                                </p>
                              </div>
                              {embedding.status === "queued" && <span className="pipelineBadge pending">Pending</span>}
                              {embedding.status === "running" && <span className="pipelineBadge running">Working</span>}
                              {embedding.status === "done" && <span className="pipelineBadge done">Finished</span>}
                              {embedding.status === "failed" && <span className="pipelineBadge failed">Failed</span>}
                            </div>
                            <div className="pipelineStepRow" style={{ marginTop: 8, marginBottom: 6 }}>
                              <span className="pipelineStepLabel">
                                {embedding.status === "queued"
                                  ? "Waiting for first pass…"
                                  : embedding.status === "done"
                                    ? "Embedding complete"
                                    : `${embedding.completedChunks}/${embedding.totalChunks} chunks`}
                              </span>
                              <span className="pipelineStepLabel">
                                {embedding.status === "queued" ? "—" : `${embedding.progressPercent}%`}
                              </span>
                            </div>
                            <div
                              className={`progressTrack ${
                                embedding.status === "failed"
                                  ? "failed"
                                  : embedding.status === "done"
                                    ? "done"
                                    : "running"
                              }`}
                            >
                              <div
                                className="progressFill"
                                style={{ width: embedding.status === "queued" ? "0%" : `${embedding.progressPercent}%` }}
                              />
                            </div>
                            {embedding.error && (
                              <p className="pipelineError">
                                {embedding.error.length > 220 ? embedding.error.slice(0, 220) + "…" : embedding.error}
                              </p>
                            )}
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </aside>

            <section className="card repositoryPanel">
              <div className="repositoryCardHeader">
                <h2 style={{ margin: 0 }}>Active Repository</h2>
              </div>
              <div className="fileRow fileHeader" style={{ borderTop: "1px solid var(--border)" }}>
                <div className="fileRowDoc">Document Name</div>
                <div className="fileRowTail">
                  <div className="fileRowDateCol">Uploaded</div>
                  <div className="fileRowActionCol">Actions</div>
                </div>
              </div>
              {error && <p style={{ color: "#b4233f", margin: "10px 14px" }}>{error}</p>}
              {files.length === 0 ? (
                <p className="muted" style={{ margin: "10px 14px 16px" }}>No files uploaded yet.</p>
              ) : (
                files.map((file) => (
                  <article key={file.id} className="fileRow">
                    <div className="fileRowDoc">
                      <strong style={{ display: "block", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
                        {file.original_name}
                      </strong>
                      <span className="muted" style={{ fontSize: 12 }}>{formatBytes(file.size_bytes)}</span>
                    </div>
                    <div className="fileRowTail">
                      <div className="muted fileRowDateCol">
                        {new Date(file.created_at).toLocaleDateString()}
                      </div>
                      <div className="fileRowActionCol">
                        <Link href={`/library/${file.id}`}><button type="button" className="btnGhost">Open</button></Link>
                        <button type="button" className="btnDanger" onClick={() => onDelete(file.id)}>Delete</button>
                      </div>
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
