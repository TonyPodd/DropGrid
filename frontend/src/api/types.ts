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
  last_validated_at?: string | null;
  campaign_send_quota?: number | null;
  token_configured?: boolean;
  id: string;
  name: string;
  vk_user_id: number | null;
  gender_tag: string | null;
  status: string;
};
export type Community = {
  resolution_status?: string;
  id: string;
  domain: string;
  name: string | null;
  category: string | null;
  vk_group_id: number | null;
  is_active: boolean;
};
export type GridCommunity = Community & {
  comment?: string | null;
  content_hint?: string | null;
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
  items: {
    community: string;
    category: string | null;
    comment?: string | null;
  }[];
  errors: { line: number; value: string; message: string }[];
};
export type Campaign = {
  photo_review_mode?: "AUTO" | "REVIEW_BEFORE_SEND";
  preparation_state?: string;
  account_id?: string | null;
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
  photo_review_mode?: "AUTO" | "REVIEW_BEFORE_SEND";
  name: string;
  grid_id: string;
  track_url: string;
  caption: string;
  publication_check_hours: number;
};
export type Stats = {
  total: number;
  statuses: Record<SubmissionStatus, number>;
  media_assigned?: number;
  media_unique?: number;
};
export type Submission = {
  id: string;
  community: Community;
  category: string | null;
  account_name: string | null;
  media_label: string | null;
  media_asset_id?: string | null;
  status: SubmissionStatus;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  published_post_url: string | null;
  published_at?: string | null;
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

export type MediaPlan = {
  campaign_id: string;
  total_submissions: number;
  previously_assigned: number;
  newly_assigned: number;
  unassigned: number;
  unique_assets: number;
  categories: {
    name: string;
    submission_count: number;
    assigned_count: number;
    unique_asset_count: number;
    warnings: string[];
  }[];
};
export type MediaAsset = {
  id: string;
  category: string | null;
  provider: string | null;
  creator_name: string | null;
  creator_url: string | null;
  source_url: string | null;
  license_name: string | null;
  license_url: string | null;
  attribution_text: string | null;
  requires_publication_attribution: boolean;
  width: number | null;
  height: number | null;
  usage_count: number;
  last_used_at: string | null;
  enabled: boolean;
};

export type CampaignPreflight = {
  accounts_ready?: number;
  total_send_capacity?: number;
  capacity_unassigned?: number;
  review_pending?: number;
  total: number;
  resolved_sendable: number;
  unavailable: number;
  gender_incompatible: number;
  intended: number;
  media_assigned: number;
  media_missing: number;
  media_invalid: number;
  account_usable: boolean;
  ready: boolean;
};
