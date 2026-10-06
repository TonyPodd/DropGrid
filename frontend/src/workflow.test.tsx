import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { GridImportPage, ParsePreview } from "./grids";
import {
  CampaignForm,
  PrepareButton,
  SubmissionTable,
  SubmissionsPanel,
} from "./campaigns";
import { gridsApi } from "./api/grids";
import { campaignsApi } from "./api/campaigns";
import { AccountsPage } from "./catalog";
import { accountsApi } from "./api/accounts";
import { ApiError } from "./api/client";
import type { Campaign, Submission } from "./api/types";

const preview = {
  items: [
    { community: "lujbit", category: "ГРУЗОВИКИ" },
    { community: "example", category: "ТРАКТОРЫ" },
  ],
  errors: [
    {
      line: 14,
      value: "14 bad link!",
      message: "Invalid VK community reference",
    },
  ],
};
const detail = {
  id: "g1",
  name: "Grid",
  created_at: "2026-10-07",
  community_count: 2,
  communities: [],
  categories: [{ category: "ALPHA", count: 2 }],
};

describe("Grid import", () => {
  it("shows valid rows, categories and original line errors", () => {
    render(<ParsePreview preview={preview} />);
    expect(screen.getByText("lujbit")).toBeInTheDocument();
    expect(screen.getByText("Строка 14")).toBeInTheDocument();
    expect(screen.getByText("14 bad link!")).toBeInTheDocument();
    expect(screen.getByText(/Найдено:/)).toHaveTextContent(
      "Найдено: 2 · Категорий: 2 · Ошибок: 1",
    );
  });
  it("requires confirmation for skipped rows and invalidates preview after text edit", async () => {
    const user = userEvent.setup();
    vi.spyOn(gridsApi, "parse").mockResolvedValue(preview);
    const save = vi
      .spyOn(gridsApi, "import")
      .mockResolvedValue({ grid: { id: "new" } });
    render(
      <MemoryRouter>
        <GridImportPage />
      </MemoryRouter>,
    );
    await user.type(screen.getByLabelText("Вставьте сетку"), "foo");
    await user.click(screen.getByText("Разобрать сетку"));
    await screen.findByText("Строка 14");
    await user.type(screen.getByLabelText("Название сетки"), "Grid");
    await user.click(screen.getByText("Сохранить сетку"));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      "2 сообществ будет импортировано, 1 строк будет пропущено",
    );
    expect(save).not.toHaveBeenCalled();
    await user.click(within(dialog).getByText("Отмена"));
    await user.type(screen.getByLabelText("Вставьте сетку"), "bar");
    expect(screen.queryByText("Предпросмотр")).not.toBeInTheDocument();
  });
});

describe("Campaign creation", () => {
  it("uses pure parser result, preserves caption and validates hours", async () => {
    const user = userEvent.setup();
    vi.spyOn(gridsApi, "list").mockResolvedValue([
      { ...detail, category_count: 1 },
    ]);
    vi.spyOn(gridsApi, "detail").mockResolvedValue(detail);
    const parse = vi
      .spyOn(campaignsApi, "parseTrack")
      .mockRejectedValue(new ApiError(422, "Invalid"));
    const save = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <CampaignForm onSave={save} />
      </MemoryRouter>,
    );
    await user.type(screen.getByLabelText("Name"), "Test");
    await screen.findByRole("option", { name: "Grid" });
    await user.selectOptions(screen.getByLabelText("Grid"), "g1");
    await user.type(
      screen.getByLabelText("VK audio link"),
      "https://vk.ru/not-audio",
    );
    await screen.findByText(/Введите ссылку на аудио VK/);
    expect(screen.getByText("Сохранить черновик")).toBeDisabled();
    parse.mockResolvedValue({ owner_id: -123, audio_id: 456 });
    await user.clear(screen.getByLabelText("VK audio link"));
    await user.type(
      screen.getByLabelText("VK audio link"),
      "https://vk.ru/audio-123_456",
    );
    await screen.findByText("✓ Audio -123_456");
    const hours = screen.getByLabelText(
      "Проверять публикацию в течение (часов)",
    );
    await user.clear(hours);
    await user.type(hours, "0");
    expect(screen.getByText("Сохранить черновик")).toBeDisabled();
    await user.clear(hours);
    await user.type(hours, "96");
    await user.type(screen.getByLabelText(/Caption/), "  caption  ");
    await user.click(screen.getByText("Сохранить черновик"));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        name: "Test",
        grid_id: "g1",
        track_url: "https://vk.ru/audio-123_456",
        caption: "  caption  ",
        publication_check_hours: 96,
      }),
    );
  });
});

it("prepares only after explicit confirmation", async () => {
  const user = userEvent.setup();
  const prepare = vi.fn().mockResolvedValue(undefined);
  render(<PrepareButton count={184} onPrepare={prepare} />);
  await user.click(screen.getByText("Prepare campaign"));
  expect(prepare).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toHaveTextContent(
    "НЕ отправляет ничего в VK",
  );
  await user.click(screen.getByText("Продолжить"));
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1));
});

it("renders submission status, empty assignments and safe result links", () => {
  const item: Submission = {
    id: "s1",
    community: {
      id: "c1",
      domain: "example",
      name: null,
      category: "ALPHA",
      vk_group_id: null,
      is_active: true,
    },
    category: "ALPHA",
    account_name: null,
    media_label: null,
    status: "pending",
    attempt_count: 0,
    error_code: null,
    error_message: null,
    published_post_url: null,
  };
  render(
    <SubmissionTable
      items={[
        item,
        {
          ...item,
          id: "s2",
          status: "published",
          published_post_url: "https://vk.ru/wall-1_2",
        },
      ]}
    />,
  );
  expect(screen.getByText("PENDING")).toBeInTheDocument();
  expect(screen.getByText("PUBLISHED")).toBeInTheDocument();
  expect(screen.getByRole("link")).toHaveAttribute(
    "href",
    "https://vk.ru/wall-1_2",
  );
});

it("displays unconfigured credentials without backend dump", async () => {
  vi.spyOn(accountsApi, "list").mockResolvedValue([
    {
      id: "a1",
      name: "Test",
      vk_user_id: null,
      gender_tag: null,
      status: "active",
    },
  ]);
  vi.spyOn(accountsApi, "validate").mockRejectedValue(
    new ApiError(503, "Unavailable"),
  );
  render(
    <MemoryRouter>
      <AccountsPage />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByText("Validate"));
  await screen.findByText(
    "VK credentials are not configured for this environment.",
  );
});

it("filters submissions and resets pagination without reserving category names", async () => {
  const user = userEvent.setup();
  vi.spyOn(gridsApi, "detail").mockResolvedValue({
    ...detail,
    categories: [{ category: "__all", count: 26 }],
  });
  const list = vi
    .spyOn(campaignsApi, "submissions")
    .mockResolvedValue({ items: [], total: 26, page: 1, page_size: 25 });
  const campaign: Campaign = {
    id: "campaign",
    name: "Test",
    grid_id: "g1",
    grid_name: "Grid",
    community_count: 26,
    submission_count: 26,
    track_url: "https://vk.ru/audio1_2",
    track_owner_id: 1,
    track_audio_id: 2,
    caption: "",
    publication_check_hours: 72,
    status: "ready",
    created_at: "2026-10-07",
  };
  render(<SubmissionsPanel campaign={campaign} revision={0} />);
  await screen.findByRole("option", { name: "__all" });
  await waitFor(() => expect(screen.getByText("Далее →")).toBeEnabled());
  await user.click(screen.getByText("Далее →"));
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith(
      "campaign",
      2,
      "",
      null,
      expect.any(AbortSignal),
    ),
  );
  await user.selectOptions(screen.getByLabelText("Category"), "category:__all");
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith(
      "campaign",
      1,
      "",
      "__all",
      expect.any(AbortSignal),
    ),
  );
  await user.selectOptions(screen.getByLabelText("Status"), "failed");
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith(
      "campaign",
      1,
      "failed",
      "__all",
      expect.any(AbortSignal),
    ),
  );
});
