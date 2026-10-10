import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { CommunityDetailPage, communityVisualApi } from "./community";
const profile = {
  community_id: "c1",
  desired_content: null,
  avoid_content: null,
  style_notes: null,
  reference_target_count: 100,
  archive_reuse_enabled: false,
  archive_reuse_min_age_days: 180,
  archive_reuse_max_age_days: 540,
  reference_count: 12,
  references_last_synced_at: null,
};
const candidate = {
  media_asset_id: null,
  reference_id: "r2",
  source_community_id: "other",
  source: "vk_category_archive",
  source_identity: "1_2",
  publication_eligible: false,
  base_score: 1,
  visual_score: 0.86,
  final_score: 0.8,
  top_references: [{ reference_id: "r1", similarity: 0.9 }],
};
const result = {
  category: "HONDA ACCORD",
  comment: "comment",
  content_hint: "Honda Accord",
  warnings: [] as string[],
  category_only: [],
  community_aware: [],
  best_matches: [
    candidate,
    {
      ...candidate,
      reference_id: null,
      media_asset_id: "m1",
      source: "pixabay",
      source_identity: "pix1",
    },
  ],
  visual_engine: {
    enabled: true,
    model: "clip",
    active: true,
    compatible_reference_count: 9,
    candidate_embeddings_available: 2,
    reason_if_inactive: null,
  },
};
function photoJob(kind = "preview", state = "ready") {
  return {
    id: "p1",
    kind,
    state,
    stage: state === "running" ? "pinterest_search" : state,
    current: 0,
    total: null,
    elapsed_seconds: 4,
    counters: {},
    error_code: null,
    result:
      kind === "preview"
        ? result
        : {
            posts_scanned: 200,
            candidates_discovered: 3,
            candidates_existing: 0,
            warnings: [],
          },
  };
}
beforeEach(() => {
  vi.spyOn(communityVisualApi, "profile").mockResolvedValue(profile);
  vi.spyOn(communityVisualApi, "metadata").mockResolvedValue({
    name: "Honda Accord",
    domain: "accordclubrus",
    category: "Honda",
  });
  vi.spyOn(communityVisualApi, "references").mockResolvedValue({
    total: 1,
    items: [
      {
        id: "r1",
        vk_post_id: 5,
        posted_at: "2026-10-08T10:00:00Z",
        embedding_model: "clip",
        reference_role: "auxiliary",
        reference_role_reason: "small_cluster",
        reference_cluster_size: 2,
      },
    ],
  });
  vi.spyOn(communityVisualApi, "save").mockResolvedValue(profile);
  vi.spyOn(communityVisualApi, "study").mockResolvedValue(null);
  vi.spyOn(communityVisualApi, "sync").mockResolvedValue({
    id: "j1",
    state: "ready",
    elapsed_seconds: 2,
    progress: {},
    result: { warnings: [] },
    error_code: null,
  });
  vi.spyOn(communityVisualApi, "latest").mockResolvedValue(null);
  vi.spyOn(communityVisualApi, "preview").mockImplementation(async () => {
    vi.mocked(communityVisualApi.latest).mockImplementation(
      async (_id, kind) => (kind === "preview" ? photoJob() : null),
    );
    return photoJob();
  });
  vi.spyOn(communityVisualApi, "archive").mockImplementation(async () => {
    vi.mocked(communityVisualApi.latest).mockImplementation(
      async (_id, kind) => (kind === "archive" ? photoJob("archive") : null),
    );
    return photoJob("archive");
  });
});
function open(entry = "/communities/c1") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/communities/:id" element={<CommunityDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
async function settings() {
  await screen.findByText("Настройки профиля и индексации");
  await userEvent.click(screen.getByText("Настройки профиля и индексации"));
}
it("loads profile without starting operations and preserves explicit archive opt-in", async () => {
  open();
  await settings();
  expect(
    screen.getByLabelText("Разрешить использование архивных фото"),
  ).not.toBeChecked();
  await userEvent.type(screen.getByLabelText("Желаемый контент"), "cars");
  await userEvent.click(
    screen.getByRole("button", { name: "Сохранить профиль" }),
  );
  await waitFor(() =>
    expect(communityVisualApi.save).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ desired_content: "cars" }),
    ),
  );
  expect(communityVisualApi.sync).not.toHaveBeenCalled();
  expect(communityVisualApi.preview).not.toHaveBeenCalled();
  expect(communityVisualApi.archive).not.toHaveBeenCalled();
});
it("submits provider toggles and explicit grid context only after click", async () => {
  open("/communities/c1?grid=g1");
  await screen.findByRole("button", { name: /Сравнить подбор/ });
  await userEvent.click(screen.getByLabelText("Pixabay"));
  await userEvent.click(
    screen.getByRole("button", { name: /Сравнить подбор/ }),
  );
  await screen.findByRole("heading", { name: "Выбрано системой" });
  expect(communityVisualApi.preview).toHaveBeenCalledExactlyOnceWith(
    "c1",
    "g1",
    expect.objectContaining({
      include_pixabay: false,
      include_category_library: true,
      include_pinterest: true,
    }),
    false,
  );
});
it("shows one ranked grid, filters sources and explains source vs target images", async () => {
  vi.mocked(communityVisualApi.latest).mockImplementation(async (_id, kind) =>
    kind === "preview" ? photoJob() : null,
  );
  open();
  await screen.findByRole("heading", { name: "Выбрано системой" });
  expect(screen.getAllByAltText("Подобранное фото")).toHaveLength(1);
  expect(screen.getByAltText("Выбрано системой")).toBeInTheDocument();
  expect(screen.getByAltText("Выбрано системой")).toHaveAttribute(
    "src",
    expect.stringContaining("/communities/other/references/r2/content"),
  );
  await userEvent.click(
    screen.getByRole("button", { name: /VK category library.*Итоговое/ }),
  );
  const dialog = screen.getByRole("dialog", { name: "Почему это фото" });
  expect(
    within(dialog).getByText(/Итоговое соответствие: 0.800/),
  ).toBeInTheDocument();
  expect(within(dialog).getByAltText("Похожий пост группы")).toHaveAttribute(
    "src",
    expect.stringContaining("/communities/c1/references/r1/content"),
  );
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Закрыть" }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("recovers queued preview after reload and shows actual stage", async () => {
  vi.mocked(communityVisualApi.latest).mockImplementation(async (_id, kind) =>
    kind === "preview" ? photoJob("preview", "running") : null,
  );
  const view = open();
  await screen.findByText(/Ищем публичные Pins/);
  expect(
    screen.getByRole("button", { name: /Сравнить подбор/ }),
  ).toBeDisabled();
  view.unmount();
  open();
  await screen.findByText(/Ищем публичные Pins/);
  expect(communityVisualApi.preview).not.toHaveBeenCalled();
});
it("recovers reference progress and explains small-cluster AUX", async () => {
  vi.mocked(communityVisualApi.study).mockResolvedValue({
    id: "j2",
    state: "embedding",
    elapsed_seconds: 14,
    progress: { embeddings_done: 7, embeddings_total: 12 },
    error_code: null,
    result: null,
  });
  open();
  await screen.findByText(/Строим embeddings: 7\/12/);
  await userEvent.click(screen.getByText("Визуальные референсы · CORE / AUX"));
  expect(screen.getByText(/Небольшой визуальный кластер/)).toBeInTheDocument();
  await settings();
  expect(screen.getByRole("button", { name: /Изучить/ })).toBeDisabled();
});
it("archive uses a durable operation and explicit age window", async () => {
  open();
  await settings();
  await userEvent.click(
    screen.getByRole("button", { name: "Проиндексировать архив" }),
  );
  await screen.findByText(/Архив: просмотрено 200/);
  expect(communityVisualApi.archive).toHaveBeenCalledExactlyOnceWith("c1");
  expect(communityVisualApi.save).toHaveBeenCalledWith(
    "c1",
    expect.objectContaining({
      archive_reuse_enabled: false,
      archive_reuse_min_age_days: 180,
      archive_reuse_max_age_days: 540,
    }),
  );
});
it("shows safe provider warning while retaining other results", async () => {
  const job = photoJob();
  job.result = { ...result, warnings: ["pinterest_search_unavailable"] };
  vi.mocked(communityVisualApi.latest).mockImplementation(async (_id, kind) =>
    kind === "preview" ? job : null,
  );
  open();
  await screen.findByText("Pinterest временно не вернул результаты.");
  expect(screen.getAllByAltText("Подобранное фото")).toHaveLength(1);
  expect(screen.getByAltText("Выбрано системой")).toBeInTheDocument();
});
it("shows source contributions, publication boundary and stores feedback", async () => {
  const job = photoJob();
  job.result = {
    ...result,
    pinterest_retrieved: 27,
    pinterest_embedded: 24,
    pixabay_status: "not_needed",
    source_contributions: {
      pinterest: {
        retrieved: 27,
        deduplicated: 24,
        materialized: 24,
        embedded: 24,
        top_10: 6,
        selected: 0,
      },
    },
    best_matches: [
      {
        ...candidate,
        source: "pinterest",
        provider: "pinterest",
        reference_id: null,
        preview_id: "pin-preview",
        pin_url: "https://www.pinterest.com/pin/123450001/",
        source_identity: "123450001",
      },
    ],
  } as unknown as typeof result;
  vi.mocked(communityVisualApi.latest).mockImplementation(async (_id, kind) =>
    kind === "preview" ? job : null,
  );
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(JSON.stringify({ rating: "like" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  open();
  await screen.findByText(/27 найдено/);
  expect(screen.getByText(/не понадобился/)).toBeInTheDocument();
  await userEvent.click(screen.getByAltText("Выбрано системой"));
  expect(
    screen.queryByText(/Права на публикацию не проверены/),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "👍 подходит" }));
  await screen.findByText(/Оценка сохранена/);
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("/photo-feedback"),
    expect.objectContaining({
      method: "PUT",
      body: expect.stringContaining('"rating":"like"'),
    }),
  );
});

it("navigates review communities and counts only persisted manual feedback", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => ({
      ok: true,
      json: async () =>
        String(url).includes("/c1/photo-feedback") ? [{ rating: "like" }] : [],
    })),
  );
  open("/communities/c1?grid=g1&review=c1,c2,c3");
  await screen.findByText("1 / 3 communities reviewed");
  expect(screen.getByRole("link", { name: "Next community" })).toHaveAttribute(
    "href",
    "/communities/c2?grid=g1&review=c1%2Cc2%2Cc3",
  );
});

it("one primary action prepares references and photos, diagnostics are collapsed", async () => {
  open("/communities/c1?grid=g1");
  await screen.findByRole("button", { name: "Подобрать фото" });
  expect(
    screen.getByText("Расширенные настройки / Диагностика").closest("details"),
  ).not.toHaveAttribute("open");
  await userEvent.click(screen.getByRole("button", { name: "Подобрать фото" }));
  await waitFor(() =>
    expect(communityVisualApi.preview).toHaveBeenCalledWith(
      "c1",
      "g1",
      expect.any(Object),
      true,
    ),
  );
  await screen.findByRole("heading", { name: "Выбрано системой" });
  expect(screen.queryByText("Только предпросмотр")).not.toBeInTheDocument();
});
