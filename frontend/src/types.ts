export type Role = "admin" | "advertiser" | "publisher";
export interface User { id: number; name: string; email: string; role: Role; advertiser_id?: number | null; publisher_id?: number | null; email_verified: boolean; }
export interface Session { user: User; token: string; }
export interface MessageResponse { message: string; }
export interface VerifyEmailResponse extends MessageResponse { email?: string | null; }
export interface VerifyEmailInput { token: string; }
export interface ResendVerificationInput { email: string; }
export interface RequestPasswordResetInput { email: string; }
export interface ResetPasswordInput { token: string; password: string; }
export const BID_TYPES: Record<number, string> = { 1: "CPM", 2: "CPC", 3: "CPA" };
export const CREATIVE_TYPES: Record<number, string> = { 1: "banner", 2: "native", 3: "video", 4: "interstitial" };
export const STATUS: Record<number, string> = { 0: "paused", 1: "active" };
export interface DeviceInfo { os: string; os_version?: string; model?: string; brand?: string; screen_width?: number; screen_height?: number; language?: string; }
export interface GeoInfo { ip?: string; country?: string; region?: string; state?: string; city?: string; pincode?: string; latitude?: number; longitude?: number; }
export interface UserContextPayload { id?: string; sector_context?: string[]; geo?: GeoInfo; }
export interface AdRequestPayload { slot_id: string; user_id?: string; user?: UserContextPayload; device?: DeviceInfo; geo?: GeoInfo; num_ads?: number; format?: number; }
export interface CreativeResponse { title?: string; description?: string; image_url?: string; video_url?: string; landing_url: string; width?: number; height?: number; creative_type: string; }
export interface TrackingUrls { impression_url: string; click_url: string; conversion_url?: string; }
export interface AdResponse { ad_id: string; campaign_id: number; creative_id: number; ad_set_id?: number | null; creative: CreativeResponse; tracking: TrackingUrls; sponsored: boolean; disclosure_label: string; metadata?: { ecpm?: number; pctr?: number } | null; }
export interface AdListResponse { request_id: string; ads: AdResponse[]; count: number; }
export interface HealthResponse { status: string; version: string; database: boolean; redis: boolean; }
export interface Advertiser { id: number; name: string; company?: string; contact_email?: string; balance: number; daily_budget: number; status: number; }
export interface Campaign { id: number; advertiser_id: number; name: string; description?: string; budget_daily: number; budget_total: number; spent_today: number; spent_total: number; bid_type: number; bid_amount: number; freq_cap_daily: number; freq_cap_hourly: number; start_time?: string; end_time?: string; status: number; autopilot: boolean; impressions: number; clicks: number; conversions: number; }
export interface AdSet { id: number; campaign_id: number; name: string; status: number; daily_budget?: number | null; bid_type: number; bid_amount: number; start_time?: string | null; end_time?: string | null; freq_cap_daily?: number | null; freq_cap_hourly?: number | null; delivery_mode: string; targeting_summary?: Record<string, any> | null; created_at: string; updated_at: string; }
export interface AdSetCreateInput { name: string; status?: number; daily_budget?: number | null; bid_type?: number; bid_amount?: number; start_time?: string | null; end_time?: string | null; freq_cap_daily?: number | null; freq_cap_hourly?: number | null; delivery_mode?: string; targeting_summary?: Record<string, any> | null; }
export interface AdSetUpdateInput { name?: string; status?: number; daily_budget?: number | null; bid_type?: number; bid_amount?: number; start_time?: string | null; end_time?: string | null; freq_cap_daily?: number | null; freq_cap_hourly?: number | null; delivery_mode?: string; targeting_summary?: Record<string, any> | null; }
import type { GeoEntry } from "./api/client";

export interface AdSetTargetingResponse { ad_set_id: number; campaign_id: number; geo_include?: GeoEntry[]; geo_exclude?: GeoEntry[]; sector_include?: string[]; sector_exclude?: string[]; }
export interface AdSetReportingItem { ad_set_id: number; ad_set_name: string; status: number; impressions: number; clicks: number; conversions: number; spend: number; ctr: number; cvr: number; }
export interface AdSetReportingResponse { campaign_id: number; start_date: string; end_date: string; ad_sets: AdSetReportingItem[]; total_impressions: number; total_clicks: number; total_conversions: number; total_spend: number; }
export interface Creative { id: number; campaign_id: number; ad_set_id?: number | null; title: string; description?: string; image_url?: string; video_url?: string; landing_url: string; creative_type: number; width: number; height: number; status: number; quality_score: number; moderation_status?: number; moderation_reason?: string | null; }
export interface Publisher { id: number; name: string; email?: string; site_url?: string; status: number; is_sandbox: boolean; }
export interface AdSlot { id: number; publisher_id: number; slot_id: string; name: string; format: number; width: number; height: number; status: number; is_chat_surface: boolean; ecpm_today?: number; }
export interface SuggestionAction {
  action: string;
  confidence?: number;
  proposed_budget_delta?: number;
  operation_type?: string;
  target_payload?: any;
}
export interface Suggestion {
  id: number;
  campaign_id: number;
  action: SuggestionAction;
  status: string;
  created_at: string;
  updated_at: string;
}

// Chat API Types
export interface ChatSessionResponse {
  session_id: string;
}

export interface ChatResponse {
  session_id: string;
  assistant_message: string;
  intent: string;
  tool_status: string | null;
  payload: Record<string, unknown> | null;
  current_campaign_id?: number | null;
  current_ad_set_id?: number | null;
  current_step?: string | null;
}

// Notification Types (Phase 6)
export type NotificationChannel = "in_app" | "email";
export type NotificationSeverity = "info" | "warning" | "error" | "success";
export type NotificationCategory =
  | "billing"
  | "campaign"
  | "creative"
  | "optimization"
  | "domain"
  | "security"
  | "publisher"
  | "system"
  | "webhook";

export interface NotificationItem {
  id: number;
  category: NotificationCategory | string;
  type: string;
  channel: string;
  severity: NotificationSeverity | string;
  title: string;
  message: string;
  link: string | null;
  resource_type: string | null;
  resource_id: string | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  unread_count: number;
}

export interface UnreadCountResponse {
  unread_count: number;
}

export interface MarkAllReadResponse {
  updated_count: number;
}

export interface NotificationPreferenceItem {
  channel: NotificationChannel | string;
  category: NotificationCategory | string;
  is_enabled: boolean;
}

export type NotificationResponse = NotificationItem;

export interface NotificationPreferenceUpdateRequest {
  preferences: NotificationPreferenceItem[];
}

export interface NotificationPreferenceListResponse {
  preferences: NotificationPreferenceItem[];
}


export interface ThemeConfig {
  primary_color: string;
  secondary_color: string;
  background_color: string;
  text_color: string;
  font_family: string;
  logo_url?: string | null;
  favicon_url?: string | null;
  border_radius: string;
}

export type BlockType =
  | "hero"
  | "text_section"
  | "features_grid"
  | "image_gallery"
  | "contact_form"
  | "cta_banner"
  | "ad_placement"
  | "footer";

export interface BlockItem {
  id: string;
  type: BlockType;
  is_visible: boolean;
  content: Record<string, any>;
}

export interface SeoMeta {
  meta_title?: string | null;
  meta_description?: string | null;
  og_image_url?: string | null;
  noindex?: boolean;
}

export interface PageSummary {
  id: number;
  project_id: number;
  title: string;
  slug: string;
  is_home: boolean;
  order_index: number;
  updated_at: string;
}

export interface PageData extends PageSummary {
  seo_meta: SeoMeta;
  blocks: {
    schema_version: number;
    blocks: BlockItem[];
  };
  created_at: string;
}

export type WebsiteStatusCode = 0 | 1 | 2 | 3;

export const WebsiteStatusMap: Record<number, string> = {
  0: "draft",
  1: "published",
  2: "archived",
  3: "suspended",
};

export interface ProjectSummary {
  id: number;
  owner_user_id: number;
  publisher_id?: number | null;
  name: string;
  slug: string;
  template_id: string;
  status: number; // 0=draft, 1=published, 2=archived, 3=suspended
  monetization_status: number;
  is_deleted: boolean;
  current_published_version_id?: number | null;
  published_url?: string;
  created_at: string;
  updated_at: string;
  page_count: number;
}

export interface ProjectData extends ProjectSummary {
  custom_domain?: string | null;
  theme_config: ThemeConfig;
  pages: PageSummary[];
}

export interface VersionSummary {
  id: number;
  project_id: number;
  version_number: number;
  published_by_user_id: number;
  notes?: string | null;
  published_at: string;
  rollback_from_version_id?: number | null;
}

export interface PreviewTokenResponse {
  preview_token: string;
  preview_url: string;
  expires_in: number;
}

export interface SlugCheckResponse {
  slug: string;
  available: boolean;
  reason?: string | null;
}

// ── Email Marketing (Phase 6.1E) ─────────────────────────────────────────────

export interface EmailMarketingList {
  id: number;
  tenant_type: string;
  tenant_id: number;
  name: string;
  description?: string | null;
  type: string;
  optin: string;
  contact_count: number;
  created_at: string;
}

export interface CreateMailingListInput {
  name: string;
  description?: string;
  type?: 'public' | 'private';
  optin?: 'single' | 'double';
}

export interface EmailMarketingContact {
  id: number;
  list_id: number;
  email: string;
  name?: string | null;
  status: string;
  consent_source: string;
  consent_at?: string | null;
  created_at: string;
}

export interface CreateContactInput {
  email: string;
  name?: string;
  consent_source: string;
}

export interface ImportContactsInput {
  csv_content: string;
  permission_source: string;
}

export interface ImportContactsResult {
  total_rows: number;
  valid_count: number;
  duplicate_count: number;
  error_count: number;
  errors: string[];
}

export interface EmailMarketingCampaign {
  id: number;
  list_id: number;
  name: string;
  subject: string;
  body: string;
  content_type: string;
  status: string;
  total_recipients: number;
  sent_at?: string | null;
  created_at: string;
}

export interface CreateCampaignInput {
  list_id: number;
  name: string;
  subject: string;
  body: string;
  content_type?: 'html' | 'markdown' | 'plain';
}

export interface UpdateCampaignInput {
  name?: string;
  subject?: string;
  body?: string;
  content_type?: 'html' | 'markdown' | 'plain';
}

export interface CampaignPreview {
  id: number;
  name: string;
  subject: string;
  rendered_body: string;
  eligible_recipient_count: number;
  has_unsubscribe_notice: boolean;
}

export interface CampaignAnalytics {
  campaign_id: number;
  status: string;
  total_recipients: number;
  views: number;
  clicks: number;
  unsubscribes: number;
  bounces: number;
}

declare global {
  interface Window {
    Razorpay?: new (options: {
      key: string;
      amount: string | number;
      currency: string;
      order_id: string;
      name?: string;
      description?: string;
      handler: (response: any) => void;
      modal?: {
        ondismiss: () => void;
      };
    }) => {
      open: () => void;
      on?: (event: string, callback: (response: any) => void) => void;
    };
  }
}
