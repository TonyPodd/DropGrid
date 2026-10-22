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
import { date, genderLabel, useLoad, State, Pager } from "./shared";
import { personaApi } from "./api/persona";
import { PersonaSetup, personaReady, type PhotoCounts } from "./persona";
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
  operator_rating?: string | null;
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
  repeats_exhausted: "Новых фото не нашлось: все варианты уже предлагались",
  download_error: "Не удалось скачать фото",
  storage_error: "Недоступно хранилище фото",
  category_archive_error: "Недоступен архив категории",
  other: "Подготовка не завершилась",
};
// One account suggests up to this many posts per campaign.
const POSTS_PER_ACCOUNT = 100;
type Ranking = {
  choices: number;
  minimum: number;
  retrain_interval?: number;
  next_training_at?: number;
  training?: boolean;
  reviewers?: Record<string, number>;
  active?: {
    trained_at: string | null;
    top1?: number | null;
    baseline_top1?: number | null;
  } | null;
  last_training?: {
    trained_at: string | null;
    outcome?: string | null;
    learned_top1?: number | null;
    baseline_top1?: number | null;
    champion_top1?: number | null;
  } | null;
};
const percent = (value?: number | null) =>
  typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
function rankerSummary(r: Ranking) {
  if (r.active)
    return `Подбор фото: обученная модель · угадывает ваш выбор в ${percent(r.active.top1)} случаев (формула — ${percent(r.active.baseline_top1)})`;
  if (r.choices < r.minimum)
    return `Подбор фото: базовая формула · выборов для обучения модели: ${r.choices} / ${r.minimum}`;
  return "Подбор фото: базовая формула · модель пока не обогнала её";
}
function trainingOutcome(t: NonNullable<Ranking["last_training"]>) {
  const fresh = `новая версия угадала ${percent(t.learned_top1)}`;
  switch (t.outcome) {
    case "promoted":
      return `${fresh} и стала рабочей.`;
    case "kept":
      return `${fresh}, рабочая — ${percent(t.champion_top1)}: оставлена рабочая.`;
    case "fallback":
      return `рабочая модель стала хуже базовой формулы (${percent(t.champion_top1)} против ${percent(t.baseline_top1)}), подбор вернулся на формулу.`;
    case "not_better":
      return `${fresh}, базовая формула — ${percent(t.baseline_top1)}: пока не лучше формулы.`;
    case "insufficient_validation":
      return "мало свежих решений для проверки, ничего не поменялось.";
    default:
      return `${fresh}.`;
  }
}
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
    [notice, setNotice] = useState(""),
    [ids, setIds] = useState<string[] | null>(null),
    [opened, setOpened] = useState<string | null>(null),
    [attentionOnly, setAttentionOnly] = useState(false),
    [dryRunUrl, setDryRunUrl] = useState(""),
    [autoSend, setAutoSend] = useState(!!campaign.auto_send);
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
  const persona = useLoad(
    (signal) => personaApi.campaign(campaign.id, signal),
    [campaign.id],
  );
  const [girlIds, setGirlIds] = useState<string[] | null>(null);
  const [girlPhotos, setGirlPhotos] = useState<Record<string, PhotoCounts>>(
    {},
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
        gender_shortfall?: number;
        category_unassigned?: number;
      }>(`/campaigns/${campaign.id}/readiness-report`, { signal }),
    [campaign.id, revision],
  );
  const ranking = useLoad(
    (signal) => request<Ranking>("/photo-ranking", { signal }),
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
  const usable =
    accounts.data?.filter(
      (a) => a.status === "active" && a.token_configured && a.vk_user_id,
    ) ?? [];
  const girls =
    girlIds ?? persona.data?.accounts?.map((a) => a.account_id) ?? [];
  const photoCounts: Record<string, PhotoCounts> = {
    ...Object.fromEntries(
      persona.data?.accounts?.map((a) => [
        a.account_id,
        { selfie: a.selfies, other: a.others },
      ]) ?? [],
    ),
    ...girlPhotos,
  };
  const dating = !!persona.data?.targets;
  // Girl accounts lead the queue: their dating/selfie publics, then the rest.
  const queue = dating
    ? [...girls, ...selected.filter((id) => !girls.includes(id))]
    : selected;
  const previousActive = useRef(false);
  const active = job.data && ["queued", "running"].includes(job.data.state);
  useEffect(() => {
    if (previousActive.current && !active) onChanged();
    previousActive.current = !!active;
  }, [active, onChanged]);
  useEffect(() => {
    if (!active && campaign.status !== "ready") return;
    // Follow preparation closely; while reviewing, refresh rarely and quietly.
    const timer = setInterval(
      () => setRevision((n) => n + 1),
      active ? 3000 : 30000,
    );
    return () => clearInterval(timer);
  }, [active, campaign.status]);
  async function action(path: string, body?: unknown, method = "POST") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await request<{ skipped_disliked?: number }>(path, {
        method,
        body,
      });
      if (result?.skipped_disliked)
        setNotice(
          `Не подтверждено фото с 👎: ${result.skipped_disliked}. Замените их другим вариантом.`,
        );
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
  const neededAccounts = Math.ceil(campaign.community_count / POSTS_PER_ACCOUNT);
  const enoughAccounts = queue.length >= neededAccounts;
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
      {!campaign.is_dry_run &&
        campaign.status === "ready" &&
        (campaign.auto_send ? (
          <p role="status">
            Режим: подобрать фото и предложить посты. Бот начнёт отправку сам,
            как только всё будет готово; что мешает — в блоке «Отправка».
          </p>
        ) : (
          <p role="status">
            Режим: только подбор фото — посты не отправляются.
          </p>
        ))}
      {!!readiness.data && readiness.data.scope > 0 && (
        <div className="activity-card">
          <p>Нужно отправить: {readiness.data.scope}</p>
          <p>Доступная ёмкость: {readiness.data.real_capacity}</p>
          <p>Не хватает ёмкости: {readiness.data.capacity_shortfall}</p>
          {!!readiness.data.gender_shortfall && (
            <p>
              Нет аккаунта нужного пола: {readiness.data.gender_shortfall}{" "}
              пабликов. Подключите аккаунт под эти категории (например, женский
              для женских).
            </p>
          )}
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
      {ranking.data && <p>{rankerSummary(ranking.data)}</p>}
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
      {notice && <p role="status">{notice}</p>}
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
            {dating && persona.data && (
              <PersonaSetup
                summary={persona.data}
                accounts={usable}
                girls={girls}
                photos={photoCounts}
                onGirls={setGirlIds}
                onAccountsChanged={accounts.reload}
                onPhotos={(id, n) =>
                  setGirlPhotos((current) => ({ ...current, [id]: n }))
                }
              />
            )}
            {!campaign.is_dry_run && (
              <fieldset className="send-mode">
                <legend>Что сделать</legend>
                <label>
                  <input
                    type="radio"
                    name="send-mode"
                    checked={!autoSend}
                    onChange={() => setAutoSend(false)}
                  />
                  Только подобрать фото — посты не отправляются
                </label>
                <label>
                  <input
                    type="radio"
                    name="send-mode"
                    checked={autoSend}
                    onChange={() => setAutoSend(true)}
                  />
                  Подобрать фото и предложить посты
                </label>
                {autoSend && (
                  <p role={enoughAccounts ? undefined : "alert"}>
                    Нужно аккаунтов: ⌈{campaign.community_count} /{" "}
                    {POSTS_PER_ACCOUNT}⌉ = {neededAccounts}, выбрано:{" "}
                    {queue.length}.{" "}
                    {enoughAccounts
                      ? campaign.photo_review_mode === "REVIEW_BEFORE_SEND"
                        ? "Бот сам начнёт предлагать посты, когда вы проверите все фото."
                        : "Бот сам начнёт предлагать посты, как только подберёт фото."
                      : "Подключите и отметьте ещё аккаунты."}
                  </p>
                )}
              </fieldset>
            )}
            <button
              className="primary"
              disabled={
                !queue.length ||
                !personaReady(persona.data, usable, girls, photoCounts) ||
                (autoSend && !enoughAccounts)
              }
              onClick={() =>
                void action(`/campaigns/${campaign.id}/prepare-workflow`, {
                  account_ids: queue,
                  ...(dating ? { persona_account_ids: girls } : {}),
                  auto_send: autoSend && !campaign.is_dry_run,
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
                      {chosen?.operator_rating === "dislike" &&
                        !row.confirmed && (
                          <p className="short-error">
                            Вы поставили 👎 этому фото. Выберите другое в
                            «Заменить / альтернативы» или выберите это снова,
                            чтобы снять 👎.
                          </p>
                        )}
                      {!row.confirmed && campaign.status === "ready" && (
                        <div className="actions">
                          <button
                            disabled={
                              busy ||
                              !chosen ||
                              chosen.operator_rating === "dislike"
                            }
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
                                {c.operator_rating === "dislike"
                                  ? "Выбрать (снять 👎)"
                                  : "Выбрать это фото"}
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
              Модель одна и учится сама на подтверждённых выборах: первый раз
              после {ranking.data.minimum}, затем каждые{" "}
              {ranking.data.retrain_interval ?? 25}. Новая версия заменяет
              рабочую, только если на одних и тех же свежих решениях угадывает
              ваш выбор не хуже неё и лучше базовой формулы.
            </p>
            <p>
              Подтверждённых выборов: {ranking.data.choices}.{" "}
              {ranking.data.training
                ? "Идёт обучение…"
                : `Следующее обучение — на ${Math.max(
                    ranking.data.next_training_at ?? ranking.data.minimum,
                    ranking.data.minimum,
                  )} выборах.`}
            </p>
            {ranking.data.active ? (
              <p>
                Сейчас фото подбирает обученная модель
                {ranking.data.active.trained_at
                  ? ` от ${date(ranking.data.active.trained_at)}`
                  : ""}
                : угадывает ваш выбор в {percent(ranking.data.active.top1)}{" "}
                случаев, базовая формула —{" "}
                {percent(ranking.data.active.baseline_top1)}.
              </p>
            ) : (
              <p>Сейчас фото подбирает базовая формула.</p>
            )}
            {ranking.data.last_training && (
              <p>
                Последнее обучение
                {ranking.data.last_training.trained_at
                  ? ` ${date(ranking.data.last_training.trained_at)}`
                  : ""}
                : {trainingOutcome(ranking.data.last_training)}
              </p>
            )}
            <button
              disabled={
                busy ||
                !!ranking.data.training ||
                ranking.data.choices < ranking.data.minimum
              }
              onClick={() => void action("/photo-ranking/train")}
            >
              Обучить сейчас
            </button>
            {ranking.data.reviewers && (
              <p>
                Reviewer breakdown:{" "}
                {Object.entries(ranking.data.reviewers)
                  .map(([name, count]) => `${name}: ${count}`)
                  .join(" · ") || "Нет подтверждённых решений"}
              </p>
            )}
          </>
        )}
      </details>
    </section>
  );
}
