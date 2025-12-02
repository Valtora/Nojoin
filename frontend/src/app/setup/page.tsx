'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { getSystemStatus, setupSystem, downloadModels, getTaskStatus, login, validateLLM, validateHF, updateSettings, getDownloadProgress, getModelStatus, listModels } from '@/lib/api';
import { Loader2, CheckCircle, Download, Check, X } from 'lucide-react';

export default function SetupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [setupSuccess, setSetupSuccess] = useState(false);
  
  // Validation State
  const [validatingLLM, setValidatingLLM] = useState(false);
  const [validatingHF, setValidatingHF] = useState(false);
  const [llmValidationMsg, setLlmValidationMsg] = useState<{valid: boolean, msg: string} | null>(null);
  const [hfValidationMsg, setHfValidationMsg] = useState<{valid: boolean, msg: string} | null>(null);
  
  // Model Selection State
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);

  // Model Download State
  const [downloadingModels, setDownloadingModels] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [downloadMessage, setDownloadMessage] = useState('Checking download status...');
  const [downloadSpeed, setDownloadSpeed] = useState('');
  const [downloadEta, setDownloadEta] = useState('');
  const [downloadComplete, setDownloadComplete] = useState(false);

  const [formData, setFormData] = useState({
    username: '',
    password: '',
    confirmPassword: '',
    llm_provider: 'gemini',
    gemini_api_key: '',
    openai_api_key: '',
    anthropic_api_key: '',
    hf_token: '',
    selected_model: ''
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

  const completeSetupAndRedirect = async () => {
    setDownloadProgress(100);
    setDownloadMessage('All models ready!');
    setDownloadComplete(true);
    
    // Auto-login after setup
    try {
      const loginResponse = await login(formData.username, formData.password);
      localStorage.setItem('token', loginResponse.access_token);
      setTimeout(() => router.push('/'), 2000);
    } catch (loginErr) {
      console.error("Auto-login failed", loginErr);
      // Fallback to login page if auto-login fails
      setTimeout(() => router.push('/login'), 2000);
    }
  };

  const startModelDownload = async () => {
    setDownloadingModels(true);
    setDownloadMessage('Checking download status...');
    
    try {
      // First, check if there's an existing download in progress or completed
      const sharedProgress = await getDownloadProgress();
      
      // If download is already complete, skip to completion
      if (sharedProgress.status === 'complete') {
        await completeSetupAndRedirect();
        return;
      }
      
      // If download is in progress (from preload_models.py), poll that instead of starting a new task
      if (sharedProgress.in_progress) {
        setDownloadMessage(sharedProgress.message || 'Download in progress...');
        setDownloadProgress(sharedProgress.progress || 0);
        if (sharedProgress.speed) setDownloadSpeed(sharedProgress.speed);
        if (sharedProgress.eta) setDownloadEta(sharedProgress.eta);
        
        // Poll the shared progress endpoint
        const pollInterval = setInterval(async () => {
          try {
            const progress = await getDownloadProgress();
            
            if (progress.status === 'complete') {
              clearInterval(pollInterval);
              await completeSetupAndRedirect();
            } else if (progress.status === 'error') {
              clearInterval(pollInterval);
              setError(progress.message || 'Model download failed. Please check logs.');
              setDownloadingModels(false);
            } else {
              setDownloadProgress(progress.progress || 0);
              setDownloadMessage(progress.message || 'Downloading...');
              if (progress.speed) setDownloadSpeed(progress.speed);
              if (progress.eta) setDownloadEta(progress.eta);
            }
          } catch (e) {
            console.error("Polling error", e);
          }
        }, 1000);
        
        return;
      }
      
      // Check if models are already downloaded (preload might have completed before we checked)
      const modelStatus = await getModelStatus('turbo');
      const allDownloaded = modelStatus.whisper?.downloaded && 
                           modelStatus.pyannote?.downloaded && 
                           modelStatus.embedding?.downloaded;
      
      if (allDownloaded) {
        await completeSetupAndRedirect();
        return;
      }
      
      // No existing download and models not ready, start a new download task
      setDownloadMessage('Starting download...');
      const { task_id } = await downloadModels({
        hf_token: formData.hf_token || undefined,
        whisper_model_size: 'turbo' 
      });

      const pollInterval = setInterval(async () => {
        try {
          const status = await getTaskStatus(task_id);
          
          if (status.status === 'SUCCESS') {
            clearInterval(pollInterval);
            await completeSetupAndRedirect();
          } else if (status.status === 'FAILURE') {
            clearInterval(pollInterval);
            setError('Model download failed. Please check logs.');
            setDownloadingModels(false);
          } else if (status.status === 'PROCESSING') {
            const meta = status.result || {};
            setDownloadProgress(meta.progress || 0);
            setDownloadMessage(meta.message || 'Downloading...');
            if (meta.speed) setDownloadSpeed(meta.speed);
            if (meta.eta) setDownloadEta(meta.eta);
          } else if (status.status === 'PENDING') {
            // Task not yet picked up by worker - check shared progress in case worker is busy
            const progress = await getDownloadProgress();
            if (progress.in_progress) {
              setDownloadProgress(progress.progress || 0);
              setDownloadMessage(progress.message || 'Download in progress...');
              if (progress.speed) setDownloadSpeed(progress.speed);
              if (progress.eta) setDownloadEta(progress.eta);
            } else {
              setDownloadMessage('Waiting for worker...');
            }
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 1000);

    } catch (err: any) {
      console.error("Failed to start download", err);
      setError('Failed to start model download. You can try logging in anyway.');
      setDownloadingModels(false);
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
      if (!setupSuccess) {
        // 1. Initial Setup
        try {
          await setupSystem({
            username: formData.username,
            password: formData.password,
            is_superuser: true,
            llm_provider: formData.llm_provider,
            gemini_api_key: formData.gemini_api_key || undefined,
            openai_api_key: formData.openai_api_key || undefined,
            anthropic_api_key: formData.anthropic_api_key || undefined,
            hf_token: formData.hf_token || undefined,
            whisper_model_size: 'turbo',
            selected_model: formData.selected_model || undefined
          });
        } catch (err: any) {
          // If system is already initialized, we might be in a retry state where the page wasn't refreshed
          if (err.response?.status === 400 && err.response?.data?.detail === "System is already initialized.") {
             // Proceed to login and update
             console.log("System already initialized, proceeding to login/update flow");
          } else {
             throw err;
          }
        }

        // 2. Auto Login
        const loginResponse = await login(formData.username, formData.password);
        localStorage.setItem('token', loginResponse.access_token);
        setSetupSuccess(true);
      } else {
        // 3. Update Settings (Retry Flow)
        await updateSettings({
            llm_provider: formData.llm_provider,
            gemini_api_key: formData.gemini_api_key || undefined,
            openai_api_key: formData.openai_api_key || undefined,
            anthropic_api_key: formData.anthropic_api_key || undefined,
            hf_token: formData.hf_token || undefined,
            whisper_model_size: 'turbo'
        });
      }
      
      // 4. Start Download
      setSubmitting(false);
      await startModelDownload();

    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Setup failed');
      setSubmitting(false);
    }
  };

  const handleValidateLLM = async () => {
    const provider = formData.llm_provider;
    const key = provider === 'gemini' ? formData.gemini_api_key : 
                provider === 'openai' ? formData.openai_api_key : 
                formData.anthropic_api_key;
    
    if (!key) {
      setLlmValidationMsg({valid: false, msg: 'Please enter an API key'});
      return;
    }

    setValidatingLLM(true);
    setLlmValidationMsg(null);
    setAvailableModels([]);
    setFetchingModels(true);
    
    try {
      const res = await validateLLM(provider, key);
      setLlmValidationMsg({valid: true, msg: res.message});
      
      // Fetch models on success
      try {
        const modelsRes = await listModels(provider, key);
        setAvailableModels(modelsRes.models);
        if (modelsRes.models.length > 0) {
            // Set default model if available
            let defaultModel = modelsRes.models[0];
            // Try to pick a sensible default based on provider
            if (provider === 'gemini') {
                const preferred = modelsRes.models.find(m => m.includes('gemini-2.0-flash-exp')) || modelsRes.models.find(m => m.includes('gemini-1.5-pro'));
                if (preferred) defaultModel = preferred;
            } else if (provider === 'openai') {
                const preferred = modelsRes.models.find(m => m.includes('gpt-4o')) || modelsRes.models.find(m => m.includes('gpt-4-turbo'));
                if (preferred) defaultModel = preferred;
            } else if (provider === 'anthropic') {
                const preferred = modelsRes.models.find(m => m.includes('claude-3-5-sonnet'));
                if (preferred) defaultModel = preferred;
            }
            setFormData(prev => ({ ...prev, selected_model: defaultModel }));
        }
      } catch (modelErr) {
        console.error("Failed to fetch models", modelErr);
        // Don't fail validation if model list fails, just let them type it or use default
      }
      
    } catch (err: any) {
      setLlmValidationMsg({valid: false, msg: err.response?.data?.detail || 'Validation failed'});
    } finally {
      setValidatingLLM(false);
      setFetchingModels(false);
    }
  };

  const handleValidateHF = async () => {
    if (!formData.hf_token) {
      setHfValidationMsg({valid: false, msg: 'Please enter a token'});
      return;
    }
    setValidatingHF(true);
    setHfValidationMsg(null);
    try {
      const res = await validateHF(formData.hf_token);
      setHfValidationMsg({valid: true, msg: res.message});
    } catch (err: any) {
      setHfValidationMsg({valid: false, msg: err.response?.data?.detail || 'Validation failed'});
    } finally {
      setValidatingHF(false);
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
              <div className="w-16 h-16 bg-orange-600 rounded-full flex items-center justify-center animate-pulse">
                <Download className="w-8 h-8 text-white" />
              </div>
            )}
          </div>
          
          <h2 className="text-2xl font-bold mb-2">
            {downloadComplete ? 'Setup Complete!' : <>Setting Up <span className="text-orange-500">Nojoin</span></>}
          </h2>
          <p className="text-gray-400 mb-6">
            {downloadComplete 
              ? 'Redirecting you to the dashboard...' 
              : 'Please wait while Nojoin downloads and setups the necessary dependencies...'}
          </p>

          <div className="w-full bg-gray-700 rounded-full h-4 mb-2 overflow-hidden">
            <div 
              className="bg-orange-500 h-4 rounded-full transition-all duration-500 ease-out"
              style={{ width: `${downloadProgress}%` }}
            />
          </div>
          <div className="flex flex-col items-center text-sm text-gray-400 font-mono mt-2">
            <span className="mb-1">{downloadMessage}</span>
            {downloadSpeed && !downloadComplete && (
               <span className="text-xs text-gray-500">{downloadSpeed} • ETA: {downloadEta}</span>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-900 text-white p-4">
      <div className="w-full max-w-md bg-gray-800 rounded-lg shadow-xl p-8 border border-gray-700">
        <div className="flex flex-col items-center mb-8">
          <Image 
            src="/assets/NojoinLogo.png" 
            alt="Nojoin Logo" 
            width={64} 
            height={64} 
            className="mb-4"
          />
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
                  onChange={(e) => {
                    setFormData({...formData, llm_provider: e.target.value, selected_model: ''});
                    setAvailableModels([]);
                    setLlmValidationMsg(null);
                  }}
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
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={formData.gemini_api_key}
                      onChange={(e) => setFormData({...formData, gemini_api_key: e.target.value})}
                      className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                      placeholder="AIza..."
                    />
                    <button
                      type="button"
                      onClick={handleValidateLLM}
                      disabled={validatingLLM || !formData.gemini_api_key}
                      className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded disabled:opacity-50"
                    >
                      {validatingLLM ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                  </div>
                  {llmValidationMsg && (
                    <div className={`text-xs mt-1 flex items-center gap-1 ${llmValidationMsg.valid ? 'text-green-500' : 'text-red-500'}`}>
                      {llmValidationMsg.valid ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                      {llmValidationMsg.msg}
                    </div>
                  )}
                </div>
              )}

              {formData.llm_provider === 'openai' && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">OpenAI API Key</label>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={formData.openai_api_key}
                      onChange={(e) => setFormData({...formData, openai_api_key: e.target.value})}
                      className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                      placeholder="sk-..."
                    />
                    <button
                      type="button"
                      onClick={handleValidateLLM}
                      disabled={validatingLLM || !formData.openai_api_key}
                      className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded disabled:opacity-50"
                    >
                      {validatingLLM ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                  </div>
                  {llmValidationMsg && (
                    <div className={`text-xs mt-1 flex items-center gap-1 ${llmValidationMsg.valid ? 'text-green-500' : 'text-red-500'}`}>
                      {llmValidationMsg.valid ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                      {llmValidationMsg.msg}
                    </div>
                  )}
                </div>
              )}

              {formData.llm_provider === 'anthropic' && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Anthropic API Key</label>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={formData.anthropic_api_key}
                      onChange={(e) => setFormData({...formData, anthropic_api_key: e.target.value})}
                      className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                      placeholder="sk-ant-..."
                    />
                    <button
                      type="button"
                      onClick={handleValidateLLM}
                      disabled={validatingLLM || !formData.anthropic_api_key}
                      className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded disabled:opacity-50"
                    >
                      {validatingLLM ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                  </div>
                  {llmValidationMsg && (
                    <div className={`text-xs mt-1 flex items-center gap-1 ${llmValidationMsg.valid ? 'text-green-500' : 'text-red-500'}`}>
                      {llmValidationMsg.valid ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                      {llmValidationMsg.msg}
                    </div>
                  )}
                </div>
              )}

              {availableModels.length > 0 && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Select Model</label>
                  <select
                    value={formData.selected_model}
                    onChange={(e) => setFormData({...formData, selected_model: e.target.value})}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                  >
                    {availableModels.map(model => (
                        <option key={model} value={model}>{model}</option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  HuggingFace Token <span className="text-gray-500 text-xs">(Required for Pyannote)</span>
                </label>
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={formData.hf_token}
                    onChange={(e) => setFormData({...formData, hf_token: e.target.value})}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500"
                    placeholder="hf_..."
                  />
                  <button
                    type="button"
                    onClick={handleValidateHF}
                    disabled={validatingHF || !formData.hf_token}
                    className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded disabled:opacity-50"
                  >
                    {validatingHF ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                  </button>
                </div>
                {hfValidationMsg && (
                  <div className={`text-xs mt-1 flex items-center gap-1 ${hfValidationMsg.valid ? 'text-green-500' : 'text-red-500'}`}>
                    {hfValidationMsg.valid ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                    {hfValidationMsg.msg}
                  </div>
                )}
              </div>
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 mt-6"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            {setupSuccess ? 'Update & Retry Download' : 'Create Admin Account'}
          </button>
        </form>
      </div>
    </div>
  );
}
