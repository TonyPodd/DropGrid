import { request } from "./client";
import type {
  Grid,
  GridDetail,
  GridPreview,
  GridCommunity,
  Page,
} from "./types";
export const gridsApi = {
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
