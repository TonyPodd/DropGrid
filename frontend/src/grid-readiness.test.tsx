import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { gridsApi } from "./api/grids";
import { accountsApi } from "./api/accounts";
import { GridReadinessPanel } from "./grid-readiness";

it("shows readiness and queues preparation only by explicit operator action", async () => {
  vi.spyOn(gridsApi, "readiness").mockResolvedValue({
    total: 521,
    resolved: 500,
    active_resolvable: 500,
    unavailable: 15,
    unresolved: 4,
    transient: 2,
    with_comment: 110,
    with_content_hint: 0,
    references_ready: 3,
    media_context_ready: 3,
    with_archive_indexed: 1,
    archive_reuse_enabled: 0,
    reference_warmup_target: 12,
    ready_to_create_campaign: false,
    preparation_states: { queued: 2 },
  });
  vi.spyOn(gridsApi, "preparationJobs").mockResolvedValue({
    items: [
      {
        id: "job",
        community_id: "c1",
        account_id: "a1",
        state: "transient",
        error_code: "media_context_incomplete",
      },
    ],
    total: 1,
    page: 1,
    page_size: 25,
  });
  vi.spyOn(accountsApi, "list").mockResolvedValue([
    {
      id: "a1",
      name: "VK Account",
      vk_user_id: 1,
      status: "active",
      token_configured: true,
      gender_tag: null,
    },
  ]);
  const queue = vi
    .spyOn(gridsApi, "prepare")
    .mockResolvedValue({ selected: 521 });
  render(<GridReadinessPanel gridId="grid" />);
  await screen.findByText(/Разрешено: 500\/521/);
  expect(queue).not.toHaveBeenCalled();
  const user = userEvent.setup();
  await user.click(screen.getByText("Подготовка media context"));
  await screen.findByText(/c1: transient/);
  await user.selectOptions(
    screen.getByLabelText("Account для read-only подготовки"),
    "a1",
  );
  await user.click(
    screen.getByRole("button", {
      name: "Поставить сетку в очередь подготовки",
    }),
  );
  await waitFor(() =>
    expect(queue).toHaveBeenCalledExactlyOnceWith("grid", "a1", false),
  );
});
