import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { request, errorMessage } from "./api/client";
import { imageUrl } from "./validation";
import "./discovery.css";

type Region = { key: string; name: string; codes: string[] };
type Options = {
  grids: { id: string; name: string }[];
  accounts: { id: string; name: string }[];
  seeds: {
    id: string;
    name: string;
    category: string | null;
    region_key: string | null;
  }[];
  regions: Region[];
  reviewer: { id: string; name: string } | null;
  reviewers: { id: string; name: string }[];
};
export type DiscoveryCandidate = {
  id: string;
  name: string;
  url: string;
  suggested_category: string | null;
  suggested_region_key: string | null;
  status: string;
  analysis_state: string;
  final_score: number;
  topic_score: number;
  visual_score: number | null;
  region_score: number | null;
  activity_score: number;
  graph_score: number;
  confidence: string;
  found_by_queries: string[];
  previews: string[];
  reason: {
    region_status?: string;
    filter?: string;
    photos_analyzed?: number;
    activity?: {
      state: string;
      posts_last_30d: number;
      posts_last_90d: number;
    };
    closest_communities?: { name: string; url: string; similarity: number }[];
  };
};
type Inbox = {
  items: DiscoveryCandidate[];
  total: number;
  counters: Record<string, number>;
};
type Run = {
  id: string;
  state: string;
  stage: string;
  query_cursor: number;
  queries: { query: string }[];
  counters: Record<string, number>;
  error_code: string | null;
};
export const discoveryStages: Record<string, string> = {
  searching: "Searching communities",
  checking_activity: "Checking activity",
  analyzing_photos: "Analyzing photos",
  ranking_candidates: "Ranking candidates",
};
const regions: Record<string, string> = {
  same_region: "Тот же регион",
  mismatch: "Другой регион",
  unverified: "Регион не подтверждён",
  not_required: "Регион не ограничен",
};
const percent = (value: number | null) =>
  value === null ? "—" : `${Math.round(value * 100)}%`;

export function DiscoveryPage() {
  const [params, setParams] = useSearchParams();
  const grid = params.get("grid") || "";
  const runId = params.get("run") || "";
  const [options, setOptions] = useState<Options>();
  const [inbox, setInbox] = useState<Inbox>();
  const [runs, setRuns] = useState<Run[]>([]);
  const [account, setAccount] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [scope, setScope] = useState("grid");
  const [scopeValue, setScopeValue] = useState("");
  const [category, setCategory] = useState("");
  const [region, setRegion] = useState("");
  const [status, setStatus] = useState("new");
  const [minimum, setMinimum] = useState("0");
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<DiscoveryCandidate>();
  const [approvalCategory, setApprovalCategory] = useState("");
  const [approvalRegion, setApprovalRegion] = useState("");
  const [hint, setHint] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void request<Options>(
      `/discovery/options${grid ? `?grid_id=${grid}` : ""}`,
      { signal: controller.signal },
    )
      .then((data) => {
        setOptions(data);
        setAccount((old) => old || data.accounts[0]?.id || "");
        setReviewer(data.reviewer?.id || "");
        if (!grid && data.grids[0])
          setParams({ grid: data.grids[0].id }, { replace: true });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    return () => controller.abort();
  }, [grid, setParams]);
  const reload = useCallback(
    async (signal?: AbortSignal) => {
      if (!grid) return;
      const query = new URLSearchParams({
        grid_id: grid,
        status,
        minimum_score: minimum,
        page: String(page),
      });
      if (runId) query.set("run_id", runId);
      if (category) query.set("category", category);
      if (region) query.set("region_key", region);
      const [data, jobs] = await Promise.all([
        request<Inbox>(`/discovery/candidates?${query}`, { signal }),
        request<Run[]>(`/discovery/runs?grid_id=${grid}`, { signal }),
      ]);
      setInbox(data);
      setRuns(jobs);
    },
    [grid, status, minimum, page, runId, category, region],
  );
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await reload(controller.signal);
      } catch (e) {
        if (!controller.signal.aborted) setError(errorMessage(e));
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => void poll(), 4000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [reload]);
  async function launch(whole = false) {
    setBusy(true);
    setError("");
    try {
      const run = await request<Run>("/discovery/runs", {
        method: "POST",
        body: {
          grid_id: grid,
          account_id: account,
          scope_type: whole ? "grid" : scope,
          scope_value: whole || scope === "grid" ? null : scopeValue,
          category: !whole && scope === "region" ? category || null : null,
        },
      });
      setParams({ grid, run: run.id });
      setStatus("new");
      setPage(1);
      setNotice(
        "Поиск запущен. Результат сохранится после перезагрузки страницы.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function decide(
    candidate: DiscoveryCandidate,
    action: "approve" | "reject" | "restore",
  ) {
    setBusy(true);
    setError("");
    try {
      await request(`/discovery/candidates/${candidate.id}/decision`, {
        method: "POST",
        body: {
          action,
          reviewer_id: reviewer,
          run_id: runId || null,
          ...(action === "approve"
            ? {
                category: approvalCategory || null,
                region_key: approvalRegion || null,
                content_hint: hint || null,
              }
            : {}),
        },
      });
      setConfirm(undefined);
      setNotice(
        action === "approve"
          ? "Сообщество добавлено в сетку. Отправки не создаются."
          : action === "reject"
            ? "Отмечено «Не подходит». Больше не появится в новых результатах."
            : "Кандидат восстановлен.",
      );
      await reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const categories = [
    ...new Set(
      options?.seeds.map((s) => s.category).filter((v): v is string => !!v),
    ),
  ].sort();
  const active = runs.find(
    (r) => r.state === "queued" || r.state === "running",
  );
  const selectedRun = runs.find((r) => r.id === runId) || active || runs[0];
  return (
    <div className="discovery-page">
      <div className="page-heading">
        <div>
          <h1>Community Discovery</h1>
          <p>Поиск новых сообществ · только чтение VK · добавление вручную.</p>
        </div>
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <section className="discovery-controls" aria-label="Настройки поиска">
        <label>
          Сетка
          <select
            value={grid}
            onChange={(e) => {
              setParams({ grid: e.target.value });
              setPage(1);
              setCategory("");
              setRegion("");
              setScopeValue("");
            }}
          >
            <option value="">Выберите сетку</option>
            {options?.grids.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Account
          <select value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="">Нужен проверенный токен</option>
            {options?.accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Область поиска
          <select
            value={scope}
            onChange={(e) => {
              setScope(e.target.value);
              setScopeValue("");
            }}
          >
            <option value="grid">Вся сетка</option>
            <option value="community">Одно сообщество</option>
            <option value="category">Категория</option>
            <option value="region">Регион / категория</option>
          </select>
        </label>
        {scope !== "grid" && (
          <label>
            Источник
            <select
              value={scopeValue}
              onChange={(e) => setScopeValue(e.target.value)}
            >
              <option value="">Выберите</option>
              {scope === "community"
                ? options?.seeds.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))
                : scope === "category"
                  ? categories.map((c) => <option key={c}>{c}</option>)
                  : options?.regions.map((r) => (
                      <option key={r.key} value={r.key}>
                        {r.name}
                      </option>
                    ))}
            </select>
          </label>
        )}
        <button
          disabled={
            busy ||
            !!active ||
            !grid ||
            !account ||
            (scope !== "grid" && !scopeValue)
          }
          onClick={() => void launch()}
        >
          Найти похожие сообщества
        </button>
        {scope !== "grid" && (
          <button
            disabled={busy || !!active || !grid || !account}
            onClick={() => void launch(true)}
          >
            Найти похожие для всей сетки
          </button>
        )}
      </section>
      {selectedRun && (
        <section className="discovery-progress" aria-live="polite">
          <strong>
            {selectedRun.state === "ready"
              ? "Поиск завершён"
              : selectedRun.state === "failed"
                ? "Поиск остановлен"
                : discoveryStages[selectedRun.stage] || selectedRun.stage}
          </strong>
          <p>
            Запросы: {selectedRun.query_cursor} / {selectedRun.queries.length} ·
            Найдено: {selectedRun.counters.raw_candidates || 0} · Фотоанализ:{" "}
            {selectedRun.counters.visually_analyzed || 0}
          </p>
          {selectedRun.error_code && (
            <p>Диагностика: {selectedRun.error_code}</p>
          )}
          <details>
            <summary>Запросы и счётчики</summary>
            <p>{selectedRun.queries.map((q) => q.query).join(" · ")}</p>
            <pre>{JSON.stringify(selectedRun.counters, null, 2)}</pre>
          </details>
        </section>
      )}
      <div className="discovery-counts">
        {[
          ["new", "Новые"],
          ["recommended", "Высокая уверенность"],
          ["needs_review", "Нужно ревью"],
          ["added", "Добавлены"],
          ["rejected", "Отклонены"],
        ].map(([key, label]) => (
          <span key={key}>
            {label}: <strong>{inbox?.counters[key] || 0}</strong>
          </span>
        ))}
      </div>
      <section className="discovery-controls" aria-label="Фильтры кандидатов">
        <label>
          Категория
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все категории</option>
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Регион
          <select
            value={region}
            onChange={(e) => {
              setRegion(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все регионы</option>
            {options?.regions.map((r) => (
              <option key={r.key} value={r.key}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Статус
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            {[
              ["new", "Новые"],
              ["reviewing", "На рассмотрении"],
              ["approved", "Добавлены"],
              ["rejected", "Отклонены"],
              ["duplicate", "Уже в сетке"],
              ["unavailable", "Не прошли фильтр"],
              ["all", "Все"],
            ].map(([v, n]) => (
              <option key={v} value={v}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          Минимальный score
          <select
            value={minimum}
            onChange={(e) => {
              setMinimum(e.target.value);
              setPage(1);
            }}
          >
            <option value="0">Любой</option>
            <option value="0.5">50%</option>
            <option value="0.7">70%</option>
          </select>
        </label>
        <label>
          Запуск
          <select
            value={runId}
            onChange={(e) => {
              setParams(
                e.target.value ? { grid, run: e.target.value } : { grid },
              );
              setPage(1);
            }}
          >
            <option value="">Все запуски</option>
            {runs.map((r, i) => (
              <option key={r.id} value={r.id}>
                Запуск {runs.length - i} · {r.state}
              </option>
            ))}
          </select>
        </label>
        {!options?.reviewer && (
          <label>
            Reviewer
            <select
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
            >
              <option value="">Выберите себя</option>
              {options?.reviewers.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>
      <p>
        {inbox?.total || 0} кандидатов по фильтрам. Score — эвристика, не
        вероятность принятия поста.
      </p>
      <div className="discovery-cards">
        {inbox?.items.map((c) => (
          <article key={c.id} className="discovery-card">
            <header>
              <h2>
                <a href={c.url} target="_blank" rel="noopener noreferrer">
                  {c.name} ↗
                </a>
              </h2>
              <span>
                {percent(c.final_score)} ·{" "}
                {c.confidence === "high"
                  ? "Высокая уверенность"
                  : "Нужно ревью"}
              </span>
            </header>
            <p>
              {c.suggested_category || "Без категории"} ·{" "}
              {options?.regions.find((r) => r.key === c.suggested_region_key)
                ?.name || "Регион не определён"}
            </p>
            <p>
              {regions[c.reason.region_status || "unverified"]} · Активность:{" "}
              {c.reason.activity?.state || "ещё не проверена"} · Визуальное
              сходство: {percent(c.visual_score)}
            </p>
            <p>Найдено по: {c.found_by_queries.join(" · ")}</p>
            <div className="discovery-thumbnails">
              {c.previews.map((p, i) => (
                <img
                  key={p}
                  src={imageUrl(p)}
                  alt={`Фото сообщества ${i + 1}`}
                  loading="lazy"
                  onError={(event) => {
                    event.currentTarget.hidden = true;
                  }}
                />
              ))}
            </div>
            <details>
              <summary>Подробнее / почему найдено</summary>
              <dl>
                {[
                  ["Тематика", c.topic_score],
                  ["Регион", c.region_score],
                  ["Активность", c.activity_score],
                  ["Граф запросов", c.graph_score],
                ].map(([n, v]) => (
                  <div key={String(n)}>
                    <dt>{n}</dt>
                    <dd>{percent(v as number | null)}</dd>
                  </div>
                ))}
              </dl>
              <p>
                Проанализировано фото: {c.reason.photos_analyzed || 0}. 30 / 90
                дней: {c.reason.activity?.posts_last_30d || 0} /{" "}
                {c.reason.activity?.posts_last_90d || 0} постов в выборке, не
                полный счётчик.
              </p>
              {c.reason.filter && <p>Фильтр: {c.reason.filter}</p>}
              {c.reason.closest_communities?.map((s) => (
                <p key={s.url}>
                  <a href={s.url} target="_blank" rel="noopener noreferrer">
                    {s.name} ↗
                  </a>{" "}
                  · {percent(s.similarity)}
                </p>
              ))}
            </details>
            <footer>
              {["new", "reviewing"].includes(c.status) && (
                <>
                  <button
                    disabled={busy || !reviewer || c.analysis_state !== "ready"}
                    onClick={() => {
                      setConfirm(c);
                      setApprovalCategory(c.suggested_category || "");
                      setApprovalRegion(c.suggested_region_key || "");
                      setHint("");
                    }}
                  >
                    Добавить в сетку
                  </button>
                  <button
                    disabled={busy || !reviewer}
                    onClick={() => void decide(c, "reject")}
                  >
                    Не подходит
                  </button>
                </>
              )}
              {c.status === "rejected" && (
                <button
                  disabled={busy || !reviewer}
                  onClick={() => void decide(c, "restore")}
                >
                  Восстановить
                </button>
              )}
            </footer>
          </article>
        ))}
      </div>
      {!inbox?.items.length && (
        <p className="empty">По этим фильтрам кандидатов нет.</p>
      )}
      <div className="discovery-pagination">
        <button
          disabled={page === 1 || busy}
          onClick={() => setPage((p) => p - 1)}
        >
          Назад
        </button>
        <span>Страница {page}</span>
        <button
          disabled={busy || page * 20 >= (inbox?.total || 0)}
          onClick={() => setPage((p) => p + 1)}
        >
          Дальше
        </button>
      </div>
      {confirm && (
        <div className="discovery-modal">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="discovery-confirm-title"
          >
            <h2 id="discovery-confirm-title">
              Добавить {confirm.name} в сетку?
            </h2>
            <p>
              Сетка: {options?.grids.find((g) => g.id === grid)?.name}. Новые
              отправки и кампании не создаются.
            </p>
            <label>
              Категория
              <input
                autoFocus
                value={approvalCategory}
                onChange={(e) => setApprovalCategory(e.target.value)}
                maxLength={200}
              />
            </label>
            <label>
              Регион
              <select
                value={approvalRegion}
                onChange={(e) => setApprovalRegion(e.target.value)}
              >
                <option value="">Не подтверждён / не ограничен</option>
                {options?.regions.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Подсказка по контенту
              <textarea
                value={hint}
                onChange={(e) => setHint(e.target.value)}
                maxLength={3000}
              />
            </label>
            <footer>
              <button
                disabled={busy}
                onClick={() => void decide(confirm, "approve")}
              >
                Подтвердить добавление
              </button>
              <button disabled={busy} onClick={() => setConfirm(undefined)}>
                Отмена
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
