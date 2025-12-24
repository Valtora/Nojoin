import { useEffect, useState } from 'react';
import { getVersion } from '@/lib/api';
import { VersionInfo } from '@/types';
import { Loader2 } from 'lucide-react';

export default function VersionTag() {
    const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchVersion = async () => {
            try {
                const data = await getVersion();
                setVersionInfo(data);
            } catch (e) {
                console.error("Failed to fetch version info", e);
            } finally {
                setLoading(false);
            }
        };
        fetchVersion();
    }, []);

    if (loading) return null;
    if (!versionInfo) return null;

    const isUpdate = versionInfo.is_update_available;

    if (isUpdate && versionInfo.latest_version) {
        return (
            <div className="flex items-center gap-3 text-sm font-medium text-gray-500 dark:text-gray-400">
                <span>{versionInfo.current_version} (Current)</span>
                <span className="w-px h-4 bg-gray-300 dark:bg-gray-600"></span>
                <span
                    className="cursor-pointer hover:underline hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
                    onClick={() => versionInfo.release_url && window.open(versionInfo.release_url, '_blank')}
                >
                    {versionInfo.latest_version} (Latest)
                </span>
            </div>
        );
    }

    return (
        <div className="text-sm font-medium text-gray-500 dark:text-gray-400">
            {versionInfo.current_version} (Current)
        </div>
    );
}
