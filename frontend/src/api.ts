const baseUrl = (import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000').replace(/\/$/, '');

export async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { signal });
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return response.json() as Promise<T>;
}
