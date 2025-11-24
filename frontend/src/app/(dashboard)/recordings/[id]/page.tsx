'use client';

import { getRecording, addTagToRecording, removeTagFromRecording } from '@/lib/api';
import RecordingPlayer from '@/components/RecordingPlayer';
import SpeakerPanel from '@/components/SpeakerPanel';
import TagsInput from '@/components/TagsInput';
import Link from 'next/link';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { Recording, RecordingStatus } from '@/types';
import { useRouter } from 'next/navigation';

export const dynamic = 'force-dynamic';

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function RecordingPage({ params }: PageProps) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const fetchRecording = async () => {
      try {
        const { id } = await params;
        const data = await getRecording(parseInt(id));
        setRecording(data);
      } catch (e) {
        console.error("Failed to fetch recording:", e);
        setError("Failed to load recording.");
      } finally {
        setLoading(false);
      }
    };
    fetchRecording();
    
    // Poll for updates if processing
    const interval = setInterval(async () => {
        if (recording && (recording.status === RecordingStatus.PROCESSING || recording.status === RecordingStatus.UPLOADING)) {
             try {
                const { id } = await params;
                const data = await getRecording(parseInt(id));
                if (data.status !== recording.status) {
                    setRecording(data);
                }
            } catch (e) {
                console.error("Polling failed", e);
            }
        }
    }, 5000);
    
    return () => clearInterval(interval);
  }, [params, recording?.status]); // Add recording.status dependency for polling logic

  const handleAddTag = async (tagName: string) => {
    if (!recording) return;
    try {
      await addTagToRecording(recording.id, tagName);
      // Optimistic update or re-fetch
      const updated = await getRecording(recording.id);
      setRecording(updated);
      router.refresh(); // Refresh server components if any
    } catch (e) {
      console.error("Failed to add tag:", e);
    }
  };

  const handleRemoveTag = async (tagName: string) => {
    if (!recording) return;
    try {
      await removeTagFromRecording(recording.id, tagName);
      // Optimistic update or re-fetch
      const updated = await getRecording(recording.id);
      setRecording(updated);
      router.refresh();
    } catch (e) {
      console.error("Failed to remove tag:", e);
    }
  };

  const handlePlaySegment = (time: number) => {
    if (audioRef.current) {
        audioRef.current.currentTime = time;
        audioRef.current.play();
    }
  };

  if (loading) {
    return <div className="h-full flex items-center justify-center text-gray-500">Loading...</div>;
  }

  if (error || !recording) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-red-600 mb-4">Error</h1>
          <p className="text-gray-600 dark:text-gray-400 mb-6">{error || "Recording not found"}</p>
          <Link href="/" className="text-orange-600 hover:underline">
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <header className="p-6 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 sticky top-0 z-10 flex-shrink-0">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white truncate mb-2">
          {recording.name}
        </h1>
        
        <TagsInput 
            tags={recording.tags || []} 
            onAddTag={handleAddTag} 
            onRemoveTag={handleRemoveTag} 
        />

        <div className="flex items-center text-sm text-gray-500 dark:text-gray-400 space-x-4">
           <span>{new Date(recording.created_at).toLocaleString()}</span>
           <span>•</span>
           <span>{recording.duration_seconds ? `${Math.floor(recording.duration_seconds / 60)} ${Math.floor(recording.duration_seconds / 60) === 1 ? 'min' : 'mins'}` : 'Unknown'}</span>
        </div>
      </header>

      <div className="flex-1 flex min-h-0">
        {(recording.status === RecordingStatus.UPLOADING || recording.status === RecordingStatus.PROCESSING) ? (
             <div className="flex-1 flex flex-col items-center justify-center space-y-4">
                <Loader2 className="w-12 h-12 text-orange-500 animate-spin" />
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                    {recording.status === RecordingStatus.UPLOADING ? "Meeting in Progress" : "Processing Recording..."}
                </h2>
                <p className="text-gray-500 dark:text-gray-400">
                    {recording.status === RecordingStatus.UPLOADING 
                        ? "Recording is active or finalizing upload..." 
                        : "This may take a few minutes depending on the recording length."}
                </p>
            </div>
        ) : (
            <>
                <div className="flex-1 overflow-y-auto p-6">
                    <RecordingPlayer recording={recording} audioRef={audioRef} />
                </div>
                <SpeakerPanel 
                    speakers={recording.speakers || []} 
                    segments={recording.transcript?.segments || []}
                    onPlaySegment={handlePlaySegment}
                />
            </>
        )}
      </div>
    </div>
  );
}
