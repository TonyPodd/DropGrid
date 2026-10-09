import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  request,
  errorMessage,
  mediaContentUrl,
  referenceContentUrl,
  photoPreviewContentUrl,
} from "./api/client";
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
  reference_role?: string;
  reference_density?: number | null;
  reference_nearest_similarity?: number | null;
  id: string;
  posted_at: string;
  vk_post_id: number;
  embedding_model: string | null;
};
type Score = {
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
  archive: (id: string) =>
    request<{
      posts_scanned: number;
      candidates_discovered: number;
      candidates_existing: number;
      warnings: string[];
    }>(`/communities/${id}/archive/sync`, {
      method: "POST",
      body: { max_pages: 20 },
    }),
  preview: (id: string, gridId?: string) =>
    request<Preview>(`/communities/${id}/photo-preview`, {
      method: "POST",
      body: {
        candidate_limit: 8,
        diagnostics: true,
        ...(gridId ? { grid_id: gridId } : {}),
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
                    ? referenceContentUrl(communityId, item.reference_id)
                    : mediaContentUrl(item.media_asset_id ?? "")
              }
              alt="Кандидат фото"
              loading="lazy"
            />
            <figcaption>
              {item.publication_eligible === false && (
                <strong>
                  EXPERIMENTAL · preview only · publication_eligible=false
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

export function CommunityDetailPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const gridId = searchParams.get("grid");
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState(1);
  const state = useLoad(
    (signal) => communityVisualApi.profile(id, signal),
    [id, revision],
  );
  const refs = useLoad(
    (signal) => communityVisualApi.references(id, page, signal),
    [id, page, revision],
  );
  const [body, setBody] = useState<ProfileInput>(defaults);
  const [busy, setBusy] = useState(false);
  const [study, setStudy] = useState<StudyJob | null>(null);
  const studying = !!study && !["ready", "failed"].includes(study.state);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview>();
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
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const job = await communityVisualApi.study(id, controller.signal);
        if (disposed) return;
        setStudy(job);
        if (job && !["ready", "failed"].includes(job.state))
          timer = setTimeout(() => void poll(), 1000);
        else if (job) setRevision((n) => n + 1);
      } catch (err) {
        if (!disposed) setError(errorMessage(err));
      }
    }
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, study?.id]);
  async function run(operation: "save" | "sync" | "archive" | "preview") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (operation === "preview")
        setPreview(
          await (gridId
            ? communityVisualApi.preview(id, gridId)
            : communityVisualApi.preview(id)),
        );
      else {
        await communityVisualApi.save(id, body);
        if (operation === "sync") {
          setStudy(await communityVisualApi.sync(id));
        } else if (operation === "archive") {
          const result = await communityVisualApi.archive(id);
          setMessage(
            `Архив: просмотрено ${result.posts_scanned}, новых ${result.candidates_discovered}, существующих ${result.candidates_existing}. ${result.warnings.join(", ")}`,
          );
        } else setMessage("Профиль сохранён.");
        setRevision((n) => n + 1);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Link to="/communities">← Communities</Link>
      <h1>Визуальный профиль сообщества</h1>
      <State {...state} retry={state.reload}>
        {state.data && (
          <>
            <p>
              Референсов: {state.data.reference_count} · Последнее изучение:{" "}
              {state.data.references_last_synced_at
                ? date(state.data.references_last_synced_at)
                : "—"}
            </p>
            <p>
              Архив: обнаружено {state.data.archive_discovered_count ?? 0},
              доступно {state.data.archive_eligible_count ?? 0}.{" "}
              {state.data.archive_oldest_eligible_at && (
                <>
                  Диапазон: {date(state.data.archive_oldest_eligible_at)} —{" "}
                  {date(
                    state.data.archive_newest_eligible_at ??
                      state.data.archive_oldest_eligible_at,
                  )}
                </>
              )}
            </p>
            <form
              className="form-card"
              onSubmit={(e) => {
                e.preventDefault();
                void run("save");
              }}
            >
              <fieldset disabled={busy || studying}>
                <label>
                  Желаемый контент
                  <textarea
                    maxLength={3000}
                    value={body.desired_content ?? ""}
                    onChange={(e) =>
                      setBody({
                        ...body,
                        desired_content: e.target.value || null,
                      })
                    }
                  />
                </label>
                <label>
                  Избегать
                  <textarea
                    maxLength={3000}
                    value={body.avoid_content ?? ""}
                    onChange={(e) =>
                      setBody({
                        ...body,
                        avoid_content: e.target.value || null,
                      })
                    }
                  />
                </label>
                <label>
                  Заметки о стиле
                  <textarea
                    maxLength={3000}
                    value={body.style_notes ?? ""}
                    onChange={(e) =>
                      setBody({ ...body, style_notes: e.target.value || null })
                    }
                  />
                </label>
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
                    required
                    min={body.archive_reuse_min_age_days + 1}
                    max={3650}
                    value={body.archive_reuse_max_age_days}
                    onChange={(e) =>
                      setBody({
                        ...body,
                        archive_reuse_max_age_days: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <p className="note">
                  Недавние references определяют стиль. Архив индексируется
                  отдельно по возрасту. При явном разрешении старые фото только
                  этого сообщества участвуют в подборе вместе с Pixabay.
                  Индексация и preview ничего не отправляют в VK.
                </p>
                <div className="actions">
                  <button>Сохранить профиль</button>
                  <button type="button" onClick={() => void run("sync")}>
                    Изучить последние посты
                  </button>
                  <button type="button" onClick={() => void run("archive")}>
                    Проиндексировать архив
                  </button>
                  <button type="button" onClick={() => void run("preview")}>
                    Сравнить подбор фото
                  </button>
                </div>
              </fieldset>
            </form>
          </>
        )}
      </State>
      {study && (
        <p role="status">
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
          {study.result?.warnings?.length
            ? ` · ${study.result.warnings.join(", ")}`
            : ""}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      <h2>Визуальные референсы</h2>
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
                <br />
                density: {ref.reference_density?.toFixed(3) ?? "—"} · nearest:{" "}
                {ref.reference_nearest_similarity?.toFixed(3) ?? "—"}
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
      {preview && (
        <>
          {!preview.visual_engine?.active && (
            <p role="alert">
              Визуальное сравнение сейчас не используется ·{" "}
              {preview.visual_engine?.reason_if_inactive ??
                "no_compatible_references"}
            </p>
          )}
          {preview.visual_engine && (
            <p className="note">
              Visual engine: {preview.visual_engine.model ?? "disabled"} · CORE:{" "}
              {preview.visual_engine.compatible_reference_count} · candidate
              embeddings: {preview.visual_engine.candidate_embeddings_available}
            </p>
          )}
          <p>
            Порядок и scores доступны для сравнения; улучшение качества требует
            визуальной оценки.
          </p>
          <p className="note">
            Category: {preview.category ?? "—"} · Comment:{" "}
            {preview.comment ?? "—"} · Content hint:{" "}
            {preview.content_hint ?? "—"}
          </p>
          <p className="note">
            Search queries:{" "}
            {(preview.generated_queries ?? []).join(" · ") || "—"}
          </p>
          {preview.timings_ms && (
            <p className="note">
              Stage timings (ms):{" "}
              {Object.entries(preview.timings_ms)
                .map(([stage, ms]) => `${stage}: ${ms.toFixed(0)}`)
                .join(" · ")}
            </p>
          )}
          {(preview.archive_age_strata?.length ?? 0) > 0 && (
            <p className="note">
              Архивный shortlist:{" "}
              {preview
                .archive_age_strata!.map(
                  (band) =>
                    `${band.min_age_days}–${band.max_age_days} дней: ${band.candidate_count}`,
                )
                .join("; ")}
            </p>
          )}
          {preview.warnings.length > 0 && (
            <p className="note">{preview.warnings.join(", ")}</p>
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
          <p className="note">
            Pinterest: {preview.pinterest_status ?? "disabled"} · retrieved:{" "}
            {preview.pinterest_retrieved ?? 0} · embedded:{" "}
            {preview.pinterest_embedded ?? 0} · queries:{" "}
            {(preview.pinterest_queries ?? []).join(" · ")}
          </p>
          {!["ready", "partial"].includes(preview.pinterest_status ?? "") && (
            <p role="status">
              Pinterest недоступен. Настройте experimental search backend.
            </p>
          )}
          <CandidateScores
            communityId={id}
            title="Pinterest · experimental preview"
            items={preview.pinterest ?? []}
          />
          <CandidateScores
            communityId={id}
            title="Community-ranked Pinterest · experimental preview"
            items={preview.community_ranked_pinterest ?? []}
          />
          <CandidateScores
            communityId={id}
            title="Pixabay + архив + библиотека"
            items={preview.mixed_source ?? []}
          />
        </>
      )}
    </>
  );
}
