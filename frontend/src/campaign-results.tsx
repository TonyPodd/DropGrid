import { request } from "./api/client";
import { State, useLoad } from "./shared";
export function CampaignResults({
  id,
  revision,
}: {
  id: string;
  revision: number;
}) {
  const state = useLoad(
    (signal) =>
      request<{
        breakdowns: Record<string, Record<string, Record<string, number>>>;
      }>(`/campaigns/${id}/results-breakdown`, { signal }),
    [id, revision],
  );
  return (
    <section>
      <h2>Результаты по категориям, источникам и аккаунтам</h2>
      <State {...state} retry={state.reload}>
        {state.data &&
          Object.entries(state.data.breakdowns).map(([kind, values]) => (
            <details key={kind}>
              <summary>
                {
                  {
                    category: "Категории",
                    provider: "Источники",
                    account: "Аккаунты",
                  }[kind]
                }
              </summary>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Группа</th>
                      {[
                        "total",
                        "sent",
                        "submitted",
                        "published",
                        "not_found",
                        "failed",
                        "skipped",
                      ].map((k) => (
                        <th key={k}>{k}</th>
                      ))}
                      <th>Принято / отправлено</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(values).map(([label, v]) => (
                      <tr key={label}>
                        <td>{label}</td>
                        {[
                          "total",
                          "sent",
                          "submitted",
                          "published",
                          "not_found",
                          "failed",
                          "skipped",
                        ].map((k) => (
                          <td key={k}>{v[k] ?? 0}</td>
                        ))}
                        <td>{((v.acceptance_rate ?? 0) * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ))}
      </State>
    </section>
  );
}
