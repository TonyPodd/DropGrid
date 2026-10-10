import { useState } from "react";
import { request } from "./api/client";
import { campaignsApi } from "./api/campaigns";
import { errorMessage } from "./api/client";
import type { Campaign } from "./api/types";
import { Confirm, State, useLoad } from "./shared";

export function CampaignSendControls({
  campaign,
  onChanged,
}: {
  campaign: Campaign;
  onChanged: () => void;
}) {
  const accounts = useLoad(
    (signal) =>
      request<
        { account_id: string; name: string; assigned: number; quota: number }[]
      >(`/campaigns/${campaign.id}/account-pool`, { signal }),
    [campaign.id, campaign.preparation_state],
  );
  const [limit, setLimit] = useState("1");
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<"start" | "cancel" | null>(null);
  const validLimit =
    full ||
    (Number.isInteger(Number(limit)) &&
      Number(limit) >= 1 &&
      Number(limit) <= 10000);
  const scope = {
    max_submissions: full ? null : Number(limit),
  };
  const report = useLoad(
    (signal) =>
      campaign.status === "ready" && validLimit
        ? campaignsApi.preflight(campaign.id, scope, signal)
        : Promise.resolve(undefined),
    [campaign.id, campaign.status, campaign.preparation_state, limit, full],
  );
  async function act() {
    setBusy(true);
    setError("");
    try {
      if (confirm === "start") await campaignsApi.start(campaign.id, scope);
      else await campaignsApi.cancel(campaign.id);
      setConfirm(null);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  if (
    campaign.is_dry_run ||
    !["ready", "running", "monitoring"].includes(campaign.status)
  )
    return null;
  return (
    <section className="form-card">
      <h2>Отправка кампании</h2>
      {campaign.status === "ready" && (
        <>
          <State {...accounts} retry={accounts.reload}>
            <p>
              Выбранный пул:{" "}
              {accounts.data
                ?.map((a) => `${a.name} ${a.assigned}/${a.quota}`)
                .join(" · ")}
            </p>
          </State>
          <label>
            <input
              type="checkbox"
              checked={full}
              onChange={(e) => setFull(e.target.checked)}
            />
            Вся подготовленная кампания
          </label>
          {!full && (
            <label>
              Pilot: первые N доступных сообществ
              <input
                type="number"
                min="1"
                max="10000"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
              />
            </label>
          )}
          <p>
            Остальные submissions pilot-кампании будут пропущены. Фото должны
            быть подготовлены заранее.
          </p>
          {
            <State {...report} retry={report.reload}>
              {report.data && (
                <p role="status">
                  Доступно: {report.data.resolved_sendable} · Недоступно:{" "}
                  {report.data.unavailable} · Несовместимый Account:{" "}
                  {report.data.gender_incompatible} · В scope:{" "}
                  {report.data.intended} · Фото назначено:{" "}
                  {report.data.media_assigned} · Без фото:{" "}
                  {report.data.media_missing} · Некорректных файлов:{" "}
                  {report.data.media_invalid} · Аккаунтов готово:{" "}
                  {report.data.accounts_ready} · Ёмкость:{" "}
                  {report.data.total_send_capacity} · Без назначения:{" "}
                  {report.data.capacity_unassigned} · Ожидают проверки:{" "}
                  {report.data.review_pending}
                </p>
              )}
            </State>
          }
          <button
            className="primary"
            disabled={
              busy ||
              (campaign.preparation_state !== undefined &&
                !["ready", "legacy"].includes(campaign.preparation_state)) ||
              !validLimit ||
              !report.data?.ready ||
              report.loading ||
              !!report.error
            }
            onClick={() => {
              setError("");
              setConfirm("start");
            }}
          >
            Start campaign
          </button>
        </>
      )}
      <button
        disabled={busy}
        onClick={() => {
          setError("");
          setConfirm("cancel");
        }}
      >
        Cancel campaign
      </button>
      {confirm && (
        <Confirm
          title={
            confirm === "start" ? "Запустить отправку" : "Остановить кампанию"
          }
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void act()}
        >
          <p>
            {confirm === "start"
              ? `Worker отправит ${report.data?.intended ?? 0} предложенных постов с выбранного пула аккаунтов.`
              : "Новые отправки прекратятся. Уже отправленные посты сохранятся."}
          </p>
          {error && <p role="alert">{error}</p>}
        </Confirm>
      )}
    </section>
  );
}
