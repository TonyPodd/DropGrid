const baseUrl = (
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000"
).replace(/\/$/, "");
export const mediaContentUrl = (id: string) =>
  `${baseUrl}/api/v1/media-assets/${encodeURIComponent(id)}/content`;
export const referenceContentUrl = (communityId: string, id: string) =>
  `${baseUrl}/api/v1/communities/${encodeURIComponent(communityId)}/references/${encodeURIComponent(id)}/content`;
export const photoPreviewContentUrl = (id: string) =>
  `${baseUrl}/api/v1/photo-previews/${encodeURIComponent(id)}/content`;
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
  options: {
    method?: string;
    body?: unknown;
    signal?: AbortSignal;
    /** A file sent as the raw request body instead of JSON. */
    file?: Blob;
  } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/v1${path}`, {
      method: options.method ?? "GET",
      signal: options.signal,
      headers: options.file
        ? { "Content-Type": options.file.type || "application/octet-stream" }
        : options.body === undefined
          ? undefined
          : { "Content-Type": "application/json" },
      body:
        options.file ??
        (options.body === undefined ? undefined : JSON.stringify(options.body)),
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
        photo_disliked:
          "Это фото отмечено «Не нравится». Чтобы одобрить его, сначала выберите его снова — отметка снимется.",
        photo_reuse_limit:
          "Это фото уже выбрано для максимального числа пабликов этой кампании. Выберите другое.",
        photo_recently_used:
          "Это фото недавно отправлялось в этот паблик. Выберите другое.",
        photo_unavailable:
          "Это фото не удалось загрузить. Выберите другой вариант.",
        persona_photo_taken:
          "Это фото уже привязано к другому аккаунту. Фото девочки уходят только с её аккаунта.",
        persona_photo_duplicate: "Это фото уже загружено для этой девочки.",
        persona_photo_limit:
          "У аккаунта уже слишком много фото. Удалите лишние, чтобы добавить новые.",
        persona_photo_in_library:
          "Это фото уже есть в общей библиотеке. Загрузите личное фото девочки.",
        accounts_needed:
          "Чтобы предложить посты, нужно по аккаунту на каждые 100 пабликов сетки.",
        campaign_in_progress:
          "Кампания выполняется. Сначала остановите её, потом удалите.",
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
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Не удалось выполнить запрос.";
}
