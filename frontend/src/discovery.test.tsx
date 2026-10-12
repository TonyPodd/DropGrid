import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { DiscoveryPage } from "./discovery";
afterEach(() => vi.restoreAllMocks());
function fixture(
  human_status: string | null = null,
  initialStatus = "new",
  reviewBatches: unknown[] = [],
) {
  let status = initialStatus;
  const candidate = {
    id: "c",
    name: "Авто Красноярск",
    url: "https://vk.com/auto24",
    suggested_category: "24 регион",
    suggested_region_key: "krasnoyarsk_krai",
    status: "new",
    human_status,
    analysis_state: "ready",
    final_score: 0.8,
    topic_score: 1,
    visual_score: 0.8,
    region_score: 1,
    activity_score: 1,
    graph_score: 0.3,
    confidence: "high",
    found_by_queries: ["авто красноярск"],
    previews: ["/api/v1/discovery/candidates/c/photos/0"],
    reason: {
      region_status: "same_region",
      activity: { state: "active", posts_last_30d: 5, posts_last_90d: 8 },
      closest_communities: [
        { name: "Seed24", url: "https://vk.com/seed24", similarity: 0.82 },
      ],
      photos_analyzed: 8,
    },
  };
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      let data: unknown;
      if (url.includes("/options"))
        data = {
          grids: [{ id: "g", name: "Сетка Тимы" }],
          accounts: [{ id: "a", name: "Account" }],
          seeds: [
            {
              id: "s",
              name: "Seed24",
              category: "24 регион",
              region_key: "krasnoyarsk_krai",
            },
          ],
          regions: [
            {
              key: "krasnoyarsk_krai",
              name: "Красноярский край",
              codes: ["24", "124"],
            },
          ],
          reviewer: { id: "r", name: "Tima" },
          reviewers: [],
        };
      else if (url.includes("/review-batches")) data = reviewBatches;
      else if (url.includes("/quality"))
        data = {
          overall: { reviewed: 0, precision: null },
          by: {},
          agreement: { rate: null, overlapping_candidates: 0 },
          precision_at_k: {},
          thresholds: [],
        };
      else if (url.includes("/cache"))
        data = { size: 0, entries: 0, oldest: null, evicted_count: 0 };
      else if (url.includes("/decision")) {
        const action = JSON.parse(String(init?.body)).action;
        status =
          action === "reject"
            ? "rejected"
            : action === "approve"
              ? "approved"
              : "new";
        data = { ...candidate, status };
      } else if (url.includes("/candidates?")) {
        const filter = new URL(url).searchParams.get("status");
        data = {
          items:
            filter === status || filter === "all"
              ? [{ ...candidate, status }]
              : [],
          total: filter === status ? 1 : 0,
          counters: {
            new: status === "new" ? 1 : 0,
            recommended: 1,
            rejected: status === "rejected" ? 1 : 0,
            added: status === "approved" ? 1 : 0,
          },
        };
      } else if (init?.method === "POST") data = { id: "run", state: "queued" };
      else
        data = [
          {
            id: "run",
            state: "ready",
            stage: "ranking_candidates",
            query_cursor: 3,
            queries: [{ query: "авто красноярск" }],
            counters: { raw_candidates: 10, visually_analyzed: 2 },
            error_code: null,
          },
        ];
      return { ok: true, json: async () => data } as Response;
    });
  render(
    <MemoryRouter initialEntries={["/discovery?grid=g"]}>
      <DiscoveryPage />
    </MemoryRouter>,
  );
  return { fetch, user: userEvent.setup() };
}
it("shows bounded progress, region, VK links and filters", async () => {
  const { fetch, user } = fixture();
  expect(
    await screen.findByRole("link", { name: "Авто Красноярск ↗" }),
  ).toHaveAttribute("href", "https://vk.com/auto24");
  const link = screen.getByRole("link", { name: "Авто Красноярск ↗" });
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.getByText(/24 регион · Красноярский край/)).toBeVisible();
  await user.selectOptions(screen.getByLabelText("Минимальный score"), "0.7");
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([u]) => String(u).includes("minimum_score=0.7")),
    ).toBe(true),
  );
  await user.click(screen.getByText("Подробнее / почему найдено"));
  expect(screen.getByRole("link", { name: "Seed24 ↗" })).toHaveAttribute(
    "href",
    "https://vk.com/seed24",
  );
  expect(screen.getByText("Поиск завершён")).toBeVisible();
});
it("requires explicit add confirmation with category/region and never creates sending", async () => {
  const { fetch, user } = fixture("approved");
  await user.click(
    await screen.findByRole("button", { name: "Добавить в сетку" }),
  );
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
    false,
  );
  await user.click(
    screen.getByRole("button", { name: "Подтвердить добавление" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  const call = fetch.mock.calls.find(([u]) => String(u).includes("/decision"));
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    action: "approve",
    reviewer_id: "r",
    category: "24 регион",
    region_key: "krasnoyarsk_krai",
  });
  expect(
    fetch.mock.calls.some(([u]) =>
      /campaign|submission|sender/.test(String(u)),
    ),
  ).toBe(false);
});
it("restores a rejected candidate explicitly while keeping new additions gated", async () => {
  const { fetch, user } = fixture("rejected", "rejected");
  await screen.findByText("Поиск завершён");
  await user.selectOptions(screen.getByLabelText("Статус"), "rejected");
  await user.click(await screen.findByRole("button", { name: "Восстановить" }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.filter(([u]) => String(u).includes("/decision")),
    ).toHaveLength(1),
  );
});
it("requires human approval before add and exposes focused review entry", async () => {
  fixture();
  expect(
    await screen.findByRole("button", { name: "Добавить в сетку" }),
  ).toBeDisabled();
  expect(screen.getByText(/Сначала подтвердите/)).toBeVisible();
  expect(screen.getByText(/Автоматический поиск:/)).toBeVisible();
});
it("starts one explicit read-only run using structured scope", async () => {
  const { fetch, user } = fixture();
  await user.click(
    await screen.findByRole("button", { name: "Найти похожие сообщества" }),
  );
  await waitFor(() =>
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
      true,
    ),
  );
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    grid_id: "g",
    account_id: "a",
    scope_type: "grid",
    scope_value: null,
    category: null,
  });
});

it("hides an evicted cache thumbnail instead of displaying a broken image", async () => {
  fixture();
  const image = await screen.findByRole("img", { name: "Фото сообщества 1" });
  fireEvent.error(image);
  expect(image).not.toBeVisible();
});

it("keeps the original 66-candidate cohort as the primary review entry", async () => {
  fixture(null, "new", [
    {
      id: "later",
      name: "Discovery validation v1 — expansion",
      total: 100,
      reviewed_by_you: 0,
      reviewers: [],
    },
    {
      id: "original",
      name: "Discovery validation v1",
      total: 66,
      reviewed_by_you: 0,
      reviewers: [],
    },
  ]);
  expect(
    await screen.findByRole("button", { name: "Начать проверку 66" }),
  ).toBeEnabled();
});
