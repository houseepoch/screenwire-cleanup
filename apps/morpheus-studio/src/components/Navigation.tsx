import { useEffect, useState } from 'react';
import { useMorpheusStore } from '../store';
import API from '../services/api';
import { desktopService } from '../services';
import { ChevronLeft, Plus } from 'lucide-react';
import { WorkerNavStrip } from './WorkerActivity';

const PROJECT_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  onboarding: 'Setup',
  skeleton_review: 'Building Review Pack',
  generating_assets: 'Building Review Pack',
  reference_review: 'Reference Review',
  generating_frames: 'Generating Frames',
  timeline_review: 'Timeline Review',
  generating_video: 'Rendering Video',
  complete: 'Complete',
};

function getContinueGate(status?: string | null): 'references' | 'timeline' | null {
  if (status === 'reference_review') {
    return 'references';
  }
  if (status === 'timeline_review') {
    return 'timeline';
  }
  return null;
}

export function Navigation() {
  const {
    currentView,
    setCurrentView,
    currentProject,
    selectProject,
    hydrateWorkspace,
    setIsExportWizardOpen,
    activeTab,
    timelineFrames,
    selectedFrameId,
    reports,
  } = useMorpheusStore();
  const [isContinuing, setIsContinuing] = useState(false);
  const [isLeavingProject, setIsLeavingProject] = useState(false);
  const [continueError, setContinueError] = useState<string | null>(null);
  const showProjectChrome = currentView === 'project' && Boolean(currentProject);
  const selectedFrame = selectedFrameId
    ? timelineFrames.find((frame) => frame.id === selectedFrameId) ?? null
    : null;
  const exportUrl =
    reports.finalExport ||
    selectedFrame?.videoUrl ||
    timelineFrames.find((frame) => frame.videoUrl)?.videoUrl ||
    null;
  const showExportAction = showProjectChrome && currentProject?.status === 'complete' && activeTab === 'video';

  const continueGate = showProjectChrome ? getContinueGate(currentProject?.status) : null;
  const continueDisabled = showExportAction
    ? !exportUrl
    : !showProjectChrome || !currentProject || !continueGate || isContinuing;
  const continueErrorLabel =
    continueError && continueError.length > 96 ? `${continueError.slice(0, 93)}...` : continueError;

  const handleStartCreating = () => {
    window.dispatchEvent(new CustomEvent('morpheus:new-project'));
  };

  const handleBackToProjects = async () => {
    setContinueError(null);
    setIsLeavingProject(true);
    try {
      if (desktopService.isAvailable()) {
        await desktopService.returnToProjects();
      }
      selectProject(null);
      setCurrentView('home');
    } catch (error) {
      console.error('Failed to return to projects:', error);
      setContinueError(error instanceof Error ? error.message : 'Failed to return to projects.');
    } finally {
      setIsLeavingProject(false);
    }
  };

  useEffect(() => {
    setContinueError(null);
  }, [currentProject?.id, currentProject?.status]);

  const handleContinue = async () => {
    if (!currentProject || !continueGate) {
      return;
    }
    setContinueError(null);
    setIsContinuing(true);
    try {
      const snapshot = await API.workflow.approve(currentProject.id, continueGate);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error(`Failed to continue ${continueGate}:`, error);
      setContinueError(error instanceof Error ? error.message : 'Failed to continue the pipeline.');
    } finally {
      setIsContinuing(false);
    }
  };

  const handleExport = () => {
    if (!exportUrl) {
      return;
    }
    setContinueError(null);
    setIsExportWizardOpen(true);
  };

  return (
    <nav className="navigation">
      <div className="nav-side nav-side-left">
        {showProjectChrome && currentProject ? (
          <>
            <button
              className="nav-back-btn"
              data-testid="nav-back-to-projects"
              onClick={() => void handleBackToProjects()}
              disabled={isLeavingProject}
            >
              <ChevronLeft size={18} />
              {isLeavingProject ? 'Returning...' : 'Back to projects'}
            </button>
            <div className="nav-project-meta">
              <span className="nav-project-label">Active project</span>
              <span className="nav-project-name">{currentProject.name}</span>
            </div>
          </>
        ) : (
          <div className="nav-brand">
            <div className="nav-brand-mark">M</div>
            <div className="nav-brand-copy">
              <div className="nav-logo">Morpheus</div>
              <div className="nav-tagline">Studio pipeline</div>
            </div>
          </div>
        )}
      </div>

      <div className="nav-center">
        {!showProjectChrome || !currentProject ? (
          <div className="nav-runtime-pill">Local desktop orchestration</div>
        ) : (
          <div className="nav-runtime-pill">
            {PROJECT_STATUS_LABELS[currentProject.status] ?? currentProject.status}
          </div>
        )}
      </div>

      <div className="nav-actions">
        <div className="nav-actions-row">
          {!showProjectChrome || !currentProject ? (
            <button
              className="btn-accent nav-create-btn"
              aria-label="Create new project"
              title="Create new project"
              onClick={handleStartCreating}
            >
              <Plus size={16} />
              New Project
            </button>
          ) : (
            <>
              <WorkerNavStrip />
            
            <button
              className={`btn-accent nav-continue-btn ${showExportAction ? 'nav-export-btn' : ''}`.trim()}
              type="button"
              data-testid="workflow-continue-button"
              disabled={continueDisabled}
              aria-label={showExportAction ? 'Export video' : 'Continue pipeline'}
              title={
                showExportAction
                  ? exportUrl
                    ? 'Export the current video output'
                    : 'Export becomes available once a render output exists'
                  : continueGate
                  ? `Continue from ${continueGate === 'references' ? 'reference review' : 'timeline review'}`
                  : 'Continue becomes available when the next review gate is ready'
              }
              onClick={showExportAction ? handleExport : () => void handleContinue()}
            >
              {showExportAction ? 'Export' : isContinuing ? 'Continuing...' : 'Continue'}
            </button>
            </>
          )}
        </div>
        {continueError ? (
          <div
            className="nav-inline-error"
            data-testid="workflow-continue-error"
            title={continueError}
            aria-live="polite"
          >
            {continueErrorLabel}
          </div>
        ) : null}
      </div>
    </nav>
  );
}
