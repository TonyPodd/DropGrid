import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CampaignWorkflow } from "./campaign-workflow";
import type { Campaign } from "./api/types";
const campaign = {
  id: "c",
  name: "QA",
  status: "ready",
  photo_review_mode: "REVIEW_BEFORE_SEND",
  preparation_state: "awaiting_review",
} as Campaign;
afterEach(() => vi.restoreAllMocks());
function mock(job: unknown = null) {
  let proposed = 1;
  let confirmed = false;
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      let data: unknown = {};
      if (url.endsWith("/accounts?limit=25&offset=0"))
        data = [
          {
            id: "a",
            name: "Anton",
            status: "active",
            vk_user_id: 123,
            token_configured: true,
          },
          {
            id: "b",
            name: "Second",
            status: "active",
            vk_user_id: 124,
            token_configured: true,
          },
          {
            id: "disabled",
            name: "Disabled",
            status: "disabled",
            token_configured: true,
          },
        ];
      else if (url.includes("account-pool"))
        data = [
          {
            account_id: "a",
            name: "Anton",
            quota: 100,
            assigned: 1,
            usable: true,
          },
          {
            account_id: "b",
            name: "Second",
            quota: 100,
            assigned: 0,
            usable: true,
          },
        ];
      else if (url.includes("preparation-workflow")) data = job;
      else if (url.includes("photo-review"))
        data = {
          total: 1,
          prepared: 1,
          needs_attention: 1,
          items: [
            {
              submission_id: "s",
              community_id: "g",
              community: "Girl car",
              media_asset_id: "m1",
              attention: ["small_candidate_pool"],
              confirmed,
              proposed_rank: proposed,
              candidates: [1, 2].map((rank) => ({
                rank,
                provider: "pinterest",
                media_asset_id: `m${rank}`,
                features: { final_score: 0.8 },
                source_community_id: "g",
              })),
            },
          ],
        };
      else if (url.includes("photo-ranking"))
        data = {
          mode: "deterministic",
          choices: 0,
          minimum: 100,
          promotion_ready: false,
        };
      else if (url.includes("photo-choice")) {
        proposed = JSON.parse(String(init?.body)).rank;
        data = { proposed_rank: proposed };
      } else if (url.includes("photo-approve")) {
        confirmed = true;
        data = { confirmed: true };
      }
      return new Response(JSON.stringify(data), { status: 200 });
    });
  return fetch;
}
it("selects multiple accounts, prepares once on click and shows per-account quota", async () => {
  const fetch = mock();
  render(
    <CampaignWorkflow
      campaign={{ ...campaign, status: "draft", photo_review_mode: "AUTO" }}
      onChanged={vi.fn()}
    />,
  );
  await screen.findByLabelText(/Anton · квота/);
  expect(screen.getByLabelText(/Anton · квота/)).toBeChecked();
  expect(screen.getByLabelText(/Second · квота/)).toBeChecked();
  expect(screen.queryByText("Disabled")).not.toBeInTheDocument();
  expect(screen.getByText("1 / 100")).toBeInTheDocument();
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("prepare-workflow")),
  ).toBe(false);
  await userEvent.click(
    screen.getByRole("button", { name: "Подготовить кампанию" }),
  );
  await waitFor(() =>
    expect(
      fetch.mock.calls.filter(([url]) =>
        String(url).endsWith("prepare-workflow"),
      ),
    ).toHaveLength(1),
  );
  expect(
    JSON.parse(
      String(
        fetch.mock.calls.find(([url]) =>
          String(url).endsWith("prepare-workflow"),
        )?.[1]?.body,
      ),
    ),
  ).toEqual({ account_ids: ["a", "b"] });
});
it("replacement remains unconfirmed until approval and only displayed choices are sent", async () => {
  const fetch = mock();
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  await screen.findByText("Girl car");
  expect(
    screen.getByText(
      "Ваш выбор будет использоваться для улучшения автоподбора.",
    ),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Заменить / альтернативы" }),
  );
  await userEvent.click(
    screen.getAllByRole("button", { name: "Выбрать это фото" })[1],
  );
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([url]) => String(url).includes("photo-choice")),
    ).toBe(true),
  );
  expect(
    JSON.parse(
      String(
        fetch.mock.calls.find(([url]) =>
          String(url).includes("photo-choice"),
        )?.[1]?.body,
      ),
    ),
  ).toEqual({ rank: 2, shown_ranks: [1, 2] });
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("photo-approve")),
  ).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
  await screen.findByText(/Подтверждено/);
});
it("recovers durable campaign progress on reload without enqueue", async () => {
  const fetch = mock({
    state: "running",
    stage: "communities",
    completed: 2,
    total: 3,
  });
  const first = render(
    <CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />,
  );
  await screen.findByText("2 / 3 групп");
  expect(
    screen.getByRole("button", { name: "Подготовить кампанию" }),
  ).toBeDisabled();
  first.unmount();
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  await screen.findByText("2 / 3 групп");
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("prepare-workflow")),
  ).toBe(false);
});
it("shows full-grid capacity and compact learning readiness", async () => {
  const fetch = mock();
  const previous = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("readiness-report"))
      return new Response(
        JSON.stringify({
          scope: 514,
          real_capacity: 100,
          capacity_shortfall: 414,
        }),
      );
    if (url.endsWith("photo-ranking"))
      return new Response(
        JSON.stringify({
          mode: "deterministic",
          choices: 24,
          minimum: 100,
          latest_model: "deterministic",
          promotion_ready: false,
        }),
      );
    return previous(input, init);
  });
  render(
    <CampaignWorkflow
      campaign={{ ...campaign, is_dry_run: true }}
      onChanged={vi.fn()}
    />,
  );
  expect(await screen.findByText("Нужно отправить: 514")).toBeInTheDocument();
  expect(screen.getByText("Доступная ёмкость: 100")).toBeInTheDocument();
  expect(screen.getByText("Не хватает: 414")).toBeInTheDocument();
  expect(
    screen.getByText("Подключите дополнительные аккаунты или уменьшите scope."),
  ).toBeInTheDocument();
  expect(
    await screen.findByText(/Manual decisions: 24 \/ 100/),
  ).toBeInTheDocument();
  expect(screen.getByText(/DRY RUN · отправка запрещена/)).toBeInTheDocument();
  expect(
    screen.queryByText("Создать отдельный dry run"),
  ).not.toBeInTheDocument();
});
it("keeps shadow diagnostics under Advanced and never promotes automatically", async () => {
  const fetch = mock();
  const previous = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    if (String(input).endsWith("photo-ranking"))
      return new Response(
        JSON.stringify({
          mode: "deterministic",
          choices: 100,
          minimum: 100,
          latest_model: "shadow:fixture-v1",
          promotion_ready: true,
          metrics: {
            train: 80,
            validation: 20,
            baseline_top1: 0.5,
            learned_top1: 0.65,
            promotion_ready: true,
          },
        }),
      );
    return previous(input, init);
  });
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  expect(
    await screen.findByText(/Latest model: shadow:fixture-v1/),
  ).toBeInTheDocument();
  const summary = screen.getByText("Обучение автоподбора / диагностика");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  expect(screen.getByText("Training decisions")).toBeInTheDocument();
  expect(screen.getByText("Validation decisions")).toBeInTheDocument();
  expect(screen.getByText("50.0%")).toBeInTheDocument();
  expect(screen.getByText("65.0%")).toBeInTheDocument();
  expect(screen.getByText("yes")).toBeInTheDocument();
  expect(
    fetch.mock.calls.filter(([, init]) => init?.method === "PUT"),
  ).toHaveLength(0);
});
it("records visible alternatives when confirming the unchanged automatic selection", async () => {
  const fetch = mock();
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  await screen.findByText("Girl car");
  await userEvent.click(
    screen.getByRole("button", { name: "Заменить / альтернативы" }),
  );
  expect(
    fetch.mock.calls.filter(([url]) => String(url).includes("photo-choice")),
  ).toHaveLength(0);
  await userEvent.click(
      screen.getByRole("button", { name: "Подтвердить" }),
  );
  await screen.findByText(/Подтверждено/);
  const approval = fetch.mock.calls.find(([url]) =>
    String(url).includes("/photo-approve"),
  );
  expect(JSON.parse(String(approval?.[1]?.body))).toEqual({
    proposed_rank: 1,
    shown_ranks: [1, 2],
  });
});
