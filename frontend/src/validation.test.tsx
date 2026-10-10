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
        if (d.action === "confirm" || d.action === "skip") {
          states[p] = d.action === "confirm" ? "confirmed" : "skipped";
          position = states.findIndex((s) => s === "pending");
          if (position < 0) position = p;
          mode = "pending";
        }
        data = summary();
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
            rating: null,
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
