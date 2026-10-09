import { useState } from "react";
import { accountsApi } from "./api/accounts";
import { gridsApi } from "./api/grids";
import { errorMessage } from "./api/client";
import { State, useLoad } from "./shared";

export function GridReadinessPanel({ gridId }: { gridId: string }) {
  const readiness = useLoad(
    (signal) => gridsApi.readiness(gridId, signal),
    [gridId],
  );
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const jobs = useLoad(
    (signal) =>
      open
        ? gridsApi.preparationJobs(gridId, page, signal)
        : Promise.resolve(null),
    [gridId, page, open],
  );
  const accounts = useLoad(
    (signal) => (open ? accountsApi.list(1, signal) : Promise.resolve([])),
    [open],
  );
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function enqueue(retry: boolean) {
    setBusy(true);
    setError("");
    try {
      await gridsApi.prepare(gridId, account, retry);
      jobs.reload();
      readiness.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const r = readiness.data;
  return (
    <section aria-label="Готовность сетки">
      <State {...readiness} retry={readiness.reload}>
        {r && (
          <p className="summary">
            Разрешено: {r.resolved}/{r.total} · Доступно: {r.active_resolvable}{" "}
            · Недоступно: {r.unavailable} · Ожидают проверки:{" "}
            {r.unresolved + r.transient}
            <br />
            Комментарии: {r.with_comment} · Hints: {r.with_content_hint} ·
            References готовы: {r.references_ready} · Media context готов:{" "}
            {r.media_context_ready}
            <br />
            Архив индексирован: {r.with_archive_indexed} · Archive reuse
            включён: {r.archive_reuse_enabled} ·{" "}
            {r.ready_to_create_campaign
              ? "Сетка готова к media planning"
              : "Нужна подготовка media context"}
          </p>
        )}
      </State>
      <button
        onClick={() => {
          readiness.reload();
          jobs.reload();
        }}
      >
        Обновить готовность
      </button>
      <details onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>Подготовка media context</summary>
        <p>
          Warmup: {r?.reference_warmup_target ?? 12} recent references.
          Существующие references сохраняются; автоматической индексации архива
          нет.
        </p>
        <label>
          Account для read-only подготовки
          <select value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="">Выберите Account</option>
            {accounts.data
              ?.filter((a) => a.status === "active" && a.token_configured)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
        <button disabled={!account || busy} onClick={() => void enqueue(false)}>
          Поставить сетку в очередь подготовки
        </button>
        <button disabled={!account || busy} onClick={() => void enqueue(true)}>
          Повторить failed / transient
        </button>
        <p>VK posts не отправляются.</p>
        {error && <p role="alert">{error}</p>}
        <State {...jobs} retry={jobs.reload}>
          {jobs.data && (
            <>
              <p>
                Jobs: {jobs.data.total}.{" "}
                {Object.entries(r?.preparation_states ?? {})
                  .map(([state, count]) => `${state}: ${count}`)
                  .join(" · ")}
              </p>
              <ul>
                {jobs.data.items.map((j) => (
                  <li key={j.id}>
                    {j.community_id}: {j.state}
                    {j.error_code ? ` (${j.error_code})` : ""}
                  </li>
                ))}
              </ul>
              <button
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Назад
              </button>
              <button
                disabled={page * 25 >= jobs.data.total}
                onClick={() => setPage((p) => p + 1)}
              >
                Далее
              </button>
            </>
          )}
        </State>
      </details>
    </section>
  );
}
