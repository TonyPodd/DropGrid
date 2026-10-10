import { useState } from "react";
import { accountsApi } from "./api/accounts";
import { ApiError, errorMessage } from "./api/client";

export function AccountTokenImport({
  accountId,
  onSaved,
}: {
  accountId?: string;
  onSaved: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    setError("");
    try {
      const result = await (accountId
        ? accountsApi.importToken(accountId, token)
        : accountsApi.connect(token));
      setToken("");
      setOpen(false);
      onSaved(
        `VK account connected · ${result.vk_user_id} · ${result.name}${"status" in result ? ` · ${result.status} · campaign quota ${result.campaign_send_quota ?? 100}` : ""}`,
      );
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 503
          ? "Хранилище токенов недоступно. Проверьте локальный APP_SECRET_KEY и подключение к backend."
          : err instanceof ApiError && err.status === 401
            ? "VK отклонил токен. Проверьте его и попробуйте снова."
            : errorMessage(err),
      );
    } finally {
      setToken("");
      setBusy(false);
    }
  }
  return (
    <>
      <button
        className={accountId ? undefined : "primary"}
        onClick={() => {
          setError("");
          setOpen(true);
        }}
      >
        {accountId ? "Добавить/заменить VK token" : "+ Подключить VK аккаунт"}
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby={`token-title-${accountId}`}
          className="token-dialog"
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <h2 id={`token-title-${accountId}`}>Подключить VK account</h2>
            <p>
              Токен вы получаете самостоятельно. В DropGrid он хранится в
              зашифрованном виде.
            </p>
            <label>
              VK user access token
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={token}
                onChange={(event) => setToken(event.target.value)}
                disabled={busy}
                required
                maxLength={8192}
              />
            </label>
            {error && <p role="alert">{error}</p>}
            <button type="submit" disabled={busy || !token.trim()}>
              {busy ? "Проверка…" : "Validate / Save"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setToken("");
                setError("");
                setOpen(false);
              }}
            >
              Отмена
            </button>
          </form>
        </div>
      )}
    </>
  );
}
