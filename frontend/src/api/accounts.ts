import { request } from "./client";
import type { Account, Community, Dashboard } from "./types";
export const accountsApi = {
  connect: (access_token: string) =>
    request<Account>("/accounts/connect", {
      method: "POST",
      body: { access_token },
    }),
  patch: (id: string, body: Partial<Account>) =>
    request<Account>(`/accounts/${id}`, { method: "PATCH", body }),
  all: async (signal?: AbortSignal): Promise<Account[]> => {
    const rows: Account[] = [];
    for (let page = 1; ; page++) {
      const next = await accountsApi.list(page, signal);
      rows.push(...next);
      if (next.length < 25) return rows;
    }
  },
  list: (page: number, signal?: AbortSignal) =>
    request<Account[]>(`/accounts?limit=25&offset=${(page - 1) * 25}`, {
      signal,
    }),
  importToken: (id: string, access_token: string) =>
    request<{
      account_id: string;
      vk_user_id: number;
      name: string;
      valid: boolean;
    }>(`/accounts/${id}/token`, {
      method: "PUT",
      body: { access_token },
    }),
  validate: (id: string) =>
    request<{ account: Account; valid: boolean }>(`/accounts/${id}/validate`, {
      method: "POST",
    }),
};
export const communitiesApi = {
  list: (page: number, signal?: AbortSignal) =>
    request<Community[]>(`/communities?limit=25&offset=${(page - 1) * 25}`, {
      signal,
    }),
};
export const getDashboard = (signal?: AbortSignal) =>
  request<Dashboard>("/dashboard", { signal });
