import { useCallback, useEffect } from 'react';
import { useMorpheusStore } from '../store';
import { useWindowSize } from '../hooks/useWindowSize';
import { DetailPanel } from './DetailPanel';
import { AgentChat } from './AgentChat';
import { TimelineBar } from './TimelineBar';
import { WorkerOverlay } from './WorkerOverlay';
import { MobileAgentChat } from './MobileAgentChat';
import { MobileTimelineTray } from './MobileTimelineTray';
import { MobileDetailView } from './MobileDetailView';
import API from '../services/api';
import type { WorkspaceSnapshot, WorkerStatus } from '../types';

export function ProjectWorkspace() {
  const { currentProject, mobileView, isTimelineTrayOpen, workers, hydrateWorkspace, setWorkers } = useMorpheusStore();
  const { isMobile } = useWindowSize();
  const projectId = currentProject?.id ?? null;
  const projectStatus = currentProject?.status ?? null;
  const isActiveGeneration =
    workers.some((worker) => worker.status === 'running' || worker.status === 'idle') ||
    ['generating_assets', 'generating_frames', 'generating_video'].includes(projectStatus ?? '');
  const refreshIntervalMs = isActiveGeneration ? 2000 : 15000;

  const refreshWorkspace = useCallback(async () => {
    if (!projectId) {
      return;
    }
    try {
      const [snapshot, workerState] = await Promise.all([
        API.workspace.get(projectId),
        API.workers.getStatus(projectId).catch(() => []),
      ]);
      hydrateWorkspace({ ...snapshot, workers: workerState });
    } catch (error) {
      console.error('Failed to hydrate workspace:', error);
    }
  }, [projectId, hydrateWorkspace]);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    API.ws.disconnect();

    void refreshWorkspace();
    API.ws.connect(projectId);
    const offWorkspaceUpdate = API.ws.on<WorkspaceSnapshot>('workspace_update', (snapshot) => {
      if (!cancelled) {
        hydrateWorkspace(snapshot);
      }
    });
    const offWorkerSnapshot = API.ws.on<WorkerStatus[]>('worker_snapshot', (workerState) => {
      if (!cancelled) {
        setWorkers(workerState);
      }
    });
    const offProjectUpdate = API.ws.on('project_update', () => {
      void refreshWorkspace();
    });
    const offWorkerUpdate = API.ws.on('worker_update', () => {
      void refreshWorkspace();
    });
    const offFrameGenerated = API.ws.on('frame_generated', () => {
      void refreshWorkspace();
    });
    const offStoryboardGenerated = API.ws.on('storyboard_generated', () => {
      void refreshWorkspace();
    });
    const offEntityImageGenerated = API.ws.on('entity_image_generated', () => {
      void refreshWorkspace();
    });
    return () => {
      cancelled = true;
      offWorkspaceUpdate();
      offWorkerSnapshot();
      offProjectUpdate();
      offWorkerUpdate();
      offFrameGenerated();
      offStoryboardGenerated();
      offEntityImageGenerated();
      API.ws.disconnect();
    };
  }, [projectId, refreshWorkspace, hydrateWorkspace, setWorkers]);

  useEffect(() => {
    if (!projectId) {
      return;
    }
    const interval = window.setInterval(() => {
      void refreshWorkspace();
    }, refreshIntervalMs);

    return () => {
      window.clearInterval(interval);
    };
  }, [projectId, refreshIntervalMs, refreshWorkspace]);

  if (!currentProject) {
    return (
      <div className="workspace-empty-state">
        <div className="workspace-empty-card glass-panel">
          <span className="workspace-empty-kicker">No project selected</span>
          <h2>Pick a production from the dashboard.</h2>
          <p>The workspace opens once a project is active in the local desktop pipeline.</p>
        </div>
      </div>
    );
  }

  const showWorkers =
    workers.some((worker) => worker.status === 'running' || worker.status === 'idle') ||
    ['generating_assets', 'generating_frames', 'generating_video'].includes(currentProject.status);

  // Mobile Layout
  if (isMobile) {
    return (
      <div className="project-workspace-mobile" data-testid="project-workspace-mobile">
        {mobileView === 'chat' ? <MobileAgentChat /> : <MobileDetailView />}
        {isTimelineTrayOpen && <MobileTimelineTray />}
        {showWorkers && <WorkerOverlay />}
      </div>
    );
  }

  // Desktop Layout
  return (
    <div className="project-workspace-shell" data-testid="project-workspace-shell">
      <div className="project-workspace" data-testid="project-workspace">
        <div className="workspace-main">
          <DetailPanel />
          <AgentChat />
        </div>
        <TimelineBar />
      </div>
    </div>
  );
}
