import type { Campaign, AdSet, Creative } from "../../../types";

export type WizardStep = 1 | 2 | 3 | 4;

export interface WizardState {
  step: WizardStep;
  campaign: Campaign | null;
  adSets: AdSet[];
  selectedAdSetId: number | null;
  creatives: Creative[];
  loading: boolean;
  error: string | null;
}

export interface CampaignFormData {
  name: string;
  description: string;
  budget_daily: string;
  budget_total: string;
  bid_type: string;
  bid_amount: string;
  freq_cap_daily: string;
  freq_cap_hourly: string;
}

export interface AdSetFormData {
  name: string;
  daily_budget: string;
  bid_type: string;
  bid_amount: string;
  delivery_mode: string;
  freq_cap_daily: string;
  freq_cap_hourly: string;
  start_time?: string;
  end_time?: string;
}

export interface AdFormData {
  title: string;
  description: string;
  creative_type: string;
  image_url: string;
  video_url: string;
  landing_url: string;
  width: string;
  height: string;
}
