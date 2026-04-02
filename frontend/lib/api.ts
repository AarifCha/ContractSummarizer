const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type PdfFile = {
  id: number;
  original_name: string;
  size_bytes: number;
  created_at: string;
  processing_stage?: string | null;
  processing_total_chunks?: number;
  processing_completed_chunks?: number;
  processing_status?: "queued" | "running" | "done" | "failed";
  processing_error?: string | null;
};

export type PdfProcessingStatus = {
  stage: string | null;
  status: "queued" | "running" | "done" | "failed";
  total_chunks: number;
  completed_chunks: number;
  progress_percent: number;
  error: string | null;
};

export type QaSearchResult = {
  rank: number | null;
  chunk_id: string;
  score: number | null;
  relevance_score?: number;
  origin_sections?: string[];
  text: string;
  page_numbers: number[];
  bboxes: number[][];
  headings: string[];
  chunk_index: number;
  source_file: string;
};

export type QaSearchResponse = {
  results: QaSearchResult[];
};

export type SectionOptionsResponse = {
  sections: string[];
};

export type CitationMapEntry = {
  raw: string;
  chunk_indices: number[];
  chunk_ids: string[];
};

export type SectionSearchResponse = {
  mode?: "all_sections_summary";
  section: string;
  queries: string[];
  results: QaSearchResult[];
  highlight_chunks?: QaSearchResult[];
  highlights_by_page?: Record<string, number[][]>;
  raw_summary_text?: string;
  summary_text?: string;
  summary_model?: string;
  task_id?: string;
  status?: "queued" | "running" | "done" | "failed";
  phase?: "queued" | "gemma_windows" | "flash_lite" | "done" | "failed";
  flash_lite_status?: "pending" | "running" | "done" | "failed";
  total_windows?: number;
  completed_windows?: number;
  citation_map?: Record<string, CitationMapEntry>;
  api_usage_summary?: {
    per_model: Record<
      string,
      {
        model_id: string;
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
        input_cost: number;
        output_cost: number;
        total_cost: number;
      }
    >;
    totals: {
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
      input_cost: number;
      output_cost: number;
      total_cost: number;
    };
  };
  error?: string | null;
};

function authHeaders(token: string | null): HeadersInit {
  if (!token) {
    return {};
  }
  return { Authorization: `Bearer ${token}` };
}

export async function register(email: string, password: string) {
  const response = await fetch(`${API_URL}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  return response;
}

export async function login(email: string, password: string) {
  const response = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  return response;
}

export async function logout(token: string) {
  return fetch(`${API_URL}/api/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token })
  });
}

export async function listPdfs(token: string | null): Promise<PdfFile[]> {
  const response = await fetch(`${API_URL}/api/pdfs`, {
    headers: authHeaders(token),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Unable to load files");
  }
  const data = (await response.json()) as { files: PdfFile[] };
  return data.files;
}

export async function uploadPdf(token: string | null, file: File) {
  const formData = new FormData();
  formData.append("file", file);

  return fetch(`${API_URL}/api/pdfs`, {
    method: "POST",
    headers: authHeaders(token),
    body: formData
  });
}

export async function deletePdf(token: string | null, id: number) {
  return fetch(`${API_URL}/api/pdfs/${id}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
}

export async function getPdfProcessingStatus(token: string | null, id: number): Promise<PdfProcessingStatus> {
  const response = await fetch(`${API_URL}/api/pdfs/${id}/processing-status`, {
    headers: authHeaders(token),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Unable to load PDF processing status");
  }
  return response.json();
}

export async function getApiKeyStatus(token: string | null): Promise<{ has_key: boolean; masked_key: string | null }> {
  const response = await fetch(`${API_URL}/api/api-key`, {
    headers: authHeaders(token),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Unable to load API key status");
  }
  return response.json();
}

export async function saveApiKey(token: string | null, apiKey: string): Promise<{ ok: boolean; masked_key: string }> {
  const response = await fetch(`${API_URL}/api/api-key`, {
    method: "POST",
    headers: {
      ...authHeaders(token),
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ api_key: apiKey })
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Unable to save API key");
  }
  return response.json();
}

export async function removeApiKey(token: string | null): Promise<{ ok: boolean }> {
  const response = await fetch(`${API_URL}/api/api-key`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Unable to remove API key");
  }
  return response.json();
}

export function pdfViewUrl(id: number, token: string | null) {
  const url = new URL(`${API_URL}/api/pdfs/${id}/file`);
  if (token) {
    url.searchParams.set("token", token);
  }
  return url.toString();
}

export async function searchPdfQa(
  token: string | null,
  id: number,
  query: string,
  topK = 5
): Promise<QaSearchResponse> {
  const response = await fetch(`${API_URL}/api/pdfs/${id}/qa-search`, {
    method: "POST",
    headers: {
      ...authHeaders(token),
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ query, top_k: topK })
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Unable to run QA search");
  }
  return response.json();
}

export async function getSectionOptions(token: string | null): Promise<SectionOptionsResponse> {
  const response = await fetch(`${API_URL}/api/pdfs/section-options`, {
    headers: authHeaders(token),
    cache: "no-store"
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Unable to load section options");
  }
  return response.json();
}

export async function searchPdfSection(
  token: string | null,
  id: number,
  section: string,
  topKPerQuery = 5
): Promise<SectionSearchResponse> {
  const response = await fetch(`${API_URL}/api/pdfs/${id}/section-search`, {
    method: "POST",
    headers: {
      ...authHeaders(token),
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ section, top_k_per_query: topKPerQuery })
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Unable to run section search");
  }
  return response.json();
}

export async function getSectionSearchStatus(
  token: string | null,
  taskId: string
): Promise<SectionSearchResponse> {
  const response = await fetch(`${API_URL}/api/pdfs/section-search-status/${taskId}`, {
    headers: authHeaders(token),
    cache: "no-store"
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Unable to load section search status");
  }
  return response.json();
}
