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
    setLoading(true);
    setError("");
    setData(undefined);
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
  children,
}: {
  loading: boolean;
  error: string;
  retry: () => void;
  children: ReactNode;
}) {
  return loading ? (
    <p role="status">Загрузка…</p>
  ) : error ? (
    <div role="alert">
      {error} <button onClick={retry}>Повторить</button>
    </div>
  ) : (
    <>{children}</>
  );
}
export function Badge({ status }: { status: string }) {
  return (
    <span className={`badge ${status}`}>
      {status.replace("_", " ").toUpperCase()}
    </span>
  );
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
