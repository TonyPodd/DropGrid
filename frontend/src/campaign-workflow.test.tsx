import { act, render, screen, waitFor } from "@testing-library/react";
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
        data = { choices: 0, minimum: 100, active: null, last_training: null };
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
  await screen.findByLabelText(/Anton · без пола · квота/);
  expect(screen.getByLabelText(/Anton · без пола · квота/)).toBeChecked();
  expect(screen.getByLabelText(/Second · без пола · квота/)).toBeChecked();
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
  await userEvent.click(screen.getByText("Диагностика snapshots"));
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
  await userEvent.click(screen.getByText("Настройки"));
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
          choices: 24,
          minimum: 100,
          latest_model: "deterministic",
          active: null,
          last_training: null,
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
    await screen.findByText(/выборов для обучения модели: 24 \/ 100/),
  ).toBeInTheDocument();
  expect(screen.getByText(/DRY RUN · отправка запрещена/)).toBeInTheDocument();
  expect(
    screen.queryByText("Создать отдельный dry run"),
  ).not.toBeInTheDocument();
});
it("shows the one working model and offers no ranking switch", async () => {
  const fetch = mock();
  const previous = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    if (String(input).endsWith("photo-ranking"))
      return new Response(
        JSON.stringify({
          choices: 130,
          minimum: 100,
          retrain_interval: 25,
          next_training_at: 150,
          latest_model: "pairwise-v1:fixture-v2",
          active: {
            trained_at: "2026-10-20T10:00:00Z",
            top1: 0.78,
            baseline_top1: 0.61,
          },
          last_training: {
            trained_at: "2026-10-21T10:00:00Z",
            outcome: "kept",
            learned_top1: 0.7,
            baseline_top1: 0.6,
            champion_top1: 0.78,
          },
        }),
      );
    return previous(input, init);
  });
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  expect(
    await screen.findByText(
      "Подбор фото: обученная модель · угадывает ваш выбор в 78% случаев (формула — 61%)",
    ),
  ).toBeInTheDocument();
  const summary = screen.getByText("Обучение автоподбора / диагностика");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  expect(screen.getByText(/Следующее обучение — на 150 выборах/)).toBeInTheDocument();
  expect(
    screen.getByText(/новая версия угадала 70%, рабочая — 78%: оставлена рабочая/),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Обучить сейчас" })).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "Включить проверенную модель" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Исходное ранжирование" }),
  ).not.toBeInTheDocument();
  expect(
    fetch.mock.calls.filter(([, init]) => init?.method === "PUT"),
  ).toHaveLength(0);
});
it("records visible alternatives when confirming the unchanged automatic selection", async () => {
  const fetch = mock();
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  await screen.findByText("Girl car");
  await userEvent.click(screen.getByText("Диагностика snapshots"));
  await userEvent.click(
    screen.getByRole("button", { name: "Заменить / альтернативы" }),
  );
  expect(
    fetch.mock.calls.filter(([url]) => String(url).includes("photo-choice")),
  ).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
  await screen.findByText(/Подтверждено/);
  const approval = fetch.mock.calls.find(([url]) =>
    String(url).includes("/photo-approve"),
  );
  expect(JSON.parse(String(approval?.[1]?.body))).toEqual({
    proposed_rank: 1,
    shown_ranks: [1, 2],
  });
});

it("never offers to approve a photo the operator disliked", async () => {
  const fetch = mock();
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("photo-review"))
      return new Response(
        JSON.stringify({
          total: 1,
          prepared: 1,
          needs_attention: 0,
          items: [
            {
              submission_id: "s",
              community_id: "g",
              community: "Girl car",
              media_asset_id: "m1",
              attention: [],
              confirmed: false,
              proposed_rank: 1,
              candidates: [1, 2].map((rank) => ({
                rank,
                provider: "pinterest",
                media_asset_id: `m${rank}`,
                features: { final_score: 0.8 },
                source_community_id: "g",
                operator_rating: rank === 1 ? "dislike" : null,
              })),
            },
          ],
        }),
        { status: 200 },
      );
    if (url.includes("photo-approve-all"))
      return new Response(
        JSON.stringify({ confirmed: 0, skipped_disliked: 1 }),
        { status: 200 },
      );
    return original(input, init);
  });
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  expect(
    await screen.findByText(/Вы поставили 👎 этому фото/),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Заменить / альтернативы" }),
  );
  expect(screen.getByRole("button", { name: "Отмечено 👎" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Выбрать это фото" }),
  ).toBeEnabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Подтвердить все выбранные" }),
  );
  expect(
    await screen.findByText(/Не подтверждено фото с 👎: 1/),
  ).toBeInTheDocument();
});

it("refreshes a ready campaign rarely and without blanking the review", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const fetch = mock();
    render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
    await screen.findByText("Girl car");
    const reviews = () =>
      fetch.mock.calls.filter(([url]) => String(url).includes("photo-review"))
        .length;
    const first = reviews();
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(reviews()).toBe(first);
    await act(() => vi.advanceTimersByTimeAsync(25000));
    expect(reviews()).toBeGreaterThan(first);
    expect(screen.getByText("Girl car")).toBeInTheDocument();
    expect(screen.queryByText("Загрузка…")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});
