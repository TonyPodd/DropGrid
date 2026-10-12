import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { request, errorMessage } from "./api/client";
import { imageUrl } from "./validation";
import type { DiscoveryCandidate } from "./discovery";
import "./discovery-review.css";

type Mode = "pending" | "approved" | "rejected" | "uncertain" | "all";
type Batch = {
  id: string;
  grid_id: string;
  name: string;
  state: string;
  total: number;
  reviewed: number;
  reviewed_by_you: number;
  reviewer_id: string | null;
  reviewer_name: string;
  position: number;
  filter: Mode;
  reviewers: { id: string; name: string; reviewed: number }[];
};
type Detail = {
  target_region?: { display_name: string; vehicle_codes: string[] } | null;
  position: number;
  version: string;
  revision: number;
  decision: string | null;
  reason: string | null;
  seed_count: number;
  seeds: { name: string; url: string }[];
  candidate: DiscoveryCandidate & {
    region?: {
      display_name: string;
      region_codes: string[];
      vehicle_codes: string[];
    };
    human_status?: string;
  };
  grid_added: boolean;
  human_status: string | null;
  run_id: string | null;
};
const modes: [Mode, string][] = [
  ["pending", "Непроверенные"],
  ["approved", "Подходят"],
  ["rejected", "Не подходят"],
  ["uncertain", "Не уверен"],
  ["all", "Все"],
];
export const rejectionReasons = [
  ["wrong_topic", "Не та тематика"],
  ["wrong_region", "Не тот регион"],
  ["dead_or_inactive", "Неактивное сообщество"],
  ["too_broad", "Слишком широкая тематика"],
  ["commercial_or_irrelevant", "Коммерческое / нерелевантное"],
  ["duplicate", "Дубликат"],
  ["other", "Другое"],
];
const pct = (value: number | null | undefined) =>
  value == null ? "N/A" : `${Math.round(value * 100)}%`;
export function CommunityReviewPage() {
  const { batchId } = useParams();
  const [batch, setBatch] = useState<Batch>();
  const [item, setItem] = useState<Detail>();
  const [reviewer, setReviewer] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [reasons, setReasons] = useState(false);
  const [zoom, setZoom] = useState<string>();
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const prefix = `/discovery/review-batches/${batchId}`;
  const apply = useCallback(
    async (data: Batch, signal?: AbortSignal) => {
      if (signal?.aborted) return;
      setBatch(data);
      const id = data.reviewer_id || reviewer;
      if (id && data.position < data.total) {
        const next = await request<Detail>(
          `${prefix}/items/${data.position}?reviewer_id=${id}`,
          { signal },
        );
        if (!signal?.aborted) {
          setItem(next);
          setFailed(new Set());
          setReasons(false);
          setZoom(undefined);
        }
      } else setItem(undefined);
    },
    [prefix, reviewer],
  );
  useEffect(() => {
    const controller = new AbortController();
    void request<Batch>(
      `${prefix}${reviewer ? `?reviewer_id=${reviewer}` : ""}`,
      { signal: controller.signal },
    )
      .then((data) => apply(data, controller.signal))
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    return () => controller.abort();
  }, [prefix, reviewer, apply]);
  async function cursor(
    position: number | null,
    mode = batch?.filter || "pending",
  ) {
    if (!batch || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await apply(
        await request<Batch>(`${prefix}/cursor`, {
          method: "PUT",
          body: { reviewer_id: batch.reviewer_id || reviewer, position, mode },
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function decide(
    decision: "approved" | "rejected" | "uncertain",
    reason: string | null = null,
  ) {
    if (!batch || !item || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await request<{
        batch: Batch;
        next_position: number | null;
      }>(`${prefix}/items/${item.position}/decision`, {
        method: "POST",
        body: {
          reviewer_id: batch.reviewer_id || reviewer,
          decision,
          reason,
          seen_version: item.version,
          expected_revision: item.revision,
        },
      });
      setNotice(
        decision === "approved"
          ? "Сохранено: подходит. В сетку не добавлено."
          : decision === "rejected"
            ? "Сохранено: не подходит."
            : "Сохранено: не уверен.",
      );
      await apply(result.batch);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function nextPending() {
    if (!batch || lock.current) return;
    try {
      const data = await request<{ positions: number[] }>(
        `${prefix}/pending?reviewer_id=${batch.reviewer_id || reviewer}`,
      );
      const next =
        data.positions.find((p) => p > (item?.position ?? -1)) ??
        data.positions[0] ??
        batch.total;
      await cursor(next);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  function openGroup() {
    if (item) window.open(item.candidate.url, "_blank", "noopener,noreferrer");
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.shiftKey ||
        event.repeat ||
        target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName) ||
        lock.current
      )
        return;
      if (event.key === "Escape") {
        setZoom(undefined);
        setReasons(false);
        return;
      }
      if (zoom) return;
      if (reasons) {
        const index = Number(event.key) - 1;
        if (index >= 0 && index < rejectionReasons.length) {
          event.preventDefault();
          void decide("rejected", rejectionReasons[index][0]);
        }
        return;
      }
      const action = event.key.toLowerCase();
      if (!item) return;
      if (["a", "r", "u", "g", "arrowleft", "arrowright"].includes(action))
        event.preventDefault();
      if (action === "a") void decide("approved");
      if (action === "r") setReasons(true);
      if (action === "u") void decide("uncertain");
      if (action === "g") openGroup();
      if (action === "arrowleft" && item.position > 0)
        void cursor(item.position - 1);
      if (action === "arrowright") void nextPending();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const candidate = item?.candidate;
  return (
    <main className="community-review">
      <header className="community-review-header">
        <div>
          <h1>Проверка сообществ</h1>
          <p>{batch?.name}</p>
        </div>
        <div>
          <strong>{batch?.reviewer_name || "Выберите reviewer"}</strong>
          <p>
            {batch?.reviewed_by_you || 0} / {batch?.total || 0} проверено
          </p>
        </div>
        <Link to={`/discovery?grid=${batch?.grid_id || ""}`}>
          Candidate Inbox
        </Link>
      </header>
      {!batch?.reviewer_id && batch && (
        <label>
          Reviewer
          <select
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
          >
            <option value="">Выберите себя</option>
            {batch.reviewers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <progress
        aria-label="Прогресс проверки"
        value={batch?.reviewed_by_you || 0}
        max={batch?.total || 1}
      />
      {error && <p role="alert">{error} При конфликте обновите страницу.</p>}
      {notice && <p role="status">{notice}</p>}
      {batch && (
        <nav aria-label="Фильтр проверки">
          {modes.map(([mode, label]) => (
            <button
              key={mode}
              aria-pressed={batch.filter === mode}
              disabled={busy || (!batch.reviewer_id && !reviewer)}
              onClick={() => void cursor(null, mode)}
            >
              {label}
            </button>
          ))}
        </nav>
      )}
      {item && candidate ? (
        <>
          <section className="community-review-context">
            <h2>
              {candidate.name}{" "}
              <a href={candidate.url} target="_blank" rel="noopener noreferrer">
                Открыть VK ↗
              </a>
            </h2>
            <p>
              Позиция {item.position + 1} / {batch?.total} ·{" "}
              {candidate.suggested_category || "Категория не определена"}
            </p>
            <p
              className={
                candidate.reason.region_status === "mismatch"
                  ? "community-region-warning"
                  : ""
              }
            >
              <strong>Регион: </strong>
              {candidate.region?.display_name || "не подтверждён"}
              {candidate.region &&
                ` · коды ${(candidate.region.region_codes || candidate.region.vehicle_codes).join(", ")}`}{" "}
              ·{" "}
              {candidate.reason.region_status === "same_region"
                ? "подтверждён тот же регион"
                : candidate.reason.region_status === "mismatch"
                  ? "НЕСОВПАДЕНИЕ РЕГИОНА"
                  : candidate.reason.region_status === "not_required"
                    ? "регион не ограничен"
                    : "нет подтверждения"}
            </p>
            <p>
              {candidate.confidence === "high"
                ? "Рекомендован системой"
                : "Требует проверки"}{" "}
              · Score {pct(candidate.final_score)} ·{" "}
              {item.decision
                ? `Ваше решение: ${modes.find(([v]) => v === item.decision)?.[1]}`
                : "Вы ещё не проверяли"}
              {item.reason &&
                ` · ${rejectionReasons.find(([v]) => v === item.reason)?.[1]}`}
            </p>
          </section>
          <div className="community-review-photos">
            {candidate.previews.map((p, i) => (
              <div key={p}>
                {failed.has(p) ? (
                  <p>Превью {i + 1} истекло в кэше.</p>
                ) : (
                  <button
                    aria-label={`Увеличить фото ${i + 1}`}
                    onClick={() => setZoom(imageUrl(p))}
                  >
                    <img
                      src={imageUrl(p)}
                      alt={`Фото сообщества ${i + 1}`}
                      onError={() => setFailed((old) => new Set([...old, p]))}
                    />
                  </button>
                )}
              </div>
            ))}
          </div>
          {!candidate.previews.length && (
            <p>
              При анализе не получены пригодные фото. Откройте VK для визуальной
              проверки.
            </p>
          )}
          <section className="community-review-evidence">
            <h3>Почему найдено</h3>
            <p>Запросы: {candidate.found_by_queries.join(" · ")}</p>
            <p>
              Существующих seed-сообществ: {item.seed_count}. Различных
              запросов: {candidate.found_by_queries.length}. Это отдельные виды
              свидетельств.
            </p>
            <div>
              {item.seeds.map((s) => (
                <a
                  key={s.url}
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {s.name} ↗{" "}
                </a>
              ))}
            </div>
            <dl>
              {[
                ["Тематика", candidate.topic_score],
                ["Визуальное сходство", candidate.visual_score],
                ["Регион", candidate.region_score],
                ["Активность", candidate.activity_score],
                ["Граф запросов", candidate.graph_score],
              ].map(([name, value]) => (
                <div key={String(name)}>
                  <dt>{name}</dt>
                  <dd>{pct(value as number | null)}</dd>
                </div>
              ))}
            </dl>
            <p>
              Активность: {candidate.reason.activity?.state || "нет данных"} ·
              За 30 дней: {candidate.reason.activity?.posts_last_30d ?? "N/A"} ·
              За 90 дней: {candidate.reason.activity?.posts_last_90d ?? "N/A"}.
              Счётчики — нижняя граница по выборке.
            </p>
            <h3>Ближайшие существующие сообщества</h3>
            {candidate.reason.closest_communities?.length ? (
              candidate.reason.closest_communities.map((s) => (
                <p key={s.url}>
                  <a href={s.url} target="_blank" rel="noopener noreferrer">
                    {s.name} ↗
                  </a>{" "}
                  · {pct(s.similarity)}
                </p>
              ))
            ) : (
              <p>Нет пригодного визуального сравнения.</p>
            )}
          </section>
          <section className="community-review-actions" aria-label="Решение">
            <button
              disabled={busy || batch?.state !== "open"}
              onClick={() => void decide("approved")}
            >
              Подходит
            </button>
            <button
              disabled={busy || batch?.state !== "open"}
              onClick={() => setReasons(true)}
            >
              Не подходит
            </button>
            <button
              disabled={busy || batch?.state !== "open"}
              onClick={() => void decide("uncertain")}
            >
              Не уверен
            </button>
          </section>
          {reasons && (
            <section
              role="dialog"
              aria-modal="true"
              aria-label="Причина отказа"
              className="community-reasons"
            >
              <p>Причина необязательна</p>
              {rejectionReasons.map(([reason, label], i) => (
                <button
                  key={reason}
                  disabled={busy}
                  onClick={() => void decide("rejected", reason)}
                >
                  {i + 1}. {label}
                </button>
              ))}
              <button disabled={busy} onClick={() => void decide("rejected")}>
                Без причины
              </button>
              <button disabled={busy} onClick={() => setReasons(false)}>
                Отмена
              </button>
            </section>
          )}
          {item.human_status === "approved" && !item.grid_added && (
            <p>
              Подтверждено человеком.{" "}
              <Link
                to={`/discovery?grid=${batch?.grid_id}&status=human_approved`}
              >
                Добавить в сетку с подтверждением →
              </Link>
            </p>
          )}
          <footer>
            <button
              disabled={busy || item.position === 0}
              onClick={() => void cursor(item.position - 1)}
            >
              ← Предыдущее
            </button>
            <button disabled={busy} onClick={() => void nextPending()}>
              Следующее непроверенное →
            </button>
          </footer>
        </>
      ) : batch?.reviewer_id || reviewer ? (
        <p className="community-review-empty">
          В этом фильтре больше нет сообществ. Можно выбрать «Все» и
          пересмотреть решения.
        </p>
      ) : (
        <p>Для проверки выберите себя.</p>
      )}
      <p className="community-review-hints">
        A — подходит · R — не подходит · U — не уверен · G — открыть VK · ← —
        предыдущее · → — следующее непроверенное · 1–6 — причина отказа
      </p>
      {zoom && (
        <div
          className="community-review-lightbox"
          role="dialog"
          aria-label="Фото целиком"
          onClick={() => setZoom(undefined)}
        >
          <button onClick={() => setZoom(undefined)}>Закрыть</button>
          <img src={zoom} alt="Фото целиком" />
        </div>
      )}
    </main>
  );
}
