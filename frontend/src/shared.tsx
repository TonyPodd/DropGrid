import { useEffect, useState, type ReactNode } from "react";
import { errorMessage } from "./api/client";

export function useLoad<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
) {
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    // Keep showing the previous data while refreshing: periodic refreshes and
    // reloads after an action must not blank the page, reset scroll or close panels.
    setLoading(true);
    setError("");
    loader(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // Loaders are defined by the caller's explicit dependency list.
  }, [...deps, revision]);
  return { data, loading, error, reload: () => setRevision((n) => n + 1) };
}
export function State({
  loading,
  error,
  retry,
  data,
  children,
}: {
  loading: boolean;
  error: string;
  retry: () => void;
  /** Loaded data: once present, refreshes keep the content on screen. */
  data?: unknown;
  children: ReactNode;
}) {
  const loaded = data !== undefined;
  if (loading && !loaded) return <p role="status">Загрузка…</p>;
  if (error && !loaded)
    return (
      <div role="alert">
        {error} <button onClick={retry}>Повторить</button>
      </div>
    );
  return (
    <>
      {error && (
        <p className="refresh-error" role="alert">
          Не удалось обновить данные: {error}{" "}
          <button onClick={retry}>Повторить</button>
        </p>
      )}
      {children}
    </>
  );
}
export function Badge({ status }: { status: string }) {
  return (
    <span className={`badge ${status}`}>
      {publicationLabel(status)}
    </span>
  );
}

export function publicationLabel(status: string) {
  const labels: Record<string, string> = {
    submitted: "На модерации",
    published: "Опубликовано",
    not_found: "Не найдено",
  };
  return labels[status] ?? status.replace("_", " ").toUpperCase();
}
export function Pager({
  page,
  hasNext,
  onPage,
  disabled = false,
}: {
  page: number;
  hasNext: boolean;
  onPage: (page: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="pager">
      <button
        disabled={page === 1 || disabled}
        onClick={() => onPage(page - 1)}
      >
        ← Назад
      </button>
      <span>Страница {page}</span>
      <button disabled={!hasNext || disabled} onClick={() => onPage(page + 1)}>
        Далее →
      </button>
    </div>
  );
}
export function Confirm({
  title,
  children,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="overlay">
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="dialog"
      >
        <h2>{title}</h2>
        {children}
        <div className="actions">
          <button autoFocus disabled={busy} onClick={onCancel}>
            Отмена
          </button>
          <button className="primary" disabled={busy} onClick={onConfirm}>
            {busy ? "Сохранение…" : "Продолжить"}
          </button>
        </div>
      </section>
    </div>
  );
}
export const date = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  });
export const categoryName = (value: string | null) => value ?? "Без категории";
/** Russian count noun: plural(5, "категория", "категории", "категорий"). */
export function plural(n: number, one: string, few: string, many: string) {
  const tens = n % 100;
  if (tens >= 11 && tens <= 14) return many;
  const last = n % 10;
  return last === 1 ? one : last >= 2 && last <= 4 ? few : many;
}
// Users who add VK accounts, as shown after an account name.
export const owners: Record<string, string> = { tony: "Тони", tima: "Тима" };
/** «Аня Иванова (Тима)»: the account and who added it. */
export const accountName = (a: { name: string; owner?: string | null }) =>
  a.owner ? `${a.name} (${owners[a.owner] ?? a.owner})` : a.name;
export const genderLabel = (value: string | null | undefined) =>
  value === "male" ? "муж." : value === "female" ? "жен." : "без пола";
