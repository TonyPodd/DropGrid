import { afterEach, expect, it, vi } from "vitest";
import { request } from "./client";
afterEach(() => vi.unstubAllGlobals());
it.each([
  ["reference_sync_in_progress", "Изучение стены уже выполняется"],
  ["archive_sync_in_progress", "Индексация архива уже выполняется"],
  ["profile_locked", "Профиль занят обработкой фото"],
  ["preview_in_progress", "Сравнение фото уже выполняется"],
])("maps only whitelisted conflict code %s", async (code, expected) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { code, message: "secret raw dump" } }),
          { status: 409 },
        ),
      ),
  );
  await expect(request("/fixture")).rejects.toThrow(expected);
});
it("ignores unknown server conflict details", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "secret raw dump" }), {
          status: 409,
        }),
      ),
  );
  await expect(request("/fixture")).rejects.toThrow(
    "Изменение недоступно в текущем состоянии записи.",
  );
});
