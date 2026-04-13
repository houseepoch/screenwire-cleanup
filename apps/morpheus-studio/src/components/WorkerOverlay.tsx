import { useState } from 'react';
import { useMorpheusStore } from '../store';
import {
  Image,
  MapPin,
  LayoutGrid,
  Film,
  Check,
  X,
  FileText,
  Clock3,
} from 'lucide-react';
import type { WorkerStatus } from '../types';

function fallbackWorkers(status: string | undefined, progress: number): WorkerStatus[] {
  if (!status) {
    return [];
  }

  const workerMap: Record<string, WorkerStatus> = {
    generating_assets: {
      id: 'phase-assets',
      name: 'Asset Preparation',
      status: 'running',
      progress,
      message: 'Building the next project phase...',
    },
    generating_frames: {
      id: 'phase-frames',
      name: 'Frame Generation',
      status: 'running',
      progress,
      message: 'Generating timeline frames...',
    },
    generating_video: {
      id: 'phase-video',
      name: 'Video Generation',
      status: 'running',
      progress,
      message: 'Rendering video outputs...',
    },
  };

  return workerMap[status] ? [workerMap[status]] : [];
}

export function WorkerOverlay() {
  const { currentProject, workers } = useMorpheusStore();
  const [isVisible, setIsVisible] = useState(true);

  const displayWorkers = workers.length
    ? workers
    : fallbackWorkers(currentProject?.status, currentProject?.progress ?? 0);

  const getWorkerIcon = (worker: WorkerStatus) => {
    const id = worker.id.toLowerCase();
    const name = worker.name.toLowerCase();

    if (worker.status === 'complete') {
      return <Check size={10} />;
    }
    if (id.includes('frame') || name.includes('frame')) {
      return <Image size={14} />;
    }
    if (id.includes('location') || name.includes('location')) {
      return <MapPin size={14} />;
    }
    if (id.includes('storyboard') || name.includes('storyboard')) {
      return <LayoutGrid size={14} />;
    }
    if (id.includes('video') || name.includes('video')) {
      return <Film size={14} />;
    }
    if (id.includes('cast') || name.includes('cast')) {
      return <Image size={14} />;
    }
    if (id.includes('wait') || name.includes('waiting')) {
      return <Clock3 size={14} />;
    }
    return <FileText size={14} />;
  };

  if (!displayWorkers.length) {
    return null;
  }

  const activeWorkers = displayWorkers.filter((worker) => worker.status === 'running' || worker.status === 'idle');
  const completedWorkers = displayWorkers.filter((worker) => worker.status === 'complete');

  if (!isVisible) {
    return (
      <button
        onClick={() => setIsVisible(true)}
        style={{
          position: 'fixed',
          bottom: '120px',
          left: '24px',
          padding: '8px 14px',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '20px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          color: 'var(--text-primary)',
          cursor: 'pointer',
          zIndex: 50,
          fontSize: '12px',
        }}
      >
        <div className="status-dot status-active" style={{ width: '6px', height: '6px' }} />
        {activeWorkers.length || displayWorkers.length} active
      </button>
    );
  }

  return (
    <div className="worker-overlay">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div
            style={{
              width: '24px',
              height: '24px',
              borderRadius: '50%',
              background: 'var(--accent-dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--accent)',
            }}
          >
            <span style={{ fontSize: '10px', fontWeight: 600 }}>W</span>
          </div>
          <div>
            <h4 style={{ fontSize: '12px', fontWeight: 600 }}>Workers</h4>
            <p style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>
              {completedWorkers.length}/{displayWorkers.length} done
            </p>
          </div>
        </div>
        <button
          onClick={() => setIsVisible(false)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            padding: '4px',
          }}
        >
          <X size={14} />
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {displayWorkers.slice(0, 6).map((worker) => (
          <div
            key={worker.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <div
              style={{
                width: '20px',
                height: '20px',
                borderRadius: '50%',
                background:
                  worker.status === 'complete'
                    ? 'rgba(16, 185, 129, 0.15)'
                    : worker.status === 'running'
                      ? 'var(--accent-dim)'
                      : 'var(--bg-tertiary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color:
                  worker.status === 'complete'
                    ? 'var(--success)'
                    : worker.status === 'running'
                      ? 'var(--accent)'
                      : 'var(--text-muted)',
              }}
            >
              {getWorkerIcon(worker)}
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span style={{ fontSize: '11px', fontWeight: 500 }}>{worker.name}</span>
                <span style={{ fontSize: '9px', color: 'var(--text-muted)' }}>
                  {Math.round(worker.progress)}%
                </span>
              </div>
              <div
                style={{
                  height: '2px',
                  background: 'var(--bg-tertiary)',
                  borderRadius: '1px',
                  overflow: 'hidden',
                  marginTop: '3px',
                }}
              >
                <div
                  style={{
                    width: `${worker.progress}%`,
                    height: '100%',
                    background: worker.status === 'complete' ? 'var(--success)' : 'var(--accent)',
                    borderRadius: '1px',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
              {worker.message ? (
                <p
                  style={{
                    marginTop: '4px',
                    fontSize: '10px',
                    color: 'var(--text-secondary)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {worker.message}
                </p>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
