import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CampaignWorkflow } from "./campaign-workflow";
import { ApiError } from "./api/client";
import { personaApi, type PersonaPhoto } from "./api/persona";
import { PersonaPhotos } from "./persona";
import type { Campaign } from "./api/types";

afterEach(() => vi.restoreAllMocks());

const campaign = {
  id: "c",
  name: "Знакомства",
  status: "draft",
  photo_review_mode: "AUTO",
} as Campaign;

const photo = (kind: "selfie" | "other", id: string): PersonaPhoto => ({
  kind,
  media_asset_id: id,
  tags: kind === "selfie" ? ["селфи"] : [],
  width: 1080,
  height: 1350,
  created_at: "2026-10-14",
});

function mockApi() {
  const accounts = [
    {
      id: "a",
      name: "Аня",
      status: "active",
      vk_user_id: 1,
      token_configured: true,
      gender_tag: "female",
    },
  ];
  const photos: Record<string, PersonaPhoto[]> = {
    a: [photo("selfie", "a-selfie-0")],
    b: [],
  };
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      let data: unknown = {};
      if (url.endsWith("/accounts?limit=25&offset=0")) data = accounts;
      else if (url.endsWith("/accounts/connect")) {
        accounts.push({
          id: "b",
          name: "Оля",
          status: "active",
          vk_user_id: 2,
          token_configured: true,
          gender_tag: null as unknown as string,
        });
        data = accounts[1];
      } else if (url.endsWith("/accounts/b") && init?.method === "PATCH") {
        accounts[1].gender_tag = "female";
        data = accounts[1];
      } else if (url.endsWith("/campaigns/c/persona"))
        data = {
          categories: [{ category: "ЗНАКОМСТВА", count: 150 }],
          targets: 150,
          default_quota: 100,
          accounts: [],
        };
      else if (url.includes("/persona-photos")) {
        const account = url.includes("/accounts/a/") ? "a" : "b";
        const kind = url.includes("kind=other") ? "other" : "selfie";
        if (init?.method === "POST") {
          photos[account].push(
            photo(kind, `${account}-${kind}-${photos[account].length}`),
          );
          data = photos[account].at(-1);
        } else data = photos[account];
      } else if (url.includes("account-pool")) data = [];
      else if (url.includes("prepare-workflow")) data = { state: "queued" };
      return new Response(JSON.stringify(data), { status: 200 });
    });
}

it("counts the girl accounts needed, asks for their tokens and two photos each", async () => {
  const fetch = mockApi();
  render(<CampaignWorkflow campaign={campaign} onChanged={vi.fn()} />);
  const user = userEvent.setup();
  const block = (
    await screen.findByRole("heading", {
      name: "Знакомства и селфи — фото девочки",
    })
  ).closest("section")!;
  const prepare = screen.getByRole("button", { name: "Подготовить кампанию" });
  expect(within(block).getByText("2")).toBeInTheDocument();
  expect(within(block).getByText(/ЗНАКОМСТВА \(150\)/)).toBeInTheDocument();
  expect(within(block).getByRole("alert")).toHaveTextContent(
    "Не хватает аккаунтов-девочек",
  );
  expect(prepare).toBeDisabled();

  // Slot 1: an already connected account; she has only a selfie so far.
  const slotOf = (n: number) =>
    within(block).getByRole("group", { name: `Аккаунт-девочка ${n}` });
  await user.selectOptions(
    within(slotOf(1)).getByLabelText("Аккаунт-девочка 1: подключённый аккаунт"),
    "a",
  );
  await within(block).findByText(/селфи: 1 · не селфи: 0/);
  const anya = slotOf(1);
  expect(within(anya).getByText(/нужно хотя бы одно/)).toBeInTheDocument();
  await user.type(
    within(anya).getByLabelText("Новые не селфи: что на фото"),
    "в зеркале",
  );
  await user.upload(within(anya).getByLabelText("Добавить не селфи для Аня"), [
    new File(["jpeg"], "mirror.jpg", { type: "image/jpeg" }),
    new File(["jpeg"], "full.jpg", { type: "image/jpeg" }),
  ]);
  await within(block).findByText(/селфи: 1 · не селфи: 2/);
  expect(prepare).toBeDisabled();

  // Slot 2: a new account connected by token right here.
  const second = slotOf(2);
  const token = within(second).getByLabelText("Аккаунт-девочка 2: токен VK");
  expect(token).toHaveAttribute("type", "password");
  await user.type(token, "vk1.secret-token");
  await user.click(within(second).getByRole("button", { name: "Подключить" }));
  const olya = await within(block).findByRole("heading", {
    name: "Аккаунт-девочка 2: Оля",
  });
  const slot = olya.closest("[role=group]") as HTMLElement;
  for (const label of ["Добавить селфи для Оля", "Добавить не селфи для Оля"])
    await user.upload(
      within(slot).getByLabelText(label),
      new File(["jpeg"], "photo.jpg", { type: "image/jpeg" }),
    );
  await waitFor(() => expect(prepare).toBeEnabled());

  const calls = fetch.mock.calls.map(
    ([url, init]) => [String(url), init] as const,
  );
  const connect = calls.find(([url]) => url.endsWith("/accounts/connect"))!;
  expect(JSON.parse(String(connect[1]?.body))).toEqual({
    access_token: "vk1.secret-token",
  });
  // The token is never kept on screen after connecting.
  expect(screen.queryByDisplayValue("vk1.secret-token")).toBeNull();
  const female = calls.find(
    ([url, init]) => url.endsWith("/accounts/b") && init?.method === "PATCH",
  )!;
  expect(JSON.parse(String(female[1]?.body))).toEqual({ gender_tag: "female" });
  const mirrors = calls.filter(
    ([url, init]) =>
      url.includes("/accounts/a/persona-photos?kind=other") &&
      init?.method === "POST",
  );
  expect(mirrors).toHaveLength(2);
  expect(mirrors[0][0]).toContain(
    "tags=%D0%B2+%D0%B7%D0%B5%D1%80%D0%BA%D0%B0%D0%BB%D0%B5",
  );

  await user.click(prepare);
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([url]) => String(url).endsWith("prepare-workflow")),
    ).toBe(true),
  );
  const body = fetch.mock.calls.find(([url]) =>
    String(url).endsWith("prepare-workflow"),
  )?.[1]?.body;
  expect(JSON.parse(String(body))).toEqual({
    account_ids: ["a", "b"],
    persona_account_ids: ["a", "b"],
    auto_send: false,
  });
});

it("retags, moves, removes and explains rejected girl photos", async () => {
  vi.spyOn(personaApi, "photos").mockResolvedValue([
    photo("selfie", "m1"),
    photo("other", "m2"),
  ]);
  const patch = vi
    .spyOn(personaApi, "patch")
    .mockResolvedValue(photo("selfie", "m1"));
  const remove = vi.spyOn(personaApi, "remove").mockResolvedValue(undefined);
  vi.spyOn(personaApi, "add").mockRejectedValue(
    new ApiError(409, "Это фото уже привязано к другому аккаунту."),
  );
  const onCount = vi.fn();
  render(<PersonaPhotos accountId="a" name="Аня" onCount={onCount} />);
  const user = userEvent.setup();
  const selfies = await screen.findByRole("region", { name: "Селфи: Аня" });
  await user.type(within(selfies).getByLabelText("Селфи: что на фото"), "лицо");
  await user.tab();
  expect(patch).toHaveBeenCalledWith("a", "m1", { tags: ["лицо"] });
  await user.click(
    within(await screen.findByRole("region", { name: "Селфи: Аня" })).getByRole(
      "button",
      { name: "В «Не селфи»" },
    ),
  );
  expect(patch).toHaveBeenCalledWith("a", "m1", { kind: "other" });
  await user.upload(
    await screen.findByLabelText("Добавить не селфи для Аня"),
    new File(["jpeg"], "other.jpg", { type: "image/jpeg" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "уже привязано к другому аккаунту",
  );
  await user.click(
    within(await screen.findByRole("region", { name: "Не селфи: Аня" })).getByRole(
      "button",
      { name: "Удалить" },
    ),
  );
  expect(remove).toHaveBeenCalledWith("a", "m2");
  expect(onCount).toHaveBeenCalledWith({ selfie: 1, other: 1 });
});
