import { useEffect, useState } from "react";
import { accountsApi } from "./api/accounts";
import { ApiError, errorMessage, mediaContentUrl } from "./api/client";
import {
  personaApi,
  type CampaignPersona,
  type PersonaPhoto,
  type PersonaPhotoKind,
} from "./api/persona";
import type { Account } from "./api/types";
import { accountName, genderLabel, plural, State, useLoad } from "./shared";

/** Selfies and other photos of one girl; she needs at least one of each. */
export type PhotoCounts = Record<PersonaPhotoKind, number>;
const SELFIE_TAG = "селфи";
const KINDS: { kind: PersonaPhotoKind; title: string; hint: string }[] = [
  { kind: "selfie", title: "Селфи", hint: "например: лицо крупно" },
  { kind: "other", title: "Не селфи", hint: "например: в зеркале, в полный рост" },
];
const complete = (counts: PhotoCounts | undefined) =>
  !!counts && counts.selfie > 0 && counts.other > 0;

const splitTags = (value: string) =>
  value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);

export const accountsNeeded = (summary: CampaignPersona) =>
  Math.ceil(summary.targets / summary.default_quota);

/** Whether the chosen girl accounts can take every dating/selfie public. */
export function personaReady(
  summary: CampaignPersona | undefined,
  accounts: Account[],
  girls: string[],
  photos: Record<string, PhotoCounts>,
) {
  if (!summary?.targets) return true;
  const quota = (id: string) =>
    accounts.find((a) => a.id === id)?.campaign_send_quota ??
    summary.default_quota;
  const capacity = girls.reduce((sum, id) => sum + quota(id), 0);
  return (
    capacity >= summary.targets && girls.every((id) => complete(photos[id]))
  );
}

export function PersonaSetup({
  summary,
  accounts,
  girls,
  photos,
  onGirls,
  onPhotos,
  onAccountsChanged,
}: {
  summary: CampaignPersona;
  accounts: Account[];
  girls: string[];
  photos: Record<string, PhotoCounts>;
  onGirls: (ids: string[]) => void;
  onPhotos: (accountId: string, counts: PhotoCounts) => void;
  onAccountsChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const needed = accountsNeeded(summary);
  const quota = (id: string) =>
    accounts.find((a) => a.id === id)?.campaign_send_quota ??
    summary.default_quota;
  const capacity = girls.reduce((sum, id) => sum + quota(id), 0);
  const slots = Math.max(needed, girls.length + (adding ? 1 : 0));
  function pick(index: number, id: string) {
    const next = [...girls];
    next[index] = id;
    onGirls(next.filter(Boolean));
    setAdding(false);
  }
  return (
    <section className="persona-setup">
      <h3>Знакомства и селфи — фото девочки</h3>
      <p>
        В сетке {summary.targets}{" "}
        {plural(summary.targets, "паблик", "паблика", "пабликов")} знакомств и
        селфи:{" "}
        {summary.categories.map((c) => `${c.category} (${c.count})`).join(", ")}
        .
      </p>
      <p className="note">
        1 аккаунт — {summary.default_quota}{" "}
        {plural(summary.default_quota, "паблик", "паблика", "пабликов")}. Нужно
        аккаунтов-девочек: <strong>{needed}</strong>. У каждого аккаунта своя
        девочка: загрузите её селфи и фото не селфи — минимум по одному, можно
        несколько. Эти фото уходят только с этого аккаунта. Бот изучит каждый
        паблик и предложит то фото девочки, которое подходит лучше. После этих
        пабликов аккаунт продолжит обычные категории.
      </p>
      {Array.from({ length: slots }, (_, index) => (
        <GirlSlot
          key={girls[index] ?? `empty-${index}`}
          index={index}
          accountId={girls[index]}
          accounts={accounts}
          taken={girls}
          quota={quota}
          onPick={(id) => pick(index, id)}
          onConnected={(id) => {
            onAccountsChanged();
            pick(index, id);
          }}
          onRemove={() => onGirls(girls.filter((_, i) => i !== index))}
          onPhotos={onPhotos}
          photos={photos}
        />
      ))}
      {girls.length >= needed && !adding && (
        <button type="button" onClick={() => setAdding(true)}>
          + Ещё аккаунт-девочка
        </button>
      )}
      {capacity < summary.targets && (
        <p role="alert">
          Не хватает аккаунтов-девочек: подключено на {capacity} из{" "}
          {summary.targets} пабликов.
        </p>
      )}
    </section>
  );
}

function GirlSlot({
  index,
  accountId,
  accounts,
  taken,
  quota,
  photos,
  onPick,
  onConnected,
  onRemove,
  onPhotos,
}: {
  index: number;
  accountId: string | undefined;
  accounts: Account[];
  taken: string[];
  quota: (id: string) => number;
  photos: Record<string, PhotoCounts>;
  onPick: (id: string) => void;
  onConnected: (id: string) => void;
  onRemove: () => void;
  onPhotos: (accountId: string, counts: PhotoCounts) => void;
}) {
  const title = `Аккаунт-девочка ${index + 1}`;
  const account = accounts.find((a) => a.id === accountId);
  if (accountId)
    return (
      <div className="girl-slot" role="group" aria-label={title}>
        <div className="girl-slot-head">
          <h4>
            {title}: {account?.name ?? "загрузка…"}
          </h4>
          {account && (
            <span className="muted">
              {genderLabel(account.gender_tag)} · квота {quota(account.id)} ·
              селфи: {photos[account.id]?.selfie ?? 0} · не селфи:{" "}
              {photos[account.id]?.other ?? 0}
            </span>
          )}
          <button type="button" onClick={onRemove}>
            Убрать
          </button>
        </div>
        <PersonaPhotos
          accountId={accountId}
          name={account?.name ?? title}
          onCount={(n) => onPhotos(accountId, n)}
        />
      </div>
    );
  return (
    <div className="girl-slot" role="group" aria-label={title}>
      <h4>{title}</h4>
      <AccountPicker
        title={title}
        accounts={accounts.filter((a) => !taken.includes(a.id))}
        onPick={onPick}
        onConnected={onConnected}
      />
    </div>
  );
}

function AccountPicker({
  title,
  accounts,
  onPick,
  onConnected,
}: {
  title: string;
  accounts: Account[];
  onPick: (id: string) => void;
  onConnected: (id: string) => void;
}) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function connect() {
    setBusy(true);
    setError("");
    try {
      const account = await accountsApi.connect(token);
      // A girl's account sends her own gender's categories after her publics.
      if (!account.gender_tag)
        await accountsApi.patch(account.id, { gender_tag: "female" });
      onConnected(account.id);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "VK отклонил токен. Проверьте его и попробуйте снова."
          : err instanceof ApiError && err.status === 503
            ? "Хранилище токенов недоступно. Проверьте настройки сервера."
            : errorMessage(err),
      );
    } finally {
      setToken("");
      setBusy(false);
    }
  }
  return (
    <div className="girl-picker">
      {accounts.length > 0 && (
        <label>
          Выбрать подключённый аккаунт
          <select
            aria-label={`${title}: подключённый аккаунт`}
            value=""
            onChange={(e) => e.target.value && onPick(e.target.value)}
          >
            <option value="">— выберите —</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {accountName(a)} · {genderLabel(a.gender_tag)}
              </option>
            ))}
          </select>
        </label>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (token.trim()) void connect();
        }}
      >
        <label>
          {accounts.length ? "или подключить новый по токену" : "Токен VK аккаунта"}
          <input
            type="password"
            aria-label={`${title}: токен VK`}
            autoComplete="off"
            spellCheck={false}
            maxLength={8192}
            value={token}
            disabled={busy}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || !token.trim()}>
          {busy ? "Подключаем…" : "Подключить"}
        </button>
      </form>
      <p className="muted">
        Токен хранится в зашифрованном виде и никому не показывается.
      </p>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export function PersonaPhotos({
  accountId,
  name,
  onCount,
}: {
  accountId: string;
  name: string;
  onCount: (counts: PhotoCounts) => void;
}) {
  const [revision, setRevision] = useState(0);
  const photos = useLoad(
    (signal) => personaApi.photos(accountId, signal),
    [accountId, revision],
  );
  const [error, setError] = useState("");
  useEffect(() => {
    if (photos.data)
      onCount({
        selfie: photos.data.filter((p) => p.kind === "selfie").length,
        other: photos.data.filter((p) => p.kind === "other").length,
      });
    // The parent's callback identity changes every render; only data matters.
  }, [photos.data]);
  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 422
          ? "Фото не подошло: нужен JPEG, PNG или WEBP, короткая сторона от 850 px."
          : errorMessage(err),
      );
    } finally {
      setRevision((n) => n + 1);
    }
  }
  return (
    <div className="persona-photos">
      {error && <p role="alert">{error}</p>}
      <State {...photos} retry={photos.reload}>
        {KINDS.map(({ kind, title, hint }) => {
          const items = photos.data?.filter((p) => p.kind === kind) ?? [];
          const other = KINDS.find((k) => k.kind !== kind)!;
          return (
            <section
              key={kind}
              className="persona-kind"
              aria-label={`${title}: ${name}`}
            >
              <h5>
                {title} <span className="muted">{items.length}</span>
                {!items.length && (
                  <span className="persona-missing"> · нужно хотя бы одно</span>
                )}
              </h5>
              <div className="persona-grid">
                {items.map((photo) => (
                  <PhotoCard
                    key={photo.media_asset_id}
                    photo={photo}
                    title={title}
                    hint={hint}
                    moveTo={other.title}
                    onRetag={(tags) =>
                      run(() =>
                        personaApi.patch(accountId, photo.media_asset_id, {
                          tags,
                        }),
                      )
                    }
                    onMove={() =>
                      run(() =>
                        personaApi.patch(accountId, photo.media_asset_id, {
                          kind: other.kind,
                        }),
                      )
                    }
                    onRemove={() =>
                      run(() => personaApi.remove(accountId, photo.media_asset_id))
                    }
                  />
                ))}
                <AddPhotos
                  title={title}
                  name={name}
                  hint={hint}
                  onAdd={(files, tags) =>
                    run(async () => {
                      for (const file of files)
                        await personaApi.add(accountId, kind, file, tags);
                    })
                  }
                />
              </div>
            </section>
          );
        })}
      </State>
    </div>
  );
}

function PhotoCard({
  photo,
  title,
  hint,
  moveTo,
  onRetag,
  onMove,
  onRemove,
}: {
  photo: PersonaPhoto;
  title: string;
  hint: string;
  moveTo: string;
  onRetag: (tags: string[]) => void;
  onMove: () => void;
  onRemove: () => void;
}) {
  const label = photo.tags.filter((t) => t !== SELFIE_TAG).join(", ");
  const [text, setText] = useState(label);
  useEffect(() => setText(label), [label]);
  return (
    <figure className="persona-card">
      <img
        src={mediaContentUrl(photo.media_asset_id)}
        alt={`${title}: ${label || "фото девочки"}`}
        loading="lazy"
      />
      <figcaption>
        <input
          aria-label={`${title}: что на фото`}
          value={text}
          placeholder={hint}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            if (splitTags(text).join(", ") !== label) onRetag(splitTags(text));
          }}
        />
        <button type="button" onClick={onMove}>
          В «{moveTo}»
        </button>
        <button type="button" onClick={onRemove}>
          Удалить
        </button>
      </figcaption>
    </figure>
  );
}

function AddPhotos({
  title,
  name,
  hint,
  onAdd,
}: {
  title: string;
  name: string;
  hint: string;
  onAdd: (files: File[], tags: string[]) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="persona-card persona-add">
      <div className="persona-empty">+ {title.toLowerCase()}</div>
      <figcaption>
        <input
          aria-label={`Новые ${title.toLowerCase()}: что на фото`}
          value={text}
          placeholder={hint}
          onChange={(e) => setText(e.target.value)}
        />
        <label className="button">
          {busy ? "Загрузка…" : "Добавить фото"}
          <input
            type="file"
            aria-label={`Добавить ${title.toLowerCase()} для ${name}`}
            accept="image/jpeg,image/png,image/webp"
            multiple
            disabled={busy}
            onChange={(e) => {
              const files = [...(e.target.files ?? [])];
              e.target.value = "";
              if (!files.length) return;
              setBusy(true);
              void onAdd(files, splitTags(text)).finally(() => setBusy(false));
            }}
          />
        </label>
      </figcaption>
    </div>
  );
}
