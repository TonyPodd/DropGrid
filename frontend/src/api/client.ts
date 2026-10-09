const baseUrl = (
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000"
).replace(/\/$/, "");
export const mediaContentUrl = (id: string) =>
  `${baseUrl}/api/v1/media-assets/${encodeURIComponent(id)}/content`;
export const referenceContentUrl = (communityId: string, id: string) =>
  `${baseUrl}/api/v1/communities/${encodeURIComponent(communityId)}/references/${encodeURIComponent(id)}/content`;
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/v1${path}`, {
      method: options.method ?? "GET",
      signal: options.signal,
      headers:
        options.body === undefined
          ? undefined
          : { "Content-Type": "application/json" },
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error(
      "Не удалось подключиться к backend. Проверьте соединение и повторите.",
    );
  }
  if (!response.ok) {
    // Fixed messages avoid echoing arbitrary server dumps or credentials.
    const messages: Record<number, string> = {
      404: "Запись не найдена.",
      409: "Изменение недоступно в текущем состоянии записи.",
      422: "Проверьте поля формы: данные не прошли проверку.",
      503: "Сервис временно недоступен. Повторите позже.",
    };
    if (response.status === 409) {
      const conflicts: Record<string, string> = {
        reference_sync_in_progress:
          "Изучение стены уже выполняется. Дождитесь завершения.",
        archive_sync_in_progress:
          "Индексация архива уже выполняется. Дождитесь завершения.",
        profile_locked:
          "Профиль занят обработкой фото. Повторите после завершения.",
        preview_in_progress:
          "Сравнение фото уже выполняется. Дождитесь завершения.",
      };
      const payload: unknown = await response.json().catch(() => null);
      if (payload && typeof payload === "object" && "detail" in payload) {
        const detail = payload.detail;
        if (
          detail &&
          typeof detail === "object" &&
          "code" in detail &&
          typeof detail.code === "string" &&
          conflicts[detail.code]
        ) {
          throw new ApiError(409, conflicts[detail.code]);
        }
      }
    }
    throw new ApiError(
      response.status,
      messages[response.status] ??
        `Запрос не выполнен (HTTP ${response.status}).`,
    );
  }
  return response.json() as Promise<T>;
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Не удалось выполнить запрос.";
}
