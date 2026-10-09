import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CampaignSendControls } from "./campaign-sending";
import { campaignsApi } from "./api/campaigns";
import { accountsApi } from "./api/accounts";
import type { Campaign } from "./api/types";
const campaign: Campaign = {
  id: "campaign",
  name: "Pilot",
  grid_id: "g",
  grid_name: "G",
  community_count: 2,
  submission_count: 2,
  track_url: "audio1_2",
  track_owner_id: 1,
  track_audio_id: 2,
  caption: null,
  publication_check_hours: 72,
  status: "ready",
  created_at: "2026-10-09",
};
const report = {
  total: 2,
  resolved_sendable: 1,
  unavailable: 1,
  gender_incompatible: 0,
  intended: 1,
  media_assigned: 1,
  media_missing: 0,
  media_invalid: 0,
  account_usable: true,
  ready: true,
};
afterEach(() => vi.restoreAllMocks());
function mock() {
  vi.spyOn(accountsApi, "list").mockResolvedValue([
    {
      id: "a",
      name: "Usable",
      vk_user_id: 123,
      status: "active",
      gender_tag: null,
      token_configured: true,
    },
    {
      id: "disabled",
      name: "Disabled",
      vk_user_id: 123,
      status: "disabled",
      gender_tag: null,
      token_configured: true,
    },
  ]);
  vi.spyOn(campaignsApi, "preflight").mockResolvedValue(report);
  return vi
    .spyOn(campaignsApi, "start")
    .mockResolvedValue({ ...campaign, status: "running" });
}
it("requires explicit Account and confirmation, starts a pilot scope only on click", async () => {
  const start = mock(),
    changed = vi.fn();
  render(<CampaignSendControls campaign={campaign} onChanged={changed} />);
  expect(screen.getByRole("button", { name: "Start campaign" })).toBeDisabled();
  await screen.findByRole("option", { name: /Usable/ });
  expect(
    screen.queryByRole("option", { name: /Disabled/ }),
  ).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByRole("combobox"), "a");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Start campaign" }),
    ).toBeEnabled(),
  );
  expect(screen.getByRole("status")).toHaveTextContent("Недоступно: 1");
  expect(start).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Start campaign" }));
  expect(start).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(start).toHaveBeenCalledWith("campaign", {
    account_id: "a",
    max_submissions: 1,
  });
  expect(changed).toHaveBeenCalledOnce();
});
it("blocks missing media preflight", async () => {
  mock();
  vi.mocked(campaignsApi.preflight).mockResolvedValue({
    ...report,
    ready: false,
    media_missing: 1,
  });
  render(<CampaignSendControls campaign={campaign} onChanged={vi.fn()} />);
  await screen.findByRole("option", { name: /Usable/ });
  await userEvent.selectOptions(screen.getByRole("combobox"), "a");
  await screen.findByText(/Без фото: 1/);
  expect(screen.getByRole("button", { name: "Start campaign" })).toBeDisabled();
});
it("supports full scope explicitly and surfaces fixed start errors", async () => {
  const start = mock();
  start.mockRejectedValue(new Error("Temporary failure"));
  render(<CampaignSendControls campaign={campaign} onChanged={vi.fn()} />);
  await screen.findByRole("option", { name: /Usable/ });
  await userEvent.selectOptions(screen.getByRole("combobox"), "a");
  await userEvent.click(screen.getByRole("checkbox"));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Start campaign" }),
    ).toBeEnabled(),
  );
  await userEvent.click(screen.getByRole("button", { name: "Start campaign" }));
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(start).toHaveBeenCalledWith("campaign", {
    account_id: "a",
    max_submissions: null,
  });
  expect(await screen.findByRole("alert")).toBeInTheDocument();
});
it("cancels running campaign without initiating any send", async () => {
  const start = mock();
  const cancel = vi
    .spyOn(campaignsApi, "cancel")
    .mockResolvedValue({ ...campaign, status: "cancelled" });
  render(
    <CampaignSendControls
      campaign={{ ...campaign, status: "running" }}
      onChanged={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Start campaign" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Cancel campaign" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(cancel).toHaveBeenCalledWith("campaign");
  expect(start).not.toHaveBeenCalled();
});
