import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  request,
  errorMessage,
  mediaContentUrl,
  referenceContentUrl,
  photoPreviewContentUrl,
} from "./api/client";
import { CommunityActivities } from "./activity";
import { Pager, State, date, useLoad } from "./shared";

type ProfileInput = {
  desired_content: string | null;
  avoid_content: string | null;
  style_notes: string | null;
  reference_target_count: number;
  archive_reuse_enabled: boolean;
  archive_reuse_min_age_days: number;
  archive_reuse_max_age_days: number;
};
type Profile = ProfileInput & {
  community_id: string;
  reference_count: number;
  reference_core_count?: number;
  reference_aux_count?: number;
  references_last_synced_at: string | null;
  archive_discovered_count?: number;
  archive_eligible_count?: number;
  archive_oldest_eligible_at?: string | null;
  archive_newest_eligible_at?: string | null;
};
type StudyJob = {
  id: string;
  state: string;
  elapsed_seconds: number;
  progress: Record<string, number>;
  error_code: string | null;
  result: { warnings: string[] } | null;
};
type Reference = {
  reference_cluster_size?: number | null;
  reference_role_reason?: string;
  reference_role?: string;
  reference_density?: number | null;
  reference_nearest_similarity?: number | null;
  id: string;
  posted_at: string;
  vk_post_id: number;
  embedding_model: string | null;
};
type Score = {
  provider?: string;
  source_community_id?: string | null;
  source_post_id?: number | null;
  vk_photo_owner_id?: number | null;
  vk_photo_id?: number | null;
  preview_id?: string | null;
  publication_eligible?: boolean;
  pin_url?: string | null;
  title?: string;
  top_references?: { reference_id: string; similarity: number }[];
  media_asset_id: string | null;
  reference_id?: string | null;
  source?: string;
  source_identity?: string;
  original_posted_at?: string | null;
  age_days?: number | null;
  age_reuse_score?: number;
  retrieval_queries?: string[];
  base_score: number;
  visual_score: number | null;
  final_score: number;
};
type Preview = {
  source_contributions?: Record<string, Record<string, number>>;
  pixabay_status?: string;
  pixabay_requests?: number;
  best_matches?: Score[];
  category_library?: Score[];
  category_library_stats?: Record<string, unknown>;
  pinterest_status?: string;
  pinterest_queries?: string[];
  pinterest_retrieved?: number;
  pinterest_embedded?: number;
  pinterest?: Score[];
  community_ranked_pinterest?: Score[];
  visual_engine?: {
    enabled: boolean;
    model: string | null;
    compatible_reference_count: number;
    candidate_embeddings_available: number;
    active: boolean;
    reason_if_inactive: string | null;
  };
  timings_ms?: Record<string, number> | null;
  category?: string | null;
  comment?: string | null;
  content_hint?: string | null;
  generated_queries?: string[];
  category_only: Score[];
  community_aware: Score[];
  mixed_source?: Score[];
  archive_age_strata?: {
    min_age_days: number;
    max_age_days: number;
    candidate_count: number;
  }[];
  archive_shortlist?: { source_identity: string; age_days: number }[];
  warnings: string[];
};
type PhotoJob = {
  id: string;
  kind: string;
  state: string;
  stage: string;
  current: number;
  total: number | null;
  elapsed_seconds: number;
  counters: Record<string, number>;
  result:
    | Preview
    | {
        posts_scanned: number;
        candidates_discovered: number;
        candidates_existing: number;
        warnings: string[];
      }
    | null;
  error_code: string | null;
};
type Providers = {
  include_pixabay: boolean;
  include_archive: boolean;
  include_category_library: boolean;
  include_pinterest: boolean;
  include_library: boolean;
};
const defaults: ProfileInput = {
  desired_content: null,
  avoid_content: null,
  style_notes: null,
  reference_target_count: 100,
  archive_reuse_enabled: false,
  archive_reuse_min_age_days: 180,
  archive_reuse_max_age_days: 540,
};
export const communityVisualApi = {
  profile: (id: string, signal?: AbortSignal) =>
    request<Profile>(`/communities/${id}/content-profile`, { signal }),
  save: (id: string, body: ProfileInput) =>
    request<Profile>(`/communities/${id}/content-profile`, {
      method: "PUT",
      body,
    }),
  references: (id: string, page: number, signal?: AbortSignal) =>
    request<{ items: Reference[]; total: number }>(
      `/communities/${id}/references?page=${page}&page_size=20`,
      { signal },
    ),
  sync: (id: string) =>
    request<StudyJob>(`/communities/${id}/references/jobs`, {
      method: "POST",
      body: {},
    }),
  study: (id: string, signal?: AbortSignal) =>
    request<StudyJob | null>(`/communities/${id}/references/jobs/latest`, {
      signal,
    }),
  metadata: (id: string, signal?: AbortSignal) =>
    request<{ name: string | null; domain: string; category: string | null }>(
      `/communities/${id}`,
      { signal },
    ),
  latest: (id: string, kind: string, signal?: AbortSignal) =>
    request<PhotoJob | null>(
      `/communities/${id}/photo-jobs/latest?kind=${kind}`,
      { signal },
    ),
  archive: (id: string) =>
    request<PhotoJob>(`/communities/${id}/photo-jobs`, {
      method: "POST",
      body: { kind: "archive", archive: { max_pages: 20 } },
    }),
  preview: (id: string, gridId?: string, providers?: Providers) =>
    request<PhotoJob>(`/communities/${id}/photo-jobs`, {
      method: "POST",
      body: {
        kind: "preview",
        preview: {
          candidate_limit: 8,
          diagnostics: true,
          ...(gridId ? { grid_id: gridId } : {}),
          ...providers,
        },
      },
    }),
};

function CandidateScores({
  title,
  items,
  communityId,
}: {
  title: string;
  items: Score[];
  communityId: string;
}) {
  return (
    <section>
      <h3>{title}</h3>
      <div className="reference-grid">
        {items.map((item, rank) => (
          <figure
            key={
              item.source_identity ?? item.media_asset_id ?? item.reference_id
            }
          >
            <img
              src={
                item.preview_id
                  ? photoPreviewContentUrl(item.preview_id)
                  : item.reference_id
                    ? referenceContentUrl(
                        item.source_community_id ?? communityId,
                        item.reference_id,
                      )
                    : mediaContentUrl(item.media_asset_id ?? "")
              }
              alt="Кандидат фото"
              loading="lazy"
            />
            <figcaption>
              {item.publication_eligible === false && (
                <strong>
                  Только предпросмотр
                  <br />
                </strong>
              )}
              {item.pin_url && (
                <a href={item.pin_url} target="_blank" rel="noreferrer">
                  Pin · {item.title || item.source_identity}
                </a>
              )}
              #{rank + 1} · {item.source ?? "pixabay"} · {item.source_identity}
              <br />
              {item.original_posted_at && (
                <>
                  Опубликовано: {date(item.original_posted_at)} · Возраст:{" "}
                  {item.age_days?.toFixed(0)} дней
                  <br />
                </>
              )}
              age/reuse: {(item.age_reuse_score ?? 0).toFixed(3)} · base:{" "}
              {item.base_score.toFixed(3)} · visual:{" "}
              {item.visual_score?.toFixed(3) ?? "—"} · final:{" "}
              {item.final_score.toFixed(3)}
              <br />
              Queries:{" "}
              {(item.retrieval_queries ?? []).join(" · ") ||
                "library / archive"}
              {(item.top_references ?? []).length > 0 && (
                <div>
                  Ближайшие CORE references:
                  {item.top_references?.map((ref) => (
                    <span key={ref.reference_id}>
                      <img
                        style={{ width: 54, height: 54, objectFit: "cover" }}
                        src={referenceContentUrl(communityId, ref.reference_id)}
                        alt="Ближайший CORE reference"
                      />{" "}
                      {ref.similarity.toFixed(3)}{" "}
                    </span>
                  ))}
                </div>
              )}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

const sources: Record<string, string> = {
  pinterest: "Pinterest",
  vk_category_archive: "VK category library",
  vk_archive: "Own archive",
  pixabay: "Pixabay",
  library: "Library",
};
function imageUrl(item: Score, id: string) {
  return item.preview_id
    ? photoPreviewContentUrl(item.preview_id)
    : item.reference_id
      ? referenceContentUrl(item.source_community_id ?? id, item.reference_id)
      : mediaContentUrl(item.media_asset_id ?? "");
}
function styleScore(item: Score) {
  return item.visual_score == null
    ? "Стиль не оценён"
    : `Совпадение со стилем: ${Math.round(Math.max(0, Math.min(1, item.visual_score)) * 100)}%`;
}
const roleReasons: Record<string, string> = {
  duplicate: "Повтор похожего фото",
  small_cluster: "Небольшой визуальный кластер",
  coherent_cluster: "Плотная группа похожих фото",
  low_density: "Меньше сходства с основным стилем",
  small_reference_fallback: "Пока мало уникальных примеров",
  incompatible_embedding: "Нет совместимых визуальных признаков",
  density_fallback: "Отбор по плотности сходства",
};
function PhotoProgress({ job }: { job: PhotoJob }) {
  const stages: Record<string, string> = {
    queued: "В очереди",
    archive_seek: "Ищем начало диапазона архива",
    archive_scan: "Индексируем посты",
    pixabay_search: "Ищем фото в Pixabay",
    pinterest_search: "Ищем публичные Pins",
    category_shortlist: "Сравниваем фото других групп",
    materializing: "Подготавливаем изображения",
    ranking: "Ранжируем по стилю",
    finalizing: "Сохраняем результат",
    own_archive: "Подбираем собственный архив",
    ready: "Готово",
    failed: "Задача не завершена. Повторите позже.",
  };
  return (
    <article className="activity-card" role="status">
      <strong>
        {job.kind === "archive" ? "Индексация архива" : "Сравнение фото"}
      </strong>
      <p>
        {stages[job.stage] ?? "Выполняется"}
        {job.total != null
          ? ` · ${job.current} / ${job.total}`
          : job.current
            ? ` · ${job.current}`
            : ""}{" "}
        · {job.elapsed_seconds} сек.
      </p>
      {!["ready", "failed"].includes(job.state) && (
        <progress
          aria-label="Прогресс фото"
          {...(job.total ? { value: job.current, max: job.total } : {})}
        />
      )}
      {job.counters.photos != null && (
        <p>Найдено: {job.counters.photos} фото</p>
      )}
      {job.counters.min_age_days != null && (
        <p>
          {job.counters.min_age_days}–{job.counters.max_age_days} дней
        </p>
      )}
    </article>
  );
}
export function CommunityDetailPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const gridId = searchParams.get("grid") ?? undefined;
  const reviewKey = searchParams.get("review") ?? "";
  const reviewIds = [
    ...new Set(
      reviewKey
        .split(",")
        .filter((value) => /^[a-zA-Z0-9-]{1,64}$/.test(value)),
    ),
  ].slice(0, 16);
  const reviewIndex = reviewIds.indexOf(id);
  const [feedbackRevision, setFeedbackRevision] = useState(0);
  const reviewFeedback = useLoad(
    async (signal) =>
      Promise.all(
        reviewIds.map(async (communityId) => {
          const ratings = await request<unknown[]>(
            `/communities/${communityId}/photo-feedback`,
            { signal },
          );
          return ratings.length > 0;
        }),
      ),
    [reviewKey, feedbackRevision],
  );
  useEffect(() => {
    setPreview(undefined);
    setSelected(undefined);
    setMessage("");
    setError("");
    setPage(1);
  }, [id]);
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState(1);
  const state = useLoad(
    (signal) => communityVisualApi.profile(id, signal),
    [id, revision],
  );
  const metadata = useLoad(
    (signal) => communityVisualApi.metadata(id, signal),
    [id],
  );
  const refs = useLoad(
    (signal) => communityVisualApi.references(id, page, signal),
    [id, page, revision],
  );
  const [body, setBody] = useState<ProfileInput>(defaults);
  const [busy, setBusy] = useState(false);
  const [study, setStudy] = useState<StudyJob | null>(null);
  const [jobs, setJobs] = useState<PhotoJob[]>([]);
  const [pollRevision, setPollRevision] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview>();
  const [selected, setSelected] = useState<Score>();
  const [source, setSource] = useState("all");
  const [providers, setProviders] = useState<Providers>({
    include_pixabay: true,
    include_archive: true,
    include_category_library: true,
    include_pinterest: true,
    include_library: true,
  });
  const studying = !!study && !["ready", "failed"].includes(study.state);
  const working =
    studying || jobs.some((job) => !["ready", "failed"].includes(job.state));
  useEffect(() => {
    if (state.data) {
      const {
        desired_content,
        avoid_content,
        style_notes,
        reference_target_count,
        archive_reuse_enabled,
        archive_reuse_min_age_days,
        archive_reuse_max_age_days,
      } = state.data;
      setBody({
        desired_content,
        avoid_content,
        style_notes,
        reference_target_count,
        archive_reuse_enabled,
        archive_reuse_min_age_days,
        archive_reuse_max_age_days,
      });
    }
  }, [state.data]);
  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const [studyJob, archive, comparison] = await Promise.all([
          communityVisualApi.study(id, controller.signal),
          communityVisualApi.latest(id, "archive", controller.signal),
          communityVisualApi.latest(id, "preview", controller.signal),
        ]);
        if (disposed) return;
        setStudy(studyJob);
        const photoJobs = [archive, comparison].filter(
          (job): job is PhotoJob => !!job,
        );
        setJobs(photoJobs);
        if (
          comparison?.state === "ready" &&
          comparison.result &&
          "category_only" in comparison.result
        )
          setPreview(comparison.result);
        if (
          archive?.state === "ready" &&
          archive.result &&
          "posts_scanned" in archive.result
        )
          setMessage(
            `Архив: просмотрено ${archive.result.posts_scanned}, новых ${archive.result.candidates_discovered}, существующих ${archive.result.candidates_existing}.`,
          );
        if (
          [studyJob, ...photoJobs].some(
            (job) => job && !["ready", "failed"].includes(job.state),
          )
        )
          timer = setTimeout(() => void poll(), 1500);
        else setRevision((n) => n + 1);
      } catch (err) {
        if (!disposed) {
          setError(errorMessage(err));
          timer = setTimeout(() => void poll(), 3000);
        }
      }
    }
    setPreview(undefined);
    setSelected(undefined);
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, pollRevision]);
  async function run(operation: "save" | "sync" | "archive" | "preview") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (operation === "preview") {
        const job = await communityVisualApi.preview(id, gridId, providers);
        setJobs((old) => [...old.filter((row) => row.kind !== "preview"), job]);
      } else {
        await communityVisualApi.save(id, body);
        if (operation === "sync") setStudy(await communityVisualApi.sync(id));
        else if (operation === "archive") {
          const job = await communityVisualApi.archive(id);
          setJobs((old) => [
            ...old.filter((row) => row.kind !== "archive"),
            job,
          ]);
        } else setMessage("Профиль сохранён.");
      }
      if (operation !== "save") setPollRevision((n) => n + 1);
      setRevision((n) => n + 1);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(undefined);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [selected]);
  const best =
    preview?.best_matches ??
    preview?.mixed_source ??
    preview?.community_aware ??
    [];
  return (
    <>
      <Link to="/communities">← Communities</Link>
      {reviewIndex >= 0 && (
        <div className="actions" aria-label="Community quality review">
          <span>
            {reviewFeedback.data?.filter(Boolean).length ?? 0} /{" "}
            {reviewIds.length} communities reviewed
          </span>
          {reviewIds.length > 1 && (
            <Link
              to={`/communities/${reviewIds[(reviewIndex + 1) % reviewIds.length]}?${new URLSearchParams(
                {
                  ...(gridId ? { grid: gridId } : {}),
                  review: reviewIds.join(","),
                },
              )}`}
            >
              Next community
            </Link>
          )}
        </div>
      )}
      <section className="photo-lab-header">
        <div>
          <small>PHOTO LAB</small>
          <h1>
            {metadata.data?.name ||
              metadata.data?.domain ||
              "Визуальный профиль сообщества"}
          </h1>
          <p>
            {metadata.data?.domain} ·{" "}
            {preview?.category ??
              metadata.data?.category ??
              "Категория не задана"}
          </p>
          <p>
            Комментарий: {preview?.comment ?? "—"} · Контент:{" "}
            {preview?.content_hint ?? body.desired_content ?? "—"}
          </p>
        </div>
        <span
          className={
            preview?.visual_engine?.active ? "visual-active" : "visual-inactive"
          }
        >
          Визуальный движок:{" "}
          {preview?.visual_engine?.active ? "ACTIVE" : "INACTIVE"}
        </span>
      </section>
      <div className="lab-counters">
        <span>
          Референсы <strong>{state.data?.reference_count ?? 0}</strong>
        </span>
        <span>
          CORE{" "}
          <strong>
            {state.data?.reference_core_count ??
              preview?.visual_engine?.compatible_reference_count ??
              "—"}
          </strong>
        </span>
        <span>
          AUX{" "}
          <strong>
            {state.data?.reference_aux_count ??
              (preview?.visual_engine
                ? Math.max(
                    0,
                    (state.data?.reference_count ?? 0) -
                      preview.visual_engine.compatible_reference_count,
                  )
                : "—")}
          </strong>
        </span>
        <span>
          Архив <strong>{state.data?.archive_discovered_count ?? 0}</strong>
        </span>
        <span>
          Другие группы{" "}
          <strong>
            {String(
              preview?.category_library_stats?.compatible_embeddings ?? 0,
            )}
          </strong>
        </span>
        <span>
          Pinterest{" "}
          <strong>
            {(
              {
                ready: "Доступен",
                partial: "Частично",
                search_unavailable: "Нет результатов",
                disabled: "Выключен",
                direct: "Готов к поиску",
                backend_unavailable: "Не настроен",
              } as Record<string, string>
            )[preview?.pinterest_status ?? ""] ?? "Не проверен"}
          </strong>
        </span>
      </div>
      <CommunityActivities id={id} />
      {jobs.map((job) => (
        <PhotoProgress key={job.id} job={job} />
      ))}
      {study && (
        <article className="activity-card" role="status">
          {
            (
              {
                queued: "В очереди",
                reading_wall: `Читаем стену: ${study.progress.posts_scanned ?? 0} постов`,
                downloading: `Скачиваем фото: ${study.progress.downloads_done ?? 0}/${study.progress.downloads_total ?? 0}`,
                embedding: `Строим embeddings: ${study.progress.embeddings_done ?? 0}/${study.progress.embeddings_total ?? 0}`,
                finalizing: "Определяем CORE references",
                ready: "Изучение стены завершено",
                failed: "Изучение стены не завершено. Повторите позже.",
              } as Record<string, string>
            )[study.state]
          }{" "}
          · {study.elapsed_seconds} сек.
          {studying && <progress aria-label="Прогресс изучения" />}
        </article>
      )}
      <State {...state} retry={state.reload}>
        {state.data && (
          <>
            <section className="form-card">
              <h2>Сравнить источники</h2>
              <div className="provider-toggles">
                {(
                  Object.entries({
                    include_pixabay: "Pixabay",
                    include_archive: "Own VK archive",
                    include_category_library: "VK category library",
                    include_pinterest: "Pinterest",
                    include_library: "Existing library",
                  }) as [keyof Providers, string][]
                ).map(([key, label]) => (
                  <label key={key}>
                    <input
                      type="checkbox"
                      checked={providers[key]}
                      onChange={(e) =>
                        setProviders({ ...providers, [key]: e.target.checked })
                      }
                    />
                    {label}
                  </label>
                ))}
              </div>
              <button
                disabled={busy || working}
                onClick={() => void run("preview")}
              >
                Сравнить подбор фото
              </button>
              <p className="note">
                Pinterest и фото других сообществ доступны только для
                исследования. Визуальное сходство — не вероятность качества.
              </p>
            </section>
            <details className="form-card">
              <summary>Настройки профиля и индексации</summary>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run("save");
                }}
              >
                <fieldset disabled={busy || working}>
                  {(
                    ["desired_content", "avoid_content", "style_notes"] as const
                  ).map((key, i) => (
                    <label key={key}>
                      {["Желаемый контент", "Избегать", "Заметки о стиле"][i]}
                      <textarea
                        maxLength={3000}
                        value={body[key] ?? ""}
                        onChange={(e) =>
                          setBody({ ...body, [key]: e.target.value || null })
                        }
                      />
                    </label>
                  ))}
                  <label>
                    Количество референсов
                    <input
                      type="number"
                      min={1}
                      max={300}
                      required
                      value={body.reference_target_count}
                      onChange={(e) =>
                        setBody({
                          ...body,
                          reference_target_count: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={body.archive_reuse_enabled}
                      onChange={(e) =>
                        setBody({
                          ...body,
                          archive_reuse_enabled: e.target.checked,
                        })
                      }
                    />
                    Разрешить использование архивных фото
                  </label>
                  <label>
                    Минимальный возраст архивного фото (дней)
                    <input
                      type="number"
                      min={0}
                      max={3649}
                      required
                      value={body.archive_reuse_min_age_days}
                      onChange={(e) =>
                        setBody({
                          ...body,
                          archive_reuse_min_age_days: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    Максимальный возраст архивного фото (дней)
                    <input
                      type="number"
                      min={body.archive_reuse_min_age_days + 1}
                      max={3650}
                      required
                      value={body.archive_reuse_max_age_days}
                      onChange={(e) =>
                        setBody({
                          ...body,
                          archive_reuse_max_age_days: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <div className="actions">
                    <button>Сохранить профиль</button>
                    <button type="button" onClick={() => void run("sync")}>
                      Изучить последние посты
                    </button>
                    <button type="button" onClick={() => void run("archive")}>
                      Проиндексировать архив
                    </button>
                  </div>
                </fieldset>
              </form>
            </details>
          </>
        )}
      </State>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {preview && (
        <section>
          {!preview.visual_engine?.active && (
            <p role="alert">
              Визуальное сравнение сейчас не используется.{" "}
              {preview.visual_engine?.reason_if_inactive === "model_disabled"
                ? "CLIP-модель не запущена."
                : "Нужны совместимые референсы и изображения."}
            </p>
          )}
          {preview.warnings.map((code) => (
            <p className="note" key={code}>
              {(
                {
                  pinterest_search_unavailable:
                    "Pinterest временно не вернул результаты.",
                  pinterest_fallback_used:
                    "Pinterest недоступен — использован резервный источник",
                  category_library_disabled:
                    "Библиотека других групп отключена в настройках окружения.",
                  photo_preview_timeout:
                    "Сравнение не успело завершиться. Повторите вручную.",
                } as Record<string, string>
              )[code] ??
                "Часть источников недоступна; сравнение остальных сохранено."}
            </p>
          ))}
          <h2>BEST MATCHES</h2>
          <p>
            Лучшее совпадение не означает разрешение использовать фото в
            кампании.
          </p>
          <p>
            Pinterest: {preview.pinterest_retrieved ?? 0} найдено ·{" "}
            {preview.pinterest_embedded ?? 0} проверено. Pixabay:{" "}
            {preview.pixabay_status === "not_needed"
              ? "не понадобился"
              : "резервный источник"}
            {" · "}
            {preview.pixabay_requests ?? 0} запросов.
          </p>
          <p>
            VK category:{" "}
            {preview.source_contributions?.vk_category_archive?.retrieved ?? 0}{" "}
            · Library: {preview.source_contributions?.library?.retrieved ?? 0} ·
            Собственный архив:{" "}
            {preview.source_contributions?.vk_archive?.retrieved ?? 0}
          </p>
          {preview.source_contributions && (
            <details>
              <summary>Вклад источников</summary>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Источник</th>
                      <th>Найдено</th>
                      <th>После dedup</th>
                      <th>Проверено</th>
                      <th>Embedded</th>
                      <th>Top 10</th>
                      <th>Выбрано</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(preview.source_contributions).map(
                      ([provider, counts]) => (
                        <tr key={provider}>
                          <td>{sources[provider] ?? provider}</td>
                          {[
                            "retrieved",
                            "deduplicated",
                            "materialized",
                            "embedded",
                            "top_10",
                            "selected",
                          ].map((k) => (
                            <td key={k}>{counts[k] ?? 0}</td>
                          ))}
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </details>
          )}
          <div className="source-filters" aria-label="Источники">
            {["all", ...Object.keys(sources)].map((key) => (
              <button
                aria-pressed={source === key}
                key={key}
                onClick={() => setSource(key)}
              >
                {key === "all" ? "All" : sources[key]}
              </button>
            ))}
          </div>
          <div className="match-grid">
            {best
              .filter((item) => source === "all" || item.source === source)
              .map((item) => (
                <button
                  className="match-card"
                  key={`${item.source}:${item.source_identity ?? item.media_asset_id ?? item.reference_id}`}
                  onClick={() => setSelected(item)}
                >
                  <img
                    src={imageUrl(item, id)}
                    alt="Подобранное фото"
                    loading="lazy"
                  />
                  <strong>
                    #{best.indexOf(item) + 1} ·{" "}
                    {sources[item.source ?? "library"] ?? item.source}
                  </strong>
                  <span>{styleScore(item)}</span>
                  {(item.provider === "pinterest" ||
                    item.source === "pinterest") &&
                    item.publication_eligible === true && (
                      <small>Разрешено настройкой</small>
                    )}
                  {item.publication_eligible === false && (
                    <small>Только предпросмотр</small>
                  )}
                </button>
              ))}
          </div>
          {best.length === 0 && (
            <p>
              Подходящих фото пока нет. Проверьте источники и визуальные
              референсы.
            </p>
          )}
          <p className="note">
            Другие группы:{" "}
            {String(preview.category_library_stats?.communities_indexed ?? 0)} ·
            Фото:{" "}
            {String(preview.category_library_stats?.reference_photos ?? 0)} ·
            Архив: {String(preview.category_library_stats?.archive_photos ?? 0)}{" "}
            · Совместимых:{" "}
            {String(preview.category_library_stats?.compatible_embeddings ?? 0)}
          </p>
          <details>
            <summary>Подробное сравнение</summary>
            <p>Search queries: {preview.generated_queries?.join(" · ")}</p>
            <p>
              Категория: {preview.category} · Комментарий: {preview.comment} ·
              Контент: {preview.content_hint}
            </p>
            <p>
              Pinterest: {preview.pinterest_status} · retrieved:{" "}
              {preview.pinterest_retrieved ?? 0} · embedded:{" "}
              {preview.pinterest_embedded ?? 0}
            </p>
            {preview.archive_age_strata && (
              <p>
                Архивный shortlist:{" "}
                {preview.archive_age_strata
                  .map(
                    (band) =>
                      `${band.min_age_days}–${band.max_age_days} дней: ${band.candidate_count}`,
                  )
                  .join("; ")}
              </p>
            )}
            {preview.timings_ms && (
              <p>
                {Object.entries(preview.timings_ms)
                  .map(([stage, ms]) => `${stage}: ${ms.toFixed(0)} ms`)
                  .join(" · ")}
              </p>
            )}
            <CandidateScores
              communityId={id}
              title="Pixabay"
              items={preview.category_only}
            />
            <CandidateScores
              communityId={id}
              title={
                preview.visual_engine?.active
                  ? "Community-ranked Pixabay"
                  : "Pixabay · визуальное сравнение недоступно"
              }
              items={preview.community_aware}
            />
            <CandidateScores
              communityId={id}
              title="Pinterest · experimental preview"
              items={preview.pinterest ?? []}
            />
            <CandidateScores
              communityId={id}
              title="Community-ranked Pinterest"
              items={preview.community_ranked_pinterest ?? []}
            />
            <CandidateScores
              communityId={id}
              title="VK category library"
              items={preview.category_library ?? []}
            />
            <CandidateScores
              communityId={id}
              title="Pixabay + архив + библиотека"
              items={preview.mixed_source ?? []}
            />
          </details>
        </section>
      )}
      <details className="reference-section">
        <summary>Визуальные референсы · CORE / AUX</summary>
        <State {...refs} retry={refs.reload}>
          <div className="reference-grid">
            {refs.data?.items.map((ref) => (
              <figure key={ref.id}>
                <img
                  src={referenceContentUrl(id, ref.id)}
                  alt={`Референс поста ${ref.vk_post_id}`}
                  loading="lazy"
                />
                <figcaption>
                  <strong>
                    {ref.reference_role === "auxiliary" ? "AUX" : "CORE"}
                  </strong>{" "}
                  · {date(ref.posted_at)}
                  <p>
                    {roleReasons[ref.reference_role_reason ?? ""] ??
                      "Отбор по визуальному сходству"}{" "}
                    · Кластер: {ref.reference_cluster_size ?? "—"}
                  </p>
                  <small>
                    density: {ref.reference_density?.toFixed(3) ?? "—"} ·
                    nearest:{" "}
                    {ref.reference_nearest_similarity?.toFixed(3) ?? "—"}
                  </small>
                </figcaption>
              </figure>
            ))}
          </div>
        </State>
        <Pager
          page={page}
          onPage={setPage}
          hasNext={page * 20 < (refs.data?.total ?? 0)}
          disabled={refs.loading}
        />
      </details>
      {selected && (
        <div
          className="photo-modal-backdrop"
          onClick={() => setSelected(undefined)}
        >
          <section
            className="photo-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Почему это фото"
            onClick={(e) => e.stopPropagation()}
          >
            <button autoFocus onClick={() => setSelected(undefined)}>
              Закрыть
            </button>
            <img
              className="candidate-large"
              src={imageUrl(selected, id)}
              alt="Выбранное фото"
            />
            <h2>Почему это фото</h2>
            <p>
              {styleScore(selected)} · {sources[selected.source ?? "library"]}
            </p>
            <p>
              Запрос:{" "}
              {selected.retrieval_queries?.join(" · ") ||
                "Индексированная библиотека"}
            </p>
            {(selected.provider === "pinterest" ||
              selected.source === "pinterest") && (
              <p>
                {selected.publication_eligible
                  ? "Разрешено настройкой"
                  : "Только предпросмотр"}
                . Права на публикацию не проверены.
              </p>
            )}
            <div className="actions">
              {["like", "dislike"].map((rating) => (
                <button
                  key={rating}
                  onClick={async () => {
                    try {
                      await request(`/communities/${id}/photo-feedback`, {
                        method: "PUT",
                        body: {
                          provider:
                            selected.provider ?? selected.source ?? "library",
                          source_identity:
                            selected.source_identity ?? selected.media_asset_id,
                          rating,
                        },
                      });
                      setFeedbackRevision((value) => value + 1);
                      setMessage(
                        "Оценка сохранена. Она пока не влияет на подбор.",
                      );
                    } catch (e) {
                      setError(errorMessage(e));
                    }
                  }}
                >
                  {rating === "like" ? "👍 подходит" : "👎 не подходит"}
                </button>
              ))}
            </div>
            {selected.pin_url && (
              <a href={selected.pin_url} target="_blank" rel="noreferrer">
                Открыть Pin
              </a>
            )}
            {selected.source_community_id && (
              <p>
                Источник:{" "}
                <Link to={`/communities/${selected.source_community_id}`}>
                  {selected.source_community_id}
                </Link>{" "}
                · Пост: {selected.source_post_id} · Фото:{" "}
                {selected.vk_photo_owner_id}_{selected.vk_photo_id}
              </p>
            )}
            <p>
              Дата:{" "}
              {selected.original_posted_at
                ? date(selected.original_posted_at)
                : "—"}
            </p>
            <h3>Похоже на эти посты группы</h3>
            <div className="reference-matches">
              {selected.top_references?.slice(0, 5).map((ref) => (
                <figure key={ref.reference_id}>
                  <img
                    src={referenceContentUrl(id, ref.reference_id)}
                    alt="Похожий пост группы"
                  />
                  <figcaption>{Math.round(ref.similarity * 100)}%</figcaption>
                </figure>
              ))}
            </div>
            <details>
              <summary>Диагностика оценки</summary>
              <p>
                base: {selected.base_score.toFixed(3)} · visual:{" "}
                {selected.visual_score?.toFixed(3) ?? "—"} · final:{" "}
                {selected.final_score.toFixed(3)}
              </p>
            </details>
          </section>
        </div>
      )}
    </>
  );
}
