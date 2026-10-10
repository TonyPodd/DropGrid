import { useEffect, useState, useRef } from "react";
import {
  request,
  errorMessage,
  mediaContentUrl,
  photoPreviewContentUrl,
  referenceContentUrl,
} from "./api/client";
import { accountsApi } from "./api/accounts";
import type { Campaign } from "./api/types";
import { genderLabel, useLoad, State, Pager } from "./shared";
type Job = {
  state: string;
  stage: string;
  completed: number;
  total: number;
  error_code?: string;
  result?: { progress_stage?: string; current?: number; total?: number };
};
type Candidate = {
  rank: number;
  provider: string;
  media_asset_id: string | null;
  preview_id: string | null;
  reference_id: string | null;
  source_community_id: string;
  features: Record<string, number>;
};
type Row = {
  selection_id?: string;
  submission_id: string;
  community_id: string;
  community: string;
  media_asset_id: string | null;
  attention: string[];
  confirmed: boolean;
  proposed_rank: number;
  candidates: Candidate[];
};
type Review = {
  items: Row[];
  total: number;
  prepared: number;
  needs_attention: number;
};
const attentionNames: Record<string, string> = {
  no_compatible_core_references: "Мало референсов",
  no_candidate_embedding: "Нет визуальных признаков",
  small_candidate_pool: "Мало вариантов",
  fallback_only: "Резервный источник",
  pinterest_unavailable: "Pinterest недоступен",
  operator_disliked: "Вы поставили 👎",
  community_unavailable: "Группа недоступна",
  preparation_error: "Ошибка подготовки",
  no_candidates: "Нет подходящих кандидатов",
  no_references: "Нет совместимых референсов",
  materialization_failed: "Не удалось импортировать фото",
  dedup_exhausted: "Кандидаты исчерпаны после dedup / rotation",
  cooldown_exhausted: "Фото недавно использованы в этой группе",
  download_error: "Не удалось скачать фото",
  storage_error: "Недоступно хранилище фото",
  category_archive_error: "Недоступен архив категории",
  other: "Подготовка не завершилась",
};
function image(c: Candidate, row: Row) {
  return c.media_asset_id
    ? mediaContentUrl(c.media_asset_id)
    : c.preview_id
      ? photoPreviewContentUrl(c.preview_id)
      : c.reference_id
        ? referenceContentUrl(
            c.source_community_id || row.community_id,
            c.reference_id,
          )
        : "";
}
export function CampaignWorkflow({
  campaign,
  onChanged,
}: {
  campaign: Campaign;
  onChanged: () => void;
}) {
  const [revision, setRevision] = useState(0),
    [page, setPage] = useState(1),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [ids, setIds] = useState<string[] | null>(null),
    [opened, setOpened] = useState<string | null>(null),
    [attentionOnly, setAttentionOnly] = useState(false),
    [dryRunUrl, setDryRunUrl] = useState("");
  const accounts = useLoad((signal) => accountsApi.all(signal), []);
  const pool = useLoad(
    (signal) =>
      request<
        {
          account_id: string;
          name: string;
          quota: number;
          assigned: number;
          usable: boolean;
        }[]
      >(`/campaigns/${campaign.id}/account-pool`, { signal }),
    [campaign.id, revision],
  );
  const job = useLoad(
    (signal) =>
      request<Job | null>(`/campaigns/${campaign.id}/preparation-workflow`, {
        signal,
      }),
    [campaign.id, revision],
  );
  const review = useLoad(
    (signal) =>
      request<Review>(`/campaigns/${campaign.id}/photo-review?page=${page}`, {
        signal,
      }),
    [campaign.id, page, revision],
  );
  const readiness = useLoad(
    (signal) =>
      request<{
        scope: number;
        real_capacity: number;
        capacity_shortfall: number;
        category_unassigned?: number;
      }>(`/campaigns/${campaign.id}/readiness-report`, { signal }),
    [campaign.id, revision],
  );
  const ranking = useLoad(
    (signal) =>
      request<{
        mode: string;
        choices: number;
        minimum: number;
        promotion_ready: boolean;
        metrics: Record<string, unknown> | null;
        latest_model?: string;
        remaining?: number;
        reviewers?: Record<string, number>;
      }>("/photo-ranking", { signal }),
    [revision],
  );
  const batches = useLoad(
    (signal) =>
      request<
        { id: string; target_count: number; confirmed: number; done: number }[]
      >(`/campaigns/${campaign.id}/review-batches`, { signal }),
    [campaign.id, revision],
  );
  const selected =
    ids ??
    pool.data?.filter((a) => a.usable).map((a) => a.account_id) ??
    accounts.data
      ?.filter(
        (a) => a.status === "active" && a.token_configured && a.vk_user_id,
      )
      .map((a) => a.id) ??
    [];
  const previousActive = useRef(false);
  const active = job.data && ["queued", "running"].includes(job.data.state);
  useEffect(() => {
    if (previousActive.current && !active) onChanged();
    previousActive.current = !!active;
  }, [active, onChanged]);
  useEffect(() => {
    if (!active && campaign.status !== "ready") return;
    const timer = setInterval(() => setRevision((n) => n + 1), 3000);
    return () => clearInterval(timer);
  }, [active, campaign.status]);
  async function action(path: string, body?: unknown, method = "POST") {
    setBusy(true);
    setError("");
    try {
      await request(path, { method, body });
      setRevision((n) => n + 1);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function dryRun() {
    setBusy(true);
    setError("");
    try {
      const result = await request<{ campaign_id: string }>(
        "/campaign-dry-runs",
        {
          method: "POST",
          body: {
            grid_id: campaign.grid_id,
            track_url: campaign.track_url,
            name: `${campaign.name.slice(0, 150)} · DRY RUN`,
            photo_review_mode: campaign.photo_review_mode ?? "AUTO",
            account_ids: selected,
          },
        },
      );
      setDryRunUrl(`/campaigns/${result.campaign_id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  function approval(row: Row) {
    return {
      selection_id: row.selection_id,
      proposed_rank: row.proposed_rank,
      shown_ranks:
        opened === row.submission_id
          ? row.candidates.map((c) => c.rank)
          : [row.proposed_rank],
    };
  }
  const stage =
    campaign.status === "draft"
      ? "Настройки"
      : active
        ? "Подготовка"
        : campaign.preparation_state === "awaiting_review"
          ? "Проверка фото"
          : campaign.is_dry_run && !!review.data?.prepared
            ? "Проверка фото"
            : campaign.status === "ready"
              ? "Отправка"
              : campaign.status === "running"
                ? "Отправка"
                : campaign.status === "monitoring"
                  ? "Мониторинг"
                  : "Результаты";
  return (
    <section className="form-card">
      <h2>Сейчас: {stage}</h2>
      {campaign.is_dry_run && (
        <p role="status">
          <strong>DRY RUN · отправка запрещена</strong>. Подготовка использует
          рабочий pipeline.
        </p>
      )}
      {!!readiness.data && readiness.data.scope > 0 && (
        <div className="activity-card">
          <p>Нужно отправить: {readiness.data.scope}</p>
          <p>Доступная ёмкость: {readiness.data.real_capacity}</p>
          <p>Не хватает: {readiness.data.capacity_shortfall}</p>
          {readiness.data.capacity_shortfall > 0 && (
            <p>Подключите дополнительные аккаунты или уменьшите scope.</p>
          )}
          {!!readiness.data.category_unassigned && (
            <p role="alert">
              {readiness.data.category_unassigned} сообществ в нераспределённых
              категориях.{" "}
              <a href={`/categories?grid=${campaign.grid_id}`}>
                Распределить категории сетки
              </a>
            </p>
          )}
        </div>
      )}
      {ranking.data && (
        <p>
          Manual decisions: {ranking.data.choices} / {ranking.data.minimum} ·
          Latest model: {ranking.data.latest_model ?? "deterministic"}
        </p>
      )}
      {dryRunUrl && (
        <p>
          <a href={dryRunUrl}>Открыть отдельную dry-run кампанию</a>
        </p>
      )}
      <p>
        Настройки → Подготовка →{" "}
        {campaign.photo_review_mode === "REVIEW_BEFORE_SEND"
          ? "Проверка фото → "
          : ""}
        Отправка → Мониторинг → Результаты
      </p>
      {error && <p role="alert">{error}</p>}
      {["draft", "ready"].includes(campaign.status) && (
        <details open={stage === "Настройки"}>
          <summary>Настройки</summary>
          <fieldset disabled={busy || !!active}>
            <legend>Аккаунты для отправки</legend>
            <State {...accounts} retry={accounts.reload}>
              {accounts.data
                ?.filter(
                  (a) =>
                    a.status === "active" && a.token_configured && a.vk_user_id,
                )
                .map((a) => (
                  <label key={a.id}>
                    <input
                      type="checkbox"
                      checked={selected.includes(a.id)}
                      onChange={(e) =>
                        setIds(
                          e.target.checked
                            ? [...selected, a.id]
                            : selected.filter((id) => id !== a.id),
                        )
                      }
                    />
                    {a.name} · {genderLabel(a.gender_tag)} · квота{" "}
                    {pool.data?.find((row) => row.account_id === a.id)?.quota ??
                      a.campaign_send_quota ??
                      100}
                    {selected.includes(a.id) &&
                      ` · №${selected.indexOf(a.id) + 1} в очереди`}
                  </label>
                ))}
            </State>
            <button
              className="primary"
              disabled={!selected.length}
              onClick={() =>
                void action(`/campaigns/${campaign.id}/prepare-workflow`, {
                  account_ids: selected,
                })
              }
            >
              Подготовить кампанию
            </button>
          </fieldset>
          {!campaign.is_dry_run && (
            <button
              disabled={busy || !!active || !selected.length}
              onClick={() => void dryRun()}
            >
              Создать отдельный dry run
            </button>
          )}
          <p>
            {campaign.photo_review_mode === "REVIEW_BEFORE_SEND"
              ? "Проверка фото перед отправкой"
              : "Фото выбираются автоматически; preview необязателен."}
          </p>
        </details>
      )}
      <details open={!!active || stage === "Подготовка"}>
        <summary>Подготовка</summary>
        {job.data && (
          <article className="activity-card" role="status">
            <h3>
              {job.data.state === "ready"
                ? "Подготовка завершена"
                : job.data.stage === "communities"
                  ? "Изучаем группы и референсы"
                  : "Ищем фото и назначаем медиа"}
            </h3>
            {job.data.result?.progress_stage && (
              <p>
                {(
                  {
                    pinterest_search: "Ищем фото Pinterest",
                    pinterest_materializing: "Проверяем изображения",
                    ranking: "Сравниваем со стилем",
                    assigning: "Назначаем фото",
                  } as Record<string, string>
                )[job.data.result.progress_stage] ?? "Подбираем фото"}{" "}
                · {job.data.result.current ?? 0} /{" "}
                {job.data.result.total ?? "…"}
              </p>
            )}
            <p>
              {job.data.completed} / {job.data.total} групп
            </p>
            {active && (
              <progress value={job.data.completed} max={job.data.total || 1} />
            )}{" "}
            {job.data.error_code && (
              <p>Подготовка не завершена. Повторите вручную.</p>
            )}
          </article>
        )}
        {pool.data && (
          <div className="metrics">
            {pool.data.map((a) => (
              <div key={a.account_id}>
                <span>{a.name}</span>
                <strong>
                  {a.assigned} / {a.quota}
                </strong>
              </div>
            ))}
          </div>
        )}
      </details>
      {!!review.data?.total && (
        <details className="validation-entry" open={stage === "Проверка фото"}>
          <summary>Проверка фото</summary>
          <p>
            Фото подготовлены: {review.data.prepared} / {review.data.total}
          </p>
          <p>
            Ошибки: {review.data.total - review.data.prepared} · Требуют
            внимания: {review.data.needs_attention}
          </p>
          {review.data.total > review.data.prepared && (
            <button
              disabled={busy || !!active}
              onClick={() =>
                void action(`/campaigns/${campaign.id}/retry-failed-photos`)
              }
            >
              Повторить {review.data.total - review.data.prepared}
            </button>
          )}
          {Array.isArray(batches.data) && batches.data.length ? (
            batches.data.map((b) => (
              <div key={b.id}>
                <p>
                  Validation batch: {b.done} / {b.target_count}
                </p>
                <a className="button-link" href={`/review/${b.id}`}>
                  Начать проверку фото
                </a>
              </div>
            ))
          ) : (
            <button
              disabled={busy || !!active || !review.data.prepared}
              onClick={() =>
                void action(`/campaigns/${campaign.id}/review-batches`, {
                  target_count: Math.min(150, review.data?.prepared ?? 0),
                })
              }
            >
              Создать проверку фото
            </button>
          )}
          <p>
            Review — быстрая проверка подборки. Photo Lab — исследование одной
            группы.
          </p>
          <details>
            <summary>Диагностика snapshots</summary>
            <h3>
              Фото: подготовлено {review.data.prepared} / {review.data.total} ·
              требуют внимания {review.data.needs_attention}
            </h3>
            <label>
              <input
                type="checkbox"
                checked={attentionOnly}
                onChange={(e) => setAttentionOnly(e.target.checked)}
              />
              Показывать только требующие внимания на странице
            </label>
            <p>Ваш выбор будет использоваться для улучшения автоподбора.</p>
            {campaign.status === "ready" && (
              <button
                disabled={busy || !!active || !review.data.prepared}
                onClick={() =>
                  void action(`/campaigns/${campaign.id}/photo-approve-all`, {
                    reviews: review.data?.items
                      .filter((row) => !row.confirmed && row.candidates.length)
                      .map((row) => ({
                        submission_id: row.submission_id,
                        ...approval(row),
                      })),
                  })
                }
              >
                Подтвердить все выбранные
              </button>
            )}
            <div className="review-grid">
              {review.data.items
                .filter(
                  (row) =>
                    !attentionOnly ||
                    row.attention.length ||
                    !row.media_asset_id,
                )
                .map((row) => {
                  const chosen = row.candidates.find(
                    (c) => c.rank === row.proposed_rank,
                  );
                  return (
                    <article className="review-card" key={row.submission_id}>
                      <h4>{row.community}</h4>
                      {chosen ? (
                        <img
                          src={image(chosen, row)}
                          alt={`Выбрано: ${row.community}`}
                          loading="lazy"
                        />
                      ) : row.media_asset_id ? (
                        <img
                          src={mediaContentUrl(row.media_asset_id)}
                          alt={row.community}
                        />
                      ) : (
                        <p>Фото не назначено</p>
                      )}
                      <p>
                        {chosen?.provider ?? "Library"} ·{" "}
                        {row.confirmed ? "Подтверждено" : "Выбрано системой"}
                      </p>
                      {row.attention.map((flag) => (
                        <small key={flag}>
                          {attentionNames[flag] ?? "Требует проверки"} ·{" "}
                        </small>
                      ))}
                      {!row.confirmed && campaign.status === "ready" && (
                        <div className="actions">
                          <button
                            disabled={busy || !chosen}
                            onClick={() =>
                              void action(
                                `/submissions/${row.submission_id}/photo-approve`,
                                approval(row),
                              )
                            }
                          >
                            Подтвердить
                          </button>
                          <button
                            onClick={() =>
                              setOpened(
                                opened === row.submission_id
                                  ? null
                                  : row.submission_id,
                              )
                            }
                          >
                            Заменить / альтернативы
                          </button>
                          {chosen &&
                            ["like", "dislike"].map((rating) => (
                              <button
                                key={rating}
                                disabled={busy}
                                aria-label={
                                  rating === "like" ? "Нравится" : "Не нравится"
                                }
                                onClick={() =>
                                  void action(
                                    `/submissions/${row.submission_id}/photo-choice`,
                                    {
                                      rank: chosen.rank,
                                      rating,
                                      shown_ranks:
                                        opened === row.submission_id
                                          ? row.candidates.map((c) => c.rank)
                                          : [chosen.rank],
                                    },
                                    "PUT",
                                  )
                                }
                              >
                                {rating === "like" ? "👍" : "👎"}
                              </button>
                            ))}
                        </div>
                      )}
                      {opened === row.submission_id && (
                        <div className="reference-grid">
                          {row.candidates.map((c) => (
                            <figure
                              key={c.rank}
                              data-selected={c.rank === row.proposed_rank}
                            >
                              <img
                                src={image(c, row)}
                                alt={`Вариант ${c.rank}`}
                                loading="lazy"
                              />
                              <figcaption>
                                {c.provider} · итог{" "}
                                {Number(c.features.final_score ?? 0).toFixed(3)}
                              </figcaption>
                              <button
                                aria-pressed={c.rank === row.proposed_rank}
                                disabled={busy}
                                onClick={() =>
                                  void action(
                                    `/submissions/${row.submission_id}/photo-choice`,
                                    {
                                      rank: c.rank,
                                      shown_ranks: row.candidates.map(
                                        (c) => c.rank,
                                      ),
                                    },
                                    "PUT",
                                  )
                                }
                              >
                                Выбрать это фото
                              </button>
                            </figure>
                          ))}
                        </div>
                      )}
                    </article>
                  );
                })}
            </div>
            <Pager
              page={page}
              hasNext={page * 25 < review.data.total}
              onPage={setPage}
            />
          </details>
        </details>
      )}
      <details>
        <summary>Обучение автоподбора / диагностика</summary>
        {ranking.data && (
          <>
            <p>
              Подтверждённых выборов: {ranking.data.choices} /{" "}
              {ranking.data.minimum}. Режим: {ranking.data.mode}. До явного
              включения используется исходное ранжирование.
            </p>
            <button
              disabled={busy || ranking.data.choices < ranking.data.minimum}
              onClick={() => void action("/photo-ranking/train")}
            >
              Обновить модель
            </button>
            <button
              disabled={busy || !ranking.data.promotion_ready}
              onClick={() =>
                void action("/photo-ranking", { mode: "learned" }, "PUT")
              }
            >
              Включить проверенную модель
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void action("/photo-ranking", { mode: "deterministic" }, "PUT")
              }
            >
              Исходное ранжирование
            </button>
            {ranking.data.reviewers && (
              <p>
                Reviewer breakdown:{" "}
                {Object.entries(ranking.data.reviewers)
                  .map(([name, count]) => `${name}: ${count}`)
                  .join(" · ") || "Нет подтверждённых решений"}
              </p>
            )}
            {ranking.data.metrics && (
              <dl>
                {Object.entries(ranking.data.metrics).map(([key, value]) => (
                  <div key={key}>
                    <dt>
                      {(
                        {
                          train: "Training decisions",
                          validation: "Validation decisions",
                          baseline_top1: "Baseline top-1 agreement",
                          learned_top1: "Learned top-1 agreement",
                          promotion_ready: "Promotion ready",
                        } as Record<string, string>
                      )[key] ?? key}
                    </dt>
                    <dd>
                      {key === "promotion_ready"
                        ? value
                          ? "yes"
                          : "no"
                        : ["baseline_top1", "learned_top1"].includes(key) &&
                            typeof value === "number"
                          ? `${(value * 100).toFixed(1)}%`
                          : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}
      </details>
    </section>
  );
}
