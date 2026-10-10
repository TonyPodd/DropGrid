import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { PhotoValidationPage } from "./validation";
afterEach(() => vi.restoreAllMocks());
function fixture() {
  let position = 0,
    mode = "pending";
  const states = ["pending", "pending", "pending"];
  const ranks = [1, 1, 1];
  const ratings: (string | null)[][] = states.map(() => [null, null, null]);
  const summary = () => ({
    id: "b",
    name: "Validation",
    state: "open",
    target_count: 3,
    reviewer_id: "t",
    current_position: position,
    current_filter: mode,
    done: states.filter((s) => s !== "pending").length,
    confirmed: states.filter((s) => s === "confirmed").length,
    skipped: states.filter((s) => s === "skipped").length,
    remaining: states.filter((s) => s === "pending").length,
    kept: 1,
    replaced: 0,
    selected_sources: {},
    items: states.map((state, position) => ({
      position,
      state,
      attention: position === 1,
    })),
  });
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const u = String(input);
      let data: unknown;
      if (u.endsWith("photo-reviewers"))
        data = [{ id: "t", display_name: "Tima" }];
      else if (u.endsWith("/cursor")) {
        const d = JSON.parse(String(init?.body));
        position = d.position;
        mode = d.mode;
        data = { saved: true };
      } else if (u.endsWith("/action")) {
        const d = JSON.parse(String(init?.body));
        const p = Number(u.match(/items\/(\d+)/)?.[1]);
        ranks[p] = d.rank;
        let nextRank: number | null = null;
        if (d.action === "dislike") {
          ratings[p][d.rank - 1] = "dislike";
          const available = [1, 2, 3].filter(
            (rank) => ratings[p][rank - 1] !== "dislike",
          );
          nextRank =
            available.find((rank) => rank > d.rank) ?? available[0] ?? null;
          if (nextRank !== null) ranks[p] = nextRank;
        }
        if (d.action === "confirm" || d.action === "skip") {
          states[p] = d.action === "confirm" ? "confirmed" : "skipped";
          position = states.findIndex((s) => s === "pending");
          if (position < 0) position = p;
          mode = "pending";
        }
        data = {
          ...summary(),
          ...(d.action === "dislike" ? { next_rank: nextRank } : {}),
        };
      } else if (/items\/\d+$/.test(u)) {
        const p = Number(u.match(/items\/(\d+)/)?.[1]);
        data = {
          position: p,
          selection_id: "sel" + p,
          state: states[p],
          automatic_rank: 1,
          active_rank: ranks[p],
          community: "Community " + p,
          category: "Cars",
          intent: "Honda Accord",
          attention: [],
          neighbors: ["/api/v1/media-assets/next/content"],
          candidates: [1, 2, 3].map((rank) => ({
            rank,
            provider: rank === 2 ? "pinterest" : "vk_category_archive",
            image: `/api/v1/media-assets/${p}-${rank}/content`,
            rating: ratings[p][rank - 1],
            diagnostics: { final_score: 0.9 },
          })),
        };
      } else data = summary();
      return new Response(JSON.stringify(data), { status: 200 });
    });
  return fetch;
}
function mount() {
  return render(
    <MemoryRouter initialEntries={["/review/b"]}>
      <Routes>
        <Route path="/review/:batchId" element={<PhotoValidationPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
it("saves dislike, shows the next alternative in the same community, and restores both on reload", async () => {
  const fetch = fixture();
  const view = mount();
  await screen.findByRole("heading", { name: "Community 0" });
  await userEvent.click(screen.getByRole("button", { name: "Не нравится" }));
  await waitFor(() =>
    expect(
      screen.getByRole("img", { name: "Выбрано: Community 0" }),
    ).toHaveAttribute("src", expect.stringContaining("/0-2/content")),
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Отметка сохранена. Показан следующий вариант",
  );
  expect(screen.getByText("Отклонено вариантов: 1")).toBeInTheDocument();
  expect(screen.getByText("0 / 3")).toBeInTheDocument();
  const actions = fetch.mock.calls.filter(([u]) =>
    String(u).endsWith("/action"),
  );
  expect(actions).toHaveLength(1);
  expect(JSON.parse(String(actions[0][1]?.body))).toMatchObject({
    action: "dislike",
    rank: 1,
    shown_ranks: [1],
  });
  view.unmount();
  mount();
  await screen.findByRole("heading", { name: "Community 0" });
  expect(
    screen.getByRole("img", { name: "Выбрано: Community 0" }),
  ).toHaveAttribute("src", expect.stringContaining("/0-2/content"));
  await userEvent.click(
    screen.getByRole("button", { name: "Альтернативы" }),
  );
  expect(screen.getByRole("button", { name: /1\..*👎/ })).toHaveClass(
    "is-disliked",
  );
  expect(screen.getByRole("button", { name: /2\. Pinterest/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

it("keyboard dislike never wraps to a rejected photo and reports exhausted alternatives without confirming", async () => {
  const fetch = fixture();
  mount();
  await screen.findByRole("heading", { name: "Community 0" });
  for (const rank of [2, 3]) {
    await userEvent.keyboard("d");
    await waitFor(() =>
      expect(
        screen.getByRole("img", { name: "Выбрано: Community 0" }),
      ).toHaveAttribute("src", expect.stringContaining(`/0-${rank}/content`)),
    );
  }
  await userEvent.keyboard("d");
  await screen.findByText(/Все доступные варианты отклонены/);
  expect(screen.getByText("Отклонено вариантов: 3")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Не нравится" })).toBeDisabled();
  const before = fetch.mock.calls.filter(([u]) =>
    String(u).endsWith("/action"),
  ).length;
  await userEvent.keyboard("d");
  expect(
    fetch.mock.calls.filter(([u]) => String(u).endsWith("/action")),
  ).toHaveLength(before);
  expect(before).toBe(3);
  expect(
    screen.getByRole("heading", { name: "Community 0" }),
  ).toBeInTheDocument();
  expect(screen.getByText("0 / 3")).toBeInTheDocument();
});

it("a failed dislike request leaves the photo unchanged and does not claim the mark was saved", async () => {
  const fetch = fixture();
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    if (
      String(input).endsWith("/action") &&
      JSON.parse(String(init?.body)).action === "dislike"
    )
      return new Response(JSON.stringify({ detail: "Could not save rating" }), {
        status: 503,
      });
    return original(input, init);
  });
  mount();
  await screen.findByRole("heading", { name: "Community 0" });
  await userEvent.click(screen.getByRole("button", { name: "Не нравится" }));
  await screen.findByRole("alert");
  expect(
    screen.getByRole("img", { name: "Выбрано: Community 0" }),
  ).toHaveAttribute("src", expect.stringContaining("/0-1/content"));
  expect(screen.queryByText(/Отметка сохранена/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Отклонено вариантов/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Не нравится" })).toBeEnabled();
});

it("keeps system selection in one click, records only shown ranks, advances and resumes", async () => {
  const fetch = fixture();
  const view = mount();
  await screen.findByRole("heading", { name: "Community 0" });
  expect(screen.queryByText("final_score")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Оставить" }));
  await screen.findByRole("heading", { name: "Community 1" });
  const call = fetch.mock.calls.find(([u]) => String(u).endsWith("/action"));
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    action: "confirm",
    rank: 1,
    shown_ranks: [1],
    reviewer_id: "t",
  });
  view.unmount();
  mount();
  await screen.findByRole("heading", { name: "Community 1" });
  expect(screen.getByText("1 / 3")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([u]) => String(u).includes("start"))).toBe(
    false,
  );
});
it("balances alternatives, saves choice without label, Enter confirms and Left goes back", async () => {
  const fetch = fixture();
  mount();
  await screen.findByRole("heading", { name: "Community 0" });
  await userEvent.click(screen.getByRole("button", { name: "Альтернативы" }));
  expect(screen.getAllByRole("img", { name: /Вариант/ })).toHaveLength(3);
  await userEvent.keyboard("2");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Выбрать" })).toBeInTheDocument(),
  );
  expect(
    JSON.parse(
      String(
        fetch.mock.calls.find(([u]) => String(u).endsWith("/action"))?.[1]
          ?.body,
      ),
    ).action,
  ).toBe("select");
  await userEvent.keyboard("{Enter}");
  await screen.findByRole("heading", { name: "Community 1" });
  const confirms = fetch.mock.calls.filter(
    ([u, init]) =>
      String(u).endsWith("/action") &&
      JSON.parse(String(init?.body)).action === "confirm",
  );
  expect(JSON.parse(String(confirms[0][1]?.body))).toMatchObject({
    rank: 2,
    shown_ranks: [1, 2, 3],
  });
  await userEvent.keyboard("{ArrowLeft}");
  await screen.findByRole("heading", { name: "Community 0" });
});
it("skip and filter persist, shortcuts ignore input fields", async () => {
  const fetch = fixture();
  mount();
  await screen.findByRole("heading", { name: "Community 0" });
  await userEvent.keyboard("s");
  await screen.findByRole("heading", { name: "Community 1" });
  await userEvent.click(screen.getByRole("button", { name: "Пропущенные" }));
  await screen.findByRole("heading", { name: "Community 0" });
  const input = document.createElement("input");
  document.body.appendChild(input);
  input.focus();
  const count = fetch.mock.calls.length;
  fireEvent.keyDown(input, { key: "Enter" });
  expect(fetch.mock.calls.length).toBe(count);
  input.remove();
  expect(
    fetch.mock.calls.some(([, init]) =>
      String(init?.body).includes('"mode":"skipped"'),
    ),
  ).toBe(true);
});

it("queues a fast Enter until the selected alternative is saved", async () => {
  const fetch = fixture();
  const implementation = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    const result = await implementation(input, init);
    if (
      String(input).endsWith("/action") &&
      JSON.parse(String(init?.body)).action === "select"
    )
      await new Promise((resolve) => setTimeout(resolve, 80));
    return result;
  });
  mount();
  await screen.findByRole("heading", { name: "Community 0" });
  await userEvent.click(screen.getByRole("button", { name: "Альтернативы" }));
  await userEvent.click(screen.getByRole("button", { name: /2\. Pinterest/ }));
  await userEvent.keyboard("{Enter}");
  await screen.findByRole("heading", { name: "Community 1" });
  const confirms = fetch.mock.calls.filter(
    ([u, init]) =>
      String(u).endsWith("/action") &&
      JSON.parse(String(init?.body)).action === "confirm",
  );
  expect(confirms).toHaveLength(1);
  expect(JSON.parse(String(confirms[0][1]?.body)).rank).toBe(2);
});

it("uses authenticated reviewer identity without a second selector", async () => {
  const fetch = fixture();
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    const response = await original(input, init);
    const data = await response.json();
    if (data.id === "b") {
      data.trusted_reviewer = { id: "t", display_name: "Tima" };
      data.owner = true;
    }
    return new Response(JSON.stringify(data), { status: 200 });
  });
  render(
    <MemoryRouter initialEntries={["/review/b"]}>
      <Routes>
        <Route path="/review/:batchId" element={<PhotoValidationPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText("Tima");
  expect(
    screen.queryByRole("combobox", { name: "Reviewer" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Проверка фото" }),
  ).toBeInTheDocument();
});
