import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AccountTokenImport } from "./account-token";
import { AccountsPage } from "./catalog";
import { accountsApi } from "./api/accounts";
import { ApiError } from "./api/client";

afterEach(() => vi.restoreAllMocks());
const token = "fake-ui-token-sentinel";
it("shows password input, saves explicitly, clears token and never redisplays saved credentials", async () => {
  const save = vi.spyOn(accountsApi, "importToken").mockResolvedValue({
    account_id: "a",
    vk_user_id: 123,
    name: "VK User",
    valid: true,
  });
  const saved = vi.fn();
  const user = userEvent.setup();
  render(<AccountTokenImport accountId="a" onSaved={saved} />);
  expect(save).not.toHaveBeenCalled();
  await user.click(screen.getByText("Добавить/заменить VK token"));
  const input = screen.getByLabelText("VK user access token");
  expect(input).toHaveAttribute("type", "password");
  await user.type(input, token);
  await user.click(screen.getByText("Validate / Save"));
  await waitFor(() =>
    expect(saved).toHaveBeenCalledWith("VK account connected · 123 · VK User"),
  );
  expect(save).toHaveBeenCalledWith("a", token);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain(token);
  await user.click(screen.getByText("Добавить/заменить VK token"));
  expect(screen.getByLabelText("VK user access token")).toHaveValue("");
});
it("clears rejected input, shows safe error and allows retry", async () => {
  vi.spyOn(accountsApi, "importToken").mockRejectedValue(
    new ApiError(401, "Authorization failed"),
  );
  const saved = vi.fn();
  const user = userEvent.setup();
  render(<AccountTokenImport accountId="a" onSaved={saved} />);
  await user.click(screen.getByText("Добавить/заменить VK token"));
  await user.type(screen.getByLabelText("VK user access token"), token);
  await user.click(screen.getByText("Validate / Save"));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "VK отклонил токен",
  );
  expect(screen.getByLabelText("VK user access token")).toHaveValue("");
  expect(saved).not.toHaveBeenCalled();
});
it("disables submit while saving and clears cancelled input", async () => {
  let resolve!: (value: {
    account_id: string;
    vk_user_id: number;
    name: string;
    valid: boolean;
  }) => void;
  vi.spyOn(accountsApi, "importToken").mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const user = userEvent.setup();
  render(<AccountTokenImport accountId="a" onSaved={() => {}} />);
  await user.click(screen.getByText("Добавить/заменить VK token"));
  await user.type(screen.getByLabelText("VK user access token"), token);
  await user.click(screen.getByText("Отмена"));
  await user.click(screen.getByText("Добавить/заменить VK token"));
  expect(screen.getByLabelText("VK user access token")).toHaveValue("");
  await user.type(screen.getByLabelText("VK user access token"), token);
  await user.click(screen.getByText("Validate / Save"));
  expect(screen.getByRole("button", { name: "Проверка…" })).toBeDisabled();
  resolve({ account_id: "a", vk_user_id: 123, name: "User", valid: true });
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
it("shows configured indicator and connected account metadata", async () => {
  vi.spyOn(accountsApi, "list").mockResolvedValue([
    {
      id: "a",
      name: "VK User",
      vk_user_id: 123,
      status: "active",
      gender_tag: null,
      token_configured: true,
    },
  ]);
  render(
    <MemoryRouter>
      <AccountsPage />
    </MemoryRouter>,
  );
  expect(await screen.findByText("yes")).toBeInTheDocument();
  expect(screen.getByText("123")).toBeInTheDocument();
  expect(document.body.textContent).not.toContain(token);
});

it("creates an account directly from an externally pasted token", async () => {
  const connect = vi
    .spyOn(accountsApi, "connect")
    .mockResolvedValue({
      id: "new",
      name: "User",
      vk_user_id: 123,
      status: "active",
      gender_tag: null,
      token_configured: true,
    });
  const saved = vi.fn();
  render(<AccountTokenImport onSaved={saved} />);
  await userEvent.click(
    screen.getByRole("button", { name: "+ Подключить VK аккаунт" }),
  );
  await userEvent.type(
    screen.getByLabelText("VK user access token"),
    "fresh-external-token",
  );
  expect(connect).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Validate / Save" }),
  );
  await waitFor(() =>
    expect(connect).toHaveBeenCalledExactlyOnceWith("fresh-external-token"),
  );
  expect(saved).toHaveBeenCalledWith(expect.stringContaining("123"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
