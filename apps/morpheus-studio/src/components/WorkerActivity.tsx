import { useState } from 'react';
import {
  AlertCircle,
  Check,
  Clock3,
  FileText,
  Film,
  Image,
  LayoutGrid,
  MapPin,
  Square,
} from 'lucide-react';
import { useMorpheusStore } from '../store';
import { getWorkerDisplayState } from './workerActivityState';
import type { WorkerStatus } from '../types';
import API from '../services/api';

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

export function WorkerStopButton({ compact = false }: { compact?: boolean }) {
  const { currentProject, workers, hydrateWorkspace, setWorkers } = useMorpheusStore();
  const [isStopping, setIsStopping] = useState(false);

  const canStop =
    Boolean(currentProject) &&
    (workers.some((worker) => worker.status === 'running' && worker.cancellable) ||
      ['generating_frames', 'generating_video'].includes(currentProject?.status ?? ''));

  if (!currentProject || !canStop) {
    return null;
  }

  const handleStop = async () => {
    setIsStopping(true);
    try {
      await API.workers.cancelAll(currentProject.id);
      const [snapshot, workerState] = await Promise.all([
        API.workspace.get(currentProject.id),
        API.workers.getStatus(currentProject.id).catch(() => []),
      ]);
      hydrateWorkspace({ ...snapshot, workers: workerState });
      setWorkers(workerState);
    } catch (error) {
      console.error('Failed to stop workers:', error);
    } finally {
      setIsStopping(false);
    }
  };

  return (
    <button
      type="button"
      className={`worker-stop-btn ${compact ? 'is-compact' : ''}`.trim()}
      onClick={() => void handleStop()}
      disabled={isStopping}
      aria-label="Stop active generation"
      title="Stop active generation"
    >
      <Square size={compact ? 11 : 12} />
      <span>{isStopping ? 'Stopping...' : 'Stop'}</span>
    </button>
  );
}

export function WorkerNavStrip() {
  const { currentProject, workers } = useMorpheusStore();
  const {
    displayWorkers,
    completedWorkers,
    primaryWorker,
    targetProgress,
    headline,
    supportingMessage,
    showAsActive,
  } = getWorkerDisplayState(currentProject, workers);

  if (!currentProject || !showAsActive || !displayWorkers.length) {
    return null;
  }

  const lineCopy = supportingMessage || headline;

  return (
    <div className="nav-worker-strip" data-testid="nav-worker-strip">
      <div className="nav-worker-strip-main">
        <div
          className={`worker-icon-wrap ${
            completedWorkers.length === displayWorkers.length
              ? 'is-complete'
              : primaryWorker?.status === 'running'
                ? 'is-running'
                : ''
          }`}
        >
          {primaryWorker ? getWorkerIcon(primaryWorker, 12) : <FileText size={12} />}
        </div>
        <div className="nav-worker-strip-copy">
          <span className="nav-worker-strip-message">{lineCopy}</span>
        </div>
      </div>
      <div className="nav-worker-strip-side">
        <WorkerStopButton compact />
        <span className="nav-worker-strip-progress">{Math.round(targetProgress)}%</span>
        <div className="nav-worker-track">
          <div
            className="nav-worker-fill"
            style={{
              width: `${targetProgress}%`,
              background:
                completedWorkers.length === displayWorkers.length ? 'var(--success)' : 'var(--accent)',
            }}
          />
        </div>
      </div>
    </div>
  );
}
