import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  request,
  errorMessage,
  mediaContentUrl,
  referenceContentUrl,
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
type Reference = {
  id: string;
  posted_at: string;
  vk_post_id: number;
  embedding_model: string | null;
};
type Score = {
  media_asset_id: string | null;
  reference_id?: string | null;
  source?: string;
  source_identity?: string;
  original_posted_at?: string | null;
  age_days?: number | null;
  age_reuse_score?: number;
  base_score: number;
  visual_score: number | null;
  final_score: number;
};
type Preview = {
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
    request<{
      posts_scanned: number;
      references_created: number;
      references_existing: number;
      references_embedded: number;
      warnings: string[];
    }>(`/communities/${id}/references/sync`, { method: "POST", body: {} }),
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
  preview: (id: string) =>
    request<Preview>(`/communities/${id}/photo-preview`, {
      method: "POST",
      body: { candidate_limit: 8 },
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
        {items.map((item) => (
          <figure
            key={
              item.source_identity ?? item.media_asset_id ?? item.reference_id
            }
          >
            <img
              src={
                item.reference_id
                  ? referenceContentUrl(communityId, item.reference_id)
                  : mediaContentUrl(item.media_asset_id ?? "")
              }
              alt="Кандидат фото"
              loading="lazy"
            />
            <figcaption>
              {item.source ?? "pixabay"} · {item.source_identity}
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
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

export function CommunityDetailPage() {
  const { id = "" } = useParams();
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
  async function run(operation: "save" | "sync" | "archive" | "preview") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (operation === "preview")
        setPreview(await communityVisualApi.preview(id));
      else {
        await communityVisualApi.save(id, body);
        if (operation === "sync") {
          const result = await communityVisualApi.sync(id);
          setMessage(
            `Просмотрено постов: ${result.posts_scanned}. Новых референсов: ${result.references_created}. Существующих: ${result.references_existing}. Embeddings: ${result.references_embedded}. ${result.warnings.join(", ")}`,
          );
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
              <fieldset disabled={busy}>
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
              <figcaption>{date(ref.posted_at)}</figcaption>
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
          <p>
            Порядок и scores доступны для сравнения; улучшение качества требует
            визуальной оценки.
          </p>
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
            title="Только категория"
            items={preview.category_only}
          />
          <CandidateScores
            communityId={id}
            title="С учётом сообщества"
            items={preview.community_aware}
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
