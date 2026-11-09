// Talking to the Daybook server. Every state-changing request carries the
// X-Daybook header (the server rejects requests without it — CSRF defence).

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

let unauthorized = null;
export const onUnauthorized = (fn) => { unauthorized = fn; };

export async function api(path, { method = 'GET', body, form, signal, raw = false, keepalive = false } = {}) {
  const headers = {};
  if (method !== 'GET') headers['X-Daybook'] = '1';
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin', signal, keepalive });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError(0, "Can't reach Daybook. Check that the server is running and you're connected.");
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    if (unauthorized) unauthorized();
    throw new ApiError(401, 'Sign in to continue.');
  }
  if (raw) {
    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return res;
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new ApiError(res.status, (data && data.detail) || `Request failed (${res.status})`, data);
  return data;
}

export const get = (p, o) => api(p, o);
export const post = (p, body, o) => api(p, { ...o, method: 'POST', body: body ?? {} });
export const put = (p, body, o) => api(p, { ...o, method: 'PUT', body: body ?? {} });
export const del = (p, o) => api(p, { ...o, method: 'DELETE' });

export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== undefined && v !== null && v !== '') sp.set(k, v);
  const s = sp.toString();
  return s ? `?${s}` : '';
}

// Streams newline-delimited JSON (local AI answers) into onEvent.
export async function streamNdjson(path, body, onEvent, signal) {
  const res = await api(path, { method: 'POST', body, raw: true, signal });
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  if (buf.trim()) onEvent(JSON.parse(buf));
}

// Multipart upload with progress (fetch can't report upload progress).
export function upload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.setRequestHeader('X-Daybook', '1');
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = null; }
      if (xhr.status === 401 && unauthorized) unauthorized();
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, (data && data.detail) || `Upload failed (${xhr.status})`, data));
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed. Check your connection."));
    xhr.send(formData);
  });
}

export function downloadUrl(url) {
  const a = document.createElement('a');
  a.href = url;
  a.rel = 'noopener';
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export async function downloadPost(path, body, filename) {
  const res = await api(path, { method: 'POST', body, raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// Live updates (Server-Sent Events) with automatic reconnect.
export function connectStream(onEvent, onState) {
  let es = null;
  let closed = false;
  let retry = 1000;
  const open = () => {
    if (closed) return;
    es = new EventSource('/api/stream', { withCredentials: true });
    es.onopen = () => { retry = 1000; onState && onState(true); };
    es.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    es.onerror = () => {
      onState && onState(false);
      es.close();
      if (!closed) setTimeout(open, retry);
      retry = Math.min(retry * 2, 30000);
    };
  };
  open();
  return () => { closed = true; if (es) es.close(); };
}
