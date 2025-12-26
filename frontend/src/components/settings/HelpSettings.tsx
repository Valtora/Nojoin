import { PlayCircle, RefreshCw } from 'lucide-react';
import { useNavigationStore } from '@/lib/store';
import { seedDemoData } from '@/lib/api';
import { useState } from 'react';
import { useNotificationStore } from '@/lib/notificationStore';
import { fuzzyMatch } from '@/lib/searchUtils';

interface HelpSettingsProps {
    userId: number | null;
    searchQuery?: string;
}

export default function HelpSettings({ userId, searchQuery = '' }: HelpSettingsProps) {
    const { setHasSeenTour, setHasSeenTranscriptTour } = useNavigationStore();
    const { addNotification } = useNotificationStore();
    const [isSeeding, setIsSeeding] = useState(false);

    const handleRestartTour = () => {
        if (userId) {
            setHasSeenTour(userId, false);
            setHasSeenTranscriptTour(userId, false);
            addNotification({ type: 'success', message: 'Tours reset. Go to the dashboard to start the Welcome Tour.' });
        }
    };

    const handleRecreateDemo = async () => {
        setIsSeeding(true);
        try {
            await seedDemoData();
            addNotification({ type: 'success', message: 'Demo meeting creation started. It will appear in your recordings shortly.' });
        } catch (error) {
            addNotification({ type: 'error', message: 'Failed to create demo meeting.' });
        } finally {
            setIsSeeding(false);
        }
    };

    const showTours = fuzzyMatch(searchQuery, ['tour', 'demo', 'welcome', 'tutorial', 'guide', 'help']);

    if (!showTours && searchQuery) return <div className="text-gray-500">No matching settings found.</div>;

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
                    <PlayCircle className="w-5 h-5 text-orange-500" /> Tours & Demos
                </h3>
                <div className="max-w-2xl space-y-4">
                    <div className="flex items-center justify-between p-4 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
                        <div>
                            <h4 className="text-sm font-medium text-gray-900 dark:text-white">Restart Welcome Tour</h4>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                                Reset the "Welcome to Nojoin" tour and the transcript walkthrough.
                            </p>
                        </div>
                        <button
                            onClick={handleRestartTour}
                            disabled={!userId}
                            className="px-3 py-1.5 text-sm font-medium text-orange-600 bg-orange-100 hover:bg-orange-200 dark:text-orange-400 dark:bg-orange-900/20 dark:hover:bg-orange-900/30 rounded-md transition-colors disabled:opacity-50"
                        >
                            Restart Tour
                        </button>
                    </div>

                    <div className="flex items-center justify-between p-4 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
                        <div>
                            <h4 className="text-sm font-medium text-gray-900 dark:text-white">Re-create Demo Meeting</h4>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                                If you deleted the "Welcome to Nojoin" meeting, this will create it again.
                            </p>
                        </div>
                        <button
                            onClick={handleRecreateDemo}
                            disabled={isSeeding}
                            className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-orange-600 bg-orange-100 hover:bg-orange-200 dark:text-orange-400 dark:bg-orange-900/20 dark:hover:bg-orange-900/30 rounded-md transition-colors disabled:opacity-50"
                        >
                            {isSeeding && <RefreshCw className="w-3 h-3 animate-spin" />}
                            {isSeeding ? 'Creating...' : 'Re-create Meeting'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
