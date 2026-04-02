"use client";

import BrandLogo from "@/components/BrandLogo";
import CitationBadge from "@/components/CitationBadge";
import { getSectionOptions, getSectionSearchStatus, pdfViewUrl, QaSearchResult, searchPdfQa, searchPdfSection, SectionSearchResponse } from "@/lib/api";
import { cleanMarkdownForRender } from "@/lib/markdownSanitize";
import { getToken } from "@/lib/session";
import dynamic from "next/dynamic";
import Link from "next/link";
import { cloneElement, FormEvent, ReactElement, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const PdfViewerPane = dynamic(() => import("./PdfViewerPane"), {
  ssr: false,
  loading: () => <p className="muted" style={{ padding: 12 }}>Loading PDF viewer...</p>
});

type TabId = "chat" | "summary";

function extractChunkTokens(raw: string): string[] {
  const pieces = raw.split(",").map((p) => p.trim());
  const out: string[] = [];
  const seen = new Set<string>();
  for (const p of pieces) {
    if (!p) continue;
    const normalized = p.replace(/^chunk-/, "chunk_");
    if (/^chunk_\d+$/i.test(normalized)) {
      const token = normalized.toLowerCase();
      if (!seen.has(token)) {
        seen.add(token);
        out.push(token);
      }
    }
  }
  return out;
}

function chunkTokensToIndices(tokens: string[]): number[] {
  const out: number[] = [];
  const seen = new Set<number>();
  for (const token of tokens) {
    const index = Number(token.replace(/^chunk_/i, ""));
    if (!Number.isFinite(index) || seen.has(index)) continue;
    seen.add(index);
    out.push(index);
  }
  return out;
}

function isKeyOverviewHeading(heading: string | null): boolean {
  if (!heading) return false;
  return /^##\s*2\.\s*Key Contract Overview\s*$/i.test(heading);
}

function nodeText(value: ReactNode): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(nodeText).join("");
  if (value && typeof value === "object" && "props" in value) {
    const child = (value as { props?: { children?: ReactNode } }).props?.children;
    return nodeText(child ?? "");
  }
  return "";
}

type ApiUsageSummary = NonNullable<SectionSearchResponse["api_usage_summary"]>;

function UsageSummaryCard({ usage }: { usage: ApiUsageSummary | null }) {
  if (!usage) return null;
  const modelRows = Object.values(usage.per_model ?? {});
  if (modelRows.length === 0) return null;
  return (
    <div className="card" style={{ padding: 10, marginBottom: 10 }}>
      <strong style={{ display: "block", marginBottom: 8 }}>API usage (current summary run)</strong>
      <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>
        Token counts from the model response when available; if output tokens are missing, they are approximated from generated text length.
      </p>
      <div style={{ display: "grid", gap: 8 }}>
        {modelRows.map((row) => (
          <div key={row.model_id} style={{ padding: 8, borderRadius: 8, background: "#f8fbff", border: "1px solid #dbe5f2" }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{row.model_id}</div>
            <div className="muted" style={{ fontSize: 12 }}>
              Input: {row.prompt_tokens.toLocaleString()} tokens
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              Output: {row.completion_tokens.toLocaleString()} tokens
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              Total (reported): {row.total_tokens.toLocaleString()} tokens
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8, fontSize: 12 }} className="muted">
        Combined input: {usage.totals.prompt_tokens.toLocaleString()} · Combined output:{" "}
        {usage.totals.completion_tokens.toLocaleString()} · Combined total: {usage.totals.total_tokens.toLocaleString()}
      </div>
    </div>
  );
}

/** Sticky section title: masks lines peeking above only when actually stuck (IntersectionObserver + sentinel), not in normal flow. */
function StickySummaryH2({ children }: { children: ReactNode }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(false);

  useEffect(() => {
    const wrap = wrapRef.current;
    const sentinel = sentinelRef.current;
    if (!wrap || !sentinel) return;
    const root = wrap.closest(".summaryMarkdownPane");
    if (!root || !(root instanceof HTMLElement)) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;
        const rb = entry.rootBounds ?? root.getBoundingClientRect();
        const b = entry.boundingClientRect;
        // Sentinel fully above the visible scrollport → sticky heading is pinned at top
        setPinned(b.bottom < rb.top);
      },
      { root, rootMargin: "0px", threshold: [0, 1] }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={wrapRef} className="summaryStickyH2Wrap">
      <div ref={sentinelRef} className="summaryStickySentinel" aria-hidden />
      <h2 className={`summaryStickyH2${pinned ? " summaryStickyH2--pinned" : ""}`}>{children}</h2>
    </div>
  );
}

export default function ViewerClient({ id }: { id: number }) {
  const token = useMemo(() => getToken(), []);
  const src = useMemo(() => pdfViewUrl(id, token), [id, token]);
  const [activeTab, setActiveTab] = useState<TabId>("summary");

  const [question, setQuestion] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<QaSearchResult[]>([]);
  const [summaryText, setSummaryText] = useState("");
  const [selectedHighlightsByPage, setSelectedHighlightsByPage] = useState<Record<string, number[][]>>({});
  const [secondaryHighlightsByPage, setSecondaryHighlightsByPage] = useState<Record<string, number[][]>>({});
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(null);
  const [multiChunkIndices, setMultiChunkIndices] = useState<number[]>([]);
  const [activeChunkCursor, setActiveChunkCursor] = useState(0);
  const [sections, setSections] = useState<string[]>([]);
  const [selectedSection, setSelectedSection] = useState("");
  const [loadingProgress, setLoadingProgress] = useState(0);
  const [loadingLabel, setLoadingLabel] = useState("");
  const [targetPage, setTargetPage] = useState(1);
  const [targetScrollBehavior, setTargetScrollBehavior] = useState<"smooth" | "auto">("smooth");
  const [activeBboxes, setActiveBboxes] = useState<number[][]>([]);
  const [apiUsageSummary, setApiUsageSummary] = useState<ApiUsageSummary | null>(null);
  const handleViewerError = useCallback((message: string | null) => {
    setError(message);
  }, []);

  useEffect(() => {
    let cancelled = false;
    getSectionOptions(token)
      .then((response) => {
        if (cancelled) return;
        const opts = response.sections ?? [];
        setSections(opts);
        setSelectedSection((prev) => prev || opts[0] || "");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unable to load section options");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function onAsk(event: FormEvent) {
    event.preventDefault();
    const q = question.trim();
    if (!q || isLoading) {
      return;
    }
    setIsLoading(true);
    setLoadingLabel("Searching chunks...");
    setLoadingProgress(10);
    setError(null);
    setSummaryText("");
    setSelectedHighlightsByPage({});
    setSecondaryHighlightsByPage({});
    setSelectedCitationId(null);
    setMultiChunkIndices([]);
    setActiveChunkCursor(0);
    setApiUsageSummary(null);
    try {
      const response = await searchPdfQa(token, id, q);
      setResults(response.results ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to search");
      setResults([]);
    } finally {
      setLoadingProgress(100);
      setTimeout(() => setLoadingProgress(0), 250);
      setIsLoading(false);
      setLoadingLabel("");
    }
  }

  async function onLoadSection(event: FormEvent) {
    event.preventDefault();
    const section = selectedSection.trim();
    if (!section || isLoading) {
      return;
    }
    setIsLoading(true);
    setLoadingLabel(section === "All Sections" ? "Generating all-sections summary..." : "Loading section chunks...");
    setLoadingProgress(section === "All Sections" ? 0 : 10);
    setError(null);
    setSelectedCitationId(null);
    setSelectedHighlightsByPage({});
    setSecondaryHighlightsByPage({});
    setMultiChunkIndices([]);
    setActiveChunkCursor(0);
    setActiveBboxes([]);
    setApiUsageSummary(null);
    try {
      const response = await searchPdfSection(token, id, section);
      if (response.mode === "all_sections_summary") {
        const taskId = response.task_id;
        setApiUsageSummary(response.api_usage_summary ?? null);
        if (!taskId && response.status === "done") {
          setSummaryText(response.summary_text ?? "");
          setResults(response.highlight_chunks ?? response.results ?? []);
          setApiUsageSummary(response.api_usage_summary ?? null);
          return;
        }
        if (!taskId) {
          throw new Error("Missing task id for all-sections summary");
        }
        let attempts = 0;
        while (attempts < 1200) {
          attempts += 1;
          const status = await getSectionSearchStatus(token, taskId);
          const total = Math.max(0, status.total_windows ?? 0);
          const done = Math.max(0, status.completed_windows ?? 0);
          if (status.phase === "flash_lite") {
            setLoadingProgress(100);
            setLoadingLabel("Synthesizing final summary...");
          } else if (total > 0) {
            const pct = Math.round((done / total) * 100);
            setLoadingProgress(Math.max(0, Math.min(100, pct)));
            setLoadingLabel(`Summarizing windows ${done}/${total}...`);
          } else {
            setLoadingProgress(0);
            setLoadingLabel("Preparing windows...");
          }
          setSummaryText(status.summary_text ?? "");
          setResults(status.highlight_chunks ?? status.results ?? []);
          setApiUsageSummary(status.api_usage_summary ?? null);

          if (status.status === "done") {
            break;
          }
          if (status.status === "failed") {
            throw new Error(status.error ?? "All-sections summary failed");
          }
          await new Promise((resolve) => setTimeout(resolve, 1200));
        }
      } else {
        setSummaryText("");
        setSelectedHighlightsByPage({});
        setSecondaryHighlightsByPage({});
        setMultiChunkIndices([]);
        setActiveChunkCursor(0);
        setResults(response.results ?? []);
        setApiUsageSummary(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load section");
      setResults([]);
      setSummaryText("");
      setSelectedHighlightsByPage({});
      setSecondaryHighlightsByPage({});
      setMultiChunkIndices([]);
      setActiveChunkCursor(0);
      setApiUsageSummary(null);
    } finally {
      setLoadingProgress(100);
      setTimeout(() => setLoadingProgress(0), 250);
      setIsLoading(false);
      setLoadingLabel("");
    }
  }

  function onSelectChunk(result: QaSearchResult) {
    setSelectedCitationId(null);
    setSelectedHighlightsByPage({});
    setSecondaryHighlightsByPage({});
    setMultiChunkIndices([]);
    setActiveChunkCursor(0);
    setTargetScrollBehavior("smooth");
    setTargetPage(Math.max(1, result.page_numbers?.[0] ?? 1));
    const bboxes = Array.isArray(result.bboxes)
      ? result.bboxes.filter((bbox): bbox is number[] => Array.isArray(bbox) && bbox.length >= 4)
      : [];
    setActiveBboxes(bboxes);
  }

  const chunkLookup = useMemo(() => {
    const byChunk = new Map<number, { highlights: Record<string, number[][]>; firstPage: number | null }>();
    for (const row of results) {
      const idx = row.chunk_index;
      const page = row.page_numbers?.[0];
      if (!Number.isFinite(idx) || !Number.isFinite(page)) continue;
      const pageNum = Math.max(1, Number(page));
      const boxes = Array.isArray(row.bboxes) ? row.bboxes.filter((b) => Array.isArray(b) && b.length >= 4) : [];
      if (!boxes.length) continue;
      const existing = byChunk.get(idx) ?? { highlights: {}, firstPage: pageNum };
      const pageKey = String(pageNum);
      existing.highlights[pageKey] = [...(existing.highlights[pageKey] ?? []), ...boxes];
      if (existing.firstPage == null || pageNum < existing.firstPage) {
        existing.firstPage = pageNum;
      }
      byChunk.set(idx, existing);
    }
    return byChunk;
  }, [results]);

  const highlightsForChunkIndicesFast = useCallback(
    (chunkIndices: number[]) => {
      const byPage: Record<string, number[][]> = {};
      for (const idx of chunkIndices) {
        const entry = chunkLookup.get(idx);
        if (!entry) continue;
        for (const [pageKey, boxes] of Object.entries(entry.highlights)) {
          byPage[pageKey] = [...(byPage[pageKey] ?? []), ...boxes];
        }
      }
      return byPage;
    },
    [chunkLookup]
  );

  useEffect(() => {
    if (activeTab !== "summary" || multiChunkIndices.length <= 1) {
      return;
    }
    const normalizedCursor = ((activeChunkCursor % multiChunkIndices.length) + multiChunkIndices.length) % multiChunkIndices.length;
    const activeChunkIndex = multiChunkIndices[normalizedCursor];
    setSelectedHighlightsByPage(highlightsForChunkIndicesFast([activeChunkIndex]));
    const page = chunkLookup.get(activeChunkIndex)?.firstPage ?? null;
    if (page != null) {
      setTargetPage((prev) => (prev === page ? prev : page));
    }
  }, [activeTab, activeChunkCursor, chunkLookup, highlightsForChunkIndicesFast, multiChunkIndices]);

  useEffect(() => {
    if (activeTab !== "summary" || multiChunkIndices.length <= 1) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat) return;
      const target = event.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName?.toLowerCase();
        const isEditable =
          tag === "input" ||
          tag === "textarea" ||
          tag === "select" ||
          target.isContentEditable;
        if (isEditable) return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setTargetScrollBehavior("auto");
        setActiveChunkCursor((prev) => (prev - 1 + multiChunkIndices.length) % multiChunkIndices.length);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setTargetScrollBehavior("auto");
        setActiveChunkCursor((prev) => (prev + 1) % multiChunkIndices.length);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeTab, multiChunkIndices]);

  const handleSummaryCitationClick = useCallback(
    (citationId: string, chunkIndices: number[]) => {
      setSelectedCitationId(citationId);
      if (chunkIndices.length <= 1) {
        const singleChunk = chunkIndices[0];
        const highlights = highlightsForChunkIndicesFast(singleChunk != null ? [singleChunk] : []);
        setSelectedHighlightsByPage(highlights);
        setSecondaryHighlightsByPage({});
        setMultiChunkIndices([]);
        setActiveChunkCursor(0);
        setTargetScrollBehavior("smooth");
        const pages = Object.keys(highlights)
          .map((k) => Number(k))
          .filter((n) => Number.isFinite(n))
          .sort((a, b) => a - b);
        if (pages.length > 0) setTargetPage(Math.max(1, pages[0]));
      } else {
        const allHighlights = highlightsForChunkIndicesFast(chunkIndices);
        setSecondaryHighlightsByPage(allHighlights);
        setMultiChunkIndices(chunkIndices);
        setActiveChunkCursor(0);
        const activeChunk = chunkIndices[0];
        const activeHighlights = highlightsForChunkIndicesFast([activeChunk]);
        setSelectedHighlightsByPage(activeHighlights);
        const page = chunkLookup.get(activeChunk)?.firstPage ?? null;
        setTargetScrollBehavior("smooth");
        if (page != null) setTargetPage(page);
      }
      setActiveBboxes([]);
    },
    [chunkLookup, highlightsForChunkIndicesFast]
  );

  return (
    <div className="appLayout">
      <aside className="sidebar">
        <BrandLogo />
        <div className="brandSub">Understand every clause instantly</div>
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
                Review AI summaries and ask contract questions in separate tabs
              </p>
            </div>
            <Link href="/library">
              <button type="button" className="btnGhost">Back to Library</button>
            </Link>
          </div>

          <div className="topTabs" role="tablist" aria-label="Contract workspace tabs">
            <button
              type="button"
              className={`topTab ${activeTab === "chat" ? "topTab--active" : ""}`}
              onClick={() => setActiveTab("chat")}
              role="tab"
              aria-selected={activeTab === "chat"}
            >
              QA Chat
            </button>
            <button
              type="button"
              className={`topTab ${activeTab === "summary" ? "topTab--active" : ""}`}
              onClick={() => setActiveTab("summary")}
              role="tab"
              aria-selected={activeTab === "summary"}
            >
              Summary
            </button>
          </div>

          {activeTab === "chat" ? (
            <div className="workspaceGrid workspaceGrid--viewer">
              <aside className="card" style={{ padding: 14 }}>
                <form onSubmit={onAsk} style={{ display: "grid", gap: 8, marginBottom: 12 }}>
                  <textarea
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="Ask about this contract..."
                    rows={3}
                    style={{ width: "100%", borderRadius: 8, border: "1px solid #dbe5f2", padding: 8, resize: "vertical" }}
                  />
                  <button type="submit" className="btnPrimary" disabled={isLoading || !question.trim()}>
                    {isLoading ? "Searching..." : "Ask"}
                  </button>
                </form>
                {error ? (
                  <div className="card" style={{ padding: 10, marginBottom: 10, borderColor: "#f5c2c7", background: "#fff5f5" }}>
                    <strong style={{ display: "block", marginBottom: 4 }}>Search error</strong>
                    <p className="muted" style={{ margin: 0 }}>{error}</p>
                  </div>
                ) : null}
                <h3 style={{ marginTop: 0, marginBottom: 8 }}>Matches and context</h3>
                <div style={{ display: "grid", gap: 10 }}>
                  {results.map((result, idx) => (
                    <button
                      key={result.chunk_id}
                      type="button"
                      className="card"
                      onClick={() => onSelectChunk(result)}
                      style={{ textAlign: "left", padding: 12, background: "#f8fbff", cursor: "pointer" }}
                    >
                      <strong style={{ display: "block", marginBottom: 6 }}>
                        [Rank {idx + 1}] Chunk {result.chunk_index}
                        {result.rank != null ? ` — vector hit #${result.rank}` : " — context"}
                        {result.page_numbers?.[0] ? ` (Page ${result.page_numbers[0]})` : ""}
                      </strong>
                      <p className="muted" style={{ margin: 0 }}>{result.text}</p>
                    </button>
                  ))}
                  {!isLoading && results.length === 0 ? (
                    <div className="card" style={{ padding: 12 }}>
                      <p className="muted" style={{ margin: 0 }}>No results yet. Ask a question to retrieve chunks.</p>
                    </div>
                  ) : null}
                </div>
              </aside>
              <section className="card" style={{ height: "calc(100vh - 190px)", padding: 10, minWidth: 0 }}>
                <div style={{ height: "100%", width: "100%", minWidth: 0, overflow: "auto", borderRadius: 10, background: "#f9fbff" }}>
                  <PdfViewerPane
                    fileUrl={src}
                    targetPage={targetPage}
                    targetScrollBehavior={targetScrollBehavior}
                    activeBboxes={activeBboxes}
                    activeBboxesByPage={selectedHighlightsByPage}
                    secondaryBboxesByPage={secondaryHighlightsByPage}
                    onError={handleViewerError}
                  />
                </div>
              </section>
            </div>
          ) : (
            <div className="summaryTabRoot">
              <form onSubmit={onLoadSection} style={{ display: "grid", gap: 8, marginBottom: 12, maxWidth: 420 }}>
                <select
                  value={selectedSection}
                  onChange={(e) => setSelectedSection(e.target.value)}
                  style={{ width: "100%", borderRadius: 8, border: "1px solid #dbe5f2", padding: 8 }}
                >
                  {sections.map((section) => (
                    <option key={section} value={section}>
                      {section}
                    </option>
                  ))}
                </select>
                <button type="submit" className="btnPrimary" disabled={isLoading || !selectedSection}>
                  {isLoading ? "Loading summary..." : "Generate Summary"}
                </button>
              </form>
              <UsageSummaryCard usage={apiUsageSummary} />

              {isLoading ? (
                <div className="card" style={{ padding: 10, marginBottom: 10 }}>
                  <strong style={{ display: "block", marginBottom: 6 }}>Working...</strong>
                  <div style={{ height: 8, borderRadius: 999, background: "#e7eef8", overflow: "hidden", marginBottom: 6 }}>
                    <div
                      style={{
                        height: "100%",
                        width: `${Math.max(4, Math.min(100, loadingProgress))}%`,
                        background: "linear-gradient(90deg, #1d4ed8 0%, #3b82f6 100%)",
                        transition: "width 0.35s ease",
                      }}
                    />
                  </div>
                  <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                    {loadingLabel || "Running summary pipeline..."} ({Math.round(Math.max(4, Math.min(100, loadingProgress)))}%)
                  </p>
                </div>
              ) : null}

              {error ? (
                <div className="card" style={{ padding: 10, marginBottom: 10, borderColor: "#f5c2c7", background: "#fff5f5" }}>
                  <strong style={{ display: "block", marginBottom: 4 }}>Summary error</strong>
                  <p className="muted" style={{ margin: 0 }}>{error}</p>
                </div>
              ) : null}

              {summaryText ? (
                <SummarySplitPane
                  summaryText={summaryText}
                  selectedCitationId={selectedCitationId}
                  fileUrl={src}
                  targetPage={targetPage}
                  targetScrollBehavior={targetScrollBehavior}
                  activeBboxes={activeBboxes}
                  activeBboxesByPage={selectedHighlightsByPage}
                  secondaryBboxesByPage={secondaryHighlightsByPage}
                  onError={handleViewerError}
                  onCitationClick={handleSummaryCitationClick}
                />
              ) : (
                <div className="card summaryEmptyState">
                  <h3 style={{ marginTop: 0 }}>No summary yet</h3>
                  <p className="muted" style={{ marginBottom: 0 }}>
                    Select a section and generate a summary to view the split-pane summary + PDF layout.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function SummarySplitPane({
  summaryText,
  selectedCitationId,
  fileUrl,
  targetPage,
  targetScrollBehavior,
  activeBboxes,
  activeBboxesByPage,
  secondaryBboxesByPage,
  onError,
  onCitationClick,
}: {
  summaryText: string;
  selectedCitationId: string | null;
  fileUrl: string;
  targetPage: number;
  targetScrollBehavior: "smooth" | "auto";
  activeBboxes: number[][];
  activeBboxesByPage: Record<string, number[][]>;
  secondaryBboxesByPage: Record<string, number[][]>;
  onError: (message: string | null) => void;
  onCitationClick: (citationId: string, chunkIndices: number[]) => void;
}) {
  const markdownContent = useMemo(() => {
    const summaryMarkdown = cleanMarkdownForRender(summaryText);
    let citationCounter = 0;
    let inKeyOverviewSection = false;

    const renderInlineText = (text: string) => {
      const citationRegex = /\[(.*?)\]/g;
      const nodes: ReactNode[] = [];
      let last = 0;
      for (const match of text.matchAll(citationRegex)) {
        const idx = match.index ?? 0;
        if (idx > last) nodes.push(<span key={`txt-${idx}`}>{text.slice(last, idx)}</span>);
        const rawInside = (match[1] || "").trim();
        const chunks = extractChunkTokens(rawInside);
        if (chunks.length > 0) {
          const cid = `c${citationCounter++}`;
          const parsedIndices = chunkTokensToIndices(chunks);
          nodes.push(
            <CitationBadge
              key={`cit-${cid}-${idx}`}
              chunks={chunks}
              active={selectedCitationId === cid}
              onClick={() => {
                onCitationClick(cid, parsedIndices);
              }}
            />
          );
        } else {
          nodes.push(<span key={`raw-${idx}`}>{match[0]}</span>);
        }
        last = idx + match[0].length;
      }
      if (last < text.length) nodes.push(<span key={`tail-${last}`}>{text.slice(last)}</span>);
      return nodes;
    };

    const processCitations = (node: ReactNode): ReactNode => {
      if (typeof node === "string") {
        return renderInlineText(node);
      }
      if (Array.isArray(node)) {
        return node.map((n, i) => (
          <span key={`frag-${i}`}>{processCitations(n)}</span>
        ));
      }
      if (node && typeof node === "object" && "props" in node) {
        const el = node as ReactElement<{ children?: ReactNode }>;
        return cloneElement(el, {
          ...el.props,
          children: processCitations(el.props.children),
        });
      }
      return node;
    };

    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h2: ({ children }) => {
            const headingText = nodeText(children);
            inKeyOverviewSection = isKeyOverviewHeading(`## ${headingText}`);
            return <StickySummaryH2>{children}</StickySummaryH2>;
          },
          p: ({ children }) => <p className="summaryParagraph">{processCitations(children)}</p>,
          ul: ({ children }) => (
            <ul className={inKeyOverviewSection ? "keyOverviewGrid" : "summaryBulletList"}>
              {children}
            </ul>
          ),
          li: ({ children }) => {
            if (inKeyOverviewSection) {
              const text = nodeText(children);
              const colonIdx = text.indexOf(":");
              if (colonIdx > -1) {
                const key = text.slice(0, colonIdx).trim();
                const value = text.slice(colonIdx + 1).trim();
                return (
                  <li className="keyOverviewItem">
                    <span className="keyOverviewLabel">{key}</span>
                    <span className="keyOverviewValue">{value ? renderInlineText(value) : "-"}</span>
                  </li>
                );
              }
            }
            return <li>{processCitations(children)}</li>;
          },
        }}
      >
        {summaryMarkdown}
      </ReactMarkdown>
    );
  }, [onCitationClick, selectedCitationId, summaryText]);

  return (
    <div className="summarySplitPane">
      <section className="card summaryMarkdownPane">
        {markdownContent}
      </section>
      <section className="card summaryPdfPane">
        <div style={{ height: "100%", width: "100%", minWidth: 0, overflow: "auto", borderRadius: 10, background: "#f9fbff" }}>
          <PdfViewerPane
            fileUrl={fileUrl}
            targetPage={targetPage}
            targetScrollBehavior={targetScrollBehavior}
            activeBboxes={activeBboxes}
            activeBboxesByPage={activeBboxesByPage}
            secondaryBboxesByPage={secondaryBboxesByPage}
            onError={onError}
          />
        </div>
      </section>
    </div>
  );
}
