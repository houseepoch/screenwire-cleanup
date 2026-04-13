import { useEffect, useRef, useState } from 'react';
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
      name: 'Preproduction Build',
      status: 'running',
      progress,
      message: 'Generating script, graph, and review assets...',
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
  const [displayProgress, setDisplayProgress] = useState(0);
  const animationFrameRef = useRef<number | null>(null);
  const displayProgressRef = useRef(0);
  const projectKeyRef = useRef<string | null>(null);

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

  const activeWorkers = displayWorkers.filter((worker) => worker.status === 'running' || worker.status === 'idle');
  const completedWorkers = displayWorkers.filter((worker) => worker.status === 'complete');
  const primaryWorker = activeWorkers.find((worker) => worker.status === 'running') ?? activeWorkers[0] ?? displayWorkers[0];
  const projectKey = currentProject?.id ?? 'no-project';
  const derivedProgress = displayWorkers.reduce((total, worker) => {
    const workerProgress = worker.status === 'complete' ? 100 : Math.max(0, Math.min(100, worker.progress));
    return total + workerProgress;
  }, 0) / Math.max(displayWorkers.length, 1);
  const targetProgress = Math.max(
    0,
    Math.min(100, Math.max(currentProject?.progress ?? 0, derivedProgress)),
  );
  const statusLabel =
    completedWorkers.length === displayWorkers.length
      ? 'Complete'
      : primaryWorker?.status === 'running'
        ? 'In progress'
        : primaryWorker?.status === 'idle'
          ? 'Queued'
          : 'Standby';
  const headline =
    completedWorkers.length === displayWorkers.length
      ? 'All workers complete'
      : primaryWorker?.name || 'Worker queue';
  const supportingMessage =
    primaryWorker?.message ||
    (completedWorkers.length === displayWorkers.length
      ? 'Pipeline pass finished successfully.'
      : `${activeWorkers.length} worker${activeWorkers.length === 1 ? '' : 's'} active`);

  useEffect(() => {
    displayProgressRef.current = displayProgress;
  }, [displayProgress]);

  useEffect(() => {
    if (projectKeyRef.current !== projectKey) {
      projectKeyRef.current = projectKey;
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
      animationFrameRef.current = requestAnimationFrame(() => {
        displayProgressRef.current = targetProgress;
        setDisplayProgress(targetProgress);
        animationFrameRef.current = null;
      });
      return;
    }

    const start = performance.now();
    const from = displayProgressRef.current;
    const to = targetProgress;
    const duration = Math.max(600, Math.min(1800, Math.abs(to - from) * 24));

    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
    }

    const step = (timestamp: number) => {
      const elapsed = timestamp - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const nextProgress = from + (to - from) * eased;
      displayProgressRef.current = nextProgress;
      setDisplayProgress(nextProgress);

      if (progress < 1) {
        animationFrameRef.current = requestAnimationFrame(step);
      } else {
        animationFrameRef.current = null;
      }
    };

    animationFrameRef.current = requestAnimationFrame(step);

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
    };
  }, [projectKey, targetProgress]);

  if (!displayWorkers.length) {
    return null;
  }

  if (!isVisible) {
    return (
      <button
        type="button"
        onClick={() => setIsVisible(true)}
        className="worker-overlay-toggle"
      >
        <div className="status-dot status-active" style={{ width: '6px', height: '6px' }} />
        {activeWorkers.length || displayWorkers.length} active
      </button>
    );
  }

  return (
    <div className="worker-overlay" data-testid="worker-overlay">
      <div className="worker-overlay-header">
        <div className="worker-overlay-title">
          <div className="worker-overlay-badge">
            <span style={{ fontSize: '10px', fontWeight: 600 }}>W</span>
          </div>
          <div>
            <h4>Workers</h4>
            <p>
              {completedWorkers.length}/{displayWorkers.length} done
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setIsVisible(false)}
          className="worker-close-btn"
        >
          <X size={14} />
        </button>
      </div>

      <div className="worker-summary">
        <div className="worker-summary-top">
          <div className="worker-summary-main">
            <div
              className={`worker-icon-wrap ${
                completedWorkers.length === displayWorkers.length
                  ? 'is-complete'
                  : primaryWorker?.status === 'running'
                    ? 'is-running'
                    : 'is-idle'
              }`}
            >
              {primaryWorker ? getWorkerIcon(primaryWorker) : <FileText size={14} />}
            </div>
            <div className="worker-summary-copy">
              <span className="worker-summary-name">{headline}</span>
              <p className="worker-summary-message">{supportingMessage}</p>
            </div>
          </div>
          <div className="worker-summary-stats">
            <span className="worker-summary-status">{statusLabel}</span>
            <span className="worker-row-progress">{Math.round(displayProgress)}%</span>
          </div>
        </div>

        <div className="worker-progress-track is-large">
          <div
            className="worker-progress-fill"
            style={{
              width: `${displayProgress}%`,
              background:
                completedWorkers.length === displayWorkers.length ? 'var(--success)' : 'var(--accent)',
            }}
          />
        </div>

        <div className="worker-summary-meta">
          <span className="worker-summary-chip">{activeWorkers.length} active</span>
          <span className="worker-summary-chip">{completedWorkers.length} complete</span>
          <span className="worker-summary-chip">{displayWorkers.length} total</span>
        </div>
      </div>
    </div>
  );
}
