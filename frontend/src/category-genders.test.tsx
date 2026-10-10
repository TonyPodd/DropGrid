import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CategoryGendersPage } from "./category-genders";
import { plural } from "./shared";
import { accountsApi } from "./api/accounts";
import { gridsApi } from "./api/grids";
import type { GridCategoryGenders } from "./api/types";

afterEach(() => vi.restoreAllMocks());

const distribution: GridCategoryGenders = {
  grid_id: "g1",
  complete: false,
  updated_at: null,
  categories: [
    { category: "АВТО", count: 12, gender: null, suggested_gender: null },
    { category: "КРАСОТА", count: 7, gender: null, suggested_gender: "female" },
    { category: "ЮМОР", count: 30, gender: null, suggested_gender: null },
    { category: null, count: 2, gender: null, suggested_gender: null },
  ],
};

function mock(data = distribution) {
  vi.spyOn(gridsApi, "latest").mockResolvedValue([
    {
      id: "g1",
      name: "Октябрь",
      created_at: "2026-10-10",
      community_count: 51,
      category_count: 4,
    },
  ]);
  vi.spyOn(accountsApi, "all").mockResolvedValue([
    {
      id: "a1",
      name: "Иван",
      vk_user_id: 1,
      gender_tag: "male",
      status: "active",
      token_configured: true,
    },
  ]);
  vi.spyOn(gridsApi, "categoryGenders").mockResolvedValue(data);
  return vi
    .spyOn(gridsApi, "saveCategoryGenders")
    .mockImplementation(async (_id, items) => ({
      ...data,
      complete: true,
      updated_at: "2026-10-11T10:00:00Z",
      categories: data.categories.map((row) => ({
        ...row,
        gender: items.find((item) => item.category === row.category)!.gender,
      })),
    }));
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/categories"]}>
      <CategoryGendersPage />
    </MemoryRouter>,
  );
}

it("detects grid categories, places them into three columns and saves only on click", async () => {
  const save = mock();
  renderPage();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /АВТО/ }));
  await user.click(
    screen.getByRole("button", { name: "Добавить выбранные в «Мужские»" }),
  );
  const male = screen.getByTestId("column-male");
  expect(within(male).getByText("АВТО")).toBeInTheDocument();
  // A matching category from an earlier grid is suggested but not saved.
  expect(within(screen.getByTestId("column-female")).getByText("КРАСОТА"));
  expect(screen.getByText(/предзаполнена по прошлым сеткам/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /ЮМОР/ }));
  await user.click(screen.getByRole("button", { name: /Без категории/ }));
  await user.click(
    screen.getByRole("button", { name: "Добавить выбранные в «Унисекс»" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Перенести ЮМОР в «Мужские»" }),
  );
  expect(within(male).getByText("ЮМОР")).toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Сохранить распределение" }),
  );
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith("g1", [
      { category: "АВТО", gender: "male" },
      { category: "КРАСОТА", gender: "female" },
      { category: "ЮМОР", gender: "male" },
      { category: null, gender: "unisex" },
    ]),
  );
  expect(await screen.findByText(/Распределение сохранено/)).toBeInTheDocument();
  expect(screen.getByText(/мужских — 1, женских — 0/)).toBeInTheDocument();
});

it("asks which column to use for categories left unplaced", async () => {
  const save = mock();
  renderPage();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Сохранить распределение" }),
  );
  const dialog = screen.getByRole("dialog", {
    name: "Нераспределённые категории",
  });
  const confirm = within(dialog).getByRole("button", { name: "Сохранить" });
  expect(confirm).toBeDisabled();
  for (const [name, column] of [
    ["АВТО", "Мужские"],
    ["ЮМОР", "Унисекс"],
    ["Без категории", "Унисекс"],
  ]) {
    await user.click(
      within(within(dialog).getByRole("group", { name })).getByRole("button", {
        name: column,
      }),
    );
  }
  expect(save).not.toHaveBeenCalled();
  await user.click(confirm);
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith("g1", [
      { category: "АВТО", gender: "male" },
      { category: "КРАСОТА", gender: "female" },
      { category: "ЮМОР", gender: "unisex" },
      { category: null, gender: "unisex" },
    ]),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(screen.getByText("Все категории распределены.")).toBeInTheDocument();
});

it("reports saved distributions and new categories after reimport", async () => {
  mock({
    ...distribution,
    categories: [
      { category: "АВТО", count: 12, gender: "male", suggested_gender: null },
      { category: "НОВАЯ", count: 3, gender: null, suggested_gender: null },
    ],
  });
  renderPage();
  expect(
    await screen.findByText(/появились новые категории/),
  ).toBeInTheDocument();
  expect(
    within(screen.getByTestId("column-male")).getByText("АВТО"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /НОВАЯ/ })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

it("uses Russian plural forms for counts", () => {
  expect(
    [1, 2, 4, 5, 11, 12, 21, 22, 25, 111].map((n) =>
      plural(n, "категория", "категории", "категорий"),
    ),
  ).toEqual([
    "категория",
    "категории",
    "категории",
    "категорий",
    "категорий",
    "категорий",
    "категория",
    "категории",
    "категорий",
    "категорий",
  ]);
});
