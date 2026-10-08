import { render, screen, waitFor } from "@testing-library/react";
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
  reference_count: 1,
  references_last_synced_at: null,
};
beforeEach(() => {
  vi.spyOn(communityVisualApi, "profile").mockResolvedValue(profile);
  vi.spyOn(communityVisualApi, "references").mockResolvedValue({
    total: 21,
    items: [
      {
        id: "r1",
        posted_at: "2026-10-08T10:00:00Z",
        vk_post_id: 5,
        embedding_model: null,
      },
    ],
  });
  vi.spyOn(communityVisualApi, "save").mockResolvedValue(profile);
  vi.spyOn(communityVisualApi, "sync").mockResolvedValue({
    posts_scanned: 2,
    references_created: 1,
    references_existing: 0,
    references_embedded: 1,
    warnings: [],
  });
  vi.spyOn(communityVisualApi, "preview").mockResolvedValue({
    warnings: [],
    category_only: [
      {
        media_asset_id: "m1",
        base_score: 5,
        visual_score: 0.8,
        final_score: 0.7,
      },
    ],
    community_aware: [
      {
        media_asset_id: "m1",
        base_score: 5,
        visual_score: 0.8,
        final_score: 0.7,
      },
    ],
  });
});
function open() {
  render(
    <MemoryRouter initialEntries={["/communities/c1"]}>
      <Routes>
        <Route path="/communities/:id" element={<CommunityDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
it("loads bounded references and saves explicit profile without starting VK sync", async () => {
  open();
  const field = await screen.findByLabelText("Желаемый контент");
  expect(
    screen.getByLabelText("Разрешить использование архивных фото"),
  ).not.toBeChecked();
  expect(
    screen.getByLabelText("Минимальный возраст архивного фото (дней)"),
  ).toHaveValue(180);
  await userEvent.type(field, "dogs");
  await userEvent.click(
    screen.getByRole("button", { name: "Сохранить профиль" }),
  );
  await waitFor(() =>
    expect(communityVisualApi.save).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ desired_content: "dogs" }),
    ),
  );
  expect(communityVisualApi.sync).not.toHaveBeenCalled();
  expect(communityVisualApi.preview).not.toHaveBeenCalled();
  expect(communityVisualApi.references).toHaveBeenCalledWith(
    "c1",
    1,
    expect.any(AbortSignal),
  );
  expect(screen.getByRole("img")).toHaveAttribute(
    "src",
    expect.stringContaining("/references/r1/content"),
  );
});
it("runs one sync only after explicit click and shows bounded preview scores", async () => {
  open();
  await screen.findByLabelText("Желаемый контент");
  expect(communityVisualApi.sync).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Изучить последние посты" }),
  );
  await screen.findByText(/Просмотрено постов: 2/);
  expect(communityVisualApi.sync).toHaveBeenCalledExactlyOnceWith("c1");
  await userEvent.click(
    screen.getByRole("button", { name: "Сравнить подбор фото" }),
  );
  await screen.findByRole("heading", { name: "С учётом сообщества" });
  expect(communityVisualApi.preview).toHaveBeenCalledExactlyOnceWith("c1");
  expect(screen.getAllByText(/visual: 0.800/)).toHaveLength(2);
});
