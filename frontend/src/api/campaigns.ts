import { request } from "./client";
import type {
  Campaign,
  CampaignPreflight,
  CampaignInput,
  Stats,
  Submission,
  Page,
  Audio,
  MediaPlan,
} from "./types";
export const campaignsApi = {
  preflight: (id: string, body: { account_id: string; max_submissions: number | null }, signal?: AbortSignal) =>
    request<CampaignPreflight>(`/campaigns/${id}/preflight`, { method: "POST", body, signal }),
  start: (id: string, body: { account_id: string; max_submissions: number | null }) =>
    request<Campaign>(`/campaigns/${id}/start`, { method: "POST", body }),
  cancel: (id: string) => request<Campaign>(`/campaigns/${id}/cancel`, { method: "POST" }),
  list: (page: number, signal?: AbortSignal) =>
    request<Campaign[]>(`/campaigns?limit=25&offset=${(page - 1) * 25}`, {
      signal,
    }),
  detail: (id: string, signal?: AbortSignal) =>
    request<Campaign>(`/campaigns/${id}`, { signal }),
  create: (body: CampaignInput) =>
    request<{ id: string }>("/campaigns", { method: "POST", body }),
  patch: (id: string, body: Omit<CampaignInput, "grid_id">) =>
    request<
      Omit<Campaign, "grid_name" | "community_count" | "submission_count">
    >(`/campaigns/${id}`, { method: "PATCH", body }),
  prepare: (id: string) =>
    request<{ created: number; total: number }>(`/campaigns/${id}/prepare`, {
      method: "POST",
    }),
  planMedia: (id: string) =>
    request<MediaPlan>(`/campaigns/${id}/media/plan`, {
      method: "POST",
      body: { force: false },
    }),
  stats: (id: string, signal?: AbortSignal) =>
    request<Stats>(`/campaigns/${id}/stats`, { signal }),
  submissions: (
    id: string,
    page: number,
    status: string,
    category: string | null,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({ page: String(page), page_size: "25" });
    if (status) params.set("status", status);
    if (category !== null) params.set("category", category);
    return request<Page<Submission>>(`/campaigns/${id}/submissions?${params}`, {
      signal,
    });
  },
  parseTrack: (track_url: string, signal?: AbortSignal) =>
    request<Audio>("/tracks/parse", {
      method: "POST",
      body: { track_url },
      signal,
    }),
  checkPublication: (id: string) => request<{ current_status: string; evidence: Record<string, unknown> }>(
    `/submissions/${id}/check-publication`, { method: "POST" }),
  published: (id: string, signal?: AbortSignal) => request<{
    community: Submission["community"]; category: string | null;
    published_post_url: string; published_at: string;
  }[]>(`/campaigns/${id}/published`, { signal }),
};
