import { useMorpheusStore } from '../store';
import { ChevronLeft, Plus } from 'lucide-react';

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

export function Navigation() {
  const { setCurrentView, currentProject, selectProject } = useMorpheusStore();

  const handleStartCreating = () => {
    window.dispatchEvent(new CustomEvent('morpheus:new-project'));
  };

  const handleBackToProjects = () => {
    selectProject(null);
    setCurrentView('home');
  };

  return (
    <nav className="navigation">
      <div className="nav-side nav-side-left">
        {currentProject ? (
          <>
            <button className="nav-back-btn" data-testid="nav-back-to-projects" onClick={handleBackToProjects}>
              <ChevronLeft size={18} />
              Back to projects
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
        {!currentProject ? (
          <div className="nav-runtime-pill">Local desktop orchestration</div>
        ) : (
          <div className="nav-runtime-pill">
            {PROJECT_STATUS_LABELS[currentProject.status] ?? currentProject.status}
          </div>
        )}
      </div>

      <div className="nav-actions">
        {!currentProject ? (
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
          <div className="nav-runtime-pill nav-runtime-pill-live">
            Stage live
          </div>
        )}
      </div>
    </nav>
  );
}
