export default function Loading() {
  return (
    <main className="min-h-dvh bg-surface-page p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8 flex justify-between items-center">
          <div>
            <div className="h-8 w-32 bg-surface-inset rounded animate-pulse mb-2"></div>
            <div className="h-4 w-64 bg-surface-inset rounded animate-pulse"></div>
          </div>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="bg-surface-card rounded-surface-panel border border-surface-border shadow-card p-4 h-32 animate-pulse"
            >
              <div className="flex justify-between items-start mb-4">
                <div className="h-6 w-3/4 bg-surface-inset rounded"></div>
                <div className="h-6 w-16 bg-surface-inset rounded-full"></div>
              </div>
              <div className="flex space-x-4">
                <div className="h-4 w-24 bg-surface-inset rounded"></div>
                <div className="h-4 w-16 bg-surface-inset rounded"></div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
