import { Fragment, useEffect, useState } from "react";
import { Dialog, Transition } from "@headlessui/react";
import { X, FileAudio, Server, Monitor } from "lucide-react";
import { getRecordingInfo } from "@/lib/api";
import { Recording } from "@/types";

interface RecordingInfoModalProps {
  isOpen: boolean;
  onClose: () => void;
  recording: Recording;
}

export default function RecordingInfoModal({
  isOpen,
  onClose,
  recording,
}: RecordingInfoModalProps) {
  const [info, setInfo] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      setError("");
      getRecordingInfo(recording.id)
        .then(setInfo)
        .catch((err) => {
          console.error(err);
          setError("Failed to load recording info.");
        })
        .finally(() => setLoading(false));
    }
  }, [isOpen, recording.id]);

  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/25 backdrop-blur-sm" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4 text-center">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-lg transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 p-6 text-left align-middle shadow-xl transition-all border border-gray-200 dark:border-gray-700">
                <div className="flex justify-between items-center mb-6">
                  <Dialog.Title
                    as="h3"
                    className="text-lg font-medium leading-6 text-gray-900 dark:text-white flex items-center gap-2"
                  >
                    <FileAudio className="w-5 h-5 text-orange-500" />
                    Recording Details
                  </Dialog.Title>
                  <button
                    onClick={onClose}
                    className="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 transition-colors"
                  >
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <div className="space-y-6">
                  {/* General Info */}
                  <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-4">
                    <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                      <Monitor className="w-4 h-4 text-blue-500" />
                      General
                    </h4>
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div>
                        <span className="text-gray-500 dark:text-gray-400 block text-xs">
                          Name
                        </span>
                        <span
                          className="font-medium text-gray-900 dark:text-white truncate block"
                          title={recording.name}
                        >
                          {recording.name}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-500 dark:text-gray-400 block text-xs">
                          ID
                        </span>
                        <span className="font-mono text-gray-700 dark:text-gray-300">
                          {recording.id}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-500 dark:text-gray-400 block text-xs">
                          Created At
                        </span>
                        <span className="text-gray-700 dark:text-gray-300">
                          {new Date(recording.created_at).toLocaleString()}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-500 dark:text-gray-400 block text-xs">
                          Status
                        </span>
                        <span className="capitalize text-gray-700 dark:text-gray-300">
                          {recording.status}
                        </span>
                      </div>
                    </div>
                  </div>

                  {loading ? (
                    <div className="flex justify-center p-8">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-orange-500"></div>
                    </div>
                  ) : error ? (
                    <div className="text-red-500 text-center text-sm p-4">
                      {error}
                    </div>
                  ) : (
                    <>
                      {/* Original File */}
                      <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                          <FileAudio className="w-4 h-4 text-purple-500" />
                          Source Audio
                        </h4>
                        {info.original ? (
                          <div className="grid grid-cols-2 gap-y-3 gap-x-4 text-sm">
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Format
                              </span>
                              <span className="text-gray-900 dark:text-white uppercase">
                                {info.original.format || "N/A"}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Bitrate
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.original.bitrate
                                  ? `${Math.round(info.original.bitrate / 1000)} kbps`
                                  : "N/A"}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Sample Rate
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.original.sample_rate
                                  ? `${info.original.sample_rate} Hz`
                                  : "N/A"}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Channels
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.original.channels}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Codec
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.original.codec || "N/A"}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Size
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.original.size
                                  ? `${(info.original.size / 1024 / 1024).toFixed(2)} MB`
                                  : "N/A"}
                              </span>
                            </div>
                          </div>
                        ) : (
                          <p className="text-sm text-gray-500 italic">
                            No info available (File might be missing)
                          </p>
                        )}
                      </div>

                      {/* Proxy File */}
                      <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                          <Server className="w-4 h-4 text-green-500" />
                          Proxy Audio (Web optimized)
                        </h4>
                        {info.proxy ? (
                          <div className="grid grid-cols-2 gap-y-3 gap-x-4 text-sm">
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Format
                              </span>
                              <span className="text-gray-900 dark:text-white uppercase">
                                {info.proxy.format || "N/A"}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Bitrate
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.proxy.bitrate
                                  ? `${Math.round(info.proxy.bitrate / 1000)} kbps`
                                  : "N/A"}
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Channels
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.proxy.channels} (Mono)
                              </span>
                            </div>
                            <div>
                              <span className="text-gray-500 dark:text-gray-400 block text-xs">
                                Size
                              </span>
                              <span className="text-gray-900 dark:text-white">
                                {info.proxy.size
                                  ? `${(info.proxy.size / 1024 / 1024).toFixed(2)} MB`
                                  : "N/A"}
                              </span>
                            </div>
                          </div>
                        ) : (
                          <p className="text-sm text-gray-500 italic">
                            Proxy file not generated yet.
                          </p>
                        )}
                      </div>
                    </>
                  )}
                </div>

                <div className="mt-6 flex justify-end">
                  <button
                    type="button"
                    className="rounded-lg bg-gray-100 dark:bg-gray-700 px-4 py-2 text-sm font-medium text-gray-900 dark:text-white hover:bg-gray-200 dark:hover:bg-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-2"
                    onClick={onClose}
                  >
                    Close
                  </button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
}
