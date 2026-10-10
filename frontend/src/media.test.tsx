import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MediaPage, PhotoPlanButton } from "./media";
import { campaignsApi } from "./api/campaigns";
import { CampaignDetailPage, SubmissionTable } from "./campaigns";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { MediaPlan } from "./api/types";

afterEach(() => vi.restoreAllMocks());
const result: MediaPlan = {
  campaign_id: "campaign",
  total_submissions: 10,
  previously_assigned: 0,
  newly_assigned: 10,
  unassigned: 0,
  unique_assets: 5,
  categories: [],
};

it("plans only on click and shows loading and full success", async () => {
  let finish!: (value: MediaPlan) => void;
  const plan = vi.spyOn(campaignsApi, "planMedia").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const onPlanned = vi.fn();
  render(<PhotoPlanButton campaignId="campaign" onPlanned={onPlanned} />);
  expect(plan).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Подобрать фото" }));
  expect(screen.getByRole("button")).toBeDisabled();
  expect(screen.getByText("Поиск и обработка фотографий…")).toBeInTheDocument();
  finish(result);
  expect(await screen.findByText(/Фото: 10 \/ 10/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Pixabay" })).toHaveAttribute(
    "href",
    "https://pixabay.com/",
  );
  expect(onPlanned).toHaveBeenCalledTimes(1);
});

it("shows a partial result and provider unavailable", async () => {
  vi.spyOn(campaignsApi, "planMedia").mockResolvedValue({
    ...result,
    newly_assigned: 4,
    unassigned: 6,
    categories: [
      {
        name: "cats",
        submission_count: 10,
        assigned_count: 4,
        unique_asset_count: 2,
        warnings: ["provider_unavailable"],
      },
    ],
  });
  render(<PhotoPlanButton campaignId="campaign" onPlanned={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Подобрать фото" }));
  expect(await screen.findByText(/Фото: 4 \/ 10/)).toBeInTheDocument();
  expect(screen.getByText(/Провайдер фото недоступен/)).toBeInTheDocument();
});

it("renders partial quality warning", async () => {
  vi.spyOn(campaignsApi, "planMedia").mockResolvedValue({
    ...result,
    unassigned: 1,
    categories: [
      {
        name: "cats",
        submission_count: 10,
        assigned_count: 9,
        unique_asset_count: 5,
        warnings: ["insufficient_photos"],
      },
    ],
  });
  render(<PhotoPlanButton campaignId="campaign" onPlanned={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Подобрать фото" }));
  expect(
    await screen.findByText(/недостаточно подходящих фото/),
  ).toBeInTheDocument();
});

it("library displays local image, provenance, usage and filters", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        items: [
          {
            id: "asset",
            category: "кошки",
            provider: "pixabay",
            creator_name: "Creator",
            source_url: "https://pixabay.com/photos/test-1/",
            license_url: "https://pixabay.com/service/license-summary/",
            license_name: "Pixabay Content License",
            width: 1000,
            height: 1000,
            usage_count: 2,
            last_used_at: null,
            enabled: true,
          },
        ],
        total: 1,
        page: 1,
        page_size: 25,
      }),
      { status: 200 },
    ),
  );
  render(<MediaPage />);
  const image = await screen.findByRole("img");
  expect(image.getAttribute("src")).toContain(
    "/api/v1/media-assets/asset/content",
  );
  expect(screen.getByText("pixabay · Creator")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Источник ↗" })).toHaveAttribute(
    "href",
    "https://pixabay.com/photos/test-1/",
  );
  expect(
    screen.getByRole("link", { name: "Pixabay Content License" }),
  ).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Категория"), "кошки");
  await waitFor(() =>
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("category="),
  );
  await userEvent.type(screen.getByLabelText("Провайдер"), "pixabay");
  await userEvent.selectOptions(screen.getByLabelText("Доступность"), "true");
  await waitFor(() =>
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("enabled=true"),
  );
});

it("submission shows local thumbnail", () => {
  render(
    <SubmissionTable
      items={[
        {
          id: "s",
          community: {
            id: "c",
            domain: "clubtest",
            name: null,
            category: "cats",
            vk_group_id: null,
            is_active: true,
          },
          category: "cats",
          account_name: null,
          media_label: "photo",
          media_asset_id: "asset",
          status: "pending",
          attempt_count: 0,
          error_code: null,
          error_message: null,
          published_post_url: null,
        },
      ]}
    />,
  );
  expect(screen.getByRole("img")).toHaveAttribute("alt", "Фото для clubtest");
});

it("campaign keeps partial warning after refreshing its data", async () => {
  let detailLoads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    let data: unknown;
    if (url.includes("/accounts?")) data = [];
    else if (url.endsWith("/stats"))
      data = { total: 10, statuses: {}, media_assigned: 4, media_unique: 2 };
    else if (url.includes("/account-pool") || url.includes("/accounts"))
      data = [];
    else if (url.includes("/preparation-workflow")) data = null;
    else if (url.includes("/photo-review"))
      data = { items: [], total: 0, prepared: 0, needs_attention: 0 };
    else if (url.includes("/photo-ranking"))
      data = { mode: "deterministic", choices: 0, minimum: 100 };
    else if (url.includes("/results-breakdown")) data = { breakdowns: {} };
    else if (url.includes("/submissions"))
      data = { items: [], total: 10, page: 1, page_size: 25 };
    else if (url.includes("/grids/"))
      data = { categories: [], communities: [] };
    else {
      detailLoads++;
      data = {
        id: "campaign",
        name: "Photo campaign",
        grid_id: "grid",
        grid_name: "Grid",
        community_count: 10,
        submission_count: 10,
        status: "ready",
        track_url: "https://vk.ru/audio1_2",
        caption: "",
        publication_check_hours: 72,
        created_at: "2026-10-08T00:00:00Z",
      };
    }
    return new Response(JSON.stringify(data), { status: 200 });
  });
  vi.spyOn(campaignsApi, "planMedia").mockResolvedValue({
    ...result,
    unassigned: 6,
    categories: [
      {
        name: "cats",
        submission_count: 10,
        assigned_count: 4,
        unique_asset_count: 2,
        warnings: ["provider_unavailable"],
      },
    ],
  });
  render(
    <MemoryRouter initialEntries={["/campaigns/campaign"]}>
      <Routes>
        <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Подобрать фото" }),
  );
  await waitFor(() => expect(detailLoads).toBeGreaterThanOrEqual(2));
  expect(
    await screen.findByText(/Провайдер фото недоступен/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Photo campaign" }),
  ).toBeInTheDocument();
});
