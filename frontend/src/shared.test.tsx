import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { State, useLoad } from "./shared";

function Counter({ loader }: { loader: () => Promise<number> }) {
  const state = useLoad(() => loader(), []);
  return (
    <State {...state} retry={state.reload}>
      <p>Значение {state.data}</p>
      <button onClick={state.reload}>Обновить</button>
    </State>
  );
}

it("keeps the content on screen while it refreshes", async () => {
  let resolve: (value: number) => void = () => {};
  let calls = 0;
  const loader = () =>
    ++calls === 1
      ? Promise.resolve(1)
      : new Promise<number>((r) => {
          resolve = r;
        });
  render(<Counter loader={loader} />);
  expect(await screen.findByText("Значение 1")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
  // The refresh is in flight: no blank «Загрузка…», the old value stays.
  expect(screen.queryByText("Загрузка…")).toBeNull();
  expect(screen.getByText("Значение 1")).toBeInTheDocument();
  await act(async () => resolve(2));
  expect(await screen.findByText("Значение 2")).toBeInTheDocument();
});

it("shows a failed refresh without wiping the content", async () => {
  let calls = 0;
  const loader = () =>
    ++calls === 1 ? Promise.resolve(1) : Promise.reject(new Error("сеть"));
  render(<Counter loader={loader} />);
  expect(await screen.findByText("Значение 1")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось обновить данные: сеть",
  );
  expect(screen.getByText("Значение 1")).toBeInTheDocument();
});
