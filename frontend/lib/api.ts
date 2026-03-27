const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type PdfFile = {
  id: number;
  original_name: string;
  size_bytes: number;
  created_at: string;
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

export function pdfViewUrl(id: number, token: string | null) {
  const url = new URL(`${API_URL}/api/pdfs/${id}/file`);
  if (token) {
    url.searchParams.set("token", token);
  }
  return url.toString();
}
