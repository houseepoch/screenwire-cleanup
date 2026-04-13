import { useEffect, useRef, useState } from 'react';
import { useMorpheusStore } from '../store';
import { Plus, ChevronRight, ChevronLeft } from 'lucide-react';
import { HowItWorksModal } from './HowItWorksModal';
import { desktopService } from '../services';
import type { Project } from '../types';

// Cover images for project cards
const PROJECT_COVERS = [
  '/storyboard-01.jpg',
  '/storyboard-02.jpg',
  '/storyboard-03.jpg',
  '/storyboard-04.jpg',
  '/storyboard-05.jpg',
  '/storyboard-06.jpg',
  '/timeline-01.jpg',
  '/timeline-02.jpg',
  '/timeline-03.jpg',
];

const PROJECT_STATUS_LABELS: Record<Project['status'], string> = {
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

export function HomeScreen() {
  const { projects, selectProject, setCurrentView } = useMorpheusStore();
  const [showNewProjectModal, setShowNewProjectModal] = useState(false);
  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectDescription, setNewProjectDescription] = useState('');
  const [displayProjects, setDisplayProjects] = useState<Project[]>(projects);
  const [actionError, setActionError] = useState<string | null>(null);
  
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(true);

  const openCreateProjectModal = () => {
    setActionError(null);
    setShowNewProjectModal(true);
  };

  function checkScroll() {
    const container = scrollContainerRef.current;
    if (container) {
      setCanScrollLeft(container.scrollLeft > 0);
      setCanScrollRight(
        container.scrollLeft < container.scrollWidth - container.clientWidth - 10
      );
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function hydrateProjects() {
      if (!desktopService.isAvailable()) {
        setDisplayProjects(projects);
        return;
      }
      try {
        const desktopProjects = await desktopService.listProjects();
        if (!cancelled) {
          setDisplayProjects(desktopProjects);
        }
      } catch (error) {
        console.error('Failed to load desktop projects:', error);
        setActionError('Failed to load local projects.');
        if (!cancelled) {
          setDisplayProjects(projects);
        }
      }
    }

    hydrateProjects();
    return () => {
      cancelled = true;
    };
  }, [projects]);

  useEffect(() => {
    const handler = () => openCreateProjectModal();
    window.addEventListener('morpheus:new-project', handler);
    return () => window.removeEventListener('morpheus:new-project', handler);
  }, []);

  useEffect(() => {
    checkScroll();
  }, [displayProjects.length]);

  const handleCreateProject = async () => {
    setActionError(null);
    if (newProjectName.trim()) {
      if (desktopService.isElectronHost() && !desktopService.isAvailable()) {
        setActionError('Desktop bridge unavailable. Fully restart Morpheus and try again.');
        return;
      }
      if (desktopService.isAvailable()) {
        try {
          const created = await desktopService.createProject({
            name: newProjectName,
            description: newProjectDescription,
            creativityLevel: 'balanced',
            frameBudget: 'auto',
            mediaStyle: 'live_clear',
          });
          if (!created) {
            setActionError('Project creation returned no project. Restart Morpheus and try again.');
            return;
          }
          const selected = await desktopService.selectProject(created.id);
          if (!selected) {
            setActionError('Project backend failed to start. Check the local backend logs and try again.');
            return;
          }
          setDisplayProjects((prev) => [created, ...prev.filter((p) => p.id !== created.id)]);
          selectProject(created);
          setCurrentView('onboarding');
        } catch (error) {
          console.error('Failed to create desktop project:', error);
          setActionError(error instanceof Error ? error.message : 'Failed to create project.');
          return;
        }
      } else {
        setActionError('Desktop backend is unavailable. Launch Morpheus through Electron to create a project.');
        return;
      }
      setShowNewProjectModal(false);
      setNewProjectName('');
      setNewProjectDescription('');
    }
  };

  const handleProjectClick = async (proj: Project) => {
    setActionError(null);
    if (desktopService.isElectronHost() && !desktopService.isAvailable()) {
      setActionError('Desktop bridge unavailable. Fully restart Morpheus and try again.');
      return;
    }
    if (desktopService.isAvailable()) {
      try {
        const selected = await desktopService.selectProject(proj.id);
        if (!selected) {
          setActionError('Project backend failed to start. Check the local backend logs and try again.');
          return;
        }
      } catch (error) {
        console.error('Failed to select desktop project:', error);
        setActionError(error instanceof Error ? error.message : 'Failed to open project.');
        return;
      }
    }
    if (!desktopService.isAvailable()) {
      setActionError('Desktop backend is unavailable. Launch Morpheus through Electron to open a project.');
      return;
    }
    selectProject(proj);
  };

  const getProjectCover = (index: number) => {
    return PROJECT_COVERS[index % PROJECT_COVERS.length];
  };

  const coverForProject = (project: Project, index: number) => {
    return project.coverImageUrl || getProjectCover(index);
  };

  const scroll = (direction: 'left' | 'right') => {
    const container = scrollContainerRef.current;
    if (container) {
      const scrollAmount = direction === 'left' ? -400 : 400;
      container.scrollBy({ left: scrollAmount, behavior: 'smooth' });
      setTimeout(checkScroll, 300);
    }
  };

  const formatDate = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
    }).format(new Date(date));
  };

  const activeProjects = displayProjects.filter((project) => project.status !== 'complete').length;
  const completedProjects = displayProjects.filter((project) => project.status === 'complete').length;
  const averageProgress = displayProjects.length
    ? Math.round(displayProjects.reduce((sum, project) => sum + project.progress, 0) / displayProjects.length)
    : 0;
  const latestProject = displayProjects[0] ?? null;

  const projectSummary = (project: Project) =>
    project.coverSummary ||
    project.description ||
    'Open the workspace to continue shaping cast, scenes, and timeline output.';

  return (
    <div className="home-screen">
      <section className="hero-section">
        <div className="hero-background">
          <img src="/hero-lighthouse.jpg" alt="Morpheus Studio" />
        </div>
        <div className="hero-vignette" />
        <div className="hero-grid">
          <div className="hero-content">
            <span className="hero-eyebrow">AI preproduction suite</span>
            <h1 className="hero-title">Turn a story into a cinematic production system.</h1>
            <p className="hero-subtitle">
              Morpheus turns source material into a working pipeline: outline, cast, locations,
              storyboard, timeline, and export-ready visual direction in one workspace.
            </p>
            <div className="hero-actions">
              <button
                className="btn-accent"
                data-testid="home-start-creating"
                onClick={openCreateProjectModal}
              >
                Start a new project
              </button>
              <button
                className="secondary-link"
                data-testid="home-see-how-it-works"
                onClick={() => setShowHowItWorks(true)}
              >
                See how it works
                <ChevronRight size={14} />
              </button>
            </div>
            <div className="hero-metrics">
              <div className="hero-metric-card glass-panel">
                <span className="hero-metric-value">{displayProjects.length}</span>
                <span className="hero-metric-label">projects tracked</span>
              </div>
              <div className="hero-metric-card glass-panel">
                <span className="hero-metric-value">{activeProjects}</span>
                <span className="hero-metric-label">currently in flight</span>
              </div>
              <div className="hero-metric-card glass-panel">
                <span className="hero-metric-value">{averageProgress}%</span>
                <span className="hero-metric-label">average progress</span>
              </div>
            </div>
          </div>

          <aside className="hero-summary-card glass-panel">
            <div className="hero-summary-header">
              <span className="hero-summary-kicker">Pipeline snapshot</span>
              <span className="hero-summary-badge">{completedProjects} delivered</span>
            </div>
            <div className="hero-summary-flow">
              <div className="hero-summary-step">
                <span>01</span>
                <p>Ingest scripts, treatments, prompts, and visual references.</p>
              </div>
              <div className="hero-summary-step">
                <span>02</span>
                <p>Build scenes, cast, locations, and storyboard logic.</p>
              </div>
              <div className="hero-summary-step">
                <span>03</span>
                <p>Shape frames, timing, and agent-driven revisions in one control room.</p>
              </div>
            </div>

            {latestProject ? (
              <button
                type="button"
                className="hero-summary-project"
                onClick={() => void handleProjectClick(latestProject)}
              >
                <span className="hero-summary-project-label">Resume latest</span>
                <strong>{latestProject.name}</strong>
                <p>{projectSummary(latestProject)}</p>
                <div className="hero-summary-project-meta">
                  <span>{PROJECT_STATUS_LABELS[latestProject.status]}</span>
                  <span>{latestProject.progress}% ready</span>
                </div>
              </button>
            ) : (
              <div className="hero-summary-empty">
                <strong>No productions started yet.</strong>
                <p>Create a project to open the full Morpheus workflow.</p>
              </div>
            )}
          </aside>
        </div>
      </section>

      <section className="projects-section">
        <div className="section-header">
          <div>
            <span className="section-kicker">Workspace</span>
            <h2 className="section-title">Recent productions</h2>
            <p className="section-description">
              Pick up an active pipeline, review completed work, or start a new visual production.
            </p>
          </div>
          <button type="button" className="btn-secondary section-action" onClick={openCreateProjectModal}>
            <Plus size={14} />
            New project
          </button>
        </div>

        {actionError && (
          <div className="inline-alert" data-testid="home-action-error">
            {actionError}
          </div>
        )}

        <div className="projects-scroll-container desktop-only">
          {displayProjects.length > 0 && (
            <>
              <button
                type="button"
                className={`scroll-arrow scroll-left ${canScrollLeft ? 'visible' : ''}`}
                onClick={() => scroll('left')}
              >
                <ChevronLeft size={24} />
              </button>
              <button
                type="button"
                className={`scroll-arrow scroll-right ${canScrollRight ? 'visible' : ''}`}
                onClick={() => scroll('right')}
              >
                <ChevronRight size={24} />
              </button>
            </>
          )}
          
          <div 
            ref={scrollContainerRef}
            className="projects-scroll"
            onScroll={checkScroll}
          >
            <button
              type="button"
              className="project-card new-project-card"
              data-testid="new-project-card"
              onClick={openCreateProjectModal}
            >
              <div className="project-card-cover">
                <div className="new-project-overlay">
                  <Plus size={34} />
                  <span className="project-card-kicker">Create</span>
                  <span className="project-card-title">New Project</span>
                  <span className="project-card-summary">Start with a concept, brief, or script upload.</span>
                </div>
              </div>
            </button>

            {displayProjects.map((project, index) => (
              <button
                type="button"
                key={project.id}
                className="project-card"
                data-testid={`project-card-${project.id}`}
                onClick={() => handleProjectClick(project)}
              >
                <div className="project-card-cover">
                  <img 
                    src={coverForProject(project, index)} 
                    alt={project.name}
                    className="project-card-image"
                  />
                  <div className="project-card-gradient" />
                  <div className="project-card-status">
                    <span
                      className="status-dot"
                      style={{
                        background:
                          project.status === 'complete'
                            ? 'var(--success)'
                            : project.status.includes('generating')
                              ? 'var(--accent)'
                              : 'var(--warning)',
                      }}
                    />
                    <span>{PROJECT_STATUS_LABELS[project.status]}</span>
                  </div>
                  <div className="project-card-info">
                    <span className="project-card-kicker">{project.progress}% ready</span>
                    <span className="project-card-title">{project.name}</span>
                    <span className="project-card-summary">{projectSummary(project)}</span>
                    <div className="project-card-meta">
                      <span className="project-card-date">{formatDate(project.updatedAt)}</span>
                      <span>{project.creativityLevel}</span>
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="projects-grid mobile-only">
          <button
            type="button"
            className="project-card new-project-card"
            data-testid="new-project-card-mobile"
            onClick={openCreateProjectModal}
          >
            <div className="project-card-cover">
              <div className="new-project-overlay">
                <Plus size={32} />
                <span className="project-card-kicker">Create</span>
                <span className="project-card-title">New Project</span>
                <span className="project-card-summary">Start with a concept or uploaded source material.</span>
              </div>
            </div>
          </button>

          {displayProjects.map((project, index) => (
            <button
              type="button"
              key={project.id}
              className="project-card"
              data-testid={`project-card-mobile-${project.id}`}
              onClick={() => handleProjectClick(project)}
            >
              <div className="project-card-cover">
                <img 
                  src={coverForProject(project, index)} 
                  alt={project.name}
                  className="project-card-image"
                />
                <div className="project-card-gradient" />
                <div className="project-card-status">
                  <span
                    className="status-dot"
                    style={{
                      background:
                        project.status === 'complete'
                          ? 'var(--success)'
                          : project.status.includes('generating')
                            ? 'var(--accent)'
                            : 'var(--warning)',
                    }}
                  />
                  <span>{PROJECT_STATUS_LABELS[project.status]}</span>
                </div>
                <div className="project-card-info">
                  <span className="project-card-kicker">{project.progress}% ready</span>
                  <span className="project-card-title">{project.name}</span>
                  <span className="project-card-summary">{projectSummary(project)}</span>
                  <div className="project-card-meta">
                    <span className="project-card-date">{formatDate(project.updatedAt)}</span>
                    <span>{project.creativityLevel}</span>
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </section>

      {showNewProjectModal && (
        <div
          className="modal-overlay"
          data-testid="create-project-modal"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setShowNewProjectModal(false);
            }
          }}
        >
          <div className="modal-content create-project-modal" data-testid="create-project-dialog">
            <div className="modal-header">
              <h3 className="modal-title">Create New Project</h3>
              <p className="modal-subtitle">
                Start your creative journey. You can always change these details later.
              </p>
            </div>

            {actionError && (
              <div className="inline-alert" data-testid="create-project-error">
                {actionError}
              </div>
            )}

            <div className="modal-form-group">
              <label className="field-label">
                Project Name *
              </label>
              <input
                type="text"
                className="input-field"
                data-testid="create-project-name"
                placeholder="e.g., The Lighthouse"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                autoFocus
              />
            </div>

            <div className="modal-form-group">
              <label className="field-label">
                Description
              </label>
              <textarea
                className="input-field textarea-field"
                data-testid="create-project-description"
                placeholder="Brief description of your project..."
                value={newProjectDescription}
                onChange={(e) => setNewProjectDescription(e.target.value)}
                rows={3}
              />
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                data-testid="create-project-cancel"
                onClick={() => setShowNewProjectModal(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-accent"
                data-testid="create-project-submit"
                onClick={handleCreateProject}
                disabled={!newProjectName.trim()}
              >
                Create Project
              </button>
            </div>
          </div>
        </div>
      )}

      <HowItWorksModal
        isOpen={showHowItWorks}
        onClose={() => setShowHowItWorks(false)}
      />
    </div>
  );
}
