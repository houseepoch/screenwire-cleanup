import type { Project, WorkerStatus } from '../types';

function clampProgress(value: number): number {
  return Math.max(0, Math.min(100, value));
}

export function fallbackWorkers(status: string | undefined, progress: number): WorkerStatus[] {
  if (!status) {
    return [];
  }

  const workerMap: Record<string, WorkerStatus> = {
    generating_assets: {
      id: 'phase-assets',
      name: 'Preproduction Build',
      status: 'running',
      progress,
      message: 'Initializing project build...',
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

export function getWorkerDisplayState(currentProject: Project | null, workers: WorkerStatus[]) {
  const displayWorkers = workers.length
    ? workers
    : fallbackWorkers(currentProject?.status, currentProject?.progress ?? 0);
  const activeWorkers = displayWorkers.filter((worker) => worker.status === 'running' || worker.status === 'idle');
  const completedWorkers = displayWorkers.filter((worker) => worker.status === 'complete');
  const primaryWorker = activeWorkers.find((worker) => worker.status === 'running') ?? activeWorkers[0] ?? displayWorkers[0];
  const derivedProgress = displayWorkers.reduce((total, worker) => {
    const workerProgress = worker.status === 'complete' ? 100 : clampProgress(worker.progress);
    return total + workerProgress;
  }, 0) / Math.max(displayWorkers.length, 1);
  const targetProgress = clampProgress(
    Math.max(currentProject?.progress ?? 0, derivedProgress),
  );
  const statusLabel =
    completedWorkers.length === displayWorkers.length
      ? 'Complete'
      : primaryWorker?.status === 'running'
        ? 'In progress'
        : primaryWorker?.status === 'idle'
          ? 'Queued'
          : primaryWorker?.status === 'error'
            ? 'Issue'
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
  const showAsActive =
    activeWorkers.length > 0 ||
    ['generating_assets', 'generating_frames', 'generating_video'].includes(currentProject?.status ?? '');

  return {
    displayWorkers,
    activeWorkers,
    completedWorkers,
    primaryWorker,
    targetProgress,
    statusLabel,
    headline,
    supportingMessage,
    showAsActive,
  };
}
