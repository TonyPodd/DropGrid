import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { GridReadinessPanel } from "./grid-readiness";
import { gridsApi } from "./api/grids";
import { errorMessage } from "./api/client";
import type { GridPreview } from "./api/types";
import { categoryName, Confirm, date, Pager, State, useLoad } from "./shared";

export function GridsPage() {
  const [page, setPage] = useState(1);
  const state = useLoad((signal) => gridsApi.list(page, signal), [page]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Grids</h1>
          <p>Сетки сообществ для подготовки кампаний.</p>
        </div>
        <Link className="button primary" to="/grids/new">
          Импортировать сетку
        </Link>
      </div>
      <State {...state} retry={state.reload}>
        {state.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Название</th>
                  <th>Сообществ</th>
                  <th>Создана</th>
                </tr>
              </thead>
              <tbody>
                {state.data.map((grid) => (
                  <tr key={grid.id}>
                    <td>
                      <Link to={`/grids/${grid.id}`}>{grid.name}</Link>
                    </td>
                    <td>{grid.community_count}</td>
                    <td>{date(grid.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            Сеток пока нет.{" "}
            <Link to="/grids/new">Импортировать первую сетку</Link>
          </div>
        )}
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
export function ParsePreview({ preview }: { preview: GridPreview }) {
  const categories = new Map<string | null, number>();
  preview.items.forEach((item) =>
    categories.set(item.category, (categories.get(item.category) ?? 0) + 1),
  );
  return (
    <section>
      <h2>Предпросмотр</h2>
      <div className="summary">
        Найдено: <strong>{preview.items.length}</strong> · Категорий:{" "}
        <strong>
          {[...categories.keys()].filter((c) => c !== null).length}
        </strong>{" "}
        · Ошибок: <strong>{preview.errors.length}</strong>
      </div>
      <div className="category-list">
        {[...categories].map(([category, count]) => (
          <span key={JSON.stringify(category)}>
            {categoryName(category)} <strong>{count}</strong>
          </span>
        ))}
      </div>
      {preview.errors.length > 0 && (
        <div className="errors">
          <h3>Ошибочные строки</h3>
          {preview.errors.map((error) => (
            <div key={error.line}>
              <strong>Строка {error.line}</strong> — {error.message}
              <pre>{error.value}</pre>
            </div>
          ))}
          <p>
            Исправьте текст выше и разберите сетку заново либо подтвердите
            импорт только валидных строк.
          </p>
        </div>
      )}
      <div className="table-wrap preview-table">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Community</th>
              <th>Category</th>
              <th>Comment</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {preview.items.map((item, index) => (
              <tr key={item.community}>
                <td>{index + 1}</td>
                <td>{item.community}</td>
                <td>{categoryName(item.category)}</td>
                <td>{item.comment ?? "—"}</td>
                <td>
                  <span className="badge ready">OK</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
export function GridImportPage() {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<GridPreview>();
  const [parsedText, setParsedText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  async function parse() {
    setBusy(true);
    setError("");
    setPreview(undefined);
    try {
      const result = await gridsApi.parse(text);
      setPreview(result);
      setParsedText(text);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!preview?.items.length || text !== parsedText || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await gridsApi.import(name.trim(), parsedText);
      navigate(`/grids/${result.grid.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setConfirm(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Link to="/grids">← Grids</Link>
      <h1>Импорт сетки</h1>
      <p>
        Вставьте ссылки или домены VK. Заголовки категорий — отдельными
        строками; для неоднозначных названий используйте #.
      </p>
      <label>
        Вставьте сетку
        <textarea
          rows={12}
          maxLength={200000}
          value={text}
          disabled={busy}
          onChange={(event) => {
            setText(event.target.value);
            setPreview(undefined);
            setError("");
          }}
          placeholder={
            "ГРУЗОВИКИ\n230 vk.com/lujbit\n231 vk.com/dmisley\n\nТРАКТОРЫ\n232 vk.com/example"
          }
        />
      </label>
      <button
        className="primary"
        disabled={busy || !text.trim()}
        onClick={() => void parse()}
      >
        {busy ? "Обработка…" : "Разобрать сетку"}
      </button>
      {error && <p role="alert">{error}</p>}
      {preview && (
        <>
          <ParsePreview preview={preview} />
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (preview.errors.length) setConfirm(true);
              else void save();
            }}
          >
            <label>
              Название сетки
              <input
                required
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Основная сетка — октябрь"
              />
            </label>
            <button
              className="primary"
              disabled={busy || !name.trim() || !preview.items.length}
            >
              Сохранить сетку
            </button>
          </form>
        </>
      )}
      {confirm && preview && (
        <Confirm
          title="Импорт с ошибками"
          busy={busy}
          onCancel={() => setConfirm(false)}
          onConfirm={() => void save()}
        >
          <p>В сетке {preview.errors.length} ошибок.</p>
          <p>
            {preview.items.length} сообществ будет импортировано,{" "}
            {preview.errors.length} строк будет пропущено.
          </p>
        </Confirm>
      )}
    </>
  );
}
function MemberHints({
  gridId,
  community,
  reload,
}: {
  gridId: string;
  community: {
    id: string;
    domain: string;
    comment?: string | null;
    content_hint?: string | null;
  };
  reload: () => void;
}) {
  const [comment, setComment] = useState(community.comment ?? "");
  const [hint, setHint] = useState(community.content_hint ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <details>
      <summary>
        {community.content_hint ||
          community.comment ||
          "Добавить комментарий / hint"}
      </summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError("");
          try {
            await gridsApi.updateMember(gridId, community.id, {
              comment: comment.trim() || null,
              content_hint: hint.trim() || null,
            });
            reload();
          } catch (e) {
            setError(errorMessage(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Комментарий {community.domain}
          <input
            maxLength={3000}
            value={comment}
            disabled={busy}
            onChange={(e) => setComment(e.target.value)}
          />
        </label>
        <label>
          Content hint {community.domain}
          <input
            maxLength={500}
            value={hint}
            disabled={busy}
            onChange={(e) => setHint(e.target.value)}
          />
        </label>
        <p className="note">
          Комментарий — заметка. Только content hint участвует в поиске фото.
        </p>
        <button
          type="button"
          disabled={busy || !comment.trim() || comment.length > 500}
          onClick={() => setHint(comment)}
        >
          Использовать комментарий как hint
        </button>
        <button disabled={busy}>Сохранить hint</button>
        {error && <p role="alert">{error}</p>}
      </form>
      <Link to={`/communities/${community.id}?grid=${gridId}`}>
        Предпросмотр фото для этой сетки
      </Link>
    </details>
  );
}

export function GridDetailPage() {
  const { id = "" } = useParams();
  const [page, setPage] = useState(1);
  const detail = useLoad((signal) => gridsApi.detail(id, signal), [id]);
  const members = useLoad(
    (signal) => gridsApi.members(id, page, signal),
    [id, page],
  );
  return (
    <>
      <Link to="/grids">← Grids</Link>
      <State {...detail} retry={detail.reload}>
        {detail.data && (
          <>
            <div className="page-heading">
              <div>
                <h1>{detail.data.name}</h1>
                <p>
                  {detail.data.community_count} сообществ ·{" "}
                  {date(detail.data.created_at)}
                </p>
              </div>
              <Link className="button primary" to={`/campaigns/new?grid=${id}`}>
                Создать кампанию
              </Link>
            </div>
            <div className="category-list">
              {detail.data.categories.map((c) => (
                <span key={JSON.stringify(c.category)}>
                  {categoryName(c.category)} <strong>{c.count}</strong>
                </span>
              ))}
            </div>
          </>
        )}
      </State>
      <GridReadinessPanel gridId={id} />
      <State {...members} retry={members.reload}>
        {members.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Community</th>
                  <th>Name</th>
                  <th>Category</th>
                  <th>Comment / content hint</th>
                  <th>VK resolved</th>
                  <th>Active</th>
                </tr>
              </thead>
              <tbody>
                {members.data.items.map((c) => (
                  <tr key={c.id}>
                    <td>{c.domain}</td>
                    <td>{c.name ?? "—"}</td>
                    <td>{categoryName(c.category)}</td>
                    <td>
                      <MemberHints
                        gridId={id}
                        community={c}
                        reload={members.reload}
                      />
                    </td>
                    <td>
                      {c.resolution_status ??
                        (c.vk_group_id ? "Unverified ID" : "Unresolved")}
                    </td>
                    <td>{c.is_active ? "Да" : "Нет"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">Сообществ на этой странице нет.</p>
        )}
      </State>
      <Pager
        page={page}
        hasNext={page * 25 < (members.data?.total ?? 0)}
        onPage={setPage}
        disabled={members.loading}
      />
    </>
  );
}
