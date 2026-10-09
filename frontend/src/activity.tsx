import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { request } from "./api/client";

export type Activity = {
  id: string;
  kind: string;
  label: string;
  state: "queued" | "running" | "success" | "warning" | "failed";
  stage: string;
  current: number;
  total: number | null;
  percent: number | null;
  started_at: string;
  updated_at: string;
  elapsed_seconds: number;
  target_url: string;
  message: string | null;
  counters: Record<string, number>;
};
export const activityApi = {
  list: (signal?: AbortSignal) => request<Activity[]>("/activity", { signal }),
};
const Context = createContext<{ items: Activity[]; unavailable: boolean }>({
  items: [],
  unavailable: false,
});
export function ActivityProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Activity[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [notice, setNotice] = useState<string>();
  const previous = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const rows = await activityApi.list(controller.signal);
        if (disposed) return;
        for (const row of rows) {
          const old = previous.current.get(row.id);
          if (
            (old === "queued" || old === "running") &&
            !["queued", "running"].includes(row.state)
          )
            setNotice(
              `${row.state === "failed" ? "Не завершено" : "Готово"}: ${row.label}${row.current ? ` · ${row.current}` : ""}`,
            );
        }
        previous.current = new Map(rows.map((row) => [row.id, row.state]));
        setItems(rows);
        setUnavailable(false);
      } catch {
        if (!disposed) setUnavailable(true);
      }
      if (!disposed) timer = setTimeout(() => void poll(), 3000);
    }
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(undefined), 8000);
    return () => clearTimeout(timer);
  }, [notice]);
  return (
    <Context.Provider value={{ items, unavailable }}>
      {children}
      {notice && (
        <div className="activity-toast" role="status">
          {notice}
          <button
            aria-label="Закрыть уведомление"
            onClick={() => setNotice(undefined)}
          >
            ×
          </button>
        </div>
      )}
    </Context.Provider>
  );
}
export function ActivityIndicator() {
  const { items, unavailable } = useContext(Context);
  const active = items.filter((row) =>
    ["queued", "running"].includes(row.state),
  ).length;
  const warning = items.some((row) =>
    ["failed", "warning"].includes(row.state),
  );
  return (
    <Link className="activity-indicator" to="/activity">
      {active ? (
        <>
          <span className="activity-spinner" /> {active} процесса
        </>
      ) : (
        "Нет активных задач"
      )}
      {(warning || unavailable) && (
        <span
          title={
            unavailable ? "Статус временно недоступен" : "Есть предупреждения"
          }
        >
          {" "}
          ⚠
        </span>
      )}
    </Link>
  );
}
export function ActivityCard({ row }: { row: Activity }) {
  return (
    <article className={`activity-card state-${row.state}`}>
      <div className="activity-heading">
        <Link to={row.target_url}>{row.label}</Link>
        <span>
          {
            {
              queued: "В очереди",
              running: "В работе",
              success: "Готово",
              warning: "Предупреждение",
              failed: "Ошибка",
            }[row.state]
          }
        </span>
      </div>
      <p>
        {row.stage}
        {row.total != null
          ? ` · ${row.current} / ${row.total}`
          : row.current
            ? ` · ${row.current}`
            : ""}
      </p>
      {["running", "queued"].includes(row.state) && (
        <progress
          aria-label="Прогресс задачи"
          {...(row.percent == null ? {} : { value: row.percent, max: 100 })}
        />
      )}
      <small>
        {row.elapsed_seconds} сек. · Начало:{" "}
        {new Date(row.started_at).toLocaleString()} · Обновлено:{" "}
        {new Date(row.updated_at).toLocaleTimeString()}
      </small>
      {Object.keys(row.counters).length > 0 && (
        <p className="note">
          {Object.entries(row.counters)
            .map(
              ([key, value]) =>
                `${
                  (
                    {
                      posts_scanned: "Постов",
                      photo_posts_found: "Фото",
                      downloads_done: "Скачано",
                      downloads_total: "К загрузке",
                      embeddings_done: "Визуальных признаков",
                      embeddings_total: "Всего фото",
                      photos: "Фото найдено",
                      raw_candidates: "Найдено кандидатов",
                      pins_materialized: "Pins загружено",
                      pins_embedded: "Pins изучено",
                      category_candidates: "Фото других групп",
                      category_shortlist: "Отобрано",
                      category_materialized: "Подготовлено",
                      pages: "Страниц",
                      candidates: "Кандидатов",
                      min_age_days: "Возраст от",
                      max_age_days: "Возраст до",
                    } as Record<string, string>
                  )[key] ?? key
                }: ${value}`,
            )
            .join(" · ")}
        </p>
      )}
      {row.message && <p>{row.message}</p>}
    </article>
  );
}
export function CommunityActivities({ id }: { id: string }) {
  const { items } = useContext(Context);
  return (
    <div>
      {items
        .filter(
          (row) =>
            row.target_url === `/communities/${id}` &&
            ["queued", "running"].includes(row.state),
        )
        .map((row) => (
          <ActivityCard key={row.id} row={row} />
        ))}
    </div>
  );
}
export function ActivityPage() {
  const { items, unavailable } = useContext(Context);
  return (
    <>
      <h1>Activity Center</h1>
      <p>
        Текущие и недавние операции. Прогресс сохраняется при переходе между
        страницами.
      </p>
      {unavailable && (
        <p role="alert">
          Статус задач временно недоступен. Проверка повторится автоматически.
        </p>
      )}
      {!items.length && !unavailable && <p>Нет активных задач</p>}
      <div className="activity-list">
        {items.map((row) => (
          <ActivityCard key={row.id} row={row} />
        ))}
      </div>
    </>
  );
}
