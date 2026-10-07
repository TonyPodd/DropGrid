import { useState } from "react";
import { request, mediaContentUrl, errorMessage } from "./api/client";
import { campaignsApi } from "./api/campaigns";
import type { MediaAsset, MediaPlan, Page } from "./api/types";
import { categoryName, date, Pager, State, useLoad } from "./shared";

export function PixabayCredit() {
  return (
    <p className="note">
      Images provided by{" "}
      <a href="https://pixabay.com/" target="_blank" rel="noreferrer">
        Pixabay
      </a>
    </p>
  );
}

export function PhotoPlanButton({
  campaignId,
  onPlanned,
  showResult = true,
}: {
  campaignId: string;
  onPlanned: (result: MediaPlan) => void;
  showResult?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MediaPlan>();
  const [error, setError] = useState("");
  async function plan() {
    setBusy(true);
    setError("");
    setResult(undefined);
    try {
      const value = await campaignsApi.planMedia(campaignId);
      setResult(value);
      onPlanned(value);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="photo-plan">
      <button disabled={busy} onClick={() => void plan()}>
        {busy ? "Подбираем фото…" : "Подобрать фото"}
      </button>
      {busy && <p role="status">Поиск и обработка фотографий…</p>}
      {error && <p role="alert">{error}</p>}
      {result && showResult && <PhotoPlanResult result={result} />}
    </div>
  );
}

export function PhotoPlanResult({ result }: { result: MediaPlan }) {
  return (
    <div role="status">
      {result.categories.some((c) =>
        c.warnings.includes("provider_context_restricted"),
      ) && (
        <p>
          Для отдельных категорий источник запрещает этот контекст
          использования. Фото для них не назначены.
        </p>
      )}
      <p>
        Фото: {result.total_submissions - result.unassigned} /{" "}
        {result.total_submissions} · Уникальных: {result.unique_assets} · Без
        фото: {result.unassigned}
      </p>
      {result.categories.some((c) =>
        c.warnings.includes("provider_unavailable"),
      ) && (
        <p>
          Провайдер фото недоступен. Проверьте настройку Pixabay на backend.
          Доступные фото из библиотеки сохранены.
        </p>
      )}
      {result.categories.some(
        (c) =>
          c.warnings.length > 0 && !c.warnings.includes("provider_unavailable"),
      ) && (
        <p>
          Для части категорий недостаточно подходящих фото. Назначены только
          прошедшие проверку изображения.
        </p>
      )}
      <PixabayCredit />
    </div>
  );
}

function safeLink(value: string | null) {
  try {
    const url = new URL(value ?? "");
    return url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function MediaPage() {
  const [page, setPage] = useState(1);
  const [category, setCategory] = useState("");
  const [provider, setProvider] = useState("");
  const [enabled, setEnabled] = useState("");
  const state = useLoad(
    (signal) => {
      const params = new URLSearchParams({
        page: String(page),
        page_size: "25",
      });
      if (category) params.set("category", category);
      if (provider) params.set("provider", provider);
      if (enabled) params.set("enabled", enabled);
      return request<Page<MediaAsset>>(`/media-assets?${params}`, { signal });
    },
    [page, category, provider, enabled],
  );
  return (
    <>
      <h1>Media</h1>
      <p>Локальная библиотека фотографий и сведения об источниках.</p>
      <PixabayCredit />
      <div className="filters">
        <label>
          Категория
          <input
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          Провайдер
          <input
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          Доступность
          <select
            value={enabled}
            onChange={(e) => {
              setEnabled(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все</option>
            <option value="true">Включены</option>
            <option value="false">Отключены</option>
          </select>
        </label>
      </div>
      <State {...state} retry={state.reload}>
        {state.data?.items.length ? (
          <div className="media-library">
            {state.data.items.map((asset) => (
              <article className="media-card" key={asset.id}>
                <img
                  src={mediaContentUrl(asset.id)}
                  alt={`Фото: ${categoryName(asset.category)}`}
                  loading="lazy"
                />
                <h2>{categoryName(asset.category)}</h2>
                <p>
                  {asset.provider ?? "Неизвестный источник"} ·{" "}
                  {asset.creator_name || "Автор не указан"}
                </p>
                <p>
                  {asset.width ?? "—"} × {asset.height ?? "—"} · Использований:{" "}
                  {asset.usage_count}
                </p>
                <p>
                  Последнее использование:{" "}
                  {asset.last_used_at
                    ? date(asset.last_used_at)
                    : "Ещё не использовано"}
                </p>
                <p>{asset.enabled ? "Включено" : "Отключено"}</p>
                {safeLink(asset.source_url) && (
                  <a
                    href={safeLink(asset.source_url)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Источник ↗
                  </a>
                )}
                {safeLink(asset.license_url) && (
                  <p>
                    <a
                      href={safeLink(asset.license_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {asset.license_name || "Лицензия"}
                    </a>
                  </p>
                )}
                {asset.requires_publication_attribution && (
                  <p>
                    Требуется attribution при публикации:{" "}
                    {asset.attribution_text}
                  </p>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="empty">
            Фотографий пока нет. Подберите фото в подготовленной кампании.
          </p>
        )}
      </State>
      <Pager
        page={page}
        hasNext={page * 25 < (state.data?.total ?? 0)}
        onPage={setPage}
        disabled={state.loading}
      />
    </>
  );
}
