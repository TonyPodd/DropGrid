import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import {
  ActivityCard,
  ActivityIndicator,
  ActivityPage,
  ActivityProvider,
  activityApi,
} from "./activity";
const row = {
  id: "p1",
  kind: "preview",
  label: "Сравнение фото · Honda",
  state: "running" as const,
  stage: "Ранжируем по стилю",
  current: 7,
  total: 12,
  percent: 58,
  started_at: "2026-10-09T10:00:00Z",
  updated_at: "2026-10-09T10:00:14Z",
  elapsed_seconds: 14,
  target_url: "/communities/c1",
  message: null,
  counters: { photos: 12 },
};
afterEach(() => vi.useRealTimers());
it("renders actual progress, timestamps and target link", () => {
  render(
    <MemoryRouter>
      <ActivityCard row={row} />
    </MemoryRouter>,
  );
  expect(screen.getByText(/7 \/ 12/)).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "58");
  expect(screen.getByRole("link")).toHaveAttribute("href", "/communities/c1");
});
it("shows active count, then idle warning without treating history as running", async () => {
  vi.spyOn(activityApi, "list").mockResolvedValue([row]);
  const view = render(
    <MemoryRouter>
      <ActivityProvider>
        <ActivityIndicator />
        <ActivityPage />
      </ActivityProvider>
    </MemoryRouter>,
  );
  await screen.findByText(/1 процесса/);
  view.unmount();
  vi.mocked(activityApi.list).mockResolvedValue([
    { ...row, state: "warning", message: "Откройте результат" },
  ]);
  render(
    <MemoryRouter>
      <ActivityProvider>
        <ActivityIndicator />
        <ActivityPage />
      </ActivityProvider>
    </MemoryRouter>,
  );
  await screen.findByText("Откройте результат");
  expect(
    screen.getByRole("link", { name: /Нет активных задач/ }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});
it("notifies completion from the global poll on any page", async () => {
  vi.spyOn(activityApi, "list")
    .mockResolvedValueOnce([row])
    .mockResolvedValue([{ ...row, state: "success" }]);
  render(
    <MemoryRouter>
      <ActivityProvider>
        <ActivityIndicator />
        <p>Other page</p>
      </ActivityProvider>
    </MemoryRouter>,
  );
  await screen.findByText(/1 процесса/);
  expect(
    await screen.findByRole("status", {}, { timeout: 5000 }),
  ).toHaveTextContent("Готово: Сравнение фото · Honda");
});
