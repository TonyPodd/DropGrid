import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { CommunityReviewPage } from "./discovery-review";
afterEach(() => vi.restoreAllMocks());
function fixture(position = 0) {
  let current = position;
  let mode = "pending";
  let decision: string | null = null;
  let revision = 0;
  const batch = () => ({
    id: "b",
    grid_id: "g",
    name: "Discovery validation v1",
    state: "open",
    total: 2,
    reviewed: decision ? 1 : 0,
    reviewed_by_you: decision ? 1 : 0,
    reviewer_id: "r",
    reviewer_name: "Tima",
    position: current,
    filter: mode,
    reviewers: [],
  });
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      let data: unknown;
      if (url.includes("/decision")) {
        const body = JSON.parse(String(init?.body));
        decision = body.decision;
        revision++;
        current = 1;
        data = { saved: true, next_position: 1, batch: batch() };
      } else if (url.includes("/cursor")) {
        const body = JSON.parse(String(init?.body));
        current = body.position ?? 0;
        mode = body.mode;
        data = batch();
      } else if (url.includes("/pending")) data = { positions: [1] };
      else if (url.includes("/items/")) {
        const position = url.includes("/items/1") ? 1 : 0;
        data = {
          position,
          version: "f".repeat(64),
          revision: position === 0 ? revision : 0,
          decision: position === 0 ? decision : null,
          seed_count: 2,
          seeds: [{ name: "Seed03", url: "https://vk.com/seed03" }],
          human_status: null,
          grid_added: false,
          candidate: {
            id: "c",
            name: position === 0 ? "Авто Бурятия" : "Следующее сообщество",
            url: "https://vk.com/auto03",
            suggested_category: "03 регион",
            region: {
              display_name: "Республика Бурятия",
              region_codes: ["03"],
              vehicle_codes: ["03", "103"],
            },
            final_score: 0.91,
            topic_score: 1,
            visual_score: 0.86,
            region_score: 1,
            activity_score: 1,
            graph_score: 0.33,
            confidence: "high",
            found_by_queries: ["авто улан-удэ"],
            previews: ["/api/v1/photo/1"],
            reason: {
              region_status: "same_region",
              activity: {
                state: "active",
                posts_last_30d: 4,
                posts_last_90d: 8,
              },
              closest_communities: [
                {
                  name: "Closest",
                  url: "https://vk.com/closest",
                  similarity: 0.88,
                },
              ],
            },
          },
        };
      } else data = batch();
      return { ok: true, json: async () => data } as Response;
    });
  render(
    <MemoryRouter initialEntries={["/discovery/review/b"]}>
      <Routes>
        <Route
          path="/discovery/review/:batchId"
          element={<CommunityReviewPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
  return { fetch, user: userEvent.setup() };
}
it("renders reviewer, evidence, safe links and enlarges full-frame photos", async () => {
  const { user } = fixture();
  await screen.findByRole("heading", { name: /Авто Бурятия/ });
  const link = screen.getByRole("link", { name: "Открыть VK ↗" });
  expect(link).toHaveAttribute("href", "https://vk.com/auto03");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.getByText(/Республика Бурятия/)).toBeVisible();
  expect(screen.getByText("Tima")).toBeVisible();
  expect(screen.getByRole("link", { name: /Closest/ })).toHaveAttribute(
    "href",
    "https://vk.com/closest",
  );
  await user.click(screen.getByRole("button", { name: "Увеличить фото 1" }));
  expect(screen.getByRole("dialog", { name: "Фото целиком" })).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("saves approval then advances without adding a Grid row or creating submissions", async () => {
  const { fetch, user } = fixture();
  await user.click(await screen.findByRole("button", { name: "Подходит" }));
  await screen.findByRole("heading", { name: /Следующее сообщество/ });
  const calls = fetch.mock.calls.filter(([, i]) => i?.method === "POST");
  expect(calls).toHaveLength(1);
  expect(String(calls[0][0])).toContain("/review-batches/b/items/0/decision");
  expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({
    decision: "approved",
    reviewer_id: "r",
    expected_revision: 0,
  });
  expect(screen.getByRole("status")).toHaveTextContent("В сетку не добавлено");
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "1");
});
it("opens a reason picker and supports one-key rejection reasons", async () => {
  const { fetch, user } = fixture();
  await screen.findByRole("heading", { name: /Авто Бурятия/ });
  await user.keyboard("r");
  expect(screen.getByRole("dialog", { name: "Причина отказа" })).toBeVisible();
  await user.keyboard("2");
  await screen.findByRole("heading", { name: /Следующее сообщество/ });
  const call = fetch.mock.calls.find(([, i]) => i?.method === "POST");
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    decision: "rejected",
    reason: "wrong_region",
  });
});
it("supports uncertainty and avoids duplicate rapid decisions", async () => {
  const { fetch } = fixture();
  await screen.findByRole("heading", { name: /Авто Бурятия/ });
  fireEvent.keyDown(window, { key: "u" });
  fireEvent.keyDown(window, { key: "u" });
  await screen.findByRole("heading", { name: /Следующее сообщество/ });
  expect(fetch.mock.calls.filter(([, i]) => i?.method === "POST")).toHaveLength(
    1,
  );
});
it("resumes the server cursor and saves filters and previous position", async () => {
  const { fetch, user } = fixture(1);
  await screen.findByRole("heading", { name: /Следующее сообщество/ });
  await user.keyboard("{ArrowLeft}");
  await screen.findByRole("heading", { name: /Авто Бурятия/ });
  await user.click(screen.getByRole("button", { name: "Все" }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([, i]) =>
          i?.method === "PUT" && JSON.parse(String(i.body)).mode === "all",
      ),
    ).toBe(true),
  );
  await user.keyboard("{ArrowRight}");
  await screen.findByRole("heading", { name: /Следующее сообщество/ });
  expect(screen.getByRole("button", { name: "Непроверенные" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
it("opens G safely only without modifiers or a typing focus", async () => {
  const { user } = fixture();
  const open = vi.spyOn(window, "open").mockReturnValue(null);
  await screen.findByRole("heading", { name: /Авто Бурятия/ });
  await user.keyboard("g");
  expect(open).toHaveBeenCalledWith(
    "https://vk.com/auto03",
    "_blank",
    "noopener,noreferrer",
  );
  fireEvent.keyDown(window, { key: "g", ctrlKey: true });
  expect(open).toHaveBeenCalledTimes(1);
  const input = document.createElement("input");
  document.body.append(input);
  input.focus();
  fireEvent.keyDown(input, { key: "a" });
  expect(screen.getByRole("heading", { name: /Авто Бурятия/ })).toBeVisible();
  input.remove();
});
it("reports cache expiry without scanning VK again", async () => {
  const { fetch } = fixture();
  const image = await screen.findByRole("img", { name: "Фото сообщества 1" });
  fireEvent.error(image);
  expect(screen.getByText(/Превью 1 истекло/)).toBeVisible();
  expect(fetch.mock.calls.some(([u]) => /wall|runs/.test(String(u)))).toBe(
    false,
  );
});
