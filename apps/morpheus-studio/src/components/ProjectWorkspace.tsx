import { useEffect } from 'react';
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

  useEffect(() => {
    if (!currentProject) return;
    let cancelled = false;
    API.ws.disconnect();

    async function refreshWorkspace() {
      if (!currentProject) {
        return;
      }
      try {
        const [snapshot, workerState] = await Promise.all([
          API.workspace.get(currentProject.id),
          API.workers.getStatus(currentProject.id).catch(() => []),
        ]);
        if (!cancelled) {
          hydrateWorkspace({ ...snapshot, workers: workerState });
        }
      } catch (error) {
        console.error('Failed to hydrate workspace:', error);
      }
    }

    refreshWorkspace();
    API.ws.connect(currentProject.id);
    const offWorkspaceUpdate = API.ws.on<WorkspaceSnapshot>('workspace_update', (snapshot) => {
      hydrateWorkspace(snapshot);
    });
    const offWorkerSnapshot = API.ws.on<WorkerStatus[]>('worker_snapshot', (workerState) => {
      setWorkers(workerState);
    });
    const offProjectUpdate = API.ws.on('project_update', () => {
      void refreshWorkspace();
    });
    const offWorkerUpdate = API.ws.on('worker_update', () => {
      void refreshWorkspace();
    });
    const interval = window.setInterval(refreshWorkspace, 15000);
    return () => {
      cancelled = true;
      offWorkspaceUpdate();
      offWorkerSnapshot();
      offProjectUpdate();
      offWorkerUpdate();
      API.ws.disconnect();
      window.clearInterval(interval);
    };
  }, [currentProject, hydrateWorkspace, setWorkers]);

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
      {showWorkers && <WorkerOverlay />}
    </div>
  );
}
