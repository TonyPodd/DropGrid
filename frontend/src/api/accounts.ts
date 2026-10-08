import { request } from "./client";
import type { Account, Community, Dashboard } from "./types";
export const accountsApi = {
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
