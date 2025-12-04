import axios from 'axios';
import { Recording, GlobalSpeaker, Settings, Tag, TranscriptSegment, VoiceprintExtractResult, VoiceprintApplyResult, BatchVoiceprintResponse, RecordingSpeaker, ChatMessage } from '@/types';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL 
  ? `${process.env.NEXT_PUBLIC_API_URL}/v1` 
  : 'https://localhost:14443/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      if (typeof window !== 'undefined' && !window.location.pathname.includes('/login')) {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export const login = async (username: string, password: string): Promise<{ access_token: string, force_password_change: boolean, is_superuser: boolean, username: string }> => {
  const formData = new FormData();
  formData.append('username', username);
  formData.append('password', password);
  
  const response = await api.post<{ access_token: string, force_password_change: boolean, is_superuser: boolean, username: string }>('/login/access-token', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data;
};

export interface RecordingFilters {
  q?: string;
  start_date?: string;
  end_date?: string;
  speaker_ids?: number[];
  tag_ids?: number[];
  include_archived?: boolean;
  include_deleted?: boolean;
  only_archived?: boolean;
  only_deleted?: boolean;
}

export const getRecordings = async (filters?: RecordingFilters): Promise<Recording[]> => {
  const params = new URLSearchParams();
  
  if (filters) {
    if (filters.q) params.append('q', filters.q);
    if (filters.start_date) params.append('start_date', filters.start_date);
    if (filters.end_date) params.append('end_date', filters.end_date);
    if (filters.speaker_ids) {
      filters.speaker_ids.forEach(id => params.append('speaker_ids', id.toString()));
    }
    if (filters.tag_ids) {
      filters.tag_ids.forEach(id => params.append('tag_ids', id.toString()));
    }
    if (filters.include_archived) params.append('include_archived', 'true');
    if (filters.include_deleted) params.append('include_deleted', 'true');
    if (filters.only_archived) params.append('only_archived', 'true');
    if (filters.only_deleted) params.append('only_deleted', 'true');
  }

  const response = await api.get<Recording[]>(`/recordings/?${params.toString()}`);
  return response.data;
};

export const getRecording = async (id: number): Promise<Recording> => {
  const response = await api.get<Recording>(`/recordings/${id}`);
  return response.data;
};

export const deleteRecording = async (id: number): Promise<void> => {
  await api.delete(`/recordings/${id}`);
};

export const renameRecording = async (id: number, name: string): Promise<Recording> => {
  const response = await api.patch<Recording>(`/recordings/${id}`, { name });
  return response.data;
};

export const retryProcessing = async (id: number): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/retry`);
  return response.data;
};

export const getRecordingStreamUrl = (id: number): string => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('token');
    if (token) {
      return `${API_BASE_URL}/recordings/${id}/stream?token=${token}`;
    }
  }
  return `${API_BASE_URL}/recordings/${id}/stream`;
};

// Speaker Management
export const getGlobalSpeakers = async (): Promise<GlobalSpeaker[]> => {
  const response = await api.get<GlobalSpeaker[]>('/speakers/');
  return response.data;
};

export const updateGlobalSpeaker = async (id: number, name: string): Promise<GlobalSpeaker> => {
  const response = await api.put<GlobalSpeaker>(`/speakers/${id}?name=${encodeURIComponent(name)}`);
  return response.data;
};

export const mergeSpeakers = async (sourceId: number, targetId: number): Promise<GlobalSpeaker> => {
  const response = await api.post<GlobalSpeaker>('/speakers/merge', {
    source_speaker_id: sourceId,
    target_speaker_id: targetId,
  });
  return response.data;
};

export const deleteGlobalSpeaker = async (id: number): Promise<void> => {
  await api.delete(`/speakers/${id}`);
};

export const deleteGlobalSpeakerEmbedding = async (id: number): Promise<void> => {
  await api.delete(`/speakers/${id}/embedding`);
};

export const updateSpeaker = async (recordingId: number, diarizationLabel: string, newName: string): Promise<void> => {
  await api.put(`/speakers/recordings/${recordingId}`, {
    diarization_label: diarizationLabel,
    global_speaker_name: newName,
  });
};

export const updateSpeakerColor = async (recordingId: number, label: string, color: string): Promise<void> => {
  await api.put(`/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(label)}/color`, {
    color,
  });
};

export const updateTranscriptSegmentSpeaker = async (recordingId: number, segmentIndex: number, newSpeakerName: string): Promise<void> => {
  await api.put(`/transcripts/${recordingId}/segments/${segmentIndex}`, {
    new_speaker_name: newSpeakerName,
  });
};

// Tags
export const getTags = async (): Promise<Tag[]> => {
  const response = await api.get<Tag[]>('/tags/');
  return response.data;
};

export const createTag = async (name: string, color?: string): Promise<Tag> => {
  const response = await api.post<Tag>('/tags/', { name, color });
  return response.data;
};

export const updateTag = async (tagId: number, data: { name?: string; color?: string }): Promise<Tag> => {
  const response = await api.patch<Tag>(`/tags/${tagId}`, data);
  return response.data;
};

export const addTagToRecording = async (recordingId: number, tagName: string): Promise<void> => {
  await api.post(`/tags/recordings/${recordingId}`, { name: tagName });
};

export const removeTagFromRecording = async (recordingId: number, tagName: string): Promise<void> => {
  await api.delete(`/tags/recordings/${recordingId}/${tagName}`);
};

export const deleteTag = async (id: number): Promise<void> => {
  await api.delete(`/tags/${id}`);
};

// Archive & Delete Management
export const archiveRecording = async (id: number): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/archive`);
  return response.data;
};

export const restoreRecording = async (id: number): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/restore`);
  return response.data;
};

export const softDeleteRecording = async (id: number): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/soft-delete`);
  return response.data;
};

export const permanentlyDeleteRecording = async (id: number): Promise<void> => {
  await api.delete(`/recordings/${id}/permanent`);
};

// Settings
export const getSettings = async (): Promise<Settings> => {
  const response = await api.get<Settings>('/settings');
  return response.data;
};

export const updateSettings = async (settings: Settings): Promise<Settings> => {
  const response = await api.post<Settings>('/settings', settings);
  return response.data;
};

// Transcript Text
export const updateTranscriptSegmentText = async (recordingId: number, segmentIndex: number, text: string): Promise<void> => {
  await api.put(`/transcripts/${recordingId}/segments/${segmentIndex}/text`, { text });
};

export const findAndReplace = async (recordingId: number, find: string, replace: string): Promise<void> => {
  await api.post(`/transcripts/${recordingId}/replace`, { find_text: find, replace_text: replace });
};

export type ExportContentType = 'transcript' | 'notes' | 'both';

export const exportContent = async (recordingId: number, contentType: ExportContentType = 'transcript'): Promise<void> => {
  const response = await api.get(`/transcripts/${recordingId}/export`, {
    params: { content_type: contentType },
    responseType: 'blob',
  });
  
  // Create a link and click it to download
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  
  // Extract filename from header if possible, or generate one
  const contentDisposition = response.headers['content-disposition'];
  let filename = `export-${recordingId}.txt`;
  if (contentDisposition) {
    // Try to match filename="name"
    const filenameMatch = contentDisposition.match(/filename="([^"]+)"/);
    if (filenameMatch && filenameMatch.length === 2) {
        filename = filenameMatch[1];
    } else {
        // Try to match filename=name
        const filenameSimpleMatch = contentDisposition.match(/filename=([^;]+)/);
        if (filenameSimpleMatch && filenameSimpleMatch.length === 2) {
            filename = filenameSimpleMatch[1].trim();
        }
    }
  }
  
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

// Keep for backward compatibility
export const exportTranscript = async (recordingId: number): Promise<void> => {
  return exportContent(recordingId, 'transcript');
};

// Meeting Notes API
export const getNotes = async (recordingId: number): Promise<{ notes: string | null }> => {
  const response = await api.get<{ notes: string | null }>(`/transcripts/${recordingId}/notes`);
  return response.data;
};

export const updateNotes = async (recordingId: number, notes: string): Promise<{ notes: string; status: string }> => {
  const response = await api.put<{ notes: string; status: string }>(`/transcripts/${recordingId}/notes`, { notes });
  return response.data;
};

export const generateNotes = async (recordingId: number): Promise<{ notes: string; status: string }> => {
  const response = await api.post<{ notes: string; status: string }>(`/transcripts/${recordingId}/notes/generate`);
  return response.data;
};

export const findAndReplaceNotes = async (recordingId: number, find: string, replace: string): Promise<void> => {
  // Use the main replace endpoint since it applies to both transcript and notes
  await api.post(`/transcripts/${recordingId}/replace`, { find_text: find, replace_text: replace });
};

export const mergeRecordingSpeakers = async (recordingId: number, targetSpeakerLabel: string, sourceSpeakerLabel: string): Promise<Recording> => {
  const response = await api.post<Recording>(`/speakers/recordings/${recordingId}/merge`, {
    target_speaker_label: targetSpeakerLabel,
    source_speaker_label: sourceSpeakerLabel,
  });
  return response.data;
};

export const deleteRecordingSpeaker = async (recordingId: number, diarizationLabel: string): Promise<void> => {
  await api.delete(`/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}`);
};

export const promoteToGlobalSpeaker = async (recordingId: number, diarizationLabel: string): Promise<RecordingSpeaker> => {
  const response = await api.post<RecordingSpeaker>(`/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/promote`);
  return response.data;
};

export const updateTranscriptSegments = async (recordingId: number, segments: TranscriptSegment[]): Promise<void> => {
  await api.put(`/transcripts/${recordingId}/segments`, { segments });
};

export interface ImportAudioOptions {
  name?: string;
  recordedAt?: Date;
  onUploadProgress?: (progress: number) => void;
}

export const importAudio = async (
  file: File, 
  options?: ImportAudioOptions
): Promise<Recording> => {
  const formData = new FormData();
  formData.append('file', file);
  
  const params = new URLSearchParams();
  if (options?.name) {
    params.append('name', options.name);
  }
  if (options?.recordedAt) {
    params.append('recorded_at', options.recordedAt.toISOString());
  }
  
  const queryString = params.toString();
  const url = `/recordings/import${queryString ? `?${queryString}` : ''}`;
  
  const response = await api.post<Recording>(url, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress: (progressEvent) => {
      if (options?.onUploadProgress && progressEvent.total) {
        const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        options.onUploadProgress(progress);
      }
    },
  });
  
  return response.data;
};

export const getSupportedAudioFormats = (): string[] => {
  return ['.wav', '.mp3', '.m4a', '.aac', '.webm', '.ogg', '.flac', '.mp4', '.wma', '.opus'];
};

export const getMaxUploadSizeMB = (): number => {
  return 500;
};

// Voiceprint Management
export const extractVoiceprint = async (
  recordingId: number,
  diarizationLabel: string
): Promise<VoiceprintExtractResult> => {
  const response = await api.post<VoiceprintExtractResult>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/voiceprint/extract`
  );
  return response.data;
};

export type VoiceprintAction = 'create_new' | 'link_existing' | 'local_only' | 'force_link';

export const applyVoiceprintAction = async (
  recordingId: number,
  diarizationLabel: string,
  action: VoiceprintAction,
  options?: { globalSpeakerId?: number; newSpeakerName?: string }
): Promise<VoiceprintApplyResult> => {
  const response = await api.post<VoiceprintApplyResult>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/voiceprint/apply`,
    {
      action,
      global_speaker_id: options?.globalSpeakerId,
      new_speaker_name: options?.newSpeakerName,
    }
  );
  return response.data;
};

export const deleteVoiceprint = async (
  recordingId: number,
  diarizationLabel: string
): Promise<void> => {
  await api.delete(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/voiceprint`
  );
};

export const extractAllVoiceprints = async (
  recordingId: number
): Promise<BatchVoiceprintResponse> => {
  const response = await api.post<BatchVoiceprintResponse>(
    `/speakers/recordings/${recordingId}/voiceprints/extract-all`
  );
  return response.data;
};

// Batch Operations
export const batchArchiveRecordings = async (ids: number[]): Promise<void> => {
  await api.post('/recordings/batch/archive', { recording_ids: ids });
};

export const batchRestoreRecordings = async (ids: number[]): Promise<void> => {
  await api.post('/recordings/batch/restore', { recording_ids: ids });
};

export const batchSoftDeleteRecordings = async (ids: number[]): Promise<void> => {
  await api.post('/recordings/batch/soft-delete', { recording_ids: ids });
};

export const batchPermanentlyDeleteRecordings = async (ids: number[]): Promise<void> => {
  await api.post('/recordings/batch/permanent', { recording_ids: ids });
};

export const batchAddTagToRecordings = async (ids: number[], tagName: string): Promise<void> => {
  await api.post('/tags/batch/add', { recording_ids: ids, tag_name: tagName });
};

export const batchRemoveTagFromRecordings = async (ids: number[], tagName: string): Promise<void> => {
  await api.post('/tags/batch/remove', { recording_ids: ids, tag_name: tagName });
};

// System Setup
export const getSystemStatus = async (): Promise<{ initialized: boolean }> => {
  const response = await api.get<{ initialized: boolean }>('/system/status');
  return response.data;
};

export const setupSystem = async (data: any): Promise<void> => {
  await api.post('/system/setup', data);
};

export const downloadModels = async (data: { hf_token?: string, whisper_model_size?: string }): Promise<{ task_id: string }> => {
  const response = await api.post('/system/download-models', null, { params: data });
  return response.data;
};

export const getTaskStatus = async (taskId: string): Promise<any> => {
  const response = await api.get(`/system/tasks/${taskId}`);
  return response.data;
};

// User Management
export const getUsers = async (): Promise<any[]> => {
  const response = await api.get<any[]>('/users/');
  return response.data;
};

export const createUser = async (data: any): Promise<any> => {
  const response = await api.post('/users/', data);
  return response.data;
};

export const updateUser = async (id: number, data: any): Promise<any> => {
  const response = await api.put(`/users/${id}`, data);
  return response.data;
};

export const deleteUser = async (id: number): Promise<any> => {
  const response = await api.delete(`/users/${id}`);
  return response.data;
};

export const updateUserMe = async (data: any): Promise<any> => {
  const response = await api.put('/users/me', data);
  return response.data;
};

export const getUserMe = async (): Promise<any> => {
  const response = await api.get('/users/me');
  return response.data;
};

export const updatePasswordMe = async (data: any): Promise<any> => {
  const response = await api.put('/users/me/password', data);
  return response.data;
};

export const validateLLM = async (provider: string, apiKey: string, model?: string, apiUrl?: string): Promise<{ valid: boolean, message: string }> => {
  const response = await api.post<{ valid: boolean, message: string }>('/setup/validate-llm', { provider, api_key: apiKey, model, api_url: apiUrl });
  return response.data;
};

export const validateHF = async (token: string): Promise<{ valid: boolean, message: string }> => {
  const response = await api.post<{ valid: boolean, message: string }>('/setup/validate-hf', { token });
  return response.data;
};

export const getModelStatus = async (whisper_model_size?: string): Promise<any> => {
  const response = await api.get('/system/models/status', { params: { whisper_model_size } });
  return response.data;
};

export const deleteModel = async (modelName: string): Promise<void> => {
  await api.delete(`/system/models/${modelName}`);
};

export interface DownloadProgress {
  in_progress: boolean;
  progress: number | null;
  message: string | null;
  speed: string | null;
  eta: string | null;
  status: 'downloading' | 'complete' | 'error' | null;
  stage?: string | null;
}

export const getDownloadProgress = async (): Promise<DownloadProgress> => {
  const response = await api.get<DownloadProgress>('/system/download-progress');
  return response.data;
};


// Chat API
export const getChatHistory = async (recordingId: number): Promise<ChatMessage[]> => {
  const response = await api.get<ChatMessage[]>(`/transcripts/${recordingId}/chat`);
  return response.data;
};

export const clearChatHistory = async (recordingId: number): Promise<void> => {
  await api.delete(`/transcripts/${recordingId}/chat`);
};

export const streamChatMessage = (
  recordingId: number,
  message: string,
  onToken: (token: string) => void,
  onComplete: () => void,
  onError: (error: string) => void
): AbortController => {
  const controller = new AbortController();
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;

  fetch(`${API_BASE_URL}/transcripts/${recordingId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': token ? `Bearer ${token}` : '',
    },
    body: JSON.stringify({ message }),
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) {
        try {
            const err = await response.json();
            onError(err.detail || 'Failed to send message');
        } catch {
            onError(`Error: ${response.statusText}`);
        }
        return;
    }
    
    if (!response.body) {
        onError("No response body");
        return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    
    while (true) {
        const { done, value } = await reader.read();
        if (done) {
            onComplete();
            break;
        }
        
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                if (data === '[DONE]') {
                    continue;
                }
                try {
                    const parsed = JSON.parse(data);
                    if (parsed.token) {
                        onToken(parsed.token);
                    } else if (parsed.error) {
                        onError(parsed.error);
                    }
                } catch (e) {
                    console.error("Failed to parse SSE data", e);
                }
            }
        }
    }
  }).catch(err => {
      if (err.name === 'AbortError') {
          console.log('Stream aborted');
      } else {
          onError(err.message || 'Network error');
      }
  });

  return controller;
};

// Infer Speakers
export const inferSpeakers = async (recordingId: number): Promise<void> => {
  await api.post(`/recordings/${recordingId}/infer-speakers`);
};

export const listModels = async (provider: string, apiKey: string, apiUrl?: string): Promise<{ models: string[] }> => {
  const response = await api.post<{ models: string[] }>('/setup/list-models', { provider, api_key: apiKey, api_url: apiUrl });
  return response.data;
};

export const fetchProxyModels = async (provider: string, apiUrl?: string, apiKey?: string): Promise<string[]> => {
  const params = new URLSearchParams();
  params.append('provider', provider);
  if (apiUrl) params.append('api_url', apiUrl);
  if (apiKey) params.append('api_key', apiKey);
  
  const response = await api.get<string[]>(`/llm/models?${params.toString()}`);
  return response.data;
};

export default api;
