import { request } from "./client";

export type PersonaPhotoKind = "selfie" | "other";
export type PersonaPhoto = {
  kind: PersonaPhotoKind;
  media_asset_id: string;
  tags: string[];
  width: number | null;
  height: number | null;
  created_at: string;
};
export type CampaignPersona = {
  categories: { category: string; count: number }[];
  targets: number;
  default_quota: number;
  accounts: {
    account_id: string;
    name: string;
    quota: number;
    selfies: number;
    others: number;
  }[];
};

const photos = (accountId: string, assetId?: string) =>
  `/accounts/${encodeURIComponent(accountId)}/persona-photos${assetId ? `/${assetId}` : ""}`;

export const personaApi = {
  campaign: (campaignId: string, signal?: AbortSignal) =>
    request<CampaignPersona>(`/campaigns/${campaignId}/persona`, { signal }),
  photos: (accountId: string, signal?: AbortSignal) =>
    request<PersonaPhoto[]>(photos(accountId), { signal }),
  /** Adds one more selfie or other photo of the girl behind this account. */
  add: (
    accountId: string,
    kind: PersonaPhotoKind,
    file: Blob,
    tags: string[],
  ) =>
    request<PersonaPhoto>(
      `${photos(accountId)}?${new URLSearchParams([["kind", kind], ...tags.map((t) => ["tags", t])])}`,
      { method: "POST", file },
    ),
  patch: (
    accountId: string,
    assetId: string,
    body: { kind?: PersonaPhotoKind; tags?: string[] },
  ) =>
    request<PersonaPhoto>(photos(accountId, assetId), {
      method: "PATCH",
      body,
    }),
  remove: (accountId: string, assetId: string) =>
    request<void>(photos(accountId, assetId), { method: "DELETE" }),
};
