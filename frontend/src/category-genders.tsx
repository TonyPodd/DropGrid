import { useState, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { accountsApi } from "./api/accounts";
import { errorMessage } from "./api/client";
import { gridsApi } from "./api/grids";
import type {
  CategoryGender,
  CategoryGenderRow,
  GridCategoryGenders,
} from "./api/types";
import { categoryName, date, plural, State, useLoad } from "./shared";

export const genderColumns: {
  gender: CategoryGender;
  title: string;
  short: string;
}[] = [
  { gender: "male", title: "Мужские", short: "М" },
  { gender: "female", title: "Женские", short: "Ж" },
  { gender: "unisex", title: "Унисекс", short: "У" },
];

type Placement = Record<string, CategoryGender | null>;
type DragProps = {
  draggable: boolean;
  onDragStart: (event: DragEvent) => void;
};

// Communities imported before any heading have a null category.
const keyOf = (category: string | null) =>
  category === null ? "none" : `c:${category}`;

function initialPlacement(data: GridCategoryGenders): Placement {
  return Object.fromEntries(
    data.categories.map((row) => [
      keyOf(row.category),
      row.gender ?? row.suggested_gender,
    ]),
  );
}

export function CategoryGendersPage() {
  const [params, setParams] = useSearchParams();
  const grids = useLoad((signal) => gridsApi.latest(signal), []);
  const gridId = params.get("grid") ?? grids.data?.[0]?.id ?? "";
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Categories</h1>
          <p>
            Разложите категории сетки по столбцам. Бот берёт аккаунты по
            очереди: сначала отправляет в категории пола аккаунта, потом в
            унисекс. Когда категории для аккаунта закончились, переходит к
            следующему аккаунту.
          </p>
        </div>
        <Link className="button" to="/grids/new">
          Импортировать сетку
        </Link>
      </div>
      <State {...grids} retry={grids.reload}>
        {grids.data?.length ? (
          <>
            <label className="grid-picker">
              Сетка
              <select
                value={gridId}
                onChange={(event) => setParams({ grid: event.target.value })}
              >
                {!grids.data.some((grid) => grid.id === gridId) && (
                  <option value={gridId}>Выбранная сетка</option>
                )}
                {grids.data.map((grid) => (
                  <option key={grid.id} value={grid.id}>
                    {grid.name} · {grid.category_count}{" "}
                    {plural(
                      grid.category_count,
                      "категория",
                      "категории",
                      "категорий",
                    )}
                  </option>
                ))}
              </select>
            </label>
            <AccountGenders />
            {gridId && <CategoryBoard key={gridId} gridId={gridId} />}
          </>
        ) : (
          <div className="empty">
            Сеток пока нет. <Link to="/grids/new">Импортируйте сетку</Link> —
            категории определятся автоматически.
          </div>
        )}
      </State>
    </>
  );
}

function AccountGenders() {
  const accounts = useLoad((signal) => accountsApi.all(signal), []);
  if (!accounts.data) return null;
  const usable = accounts.data.filter(
    (a) => a.status === "active" && a.token_configured && a.vk_user_id,
  );
  const count = (gender: string) =>
    usable.filter((a) => a.gender_tag === gender).length;
  const other = usable.length - count("male") - count("female");
  return (
    <p className="note">
      Аккаунтов с токеном: мужских — {count("male")}, женских —{" "}
      {count("female")}
      {other > 0 && `, без пола — ${other} (отправляют только в унисекс)`}.
      Пол аккаунта задаётся на странице <Link to="/accounts">Accounts</Link>.
    </p>
  );
}

function CategoryBoard({ gridId }: { gridId: string }) {
  const state = useLoad(
    (signal) => gridsApi.categoryGenders(gridId, signal),
    [gridId],
  );
  return (
    <State {...state} retry={state.reload}>
      {state.data &&
        (state.data.categories.length ? (
          <Board gridId={gridId} initial={state.data} />
        ) : (
          <p className="empty">В этой сетке нет сообществ.</p>
        ))}
    </State>
  );
}

function Board({
  gridId,
  initial,
}: {
  gridId: string;
  initial: GridCategoryGenders;
}) {
  const [saved, setSaved] = useState(initial);
  const [placement, setPlacement] = useState(() => initialPlacement(initial));
  const [selected, setSelected] = useState<string[]>([]);
  const [asking, setAsking] = useState<Placement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const rows = saved.categories;
  const unplaced = rows.filter((row) => !placement[keyOf(row.category)]);
  const dirty = rows.some(
    (row) => placement[keyOf(row.category)] !== row.gender,
  );
  const suggested = rows.some(
    (row) =>
      !row.gender &&
      row.suggested_gender &&
      placement[keyOf(row.category)] === row.suggested_gender,
  );

  function place(keys: string[], gender: CategoryGender | null) {
    setPlacement((current) => ({
      ...current,
      ...Object.fromEntries(keys.map((key) => [key, gender])),
    }));
    setSelected((current) => current.filter((key) => !keys.includes(key)));
    setMessage("");
  }
  function dropped(event: DragEvent, gender: CategoryGender | null) {
    event.preventDefault();
    const key = event.dataTransfer.getData("text/plain");
    if (!(key in placement)) return;
    place(selected.includes(key) ? selected : [key], gender);
  }
  const dragProps = (key: string): DragProps => ({
    draggable: true,
    onDragStart: (event: DragEvent) =>
      event.dataTransfer.setData("text/plain", key),
  });
  const dropProps = (gender: CategoryGender | null) => ({
    onDragOver: (event: DragEvent) => event.preventDefault(),
    onDrop: (event: DragEvent) => dropped(event, gender),
  });

  async function submit(final: Placement) {
    setBusy(true);
    setError("");
    try {
      const result = await gridsApi.saveCategoryGenders(
        gridId,
        rows.map((row) => ({
          category: row.category,
          gender: final[keyOf(row.category)] as CategoryGender,
        })),
      );
      setSaved(result);
      setPlacement(initialPlacement(result));
      setSelected([]);
      setAsking(null);
      setMessage(
        "Распределение сохранено. Бот использует его при подготовке и запуске кампаний.",
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  function save() {
    if (unplaced.length)
      setAsking(
        Object.fromEntries(unplaced.map((row) => [keyOf(row.category), null])),
      );
    else void submit(placement);
  }

  const status = dirty
    ? "Есть несохранённые изменения."
    : saved.complete
      ? `Сохранено${saved.updated_at ? ` ${date(saved.updated_at)}` : ""}.`
      : rows.some((row) => row.gender)
        ? "В сетке появились новые категории — распределите их."
        : "Распределение ещё не сохранено: бот не запустит кампании по этой сетке.";

  return (
    <>
      {suggested && (
        <p className="note">
          Часть категорий предзаполнена по прошлым сеткам с такими же
          названиями. Проверьте и сохраните.
        </p>
      )}
      <section className="gender-pool" {...dropProps(null)}>
        <h2>
          Нераспределённые категории{" "}
          <span className="muted">{unplaced.length}</span>
        </h2>
        {unplaced.length ? (
          <>
            <p className="muted">
              Отметьте категории и нажмите «Добавить» над нужным столбцом или
              перетащите их мышкой.
            </p>
            <div className="category-list">
              {unplaced.map((row) => {
                const key = keyOf(row.category);
                return (
                  <button
                    key={key}
                    type="button"
                    className="category-chip"
                    aria-pressed={selected.includes(key)}
                    onClick={() =>
                      setSelected((current) =>
                        current.includes(key)
                          ? current.filter((k) => k !== key)
                          : [...current, key],
                      )
                    }
                    {...dragProps(key)}
                  >
                    {categoryName(row.category)} <strong>{row.count}</strong>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() =>
                setSelected(unplaced.map((row) => keyOf(row.category)))
              }
            >
              Выбрать все
            </button>
          </>
        ) : (
          <p className="success">Все категории распределены.</p>
        )}
      </section>
      <div className="table-wrap gender-board">
        <table>
          <thead>
            <tr>
              {genderColumns.map((column) => {
                const items = rows.filter(
                  (row) => placement[keyOf(row.category)] === column.gender,
                );
                const communities = items.reduce(
                  (sum, row) => sum + row.count,
                  0,
                );
                return (
                  <th key={column.gender} scope="col">
                    <div className="gender-head">
                      <span className="gender-title">{column.title}</span>
                      <span className="muted">
                        {items.length} кат. · {communities}{" "}
                        {plural(
                          communities,
                          "сообщество",
                          "сообщества",
                          "сообществ",
                        )}
                      </span>
                      <button
                        type="button"
                        aria-label={`Добавить выбранные в «${column.title}»`}
                        disabled={!selected.length}
                        onClick={() => place(selected, column.gender)}
                      >
                        Добавить
                        {selected.length ? ` (${selected.length})` : ""}
                      </button>
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            <tr>
              {genderColumns.map((column) => (
                <td
                  key={column.gender}
                  data-testid={`column-${column.gender}`}
                  {...dropProps(column.gender)}
                >
                  <ColumnItems
                    rows={rows.filter(
                      (row) => placement[keyOf(row.category)] === column.gender,
                    )}
                    column={column.gender}
                    dragProps={dragProps}
                    onPlace={place}
                  />
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <div className="actions">
        <button
          className="primary"
          disabled={busy || (!dirty && saved.complete)}
          onClick={save}
        >
          {busy && !asking ? "Сохранение…" : "Сохранить распределение"}
        </button>
        <button
          type="button"
          disabled={busy || !dirty}
          onClick={() => {
            setPlacement(initialPlacement(saved));
            setSelected([]);
          }}
        >
          Отменить изменения
        </button>
        <span className="muted" role="status">
          {message || status}
        </span>
      </div>
      {error && !asking && <p role="alert">{error}</p>}
      {asking && (
        <AskColumns
          rows={rows.filter((row) => keyOf(row.category) in asking)}
          choice={asking}
          busy={busy}
          error={error}
          onChoose={(key, gender) =>
            setAsking((current) => ({ ...current, [key]: gender }))
          }
          onCancel={() => {
            setAsking(null);
            setError("");
          }}
          onConfirm={() => void submit({ ...placement, ...asking })}
        />
      )}
    </>
  );
}

function ColumnItems({
  rows,
  column,
  dragProps,
  onPlace,
}: {
  rows: CategoryGenderRow[];
  column: CategoryGender;
  dragProps: (key: string) => DragProps;
  onPlace: (keys: string[], gender: CategoryGender | null) => void;
}) {
  if (!rows.length)
    return <p className="gender-empty">Перетащите категории сюда</p>;
  return (
    <ul className="gender-list">
      {rows.map((row) => {
        const key = keyOf(row.category);
        const name = categoryName(row.category);
        return (
          <li key={key} {...dragProps(key)}>
            <span className="gender-name">
              {name} <strong>{row.count}</strong>
              {!row.gender && row.suggested_gender === column && (
                <small className="muted"> · по прошлой сетке</small>
              )}
            </span>
            <span className="gender-moves">
              {genderColumns
                .filter((other) => other.gender !== column)
                .map((other) => (
                  <button
                    key={other.gender}
                    type="button"
                    title={`Перенести в «${other.title}»`}
                    aria-label={`Перенести ${name} в «${other.title}»`}
                    onClick={() => onPlace([key], other.gender)}
                  >
                    {other.short}
                  </button>
                ))}
              <button
                type="button"
                title="Вернуть в нераспределённые"
                aria-label={`Убрать ${name} из столбца`}
                onClick={() => onPlace([key], null)}
              >
                ×
              </button>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function AskColumns({
  rows,
  choice,
  busy,
  error,
  onChoose,
  onCancel,
  onConfirm,
}: {
  rows: CategoryGenderRow[];
  choice: Placement;
  busy: boolean;
  error: string;
  onChoose: (key: string, gender: CategoryGender) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="overlay">
      <section
        role="dialog"
        aria-modal="true"
        aria-label="Нераспределённые категории"
        className="dialog"
      >
        <h2>Куда добавить оставшиеся категории?</h2>
        <p>Выберите столбец для каждой категории — бот должен знать все.</p>
        <div className="ask-columns">
          {rows.map((row) => {
            const key = keyOf(row.category);
            const name = categoryName(row.category);
            return (
              <div key={key} className="ask-row" role="group" aria-label={name}>
                <span>
                  {name} <span className="muted">{row.count}</span>
                </span>
                <span className="segmented">
                  {genderColumns.map((column) => (
                    <button
                      key={column.gender}
                      type="button"
                      aria-pressed={choice[key] === column.gender}
                      onClick={() => onChoose(key, column.gender)}
                    >
                      {column.title}
                    </button>
                  ))}
                </span>
              </div>
            );
          })}
        </div>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button autoFocus disabled={busy} onClick={onCancel}>
            Отмена
          </button>
          <button
            className="primary"
            disabled={busy || rows.some((row) => !choice[keyOf(row.category)])}
            onClick={onConfirm}
          >
            {busy ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
      </section>
    </div>
  );
}
