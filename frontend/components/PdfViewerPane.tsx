"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

type Props = {
  fileUrl: string;
  targetPage: number;
  targetScrollBehavior?: "smooth" | "auto";
  activeBboxes: number[][];
  activeBboxesByPage?: Record<string, number[][]>;
  secondaryBboxesByPage?: Record<string, number[][]>;
  onError: (message: string | null) => void;
};

const EMPTY_OVERLAYS: { left: number; top: number; width: number; height: number }[] = [];

const PdfPageLayer = memo(function PdfPageLayer({
  pageNum,
  fitWidth,
  zoom,
  onPageLoadSuccess,
  setPageRef,
  secondaryOverlays,
  selectedOverlays,
}: {
  pageNum: number;
  fitWidth: number;
  zoom: number;
  onPageLoadSuccess: (
    pageNum: number,
    page: { width: number; height: number; getViewport?: (opts: { scale: number }) => { width: number; height: number } }
  ) => void;
  setPageRef: (pageNum: number, el: HTMLDivElement | null) => void;
  secondaryOverlays: { left: number; top: number; width: number; height: number }[];
  selectedOverlays: { left: number; top: number; width: number; height: number }[];
}) {
  return (
    <div
      ref={(el) => setPageRef(pageNum, el)}
      style={{
        position: "relative",
        width: "100%",
        maxWidth: "100%",
        margin: "0 auto 16px auto",
        display: "flex",
        justifyContent: "center",
      }}
    >
      <Page
        pageNumber={pageNum}
        width={fitWidth}
        scale={zoom}
        onLoadSuccess={(page) => onPageLoadSuccess(pageNum, page)}
        renderTextLayer={false}
        renderAnnotationLayer
      />
      {secondaryOverlays.map((overlay, index) => (
        <div
          key={`sec-${pageNum}-${overlay.left}-${overlay.top}-${overlay.width}-${overlay.height}-${index}`}
          style={{
            position: "absolute",
            left: overlay.left,
            top: overlay.top,
            width: overlay.width,
            height: overlay.height,
            background: "transparent",
            border: "2px solid #facc15",
            borderRadius: 2,
            pointerEvents: "none",
            zIndex: 9,
          }}
        />
      ))}
      {selectedOverlays.map((overlay, index) => (
        <div
          key={`sel-${pageNum}-${overlay.left}-${overlay.top}-${overlay.width}-${overlay.height}-${index}`}
          style={{
            position: "absolute",
            left: overlay.left,
            top: overlay.top,
            width: overlay.width,
            height: overlay.height,
            background: "rgba(251, 113, 133, 0.08)",
            border: "2px solid #e11d48",
            borderRadius: 2,
            pointerEvents: "none",
            zIndex: 10,
          }}
        />
      ))}
    </div>
  );
});

export default function PdfViewerPane({
  fileUrl,
  targetPage,
  targetScrollBehavior = "smooth",
  activeBboxes,
  activeBboxesByPage,
  secondaryBboxesByPage,
  onError,
}: Props) {
  const [numPages, setNumPages] = useState(0);
  const [pageDimensions, setPageDimensions] = useState<Record<number, { width: number; height: number }>>({});
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [loading, setLoading] = useState(true);
  const [fitWidth, setFitWidth] = useState(320);
  const [zoom, setZoom] = useState(1);
  const measureRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setPdfBytes(null);
    onError(null);

    fetch(fileUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.arrayBuffer();
      })
      .then((buf) => {
        if (!cancelled) {
          setPdfBytes(new Uint8Array(buf));
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          onError(`PDF fetch error: ${err instanceof Error ? err.message : String(err)}`);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fileUrl, onError]);

  const fileObj = useMemo(
    () => (pdfBytes ? { data: pdfBytes.slice() } : null),
    [pdfBytes]
  );

  useEffect(() => {
    if (loading || !fileObj) {
      return;
    }
    const el = measureRef.current;
    if (!el) {
      return;
    }

    const updateWidth = () => {
      const w = el.getBoundingClientRect().width;
      if (w <= 0) return;
      setFitWidth(Math.max(200, Math.floor(w - 8)));
    };

    updateWidth();
    let innerRaf = 0;
    const outerRaf = requestAnimationFrame(() => {
      innerRaf = requestAnimationFrame(updateWidth);
    });
    const observer = new ResizeObserver(updateWidth);
    observer.observe(el);
    return () => {
      cancelAnimationFrame(outerRaf);
      cancelAnimationFrame(innerRaf);
      observer.disconnect();
    };
  }, [loading, fileObj]);

  useEffect(() => {
    const bounded = numPages > 0 ? Math.max(1, Math.min(targetPage, numPages)) : Math.max(1, targetPage);
    const pageEl = pageRefs.current[bounded];
    if (pageEl) {
      pageEl.scrollIntoView({ behavior: targetScrollBehavior, block: "start" });
    }
  }, [targetPage, numPages, targetScrollBehavior]);

  const onDocLoadSuccess = useCallback(
    (pdf: { numPages: number }) => {
      setNumPages(pdf.numPages);
      onError(null);
    },
    [onError]
  );

  const onPageLoadSuccess = useCallback(
    (
      pageNum: number,
      page: { width: number; height: number; getViewport?: (opts: { scale: number }) => { width: number; height: number } }
    ) => {
      let width = page.width;
      let height = page.height;
      if (page.getViewport) {
        const vp = page.getViewport({ scale: 1 });
        width = vp.width;
        height = vp.height;
      }
      setPageDimensions((prev) => ({ ...prev, [pageNum]: { width, height } }));
    },
    []
  );

  const renderWidth = Math.max(200, Math.round(fitWidth * zoom));

  const overlaysForPage = useCallback(
    (pageNum: number) => {
      const pageSelectedBboxes = activeBboxesByPage?.[String(pageNum)] ?? [];
      const pageSecondaryBboxes = secondaryBboxesByPage?.[String(pageNum)] ?? [];
      const selectedBboxesForPage =
        Array.isArray(pageSelectedBboxes) && pageSelectedBboxes.length > 0
          ? pageSelectedBboxes
          : pageNum === targetPage
            ? activeBboxes
            : [];
      const dims = pageDimensions[pageNum];
      if (!dims) return { secondary: [], selected: [] };
      const scale = dims.width > 0 ? renderWidth / dims.width : 1;

      const toOverlays = (bboxesForPage: number[][]) =>
        bboxesForPage
          .map((bbox) => {
            if (!Array.isArray(bbox) || bbox.length < 4) return null;
            const [v0, v1, v2, v3] = bbox.map(Number);
            if (![v0, v1, v2, v3].every(Number.isFinite)) return null;

            const pdfLeft = Math.min(v0, v2);
            const pdfRight = Math.max(v0, v2);
            const pdfBottom = Math.min(v1, v3);
            const pdfTop = Math.max(v1, v3);

            const w = pdfRight - pdfLeft;
            const h = pdfTop - pdfBottom;
            if (w <= 0 || h <= 0) return null;

            const cssTop = dims.height - pdfTop;
            return {
              left: pdfLeft * scale,
              top: cssTop * scale,
              width: w * scale,
              height: h * scale,
            };
          })
          .filter((overlay): overlay is { left: number; top: number; width: number; height: number } => overlay !== null);

      return {
        secondary: toOverlays(pageSecondaryBboxes),
        selected: toOverlays(selectedBboxesForPage),
      };
    },
    [activeBboxes, activeBboxesByPage, pageDimensions, renderWidth, secondaryBboxesByPage, targetPage]
  );

  if (loading) {
    return <p className="muted" style={{ padding: 12 }}>Loading PDF...</p>;
  }

  if (!fileObj) {
    return <p className="muted" style={{ padding: 12 }}>Failed to load PDF.</p>;
  }

  return (
    <div
      ref={measureRef}
      style={{
        height: "100%",
        width: "100%",
        minWidth: 0,
        overflow: "hidden",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          minHeight: 0,
          width: "100%",
          minWidth: 0,
          overflow: "auto",
          boxSizing: "border-box",
        }}
      >
        <div style={{ position: "sticky", top: 0, zIndex: 20, background: "#f9fbff", padding: "8px 0" }}>
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <button type="button" className="btnGhost" onClick={() => setZoom((z) => Math.max(0.5, Math.round((z - 0.1) * 100) / 100))}>
              Zoom Out
            </button>
            <button
              type="button"
              className="btnGhost"
              onClick={() => {
                setZoom(1);
              }}
            >
              Fit width
            </button>
            <button type="button" className="btnGhost" onClick={() => setZoom((z) => Math.min(3, Math.round((z + 0.1) * 100) / 100))}>
              Zoom In
            </button>
            <span className="muted" style={{ fontSize: 13, minWidth: "3.5em", textAlign: "center" }}>
              {Math.round(zoom * 100)}%
            </span>
          </div>
        </div>
        <Document
          file={fileObj}
          onLoadSuccess={onDocLoadSuccess}
          onLoadError={(err: Error) => onError(`PDF parse error: ${String(err)}`)}
          loading={<p className="muted" style={{ padding: 12 }}>Parsing PDF...</p>}
        >
          {Array.from({ length: numPages }, (_, i) => i + 1).map((pageNum) => {
            const pageOverlays = overlaysForPage(pageNum);
            return (
              <PdfPageLayer
                key={pageNum}
                pageNum={pageNum}
                fitWidth={fitWidth}
                zoom={zoom}
                onPageLoadSuccess={onPageLoadSuccess}
                setPageRef={(pn, el) => {
                  pageRefs.current[pn] = el;
                }}
                secondaryOverlays={pageOverlays.secondary.length ? pageOverlays.secondary : EMPTY_OVERLAYS}
                selectedOverlays={pageOverlays.selected.length ? pageOverlays.selected : EMPTY_OVERLAYS}
              />
            );
          })}
        </Document>
      </div>
    </div>
  );
}
