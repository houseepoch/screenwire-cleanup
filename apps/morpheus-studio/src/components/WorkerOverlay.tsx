import { useEffect, useRef, useState } from 'react';
import { useMorpheusStore } from '../store';
import {
  AlertCircle,
  Check,
  Clock3,
  FileText,
  Film,
  Image,
  LayoutGrid,
  MapPin,
  X,
} from 'lucide-react';
import type { WorkerStatus } from '../types';
import { getWorkerDisplayState } from './workerActivityState';
import { WorkerStopButton } from './WorkerActivity';

function getWorkerIcon(worker: WorkerStatus, size = 14) {
  const id = worker.id.toLowerCase();
  const name = worker.name.toLowerCase();

  if (worker.status === 'complete') {
    return <Check size={size - 4} />;
  }
  if (worker.status === 'error') {
    return <AlertCircle size={size} />;
  }
  if (id.includes('frame') || name.includes('frame')) {
    return <Image size={size} />;
  }
  if (id.includes('location') || name.includes('location')) {
    return <MapPin size={size} />;
  }
  if (id.includes('storyboard') || name.includes('storyboard')) {
    return <LayoutGrid size={size} />;
  }
  if (id.includes('video') || name.includes('video')) {
    return <Film size={size} />;
  }
  if (id.includes('cast') || name.includes('cast')) {
    return <Image size={size} />;
  }
  if (id.includes('wait') || name.includes('waiting')) {
    return <Clock3 size={size} />;
  }
  return <FileText size={size} />;
}

export function WorkerOverlay() {
  const { currentProject, workers } = useMorpheusStore();
  const [isVisible, setIsVisible] = useState(true);
  const [displayProgress, setDisplayProgress] = useState(0);
  const animationFrameRef = useRef<number | null>(null);
  const displayProgressRef = useRef(0);
  const projectKeyRef = useRef<string | null>(null);

  const {
    displayWorkers,
    activeWorkers,
    completedWorkers,
    primaryWorker,
    targetProgress,
    statusLabel,
    headline,
    supportingMessage,
  } = getWorkerDisplayState(currentProject, workers);
  const projectKey = currentProject?.id ?? 'no-project';

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
              {primaryWorker ? getWorkerIcon(primaryWorker, 14) : <FileText size={14} />}
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

        <div className="worker-summary-actions">
          <WorkerStopButton />
        </div>
      </div>
    </div>
  );
}
