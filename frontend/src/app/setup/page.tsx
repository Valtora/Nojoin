'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { getSystemStatus, setupSystem, downloadModels, getTaskStatus } from '@/lib/api';
import { Loader2, Server, CheckCircle, Download } from 'lucide-react';

export default function SetupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  
  // Model Download State
  const [downloadingModels, setDownloadingModels] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [downloadMessage, setDownloadMessage] = useState('Initializing download...');
  const [downloadComplete, setDownloadComplete] = useState(false);

  const [formData, setFormData] = useState({
    username: '',
    password: '',
    confirmPassword: '',
    llm_provider: 'gemini',
    gemini_api_key: '',
    openai_api_key: '',
    anthropic_api_key: '',
    hf_token: ''
  });

  useEffect(() => {
    // Clear any existing token when visiting setup page
    if (typeof window !== 'undefined') {
      localStorage.removeItem('token');
    }

    const checkStatus = async () => {
      try {
        const status = await getSystemStatus();
        if (status.initialized) {
          router.push('/login');
        } else {
          setLoading(false);
        }
      } catch (err) {
        console.error(err);
        setError('Failed to connect to server');
        setLoading(false);
      }
    };
    checkStatus();
  }, [router]);

  const startModelDownload = async () => {
    setDownloadingModels(true);
    try {
      const { task_id } = await downloadModels({
        hf_token: formData.hf_token || undefined,
        // Default to turbo if not specified, though backend handles default too
        whisper_model_size: 'turbo' 
      });

      const pollInterval = setInterval(async () => {
        try {
          const status = await getTaskStatus(task_id);
          
          if (status.status === 'SUCCESS') {
            clearInterval(pollInterval);
            setDownloadProgress(100);
            setDownloadMessage('All models ready!');
            setDownloadComplete(true);
            setTimeout(() => router.push('/login'), 2000);
          } else if (status.status === 'FAILURE') {
            clearInterval(pollInterval);
            setError('Model download failed. Please check logs.');
            setDownloadingModels(false); // Allow retry or manual skip?
          } else if (status.status === 'PROCESSING') {
            setDownloadProgress(status.progress || 0);
            setDownloadMessage(status.message || 'Downloading...');
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 1000);

    } catch (err: any) {
      console.error("Failed to start download", err);
      setError('Failed to start model download. You can try logging in anyway.');
      setDownloadingModels(false);
      // Optional: allow skipping
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    
    if (formData.password !== formData.confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    
    if (formData.password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setSubmitting(true);
    try {
      await setupSystem({
        username: formData.username,
        password: formData.password,
        is_superuser: true,
        llm_provider: formData.llm_provider,
        gemini_api_key: formData.gemini_api_key || undefined,
        openai_api_key: formData.openai_api_key || undefined,
        anthropic_api_key: formData.anthropic_api_key || undefined,
        hf_token: formData.hf_token || undefined
      });
      
      // Instead of redirecting, start download
      setSubmitting(false);
      await startModelDownload();

    } catch (err: any) {
      setError(err.response?.data?.detail || 'Setup failed');
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900 text-white">
        <Loader2 className="w-8 h-8 animate-spin" />
      </div>
    );
  }

  if (downloadingModels || downloadComplete) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900 text-white p-4">
        <div className="w-full max-w-md bg-gray-800 rounded-lg shadow-xl p-8 border border-gray-700 text-center">
          <div className="flex justify-center mb-6">
            {downloadComplete ? (
              <div className="w-16 h-16 bg-green-600 rounded-full flex items-center justify-center">
                <CheckCircle className="w-8 h-8 text-white" />
              </div>
            ) : (
              <div className="w-16 h-16 bg-blue-600 rounded-full flex items-center justify-center animate-pulse">
                <Download className="w-8 h-8 text-white" />
              </div>
            )}
          </div>
          
          <h2 className="text-2xl font-bold mb-2">
            {downloadComplete ? 'Setup Complete!' : 'Setting Up AI Models'}
          </h2>
          <p className="text-gray-400 mb-6">
            {downloadComplete 
              ? 'Redirecting you to login...' 
              : 'Please wait while we download the necessary AI models. This may take a few minutes.'}
          </p>

          <div className="w-full bg-gray-700 rounded-full h-4 mb-2 overflow-hidden">
            <div 
              className="bg-blue-500 h-4 rounded-full transition-all duration-500 ease-out"
              style={{ width: `${downloadProgress}%` }}
            />
          </div>
          <p className="text-sm text-gray-400 font-mono">{downloadMessage}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-900 text-white p-4">
      <div className="w-full max-w-md bg-gray-800 rounded-lg shadow-xl p-8 border border-gray-700">
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 bg-blue-600 rounded-full flex items-center justify-center mb-4">
            <Server className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold">Welcome to Nojoin</h1>
          <p className="text-gray-400 mt-2 text-center">Create your admin account to get started</p>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/50 text-red-500 p-3 rounded mb-6 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Username</label>
            <input
              type="text"
              value={formData.username}
              onChange={(e) => setFormData({...formData, username: e.target.value})}
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Password</label>
            <input
              type="password"
              value={formData.password}
              onChange={(e) => setFormData({...formData, password: e.target.value})}
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Confirm Password</label>
            <input
              type="password"
              value={formData.confirmPassword}
              onChange={(e) => setFormData({...formData, confirmPassword: e.target.value})}
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
              required
            />
          </div>

          <div className="pt-4 border-t border-gray-700">
            <h3 className="text-lg font-medium mb-4">Configuration</h3>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">LLM Provider</label>
                <select
                  value={formData.llm_provider}
                  onChange={(e) => setFormData({...formData, llm_provider: e.target.value})}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                >
                  <option value="gemini">Google Gemini</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>

              {formData.llm_provider === 'gemini' && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Gemini API Key</label>
                  <input
                    type="password"
                    value={formData.gemini_api_key}
                    onChange={(e) => setFormData({...formData, gemini_api_key: e.target.value})}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    placeholder="AIza..."
                  />
                </div>
              )}

              {formData.llm_provider === 'openai' && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">OpenAI API Key</label>
                  <input
                    type="password"
                    value={formData.openai_api_key}
                    onChange={(e) => setFormData({...formData, openai_api_key: e.target.value})}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    placeholder="sk-..."
                  />
                </div>
              )}

              {formData.llm_provider === 'anthropic' && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Anthropic API Key</label>
                  <input
                    type="password"
                    value={formData.anthropic_api_key}
                    onChange={(e) => setFormData({...formData, anthropic_api_key: e.target.value})}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    placeholder="sk-ant-..."
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  HuggingFace Token <span className="text-gray-500 text-xs">(Required for Pyannote)</span>
                </label>
                <input
                  type="password"
                  value={formData.hf_token}
                  onChange={(e) => setFormData({...formData, hf_token: e.target.value})}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                  placeholder="hf_..."
                />
              </div>
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 mt-6"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            Create Admin Account
          </button>
        </form>
      </div>
    </div>
  );
}
