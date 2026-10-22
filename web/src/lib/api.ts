/**
 * API 客户端：token 注入 + 401 门禁 + 错误 toast。
 * token 存 localStorage（无账号体系，首次从 config.json 的 server_token 取）。
 */

const TOKEN_KEY = "lm_token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token.trim());
}

function withToken(path: string): string {
  const t = getToken();
  if (!t) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(withToken(path), {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (resp.status === 401) {
    window.dispatchEvent(new CustomEvent("lm:unauthorized"));
    throw new ApiError(401, "未授权（token 无效或已变更）");
  }
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* 空体 */
  }
  if (!resp.ok) {
    const msg =
      (body as { error?: string } | null)?.error ?? `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg);
  }
  return body as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}
