import pathlib

p = pathlib.Path('frontend/src/api/client.ts')
content = p.read_text(encoding='utf-8')
content += """
// Suggestions
export const getCampaignSuggestions = (campaignId: number) => apiFetch<Suggestion[]>(`/api/v1/campaign/${campaignId}/suggestions`);
export const updateSuggestionStatus = (suggestionId: number, status: string) => apiFetch<Suggestion>(`/api/v1/suggestion/${suggestionId}`, { method: "PATCH", body: JSON.stringify({ status }) });
"""
p.write_text(content, encoding='utf-8')
