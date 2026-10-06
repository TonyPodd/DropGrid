export const campaignStatuses = [
  "draft",
  "ready",
  "running",
  "monitoring",
  "completed",
  "failed",
  "cancelled",
] as const;
export const submissionStatuses = [
  "pending",
  "sending",
  "submitted",
  "published",
  "not_found",
  "failed",
  "skipped",
] as const;
export type CampaignStatus = (typeof campaignStatuses)[number];
export type SubmissionStatus = (typeof submissionStatuses)[number];
export type Account = {
  id: string;
  name: string;
  vk_user_id: number | null;
  gender_tag: string | null;
  status: string;
};
export type Community = {
  id: string;
  domain: string;
  name: string | null;
  category: string | null;
  vk_group_id: number | null;
  is_active: boolean;
};
export type CategoryCount = { category: string | null; count: number };
export type Grid = {
  id: string;
  name: string;
  created_at: string;
  community_count: number;
  category_count: number;
};
export type GridDetail = Omit<Grid, "category_count"> & {
  categories: CategoryCount[];
  communities: Community[];
};
export type GridPreview = {
  items: { community: string; category: string | null }[];
  errors: { line: number; value: string; message: string }[];
};
export type Campaign = {
  id: string;
  name: string;
  grid_id: string;
  grid_name: string;
  community_count: number;
  submission_count: number;
  track_url: string;
  track_owner_id: number | null;
  track_audio_id: number | null;
  caption: string | null;
  publication_check_hours: number;
  status: CampaignStatus;
  created_at: string;
};
export type CampaignInput = {
  name: string;
  grid_id: string;
  track_url: string;
  caption: string;
  publication_check_hours: number;
};
export type Stats = {
  total: number;
  statuses: Record<SubmissionStatus, number>;
};
export type Submission = {
  id: string;
  community: Community;
  category: string | null;
  account_name: string | null;
  media_label: string | null;
  status: SubmissionStatus;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  published_post_url: string | null;
};
export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
export type Dashboard = {
  counts: Record<string, number>;
  campaign_statuses: Record<CampaignStatus, number>;
  recent_campaigns: Campaign[];
};
export type Audio = { owner_id: number; audio_id: number };
