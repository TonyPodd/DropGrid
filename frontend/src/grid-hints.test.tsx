import { expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { GridDetailPage, ParsePreview } from "./grids";
import { gridsApi } from "./api/grids";

it("preserves shorthand comments in import preview", () => {
  render(
    <ParsePreview
      preview={{
        items: [{ community: "test", category: "ЦИТАТА", comment: "Д-П" }],
        errors: [],
      }}
    />,
  );
  expect(screen.getByText("Д-П")).toBeInTheDocument();
});

it("edits grid-specific hint only after explicit promotion/save", async () => {
  const community = {
    id: "c1",
    domain: "test",
    name: null,
    category: "МУЗЫКА",
    vk_group_id: null,
    is_active: true,
    comment: "девушка с машиной",
    content_hint: null,
  };
  vi.spyOn(gridsApi, "detail").mockResolvedValue({
    id: "g1",
    name: "grid",
    created_at: "2026-10-09",
    community_count: 1,
    categories: [],
    communities: [],
  });
  vi.spyOn(gridsApi, "members").mockResolvedValue({
    items: [community],
    total: 1,
    page: 1,
    page_size: 25,
  });
  const save = vi.spyOn(gridsApi, "updateMember").mockResolvedValue(community);
  render(
    <MemoryRouter initialEntries={["/grids/g1"]}>
      <Routes>
        <Route path="/grids/:id" element={<GridDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByText("девушка с машиной"));
  expect(screen.getByLabelText("Content hint test")).toHaveValue("");
  expect(save).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Использовать комментарий как hint" }),
  );
  expect(screen.getByLabelText("Content hint test")).toHaveValue(
    "девушка с машиной",
  );
  expect(save).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Сохранить hint" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith("g1", "c1", {
      comment: "девушка с машиной",
      content_hint: "девушка с машиной",
    }),
  );
  expect(
    screen.getByRole("link", { name: "Предпросмотр фото для этой сетки" }),
  ).toHaveAttribute("href", "/communities/c1?grid=g1");
});
