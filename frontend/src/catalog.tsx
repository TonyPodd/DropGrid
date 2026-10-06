import { useState } from "react";
import { accountsApi, communitiesApi, getDashboard } from "./api/accounts";
import { ApiError, errorMessage } from "./api/client";
import { CampaignTable } from "./campaigns";
import { campaignStatuses } from "./api/types";
import { Badge, categoryName, Pager, State, useLoad } from "./shared";
import { Link } from "react-router-dom";

export function DashboardPage() {
  const state = useLoad(getDashboard, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Dashboard</h1>
          <p>Подготовка кампаний без подключения VK.</p>
        </div>
        <Link className="button primary" to="/grids/new">
          Импортировать сетку
        </Link>
      </div>
      <State {...state} retry={state.reload}>
        {state.data && (
          <>
            <div className="metrics catalog-metrics">
              {Object.entries(state.data.counts).map(([name, count]) => (
                <Link key={name} to={`/${name}`}>
                  <span>{name}</span>
                  <strong>{count}</strong>
                </Link>
              ))}
            </div>
            <h2>Campaign status</h2>
            <div className="metrics">
              {campaignStatuses.map((status) => (
                <div key={status}>
                  <Badge status={status} />
                  <strong>{state.data?.campaign_statuses[status]}</strong>
                </div>
              ))}
            </div>
            <section>
              <h2>Recent campaigns</h2>
              <CampaignTable campaigns={state.data.recent_campaigns} />
            </section>
          </>
        )}
      </State>
    </>
  );
}
export function AccountsPage() {
  const [page, setPage] = useState(1);
  const state = useLoad((signal) => accountsApi.list(page, signal), [page]);
  const [busy, setBusy] = useState<string>();
  const [message, setMessage] = useState("");
  async function validate(id: string) {
    setBusy(id);
    setMessage("");
    try {
      const result = await accountsApi.validate(id);
      setMessage(
        result.valid
          ? "Аккаунт проверен."
          : "VK отклонил credentials аккаунта.",
      );
      state.reload();
    } catch (err) {
      setMessage(
        err instanceof ApiError && err.status === 503
          ? "VK credentials are not configured for this environment."
          : errorMessage(err),
      );
    } finally {
      setBusy(undefined);
    }
  }
  return (
    <>
      <h1>Accounts</h1>
      <p>
        Метаданные аккаунтов. OAuth пока не реализован; проверка VK запускается
        только вручную.
      </p>
      {message && <p role="status">{message}</p>}
      <State {...state} retry={state.reload}>
        {state.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>VK user ID</th>
                  <th>Gender tag</th>
                  <th>Status</th>
                  <th>Проверка</th>
                </tr>
              </thead>
              <tbody>
                {state.data.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td>{a.vk_user_id ?? "—"}</td>
                    <td>{a.gender_tag ?? "—"}</td>
                    <td>
                      <Badge status={a.status} />
                    </td>
                    <td>
                      <button
                        disabled={!!busy || a.status === "disabled"}
                        onClick={() => void validate(a.id)}
                      >
                        {busy === a.id ? "Проверка…" : "Validate"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">
            Аккаунтов пока нет. Добавьте метаданные через API; credentials для
            подготовки кампании не нужны.
          </p>
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
export function CommunitiesPage() {
  const [page, setPage] = useState(1);
  const state = useLoad((signal) => communitiesApi.list(page, signal), [page]);
  return (
    <>
      <h1>Communities</h1>
      <p>
        Общий каталог. Категория здесь — исходная категория сообщества; внутри
        сетки используется категория её связи.
      </p>
      <State {...state} retry={state.reload}>
        {state.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>VK name</th>
                  <th>Category</th>
                  <th>VK group ID</th>
                  <th>Active</th>
                  <th>Resolved</th>
                </tr>
              </thead>
              <tbody>
                {state.data.map((c) => (
                  <tr key={c.id}>
                    <td>{c.domain}</td>
                    <td>{c.name ?? "—"}</td>
                    <td>{categoryName(c.category)}</td>
                    <td>{c.vk_group_id ?? "—"}</td>
                    <td>{c.is_active ? "Да" : "Нет"}</td>
                    <td>{c.vk_group_id ? "Resolved" : "Not resolved"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">
            Сообществ пока нет. <Link to="/grids/new">Импортировать сетку</Link>
          </p>
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
