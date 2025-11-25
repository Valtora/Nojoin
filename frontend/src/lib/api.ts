import axios from 'axios';
import { Recording, GlobalSpeaker, Settings, Tag } from '@/types';

const API_BASE_URL = 'http://localhost:8000/api/v1';

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

export const login = async (username: string, password: string): Promise<{ access_token: string }> => {
  const formData = new FormData();
  formData.append('username', username);
  formData.append('password', password);
  
  const response = await api.post<{ access_token: string }>('/login/access-token', formData, {
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

export const updateSpeaker = async (recordingId: number, diarizationLabel: string, newName: string): Promise<void> => {
  await api.put(`/speakers/recordings/${recordingId}`, {
    diarization_label: diarizationLabel,
    global_speaker_name: newName,
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

export const addTagToRecording = async (recordingId: number, tagName: string): Promise<void> => {
  await api.post(`/tags/recordings/${recordingId}`, { name: tagName });
};

export const removeTagFromRecording = async (recordingId: number, tagName: string): Promise<void> => {
  await api.delete(`/tags/recordings/${recordingId}/${tagName}`);
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
  await api.put(`/transcripts/${recordingId}/segments/${segmentIndex}/text`, {
    text: text,
  });
};

export const findAndReplace = async (recordingId: number, findText: string, replaceText: string): Promise<void> => {
  await api.post(`/transcripts/${recordingId}/replace`, {
    find_text: findText,
    replace_text: replaceText,
  });
};

export default api;
