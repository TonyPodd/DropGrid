import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CampaignTable } from "./campaigns";
import { campaignsApi } from "./api/campaigns";
import { ApiError } from "./api/client";
import type { Campaign } from "./api/types";

const base = {
  grid_id: "g",
  grid_name: "Grid",
  community_count: 521,
  submission_count: 0,
  track_url: "https://vk.ru/audio1_2",
  track_owner_id: 1,
  track_audio_id: 2,
  caption: null,
  publication_check_hours: 72,
  created_at: "2026-10-22T10:00:00Z",
};
const draft = { ...base, id: "d", name: "Черновик", status: "draft" } as Campaign;
const sending = { ...base, id: "s", name: "Рассылка", status: "running" } as Campaign;
const preparing = {
  ...base,
  id: "p",
  name: "Подбор",
  status: "ready",
  preparation_state: "preparing",
} as Campaign;
afterEach(() => vi.restoreAllMocks());

function table(campaigns: Campaign[], onChanged = vi.fn()) {
  render(
    <MemoryRouter>
      <CampaignTable campaigns={campaigns} onChanged={onChanged} />
    </MemoryRouter>,
  );
  return onChanged;
}

it("deletes a draft after confirmation", async () => {
  const remove = vi.spyOn(campaignsApi, "remove").mockResolvedValue(undefined);
  const changed = table([draft]);
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  expect(screen.getByRole("dialog", { name: "Удалить кампанию" })).toHaveTextContent(
    "не появятся в новых кампаниях",
  );
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("d"));
  expect(changed).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("offers to stop, not delete, a campaign in progress", async () => {
  const remove = vi.spyOn(campaignsApi, "remove");
  const cancel = vi
    .spyOn(campaignsApi, "cancel")
    .mockResolvedValue({ ...sending, status: "cancelled" });
  const changed = table([sending, preparing]);
  const stops = screen.getAllByRole("button", { name: "Остановить" });
  expect(stops).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Удалить" })).not.toBeInTheDocument();
  await userEvent.click(stops[0]);
  expect(screen.getByRole("dialog", { name: "Остановить кампанию" })).toHaveTextContent(
    "перестанет предлагать новые посты",
  );
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  await waitFor(() => expect(cancel).toHaveBeenCalledWith("s"));
  expect(changed).toHaveBeenCalledOnce();
  expect(remove).not.toHaveBeenCalled();
  await userEvent.click(screen.getAllByRole("button", { name: "Остановить" })[1]);
  expect(screen.getByRole("dialog")).toHaveTextContent("подбор фото прервётся");
});

it("keeps the dialog open with the reason when deletion is refused", async () => {
  vi.spyOn(campaignsApi, "remove").mockRejectedValue(
    new ApiError(409, "Кампания выполняется. Сначала остановите её, потом удалите."),
  );
  const changed = table([draft]);
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Сначала остановите её");
  expect(changed).not.toHaveBeenCalled();
});
