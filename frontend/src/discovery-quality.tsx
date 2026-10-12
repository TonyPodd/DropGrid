type Rate = {
  approved: number;
  rejected: number;
  uncertain: number;
  reviewed: number;
  precision: number | null;
  approval_rate?: number | null;
  small_sample: boolean;
};
export type Quality = {
  overall: Rate;
  by: Record<string, Record<string, Rate>>;
  agreement: { overlapping_candidates: number; rate: number | null };
  precision_at_k: Record<string, Record<string, number | null>>;
  thresholds: (Rate & {
    threshold: number;
    would_be_wrong: number;
    unresolved: number;
  })[];
};
const percent = (v: number | null | undefined) =>
  v == null ? "N/A" : `${Math.round(v * 100)}%`;
export function DiscoveryQuality({ data }: { data?: Quality }) {
  return (
    <section className="discovery-progress">
      <h2>Качество Discovery</h2>
      <p>
        Precision: <strong>{percent(data?.overall?.precision)}</strong> ·
        Проверено: {data?.overall?.reviewed || 0} · Подходят:{" "}
        {data?.overall?.approved || 0} · Не подходят:{" "}
        {data?.overall?.rejected || 0} · Не уверен:{" "}
        {data?.overall?.uncertain || 0}
      </p>
      <p>Общая доля одобрений: {percent(data?.overall?.approval_rate)}.</p>
      <p>
        Precision считается только по «Подходит» / «Не подходит». Общая доля
        одобрений учитывает все проверенные. Неуверенные решения не обучают
        отрицательный класс.
      </p>
      <p>
        Согласие reviewers: {percent(data?.agreement?.rate)} (
        {data?.agreement?.overlapping_candidates || 0} общих кандидатов).
      </p>
      <details>
        <summary>Метрики и калибровка</summary>
        <p>
          Это фактические отношения в ручной выборке. Небольшая выборка не
          доказывает качество всей сетки. Веса не изменяются.
        </p>
        {Object.entries(data?.by || {}).map(([key, groups]) => (
          <div key={key}>
            <h3>{key}</h3>
            <table>
              <thead>
                <tr>
                  <th>Группа</th>
                  <th>Подходят / проверено</th>
                  <th>Precision</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(groups).map(([label, row]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td>
                      {row.approved} / {row.reviewed}
                      {row.small_sample ? " · малая выборка" : ""}
                    </td>
                    <td>{percent(row.precision)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {!data?.overall?.reviewed && (
          <p>
            Score buckets ≥0.90 / 0.80–0.89 / 0.70–0.79 / &lt;0.70: N/A — нет
            человеческих меток.
          </p>
        )}
        <h3>Precision@K по завершённым запускам</h3>
        {Object.entries(data?.precision_at_k || {}).map(([id, values], i) => (
          <p key={id}>
            Запуск {i + 1}: @5 {percent(values["5"])} · @10{" "}
            {percent(values["10"])} · @20 {percent(values["20"])}
          </p>
        ))}
        <p>
          N/A означает, что исходный top-K ещё не полностью проверен
          определёнными решениями.
        </p>
        <h3>Гипотетические пороги — auto-add выключен</h3>
        {data?.thresholds?.map((row) => (
          <p key={row.threshold}>
            Score ≥{row.threshold}: {row.reviewed} проверено ·{" "}
            {percent(row.precision)} · ошибочно добавленных было бы{" "}
            {row.reviewed ? row.would_be_wrong : "N/A"} · неуверенных{" "}
            {row.unresolved}. {row.small_sample ? "Малая выборка." : ""}
          </p>
        ))}
      </details>
    </section>
  );
}
