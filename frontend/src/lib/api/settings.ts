import type { LanguageRegistry, PromptCatalogueEntry, Settings } from "@/types";
import api from "./client";

export const getPrompts = async (): Promise<PromptCatalogueEntry[]> => {
  const response = await api.get<PromptCatalogueEntry[]>("/settings/prompts");
  return response.data;
};

export const savePrompt = async (id: string, text: string): Promise<PromptCatalogueEntry> => {
  const response = await api.put<PromptCatalogueEntry>(`/settings/prompts/${encodeURIComponent(id)}`, { text });
  return response.data;
};

export const restorePrompt = async (id: string): Promise<PromptCatalogueEntry> => {
  const response = await api.delete<PromptCatalogueEntry>(`/settings/prompts/${encodeURIComponent(id)}`);
  return response.data;
};

export const getSettings = async (): Promise<Settings> => {
  const response = await api.get<Settings>("/settings");
  return response.data;
};

export const updateSettings = async (settings: Settings): Promise<Settings> => {
  const response = await api.post<Settings>("/settings", settings);
  return response.data;
};

export const getLanguageOptions = async (): Promise<LanguageRegistry> => {
  const response = await api.get<LanguageRegistry>("/settings/languages");
  return response.data;
};

export const getPersonalDictionary = async (): Promise<string[]> => {
  const response = await api.get<string[]>("/settings/personal-dictionary");
  return response.data;
};

export const addPersonalDictionaryWord = async (word: string): Promise<void> => {
  await api.post("/settings/personal-dictionary", { word });
};

export const removePersonalDictionaryWord = async (word: string): Promise<void> => {
  await api.delete(`/settings/personal-dictionary/${encodeURIComponent(word)}`);
};

export const getSpellcheckIgnoredWords = async (): Promise<string[]> => {
  const response = await api.get<string[]>("/settings/spellcheck-ignored");
  return response.data;
};

export const addSpellcheckIgnoredWord = async (word: string): Promise<void> => {
  await api.post("/settings/spellcheck-ignored", { word });
};

export const removeSpellcheckIgnoredWord = async (word: string): Promise<void> => {
  await api.delete(`/settings/spellcheck-ignored/${encodeURIComponent(word)}`);
};
