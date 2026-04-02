"use client";

type Props = {
  chunks: string[];
  onClick: (chunks: string[]) => void;
  active?: boolean;
};

export default function CitationBadge({ chunks, onClick, active = false }: Props) {
  const count = chunks.length;
  const label = `${count} Citation${count === 1 ? "" : "s"}`;
  return (
    <button
      type="button"
      onClick={() => onClick(chunks)}
      className={`citationBadge ${active ? "citationBadge--active" : ""}`}
      title={chunks.join(", ")}
    >
      {label}
    </button>
  );
}

