import { useEffect, useState } from "react";
import { errorMessage, request } from "./api/client";
import { Confirm, date, owners, State, useLoad } from "./shared";

type Profile = {
  id: string;
  username: string;
  url: string;
  enabled: boolean;
  status: string;
  error_code: string | null;
  photo_count: number;
  last_synced_at: string | null;
  added_by: string | null;
};
type Profiles = { configured: boolean; visual: boolean; profiles: Profile[] };

const statuses: Record<string, string> = {
  pending: "Ждёт загрузки",
  syncing: "Загружается…",
  ready: "Готов",
  error: "Ошибка",
};
const errors: Record<string, string> = {
  instagram_profile_unavailable:
    "Профиль не найден или личный: читаются только бизнес- и авторские профили",
  instagram_token_invalid: "Токен Instagram недействителен, обновите его на сервере",
  instagram_permission_missing: "У токена Instagram нет нужных прав",
  instagram_rate_limited: "Instagram ограничил запросы, повторим примерно через час",
  instagram_no_photos: "В последних постах нет подходящих фото",
  instagram_unavailable: "Instagram не ответил, повторим при следующей загрузке",
};

export function InstagramPage() {
  const [revision, setRevision] = useState(0);
  const [links, setLinks] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState<Profile | null>(null);
  const state = useLoad(
    (signal) => request<Profiles>("/instagram-profiles", { signal }),
    [revision],
  );
  const waiting = !!state.data?.profiles.some((p) =>
    ["pending", "syncing"].includes(p.status),
  );
  useEffect(() => {
    if (!waiting) return;
    // Profiles are read by the background worker; follow their status quietly.
    const timer = setInterval(() => setRevision((n) => n + 1), 30000);
    return () => clearInterval(timer);
  }, [waiting]);
  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await run();
      setRevision((n) => n + 1);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function add() {
    await act(async () => {
      const result = await request<{
        added: string[];
        existing: string[];
        invalid: string[];
      }>("/instagram-profiles", { method: "POST", body: { links } });
      setMessage(
        [
          `Добавлено профилей: ${result.added.length}`,
          result.existing.length
            ? `уже были: ${result.existing.join(", ")}`
            : "",
          result.invalid.length
            ? `не распознаны: ${result.invalid.join(", ")}`
            : "",
        ]
          .filter(Boolean)
          .join(" · "),
      );
      setLinks("");
    });
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Instagram</h1>
          <p>
            Профили, из которых бот берёт фото. Раз в сутки он загружает фото из
            последних постов, сам выбирает, какие профили подходят каждому
            паблику, и предлагает самые похожие на то, что паблик публикует.
          </p>
        </div>
      </div>
      <State {...state} retry={state.reload}>
        {state.data && !state.data.configured && (
          <p role="alert">
            Instagram не подключён: на сервере нужно задать
            INSTAGRAM_ACCESS_TOKEN и INSTAGRAM_BUSINESS_ACCOUNT_ID. Ссылки можно
            добавить уже сейчас, бот загрузит их после подключения.
          </p>
        )}
        {state.data && !state.data.visual && (
          <p role="alert">
            Анализ фото (CLIP) выключен: без него бот не сможет выбрать
            подходящие фото.
          </p>
        )}
        <section className="form-card">
          <label>
            Ссылки на профили
            <textarea
              rows={5}
              value={links}
              placeholder={
                "https://www.instagram.com/profile/\n@other.profile\nпо одной в строке"
              }
              onChange={(e) => setLinks(e.target.value)}
            />
          </label>
          <p className="note">
            Читаются только бизнес- и авторские профили, личные Instagram не
            отдаёт.
          </p>
          <button
            className="primary"
            disabled={busy || !links.trim()}
            onClick={() => void add()}
          >
            Добавить
          </button>
          {message && <p role="status">{message}</p>}
          {error && <p role="alert">{error}</p>}
        </section>
        {state.data?.profiles.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Профиль</th>
                  <th>Статус</th>
                  <th>Фото</th>
                  <th>Загружен</th>
                  <th>Добавил</th>
                  <th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {state.data.profiles.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <a href={p.url} target="_blank" rel="noreferrer">
                        @{p.username}
                      </a>
                    </td>
                    <td>
                      {statuses[p.status] ?? p.status}
                      {p.error_code && (
                        <p className="note">
                          {errors[p.error_code] ?? p.error_code}
                        </p>
                      )}
                    </td>
                    <td>{p.photo_count}</td>
                    <td>{p.last_synced_at ? date(p.last_synced_at) : "—"}</td>
                    <td>
                      {p.added_by ? (owners[p.added_by] ?? p.added_by) : "—"}
                    </td>
                    <td>
                      <div className="actions">
                        <button
                          disabled={busy || p.status === "syncing"}
                          onClick={() =>
                            void act(() =>
                              request(`/instagram-profiles/${p.id}/sync`, {
                                method: "POST",
                              }),
                            )
                          }
                        >
                          Загрузить заново
                        </button>
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() => setRemoving(p)}
                        >
                          Удалить
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">Профилей пока нет. Добавьте ссылки выше.</p>
        )}
      </State>
      {removing && (
        <Confirm
          title="Удалить профиль"
          busy={busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() =>
            void act(async () => {
              await request(`/instagram-profiles/${removing.id}`, {
                method: "DELETE",
              });
              setRemoving(null);
            })
          }
        >
          <p>
            Фото @{removing.username} перестанут предлагаться пабликам. Уже
            предложенные останутся в истории.
          </p>
        </Confirm>
      )}
    </>
  );
}
