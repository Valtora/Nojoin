import Link from 'next/link';
import { Recording, RecordingStatus } from '@/types';
import { Calendar, Clock, CheckCircle, Loader2, AlertCircle, HelpCircle } from 'lucide-react';

interface RecordingCardProps {
  recording: Recording;
}

const formatDuration = (seconds?: number) => {
  if (!seconds) return '00:00';
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
};

const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const StatusBadge = ({ status }: { status: RecordingStatus }) => {
  switch (status) {
    case RecordingStatus.PROCESSED:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
          <CheckCircle className="w-3 h-3 mr-1" />
          Processed
        </span>
      );
    case RecordingStatus.PROCESSING:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          Processing
        </span>
      );
    case RecordingStatus.ERROR:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200">
          <AlertCircle className="w-3 h-3 mr-1" />
          Error
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
          <HelpCircle className="w-3 h-3 mr-1" />
          {status}
        </span>
      );
  }
};

export default function RecordingCard({ recording }: RecordingCardProps) {
  return (
    <Link href={`/recordings/${recording.id}`} className="block">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow p-4 border border-gray-200 dark:border-gray-700">
        <div className="flex justify-between items-start mb-2">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white truncate pr-4 flex-1">
            {recording.name}
          </h3>
          <StatusBadge status={recording.status} />
        </div>
        
        <div className="flex items-center text-sm text-gray-500 dark:text-gray-400 space-x-4">
          <div className="flex items-center">
            <Calendar className="w-4 h-4 mr-1" />
            {formatDate(recording.created_at)}
          </div>
          <div className="flex items-center">
            <Clock className="w-4 h-4 mr-1" />
            {formatDuration(recording.duration_seconds)}
          </div>
        </div>
      </div>
    </Link>
  );
}
