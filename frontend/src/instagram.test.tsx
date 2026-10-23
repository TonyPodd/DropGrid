import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InstagramPage } from "./instagram";

afterEach(() => vi.restoreAllMocks());

function mock(configured = true) {
  let profiles = [
    {
      id: "p1",
      username: "cars.daily",
      url: "https://www.instagram.com/cars.daily/",
      enabled: true,
      status: "ready",
      error_code: null,
      photo_count: 42,
      last_synced_at: "2026-10-23T10:00:00Z",
      added_by: "tima",
    },
    {
      id: "p2",
      username: "my.personal",
      url: "https://www.instagram.com/my.personal/",
      enabled: true,
      status: "error",
      error_code: "instagram_profile_unavailable",
      photo_count: 0,
      last_synced_at: "2026-10-23T10:00:00Z",
      added_by: "tony",
    },
  ];
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/instagram-profiles")) {
        profiles = [
          { ...profiles[0], id: "p3", username: "cats.daily", status: "pending" },
          ...profiles,
        ];
        return new Response(
          JSON.stringify({
            added: ["cats.daily"],
            existing: ["cars.daily"],
            invalid: ["bad!name"],
          }),
        );
      }
      if (init?.method === "DELETE") {
        profiles = profiles.filter((p) => !url.endsWith(p.id));
        return new Response(null, { status: 204 });
      }
      return new Response(
        JSON.stringify({ configured, visual: true, profiles }),
      );
    });
}

it("lists profiles with who added them and why one cannot be read", async () => {
  mock();
  render(<InstagramPage />);
  expect(await screen.findByText("@cars.daily")).toHaveAttribute(
    "href",
    "https://www.instagram.com/cars.daily/",
  );
  expect(screen.getByText("42")).toBeInTheDocument();
  expect(screen.getByText("Тима")).toBeInTheDocument();
  expect(screen.getByText("Профиль не найден")).toBeInTheDocument();
  expect(screen.queryByText(/Чтение Instagram выключено/)).not.toBeInTheDocument();
});

it("adds links and reports duplicates and unrecognized ones", async () => {
  const fetch = mock();
  render(<InstagramPage />);
  await screen.findByText("@cars.daily");
  await userEvent.type(
    screen.getByLabelText("Ссылки на профили"),
    "https://www.instagram.com/cats.daily/",
  );
  await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
  expect(
    await screen.findByText(
      "Добавлено профилей: 1 · уже были: cars.daily · не распознаны: bad!name",
    ),
  ).toBeInTheDocument();
  expect(await screen.findByText("@cats.daily")).toBeInTheDocument();
  const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({
    links: "https://www.instagram.com/cats.daily/",
  });
});

it("removes a profile after confirmation", async () => {
  mock();
  render(<InstagramPage />);
  await screen.findByText("@cars.daily");
  await userEvent.click(screen.getAllByRole("button", { name: "Удалить" })[0]);
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  await waitFor(() =>
    expect(screen.queryByText("@cars.daily")).not.toBeInTheDocument(),
  );
});

it("explains that reading Instagram is switched off", async () => {
  mock(false);
  render(<InstagramPage />);
  expect(
    await screen.findByText(/Чтение Instagram выключено/),
  ).toBeInTheDocument();
});
