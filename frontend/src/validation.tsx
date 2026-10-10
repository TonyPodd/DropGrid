import { useCallback, useEffect, useState, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { request, errorMessage } from "./api/client";
import "./validation.css";
export type ReviewCandidate = {
  rank: number;
  provider: string;
  image: string | null;
  rating: string | null;
  diagnostics: Record<string, unknown>;
};
type Item = {
  position: number;
  selection_id: string;
  confirmed_at: string | null;
  state: string;
  automatic_rank: number;
  active_rank: number;
  community: string;
  category: string | null;
  intent: string | null;
  attention: string[];
  candidates: ReviewCandidate[];
  neighbors: string[];
};
type Batch = {
  id: string;
  trusted_reviewer?: Reviewer;
  owner?: boolean;
  name: string;
  target_count: number;
  reviewer_id: string | null;
  current_position: number;
  current_filter: string;
  state: string;
  confirmed: number;
  replaced: number;
  kept: number;
  skipped: number;
  done: number;
  remaining: number;
  selected_sources: Record<string, number>;
  items: { position: number; state: string; attention: boolean }[];
};
type Reviewer = { id: string; display_name: string };
export const sourceNames: Record<string, string> = {
  pinterest: "Pinterest",
  vk_category_archive: "VK · другая группа",
  vk_archive: "VK · архив группы",
  library: "Библиотека",
  pixabay: "Pixabay",
};
export function imageUrl(path: string | null) {
  return path
    ? (import.meta.env.VITE_BACKEND_URL || "http://localhost:8000").replace(
        /\/$/,
        "",
      ) + path
    : "";
}
export function PhotoValidationPage() {
  const operation = useRef<string | null>(null);
  const keyboardChoice = useRef(false);
  const [queuedConfirm, setQueuedConfirm] = useState<string | null>(null);
  const { batchId } = useParams();
  const [batch, setBatch] = useState<Batch | null>(null),
    [item, setItem] = useState<Item | null>(null),
    [reviewers, setReviewers] = useState<Reviewer[]>([]);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [alternatives, setAlternatives] = useState(false),
    [enlarged, setEnlarged] = useState(false),
    [active, setActive] = useState(0),
    [seen, setSeen] = useState<number[]>([]),
    [revision, setRevision] = useState(0),
    [newName, setNewName] = useState("");
  useEffect(() => {
    const c = new AbortController();
    setBatch(null);
    setItem(null);
    void Promise.all([
      request<Batch>(`/review-batches/${batchId}`, { signal: c.signal }),
      request<Reviewer[]>("/photo-reviewers", { signal: c.signal }),
    ])
      .then(([b, r]) => {
        setBatch(b);
        setReviewers(r);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorMessage(e));
      });
    return () => c.abort();
  }, [batchId]);
  useEffect(() => {
    if (!batch) return;
    const c = new AbortController();
    setItem(null);
    setAlternatives(false);
    setEnlarged(false);
    void request<Item>(
      `/review-batches/${batchId}/items/${batch.current_position}`,
      { signal: c.signal },
    )
      .then((i) => {
        setItem(i);
        setActive(i.active_rank);
        setSeen([i.active_rank]);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorMessage(e));
      });
    return () => c.abort();
  }, [batchId, batch?.current_position, revision]);
  useEffect(() => {
    if (!item) return;
    const urls = [...item.candidates.map((c) => c.image), ...item.neighbors];
    const images = urls
      .filter((u): u is string => !!u)
      .map((u) => {
        const i = new Image();
        i.src = imageUrl(u);
        return i;
      });
    return () => {
      images.forEach((i) => {
        i.onload = null;
      });
    };
  }, [item]);
  const navigate = useCallback(
    async (
      position: number,
      mode = batch?.current_filter ?? "pending",
      reviewerId = batch?.reviewer_id,
    ) => {
      if (!batch || busy) return;
      setBusy(true);
      operation.current = "navigate";
      keyboardChoice.current = false;
      setError("");
      try {
        await request(`/review-batches/${batch.id}/cursor`, {
          method: "PUT",
          body: { position, mode, reviewer_id: reviewerId },
        });
        setBatch({
          ...batch,
          current_position: position,
          current_filter: mode,
          reviewer_id: reviewerId ?? null,
        });
      } catch (e) {
        setQueuedConfirm(null);
        setError(errorMessage(e));
      } finally {
        operation.current = null;
        setBusy(false);
      }
    },
    [batch, busy],
  );
  const act = useCallback(
    async (action: string, rank = active) => {
      if (!batch || !item || busy) return;
      if (!batch.reviewer_id) {
        setError("Выберите своё имя перед проверкой.");
        return;
      }
      setBusy(true);
      operation.current = action;
      if (action === "confirm" || action === "skip")
        keyboardChoice.current = false;
      setError("");
      const shown = [...new Set([...seen, rank])];
      try {
        const result = await request<Batch>(
          `/review-batches/${batch.id}/items/${item.position}/action`,
          {
            method: "POST",
            body: {
              selection_id: item.selection_id,
              reviewer_id: batch.reviewer_id,
              action,
              rank,
              shown_ranks: shown,
              expected_confirmed_at: item.confirmed_at,
            },
          },
        );
        setBatch({
          ...result,
          reviewer_id: batch.reviewer_id,
          trusted_reviewer: batch.trusted_reviewer,
          owner: batch.owner,
        });
        if (action === "select") {
          setActive(rank);
          setSeen((previous) => [...new Set([...previous, ...shown])]);
        } else if (action === "dislike") {
          setItem({
            ...item,
            candidates: item.candidates.map((c) =>
              c.rank === rank ? { ...c, rating: "dislike" } : c,
            ),
          });
        } else if (result.current_position === item.position)
          setRevision((n) => n + 1);
      } catch (e) {
        setQueuedConfirm(null);
        setError(errorMessage(e));
      } finally {
        operation.current = null;
        setBusy(false);
      }
    },
    [batch, item, busy, active, seen],
  );
  useEffect(() => {
    if (!busy && queuedConfirm && item?.selection_id === queuedConfirm) {
      setQueuedConfirm(null);
      void act("confirm");
    }
  }, [busy, queuedConfirm, item, act]);
  const openAlternatives = useCallback(() => {
    if (!item) return;
    setAlternatives((v) => !v);
    setSeen((s) => [...new Set([...s, ...item.candidates.map((c) => c.rank)])]);
  }, [item]);
  const step = useCallback(
    (direction: number) => {
      if (!batch || busy) return;
      const positions =
        direction > 0
          ? batch.items
              .filter((i) => i.state === "pending")
              .map((i) => i.position)
          : batch.items.map((i) => i.position);
      const p =
        direction > 0
          ? (positions.find((p) => p > batch.current_position) ?? positions[0])
          : positions.filter((p) => p < batch.current_position).at(-1);
      if (p !== undefined)
        void navigate(p, direction < 0 ? "all" : batch.current_filter);
    },
    [batch, busy, navigate],
  );
  useEffect(() => {
    function key(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (
        e.key === "Tab" ||
        target.closest("input,textarea,select,[contenteditable=true]")
      )
        keyboardChoice.current = false;
      if (
        !e.repeat &&
        e.key === "Enter" &&
        busy &&
        operation.current === "select" &&
        item &&
        !target.closest("input,textarea,select,[contenteditable=true]")
      ) {
        e.preventDefault();
        setQueuedConfirm(item.selection_id);
        return;
      }
      if (
        e.repeat ||
        e.ctrlKey ||
        e.metaKey ||
        e.altKey ||
        target.closest("input,textarea,select,[contenteditable=true]") ||
        busy ||
        !item ||
        !batch
      )
        return;
      if (
        batch.remaining === 0 &&
        batch.current_filter === "pending" &&
        e.key !== "ArrowLeft"
      )
        return;
      if (enlarged) {
        if (e.key === "Tab") {
          e.preventDefault();
          document
            .querySelector<HTMLButtonElement>(".validation-overlay button")
            ?.focus();
        }
        if (e.key === "Escape" || e.code === "Space") {
          e.preventDefault();
          setEnlarged(false);
        }
        return;
      }
      if (e.key === "Enter") {
        if (
          !keyboardChoice.current &&
          target.closest("button,a") &&
          !target.closest(".validation-alternatives")
        )
          return;
        e.preventDefault();
        void act("confirm");
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        step(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        step(1);
      } else if (e.key.toLowerCase() === "d") {
        e.preventDefault();
        void act("dislike");
      } else if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        void act("skip");
      } else if (e.code === "Space") {
        e.preventDefault();
        setEnlarged(true);
      } else if (/^[1-9]$/.test(e.key)) {
        const c = item.candidates[Number(e.key) - 1];
        if (c) {
          keyboardChoice.current = true;
          e.preventDefault();
          setAlternatives(true);
          setSeen(item.candidates.map((c) => c.rank));
          void act("select", c.rank);
        }
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [act, step, item, batch, busy, enlarged]);
  async function createReviewer() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      const r = await request<Reviewer>("/photo-reviewers", {
        method: "POST",
        body: { display_name: newName },
      });
      setReviewers((list) =>
        list.some((x) => x.id === r.id) ? list : [...list, r],
      );
      setNewName("");
      setBusy(false);
      if (batch)
        await navigate(batch.current_position, batch.current_filter, r.id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const chosen = item?.candidates.find((c) => c.rank === active);
  const matching = batch?.items.filter(
    (i) =>
      batch.current_filter === "all" ||
      (batch.current_filter === "pending" && i.state === "pending") ||
      (batch.current_filter === "skipped" &&
        ["skipped", "needs_attention"].includes(i.state)) ||
      (batch.current_filter === "needs_attention" &&
        (i.attention || i.state === "needs_attention")),
  );
  const finish =
    batch && batch.remaining === 0 && batch.current_filter === "pending";
  return (
    <main className="validation-shell">
      <header className="validation-top">
        {(!batch?.trusted_reviewer || batch.owner) && (
          <Link to="/campaigns">DropGrid</Link>
        )}
        <h1>Проверка фото</h1>
        {batch && (
          <>
            {batch.trusted_reviewer ? (
              <strong>{batch.trusted_reviewer.display_name}</strong>
            ) : (
              <label className="reviewer-select">
                Кто проверяет
                <select
                  aria-label="Reviewer"
                  value={batch.reviewer_id ?? ""}
                  disabled={busy}
                  onChange={(e) =>
                    void navigate(
                      batch.current_position,
                      batch.current_filter,
                      e.target.value || null,
                    )
                  }
                >
                  <option value="">Выберите имя</option>
                  {reviewers.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.display_name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <strong>
              {batch.done} / {batch.target_count}
            </strong>
            <progress
              aria-label="Прогресс проверки"
              max={batch.target_count}
              value={batch.done}
            />
          </>
        )}
      </header>
      {error && <p role="alert">{error}</p>}
      {!batch ? (
        <p role="status">Загружаем проверку…</p>
      ) : (
        <>
          {!batch.reviewer_id && (
            <div className="reviewer-create">
              <label>
                Новое имя
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  maxLength={100}
                />
              </label>
              <button onClick={() => void createReviewer()} disabled={busy}>
                Добавить reviewer
              </button>
            </div>
          )}
          <nav className="validation-filters" aria-label="Фильтр проверки">
            {Object.entries({
              all: "Все",
              pending: "Непроверенные",
              needs_attention: "Требуют внимания",
              skipped: "Пропущенные",
            }).map(([mode, label]) => (
              <button
                key={mode}
                aria-pressed={batch.current_filter === mode}
                disabled={busy}
                onClick={() => {
                  const found = batch.items.find(
                    (i) =>
                      mode === "all" ||
                      (mode === "pending" && i.state === "pending") ||
                      (mode === "skipped" &&
                        ["skipped", "needs_attention"].includes(i.state)) ||
                      (mode === "needs_attention" &&
                        (i.attention || i.state === "needs_attention")),
                  );
                  void navigate(
                    found?.position ?? batch.current_position,
                    mode,
                  );
                }}
              >
                {label}
              </button>
            ))}
          </nav>
          <p className="validation-counts">
            Подтверждено {batch.confirmed} · заменено {batch.replaced} ·
            пропущено {batch.skipped} · осталось {batch.remaining}
          </p>
          {finish ? (
            <section className="validation-finish">
              <h2>Проверка завершена</h2>
              <p>Проверено: {batch.done}</p>
              <p>Оставлено решение системы: {batch.kept}</p>
              <p>Заменено: {batch.replaced}</p>
              <p>Пропущено: {batch.skipped}</p>
              {Object.entries(sourceNames).map(([p, label]) => (
                <p key={p}>
                  {label}: {batch.selected_sources[p] ?? 0}
                </p>
              ))}
              <button
                disabled={!batch.skipped}
                onClick={() => {
                  const i = batch.items.find((i) =>
                    ["skipped", "needs_attention"].includes(i.state),
                  );
                  if (i) void navigate(i.position, "skipped");
                }}
              >
                Вернуться к пропущенным
              </button>
            </section>
          ) : matching?.length === 0 ? (
            <p role="status">Нет фото в этом фильтре.</p>
          ) : !item ? (
            <div className="validation-loading" role="status">
              Загружаем фото…
            </div>
          ) : (
            <>
              <section className="validation-context">
                <div>
                  <h2>{item.community}</h2>
                  <p>
                    {item.category}
                    {item.intent && <> · {item.intent}</>}
                  </p>
                </div>
                <span>
                  Фото {item.position + 1} / {batch.target_count}
                  {item.state === "confirmed" ? " · проверено" : ""}
                </span>
              </section>
              <div className="validation-workspace">
                <div className="validation-primary">
                  <button
                    className="validation-photo"
                    aria-label="Увеличить фото"
                    onClick={() => setEnlarged(true)}
                  >
                    {chosen?.image ? (
                      <img
                        src={imageUrl(chosen.image)}
                        alt={`Выбрано: ${item.community}`}
                      />
                    ) : (
                      <span>Фото недоступно — пропустите его</span>
                    )}
                  </button>
                  <span className="source-badge">
                    {sourceNames[chosen?.provider ?? ""] ?? chosen?.provider}
                  </span>
                  <div className="validation-actions">
                    <button
                      className="primary"
                      disabled={
                        busy ||
                        !chosen?.image ||
                        !batch.reviewer_id ||
                        batch.state !== "open"
                      }
                      onClick={() => void act("confirm")}
                    >
                      {active === item.automatic_rank ? "Оставить" : "Выбрать"}
                    </button>
                    <button
                      aria-expanded={alternatives}
                      onClick={openAlternatives}
                      disabled={busy}
                    >
                      Альтернативы
                    </button>
                    <button
                      disabled={busy || item.state === "confirmed"}
                      onClick={() => void act("skip")}
                    >
                      Пропустить
                    </button>
                    <button
                      aria-label="Не нравится"
                      disabled={busy}
                      onClick={() => void act("dislike")}
                    >
                      Не нравится
                    </button>
                  </div>
                </div>
                {alternatives && (
                  <div
                    className="validation-alternatives"
                    aria-label="Альтернативы"
                  >
                    {item.candidates.map((c, i) => (
                      <button
                        key={c.rank}
                        aria-pressed={active === c.rank}
                        disabled={busy}
                        onClick={() => void act("select", c.rank)}
                      >
                        {c.image ? (
                          <img
                            src={imageUrl(c.image)}
                            alt={`Вариант ${i + 1}`}
                          />
                        ) : (
                          <span>Нет preview</span>
                        )}
                        <span>
                          {i + 1}. {sourceNames[c.provider] ?? c.provider}
                          {c.rating === "dislike" ? " · 👎" : ""}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="validation-navigation">
                <button
                  onClick={() => step(-1)}
                  disabled={busy || item.position === 0}
                >
                  ← Предыдущее
                </button>
                <button
                  onClick={() => step(1)}
                  disabled={
                    busy ||
                    !batch.items.some(
                      (i) =>
                        i.state === "pending" && i.position !== item.position,
                    )
                  }
                >
                  Следующее →
                </button>
              </div>
              <details className="validation-diagnostics">
                <summary>Почему это фото / Диагностика</summary>
                <p>
                  Выбор системы: вариант{" "}
                  {item.candidates.findIndex(
                    (c) => c.rank === item.automatic_rank,
                  ) + 1}
                </p>
                <p>{item.attention.join(", ") || "Нет предупреждений"}</p>
                <pre>{JSON.stringify(chosen?.diagnostics ?? {}, null, 2)}</pre>
              </details>
            </>
          )}
          <p className="validation-shortcuts">
            Enter — оставить/выбрать · 1–9 — вариант · ← назад · → дальше · D —
            не нравится · S — пропустить · Space — увеличить
          </p>
        </>
      )}
      {enlarged && chosen?.image && (
        <div
          className="validation-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Увеличенное фото"
        >
          <button autoFocus onClick={() => setEnlarged(false)}>
            Закрыть
          </button>
          <img src={imageUrl(chosen.image)} alt={item?.community} />
        </div>
      )}
    </main>
  );
}
