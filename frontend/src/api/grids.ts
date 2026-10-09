import { request } from "./client";
import type {
  Grid,
  GridDetail,
  GridPreview,
  GridCommunity,
  Page,
} from "./types";
export const gridsApi = {
  readiness: (id: string, signal?: AbortSignal) =>
    request<GridReadiness>(`/grids/${id}/readiness`, { signal }),
  preparationJobs: (id: string, page: number, signal?: AbortSignal) =>
    request<Page<PreparationJob>>(
      `/grids/${id}/media-preparation?page=${page}`,
      { signal },
    ),
  prepare: (id: string, account_id: string, retry_failed = false) =>
    request<{ selected: number }>(`/grids/${id}/media-preparation`, {
      method: "POST",
      body: { account_id, retry_failed },
    }),
  list: (page: number, signal?: AbortSignal) =>
    request<Grid[]>(`/grids?limit=25&offset=${(page - 1) * 25}`, { signal }),
  detail: (id: string, signal?: AbortSignal) =>
    request<GridDetail>(`/grids/${id}?limit=1`, { signal }),
  members: (id: string, page: number, signal?: AbortSignal) =>
    request<Page<GridCommunity>>(
      `/grids/${id}/communities?page=${page}&page_size=25`,
      { signal },
    ),
  updateMember: (
    gridId: string,
    communityId: string,
    body: { comment: string | null; content_hint: string | null },
  ) =>
    request<GridCommunity>(`/grids/${gridId}/communities/${communityId}`, {
      method: "PATCH",
      body,
    }),
  parse: (text: string) =>
    request<GridPreview>("/grids/parse", { method: "POST", body: { text } }),
  import: (name: string, text: string) =>
    request<{ grid: { id: string } }>("/grids/import", {
      method: "POST",
      body: { name, text },
    }),
};

export type GridReadiness = {
  total: number;
  resolved: number;
  active_resolvable: number;
  unavailable: number;
  unresolved: number;
  transient: number;
  with_comment: number;
  with_content_hint: number;
  references_ready: number;
  media_context_ready: number;
  with_archive_indexed: number;
  archive_reuse_enabled: number;
  reference_warmup_target: number;
  ready_to_create_campaign: boolean;
  preparation_states: Record<string, number>;
};
export type PreparationJob = {
  id: string;
  community_id: string;
  account_id: string;
  state: string;
  error_code: string | null;
};
