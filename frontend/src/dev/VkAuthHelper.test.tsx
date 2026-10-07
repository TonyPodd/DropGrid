import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VkAuthHelper from "./VkAuthHelper";

const { send, isEmbedded } = vi.hoisted(() => ({
  send: vi.fn(),
  isEmbedded: vi.fn(),
}));
vi.mock("@vkontakte/vk-bridge", () => ({ default: { send, isEmbedded } }));
const TOKEN = "synthetic-test-token-never-rendered";
let clipboard: ReturnType<typeof vi.fn>;
let storageWrite: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.stubEnv("VITE_VK_APP_ID", "54808974");
  send.mockReset();
  isEmbedded.mockReturnValue(true);
  send.mockImplementation((method: string) =>
    Promise.resolve(
      method === "VKWebAppInit"
        ? {}
        : { access_token: TOKEN, scope: "wall,photos,groups" },
    ),
  );
  clipboard = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal(
    "navigator",
    Object.create(navigator, {
      clipboard: { value: { writeText: clipboard }, configurable: true },
    }),
  );
  storageWrite = vi.spyOn(Storage.prototype, "setItem");
});
afterEach(() => {
  expect(storageWrite).not.toHaveBeenCalled();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
async function ready() {
  const rendered = render(<VkAuthHelper />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Получить тестовый VK token" }),
    ).toBeEnabled(),
  );
  return rendered;
}
async function receive() {
  const rendered = await ready();
  fireEvent.click(
    screen.getByRole("button", { name: "Получить тестовый VK token" }),
  );
  await screen.findByText("✓ Token received");
  return rendered;
}

describe("VK development helper", () => {
  it.each(["false", "", "TRUE"])(
    "hides the route when flag is %s",
    async (flag) => {
      vi.stubEnv("VITE_ENABLE_VK_AUTH_HELPER", flag);
      vi.resetModules();
      const { App } = await import("../App");
      render(
        <MemoryRouter initialEntries={["/dev/vk-auth"]}>
          <App />
        </MemoryRouter>,
      );
      expect(screen.getByText("Страница не найдена")).toBeInTheDocument();
      expect(
        screen.queryByText("VK test token helper"),
      ).not.toBeInTheDocument();
      expect(send).not.toHaveBeenCalled();
    },
  );
  it("provides the route only with explicit true", async () => {
    vi.stubEnv("VITE_ENABLE_VK_AUTH_HELPER", "true");
    vi.resetModules();
    const { App } = await import("../App");
    render(
      <MemoryRouter initialEntries={["/dev/vk-auth"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText("VK test token helper")).toBeInTheDocument();
    await waitFor(() => expect(send).toHaveBeenCalledWith("VKWebAppInit"));
    expect(send).not.toHaveBeenCalledWith(
      "VKWebAppGetAuthToken",
      expect.anything(),
    );
  });
  it("initializes, requests exact parameters, renders scopes without token, copies only on click, and clears", async () => {
    const { container } = await receive();
    expect(send.mock.calls).toEqual([
      ["VKWebAppInit"],
      [
        "VKWebAppGetAuthToken",
        { app_id: 54808974, scope: "wall,photos,groups" },
      ],
    ]);
    expect(container.innerHTML).not.toContain(TOKEN);
    expect(
      screen.getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual(["wall", "photos", "groups"]);
    expect(clipboard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Copy token" }));
    await screen.findByText("Token copied.");
    expect(clipboard).toHaveBeenCalledExactlyOnceWith(TOKEN);
    fireEvent.click(screen.getByRole("button", { name: "Clear token" }));
    expect(screen.queryByText("✓ Token received")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Copy token" }),
    ).not.toBeInTheDocument();
    expect(container.innerHTML).not.toContain(TOKEN);
    expect(clipboard).toHaveBeenCalledTimes(1);
  });
  it("shows only actually granted allowlisted scopes", async () => {
    send
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({
        access_token: TOKEN,
        scope: `photos,${TOKEN},messages`,
      });
    const { container } = await receive();
    expect(
      screen.getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual(["photos"]);
    expect(container.innerHTML).not.toContain(TOKEN);
  });
  it("loses credentials on remount", async () => {
    const { unmount } = await receive();
    unmount();
    await ready();
    expect(screen.queryByText("✓ Token received")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Copy token" }),
    ).not.toBeInTheDocument();
  });
  it("times out without retry and ignores a late response", async () => {
    let resolve!: (value: unknown) => void;
    send.mockResolvedValueOnce({}).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await ready();
    vi.useFakeTimers();
    fireEvent.click(
      screen.getByRole("button", { name: "Получить тестовый VK token" }),
    );
    await act(() => vi.advanceTimersByTimeAsync(25_000));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "VK authorization did not finish. Try again.",
    );
    expect(
      screen.queryByText("Waiting for VK authorization…"),
    ).not.toBeInTheDocument();
    await act(async () => resolve({ access_token: TOKEN, scope: "wall" }));
    expect(screen.queryByText("✓ Token received")).not.toBeInTheDocument();
    expect(send).toHaveBeenCalledTimes(2);
    expect(clipboard).not.toHaveBeenCalled();
  });
  it("clear invalidates a pending response", async () => {
    let resolve!: (value: unknown) => void;
    send.mockResolvedValueOnce({}).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await ready();
    fireEvent.click(
      screen.getByRole("button", { name: "Получить тестовый VK token" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear token" }));
    await act(async () => resolve({ access_token: TOKEN, scope: "wall" }));
    expect(screen.queryByText("✓ Token received")).not.toBeInTheDocument();
  });
  it.each([
    [
      {
        error_type: "client_error",
        error_data: { error_code: 4, error_reason: TOKEN },
      },
      "VK authorization was cancelled by the user.",
    ],
    [
      {
        error_type: "client_error",
        error_data: { error_code: 1, error_reason: TOKEN },
      },
      "Open this page through the VK Mini App to request a token.",
    ],
    [
      {
        error_type: "auth_error",
        error_data: { error_code: 5, error_reason: TOKEN },
      },
      "VK authorization was rejected.",
    ],
    [new Error(TOKEN), "VK authorization failed."],
  ])("sanitizes authorization failures", async (failure, message) => {
    send.mockResolvedValueOnce({}).mockRejectedValueOnce(failure);
    const { container } = await ready();
    fireEvent.click(
      screen.getByRole("button", { name: "Получить тестовый VK token" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(container.innerHTML).not.toContain(TOKEN);
  });
  it.each(["standalone", "init failed", "init timeout"])(
    "does not authorize when %s",
    async (mode) => {
      if (mode === "standalone") isEmbedded.mockReturnValue(false);
      if (mode === "init failed") send.mockRejectedValueOnce(new Error(TOKEN));
      if (mode === "init timeout") {
        vi.useFakeTimers();
        send.mockImplementationOnce(() => new Promise(() => {}));
      }
      const { container } = render(<VkAuthHelper />);
      if (mode === "init timeout")
        await act(() => vi.advanceTimersByTimeAsync(25_000));
      vi.useRealTimers();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Open this page through the VK Mini App to request a token.",
      );
      expect(
        screen.getByRole("button", { name: "Получить тестовый VK token" }),
      ).toBeDisabled();
      expect(send.mock.calls).toEqual([["VKWebAppInit"]]);
      expect(container.innerHTML).not.toContain(TOKEN);
    },
  );
  it("sanitizes clipboard failure", async () => {
    clipboard.mockRejectedValueOnce(new Error(TOKEN));
    const { container } = await receive();
    fireEvent.click(screen.getByRole("button", { name: "Copy token" }));
    await screen.findByText(
      "Could not copy token. Allow clipboard access and try again.",
    );
    expect(container.innerHTML).not.toContain(TOKEN);
  });
  it("rejects invalid app configuration", async () => {
    vi.stubEnv("VITE_VK_APP_ID", "");
    render(<VkAuthHelper />);
    await screen.findByText(
      "Set a valid VITE_VK_APP_ID before starting the frontend.",
    );
    expect(
      screen.getByRole("button", { name: "Получить тестовый VK token" }),
    ).toBeDisabled();
    expect(send.mock.calls).toEqual([["VKWebAppInit"]]);
  });
});
