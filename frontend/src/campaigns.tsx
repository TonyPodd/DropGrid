import { useEffect, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { campaignsApi } from "./api/campaigns";
import { gridsApi } from "./api/grids";
import { errorMessage, mediaContentUrl } from "./api/client";
import { PhotoPlanButton, PhotoPlanResult } from "./media";
import {
  submissionStatuses,
  type Audio,
  type Campaign,
  type CampaignInput,
  type Submission,
  type MediaPlan,
} from "./api/types";
import {
  Badge,
  categoryName,
  Confirm,
  date,
  Pager,
  State,
  useLoad,
} from "./shared";

export function CampaignsPage() {
  const [page, setPage] = useState(1);
  const state = useLoad((signal) => campaignsApi.list(page, signal), [page]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Campaigns</h1>
          <p>Черновики и подготовленные кампании.</p>
        </div>
        <Link className="button primary" to="/campaigns/new">
          Создать кампанию
        </Link>
      </div>
      <State {...state} retry={state.reload}>
        <CampaignTable campaigns={state.data ?? []} />
      </State>
      <Pager
        page={page}
        hasNext={state.data?.length === 25}
        onPage={setPage}
        disabled={state.loading}
      />
    </>
  );
}
export function CampaignTable({ campaigns }: { campaigns: Campaign[] }) {
  return campaigns.length ? (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Название</th>
            <th>Сетка</th>
            <th>Status</th>
            <th>Submissions</th>
            <th>Создана</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((c) => (
            <tr key={c.id}>
              <td>
                <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
              </td>
              <td>
                <Link to={`/grids/${c.grid_id}`}>{c.grid_name}</Link>
              </td>
              <td>
                <Badge status={c.status} />
              </td>
              <td>{c.submission_count}</td>
              <td>{date(c.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <div className="empty">
      Кампаний пока нет.{" "}
      <Link to="/campaigns/new">Создать первую кампанию</Link>
    </div>
  );
}
export function CampaignForm({
  initial,
  onSave,
  onCancel,
}: {
  initial?: Campaign;
  onSave: (body: CampaignInput) => Promise<void>;
  onCancel?: () => void;
}) {
  const [params] = useSearchParams();
  const [gridPage, setGridPage] = useState(1);
  const grids = useLoad(
    (signal) => gridsApi.list(gridPage, signal),
    [gridPage],
  );
  const [name, setName] = useState(initial?.name ?? "");
  const [gridId, setGridId] = useState(
    initial?.grid_id ?? params.get("grid") ?? "",
  );
  const selected = useLoad(
    (signal) =>
      gridId ? gridsApi.detail(gridId, signal) : Promise.resolve(undefined),
    [gridId],
  );
  const [track, setTrack] = useState(initial?.track_url ?? "");
  const [caption, setCaption] = useState(initial?.caption ?? "");
  const [hours, setHours] = useState(
    String(initial?.publication_check_hours ?? 72),
  );
  const [audio, setAudio] = useState<Audio>();
  const [trackError, setTrackError] = useState("");
  const [parsing, setParsing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setAudio(undefined);
    setTrackError("");
    if (!track.trim()) {
      setParsing(false);
      return;
    }
    const controller = new AbortController();
    setParsing(true);
    const timer = setTimeout(() => {
      campaignsApi
        .parseTrack(track, controller.signal)
        .then((result) => {
          if (!controller.signal.aborted) setAudio(result);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted)
            setTrackError(
              err instanceof Error && "status" in err && err.status === 422
                ? "Введите ссылку на аудио VK, например https://vk.ru/audio474499203_456961224."
                : errorMessage(err),
            );
        })
        .finally(() => {
          if (!controller.signal.aborted) setParsing(false);
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [track]);
  const validHours =
    Number.isInteger(Number(hours)) &&
    Number(hours) >= 1 &&
    Number(hours) <= 2147483647;
  async function submit() {
    if (!name.trim() || !gridId || !audio || !validHours || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSave({
        name: name.trim(),
        grid_id: gridId,
        track_url: track,
        caption,
        publication_check_hours: Number(hours),
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="form-card"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <fieldset disabled={busy}>
        <label>
          Name
          <input
            required
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {!initial && (
          <>
            <label>
              Grid
              <select
                required
                value={gridId}
                onChange={(e) => setGridId(e.target.value)}
              >
                <option value="">Выберите сетку</option>
                {gridId && !grids.data?.some((g) => g.id === gridId) && (
                  <option value={gridId}>
                    {selected.data?.name ?? "Выбранная сетка"}
                  </option>
                )}
                {grids.data?.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
            <State {...grids} retry={grids.reload}>
              {grids.data?.length === 0 && (
                <p>
                  Сеток на этой странице нет.{" "}
                  <Link to="/grids/new">Импортировать сетку</Link>
                </p>
              )}
            </State>
            <Pager
              page={gridPage}
              hasNext={grids.data?.length === 25}
              onPage={setGridPage}
              disabled={grids.loading}
            />
          </>
        )}
        {gridId && (
          <State {...selected} retry={selected.reload}>
            {selected.data && (
              <p className="muted">
                {selected.data.community_count} communities ·{" "}
                {
                  selected.data.categories.filter((c) => c.category !== null)
                    .length
                }{" "}
                categories
              </p>
            )}
          </State>
        )}
        <label>
          VK audio link
          <input
            required
            maxLength={2048}
            type="url"
            value={track}
            placeholder="https://vk.ru/audio474499203_456961224"
            onChange={(e) => setTrack(e.target.value)}
          />
        </label>
        {parsing && <p role="status">Проверка ссылки…</p>}
        {audio && (
          <p className="success">
            ✓ Audio {audio.owner_id}_{audio.audio_id}
          </p>
        )}
        {trackError && <p role="alert">{trackError}</p>}
        <label>
          Caption <span className="muted">— необязательно</span>
          <textarea
            rows={4}
            maxLength={10000}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
          />
        </label>
        <label>
          Проверять публикацию в течение (часов)
          <input
            required
            type="number"
            min={1}
            max={2147483647}
            step={1}
            value={hours}
            onChange={(e) => setHours(e.target.value)}
          />
        </label>
        {!validHours && (
          <p role="alert">Введите целое число от 1 до 2147483647.</p>
        )}
        <p className="note">
          Сохранение и Prepare не обращаются к VK. Проверка публикаций пока не
          реализована.
        </p>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button
            className="primary"
            disabled={
              busy ||
              !audio ||
              parsing ||
              !name.trim() ||
              !gridId ||
              !validHours ||
              selected.loading ||
              !!selected.error
            }
          >
            {busy ? "Сохранение…" : "Сохранить черновик"}
          </button>
          {onCancel && (
            <button type="button" onClick={onCancel}>
              Отмена
            </button>
          )}
        </div>
      </fieldset>
    </form>
  );
}
export function CampaignNewPage() {
  const navigate = useNavigate();
  return (
    <>
      <Link to="/campaigns">← Campaigns</Link>
      <h1>Новая кампания</h1>
      <CampaignForm
        onSave={async (body) => {
          const result = await campaignsApi.create(body);
          navigate(`/campaigns/${result.id}`);
        }}
      />
    </>
  );
}
export function PrepareButton({
  count,
  onPrepare,
}: {
  count: number;
  onPrepare: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function prepare() {
    setBusy(true);
    setError("");
    try {
      await onPrepare();
      setConfirm(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button className="primary" onClick={() => setConfirm(true)}>
        Prepare campaign
      </button>
      {confirm && (
        <Confirm
          title="Подготовить кампанию"
          busy={busy}
          onCancel={() => setConfirm(false)}
          onConfirm={() => void prepare()}
        >
          <p>Будет создано {count} submissions.</p>
          <p>
            Это пока НЕ отправляет ничего в VK. Повторная подготовка не создаёт
            дубликаты.
          </p>
          {error && <p role="alert">{error}</p>}
        </Confirm>
      )}
    </>
  );
}
export function SubmissionTable({ items }: { items: Submission[] }) {
  return items.length ? (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Community</th>
            <th>Category</th>
            <th>Account</th>
            <th>Media</th>
            <th>Status</th>
            <th>Attempts</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id}>
              <td>{s.community.domain}</td>
              <td>{categoryName(s.category)}</td>
              <td>{s.account_name ?? "—"}</td>
              <td>
                {s.media_asset_id ? (
                  <img
                    className="submission-photo"
                    src={mediaContentUrl(s.media_asset_id)}
                    alt={`Фото для ${s.community.domain}`}
                    loading="lazy"
                  />
                ) : (
                  (s.media_label ?? "—")
                )}
              </td>
              <td>
                <Badge status={s.status} />
              </td>
              <td>{s.attempt_count}</td>
              <td>
                {s.published_post_url ? (
                  <a
                    href={s.published_post_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Публикация ↗
                  </a>
                ) : s.error_message ? (
                  <span className="short-error">
                    {s.error_code && <strong>{s.error_code}: </strong>}
                    {s.error_message}
                  </span>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <p className="empty">Submissions по выбранным фильтрам нет.</p>
  );
}
export function SubmissionsPanel({
  campaign,
  revision,
}: {
  campaign: Campaign;
  revision: number;
}) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const categories = useLoad(
    (signal) => gridsApi.detail(campaign.grid_id, signal),
    [campaign.grid_id],
  );
  const submissions = useLoad(
    (signal) =>
      campaignsApi.submissions(campaign.id, page, status, category, signal),
    [campaign.id, page, status, category, revision],
  );
  return (
    <section>
      <h2>Submissions</h2>
      <div className="filters">
        <label>
          Status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All</option>
            {submissionStatuses.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Category
          <select
            value={category === null ? "all" : `category:${category}`}
            onChange={(e) => {
              setCategory(
                e.target.value === "all" ? null : e.target.value.slice(9),
              );
              setPage(1);
            }}
          >
            <option value="all">All</option>
            {categories.data?.categories.map((c) => (
              <option
                key={JSON.stringify(c.category)}
                value={`category:${c.category ?? ""}`}
              >
                {categoryName(c.category)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {categories.error && (
        <p role="alert">
          {categories.error}{" "}
          <button onClick={categories.reload}>Повторить категории</button>
        </p>
      )}
      <State {...submissions} retry={submissions.reload}>
        {submissions.data && (
          <>
            <p className="muted">Найдено: {submissions.data.total}</p>
            <SubmissionTable items={submissions.data.items} />
          </>
        )}
      </State>
      <Pager
        page={page}
        hasNext={page * 25 < (submissions.data?.total ?? 0)}
        onPage={setPage}
        disabled={submissions.loading}
      />
    </section>
  );
}
export function CampaignDetailPage() {
  const { id = "" } = useParams();
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState(false);
  const [photoResult, setPhotoResult] = useState<MediaPlan>();
  const state = useLoad(
    (signal) => campaignsApi.detail(id, signal),
    [id, revision],
  );
  const stats = useLoad(
    (signal) => campaignsApi.stats(id, signal),
    [id, revision],
  );
  return (
    <>
      <Link to="/campaigns">← Campaigns</Link>
      {photoResult?.campaign_id === id && (
        <PhotoPlanResult result={photoResult} />
      )}
      <State {...state} retry={state.reload}>
        {state.data && (
          <>
            <div className="page-heading">
              <h1>{state.data.name}</h1>
              <Badge status={state.data.status} />
            </div>
            {editing && state.data.status === "draft" ? (
              <CampaignForm
                initial={state.data}
                onCancel={() => setEditing(false)}
                onSave={async ({ grid_id: _gridId, ...body }) => {
                  await campaignsApi.patch(id, body);
                  setEditing(false);
                  setRevision((n) => n + 1);
                }}
              />
            ) : (
              <>
                <dl className="details">
                  <dt>Grid</dt>
                  <dd>
                    <Link to={`/grids/${state.data.grid_id}`}>
                      {state.data.grid_name}
                    </Link>
                  </dd>
                  <dt>Communities</dt>
                  <dd>{state.data.community_count}</dd>
                  <dt>Track</dt>
                  <dd>{state.data.track_url}</dd>
                  <dt>Caption</dt>
                  <dd className="caption">{state.data.caption || "—"}</dd>
                  <dt>Publication window</dt>
                  <dd>{state.data.publication_check_hours} часов</dd>
                  <dt>Created</dt>
                  <dd>{date(state.data.created_at)}</dd>
                </dl>
                <div className="actions">
                  {state.data.status === "draft" ? (
                    <>
                      <button onClick={() => setEditing(true)}>
                        Редактировать
                      </button>
                      <PrepareButton
                        count={state.data.community_count}
                        onPrepare={async () => {
                          await campaignsApi.prepare(id);
                          setRevision((n) => n + 1);
                        }}
                      />
                    </>
                  ) : (
                    <p className="note">
                      Кампания подготовлена. Отправка будет доступна после VK
                      live verification.
                    </p>
                  )}
                </div>
                {state.data.status === "ready" && (
                  <PhotoPlanButton
                    campaignId={id}
                    showResult={false}
                    onPlanned={(result) => {
                      setPhotoResult(result);
                      setRevision((n) => n + 1);
                    }}
                  />
                )}
              </>
            )}
            <h2>Статистика</h2>
            {stats.data && (
              <p>
                Фото: {stats.data.media_assigned ?? 0} / {stats.data.total} ·
                Уникальных: {stats.data.media_unique ?? 0} · Без фото:{" "}
                {stats.data.total - (stats.data.media_assigned ?? 0)}
              </p>
            )}
            <State {...stats} retry={stats.reload}>
              {stats.data && (
                <div className="metrics">
                  <div>
                    <span>Total</span>
                    <strong>{stats.data.total}</strong>
                  </div>
                  {submissionStatuses.map((s) => (
                    <div key={s}>
                      <span>{s.replace("_", " ")}</span>
                      <strong>{stats.data?.statuses[s]}</strong>
                    </div>
                  ))}
                </div>
              )}
            </State>
            <SubmissionsPanel
              key={id}
              campaign={state.data}
              revision={revision}
            />
          </>
        )}
      </State>
    </>
  );
}
