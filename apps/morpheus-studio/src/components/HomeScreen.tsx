import { useEffect, useMemo, useState } from 'react';
import { useMorpheusStore } from '../store';
import {
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Clock3,
  FolderOpen,
  Plus,
  Sparkles,
} from 'lucide-react';
import { HowItWorksModal } from './HowItWorksModal';
import { desktopService } from '../services';
import type { Project } from '../types';

const HOME_SPLASH_MIN_DURATION_MS = 3500;
const HOME_SPLASH_SESSION_KEY = 'morpheus-home-splash-seen';
const LAST_OPENED_PROJECT_KEY = 'morpheus-last-opened-project-id';
const RECENT_PROJECT_LIMIT = 4;

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

function getStoredProjectId(key: string): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return window.localStorage.getItem(key);
}

export function HomeScreen() {
  const { projects, selectProject, setCurrentView } = useMorpheusStore();
  const [showNewProjectModal, setShowNewProjectModal] = useState(false);
  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectDescription, setNewProjectDescription] = useState('');
  const [displayProjects, setDisplayProjects] = useState<Project[]>(projects);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [lastOpenedProjectId, setLastOpenedProjectId] = useState<string | null>(() =>
    getStoredProjectId(LAST_OPENED_PROJECT_KEY),
  );
  const [showSplash, setShowSplash] = useState(() => {
    if (typeof window === 'undefined') {
      return false;
    }
    return window.sessionStorage.getItem(HOME_SPLASH_SESSION_KEY) !== '1';
  });
  const [isSplashFading, setIsSplashFading] = useState(false);

  const rememberProject = (projectId: string) => {
    setLastOpenedProjectId(projectId);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(LAST_OPENED_PROJECT_KEY, projectId);
    }
  };

  const openCreateProjectModal = () => {
    setActionError(null);
    setShowNewProjectModal(true);
  };

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: number | undefined;

    async function hydrateProjects(surfaceErrors: boolean) {
      if (!desktopService.isAvailable()) {
        if (!cancelled) {
          setDisplayProjects(projects);
        }
        return;
      }
      try {
        const desktopProjects = await desktopService.listProjects();
        if (!cancelled) {
          setDisplayProjects(desktopProjects);
        }
      } catch (error) {
        console.error('Failed to load desktop projects:', error);
        if (!cancelled) {
          if (surfaceErrors) {
            setActionError('Failed to load local projects.');
          }
          setDisplayProjects(projects);
        }
      }
    }

    void hydrateProjects(true);
    if (desktopService.isAvailable()) {
      refreshTimer = window.setInterval(() => {
        void hydrateProjects(false);
      }, 9000);
    }

    return () => {
      cancelled = true;
      if (refreshTimer) {
        window.clearInterval(refreshTimer);
      }
    };
  }, [projects]);

  useEffect(() => {
    const handler = () => openCreateProjectModal();
    window.addEventListener('morpheus:new-project', handler);
    return () => window.removeEventListener('morpheus:new-project', handler);
  }, []);

  useEffect(() => {
    if (!showSplash || typeof window === 'undefined') {
      return;
    }
    const fadeTimer = window.setTimeout(() => {
      setIsSplashFading(true);
      window.sessionStorage.setItem(HOME_SPLASH_SESSION_KEY, '1');
    }, HOME_SPLASH_MIN_DURATION_MS);
    const hideTimer = window.setTimeout(() => {
      setShowSplash(false);
    }, HOME_SPLASH_MIN_DURATION_MS + 420);

    return () => {
      window.clearTimeout(fadeTimer);
      window.clearTimeout(hideTimer);
    };
  }, [showSplash]);

  const handleCreateProject = async () => {
    setActionError(null);
    if (!newProjectName.trim()) {
      return;
    }
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
        rememberProject(created.id);
        setDisplayProjects((prev) => [created, ...prev.filter((project) => project.id !== created.id)]);
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
  };

  const handleProjectClick = async (project: Project) => {
    setActionError(null);
    if (desktopService.isElectronHost() && !desktopService.isAvailable()) {
      setActionError('Desktop bridge unavailable. Fully restart Morpheus and try again.');
      return;
    }
    if (desktopService.isAvailable()) {
      try {
        const selected = await desktopService.selectProject(project.id);
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
    rememberProject(project.id);
    selectProject(project);
  };

  const sortedProjects = useMemo(
    () =>
      [...displayProjects].sort(
        (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
      ),
    [displayProjects],
  );

  const featuredProject = useMemo(() => {
    if (!sortedProjects.length) {
      return null;
    }
    return (
      sortedProjects.find((project) => project.id === lastOpenedProjectId) ?? sortedProjects[0] ?? null
    );
  }, [lastOpenedProjectId, sortedProjects]);

  const recentProjects = useMemo(
    () => sortedProjects.filter((project) => project.id !== featuredProject?.id),
    [featuredProject?.id, sortedProjects],
  );
  const visibleProjects = showAllProjects
    ? recentProjects
    : recentProjects.slice(0, RECENT_PROJECT_LIMIT);
  const hasHiddenProjects = recentProjects.length > RECENT_PROJECT_LIMIT;

  const getProjectCover = (index: number) => PROJECT_COVERS[index % PROJECT_COVERS.length];
  const coverForProject = (project: Project, index: number) => project.coverImageUrl || getProjectCover(index);

  const formatDate = (date: Date) =>
    new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
    }).format(new Date(date));

  const projectSummary = (project: Project) =>
    project.coverSummary ||
    project.description ||
    'Open the workspace to continue shaping cast, scenes, and timeline output.';

  const activeProjects = sortedProjects.filter((project) => project.status !== 'complete').length;
  const completedProjects = sortedProjects.filter((project) => project.status === 'complete').length;
  const averageProgress = sortedProjects.length
    ? Math.round(
        sortedProjects.reduce((sum, project) => sum + project.progress, 0) / sortedProjects.length,
      )
    : 0;

  const renderProjectCard = (project: Project, index: number) => (
    <button
      type="button"
      key={project.id}
      className="directory-project-card"
      data-testid={`project-card-${project.id}`}
      onClick={() => void handleProjectClick(project)}
    >
      <div className="directory-project-card-cover">
        <img src={coverForProject(project, index)} alt={project.name} className="directory-project-card-image" />
        <div className="directory-project-card-gradient" />
        <div className="directory-project-card-status">
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
      </div>
      <div className="directory-project-card-body">
        <div className="directory-project-card-head">
          <span className="directory-project-card-title">{project.name}</span>
          <span className="directory-project-card-progress">{project.progress}%</span>
        </div>
        <p className="directory-project-card-summary">{projectSummary(project)}</p>
        <div className="directory-project-card-meta">
          <span>{formatDate(project.updatedAt)}</span>
          <span>{project.creativityLevel}</span>
        </div>
      </div>
    </button>
  );

  return (
    <div className="home-screen home-directory-screen">
      {showSplash ? (
        <section className={`home-splash ${isSplashFading ? 'is-fading' : ''}`.trim()}>
          <div className="home-splash-background">
            <img src="/hero-lighthouse.jpg" alt="Morpheus Studio splash" />
          </div>
          <div className="home-splash-vignette" />
          <div className="home-splash-copy">
            <span className="hero-eyebrow">Morpheus Studio</span>
            <h1 className="home-splash-title">Build the visual world before the first final render.</h1>
            <p className="home-splash-subtitle">
              Loading your project directory, recent work, and cover art.
            </p>
            <div className="home-splash-progress" aria-hidden="true">
              <span className="home-splash-progress-bar" />
            </div>
          </div>
        </section>
      ) : null}

      <section className={`project-directory ${showSplash ? 'is-obscured' : 'is-ready'}`.trim()}>
        <div className="project-directory-header">
          <div className="project-directory-title-block">
            <span className="section-kicker">Project Directory</span>
            <h1 className="project-directory-title">Projects first, workspace one click away.</h1>
            <p className="project-directory-description">
              The latest project stays on top, recent productions stay centered, and the full directory opens only when you want it.
            </p>
          </div>
          <div className="project-directory-actions">
            <button
              type="button"
              className="btn-secondary project-directory-action"
              data-testid="home-see-how-it-works"
              onClick={() => setShowHowItWorks(true)}
            >
              <Sparkles size={15} />
              How it works
            </button>
            <button
              type="button"
              className="btn-accent project-directory-action"
              data-testid="home-start-creating"
              onClick={openCreateProjectModal}
            >
              <Plus size={15} />
              New project
            </button>
          </div>
        </div>

        {actionError ? (
          <div className="inline-alert" data-testid="home-action-error">
            {actionError}
          </div>
        ) : null}

        {featuredProject ? (
          <div className="project-directory-top">
            <button
              type="button"
              className="directory-feature-card glass-panel"
              onClick={() => void handleProjectClick(featuredProject)}
            >
              <div className="directory-feature-visual">
                <img
                  src={coverForProject(featuredProject, 0)}
                  alt={featuredProject.name}
                  className="directory-feature-image"
                />
                <div className="directory-feature-gradient" />
                <div className="directory-feature-badges">
                  <span className="directory-feature-chip">
                    {featuredProject.id === lastOpenedProjectId ? 'Last opened' : 'Latest update'}
                  </span>
                  <span className="directory-feature-chip is-muted">
                    {PROJECT_STATUS_LABELS[featuredProject.status]}
                  </span>
                </div>
              </div>
              <div className="directory-feature-body">
                <span className="directory-feature-kicker">Resume first</span>
                <h2 className="directory-feature-title">{featuredProject.name}</h2>
                <p className="directory-feature-summary">{projectSummary(featuredProject)}</p>
                <div className="directory-feature-meta">
                  <span>
                    <Clock3 size={13} />
                    Updated {formatDate(featuredProject.updatedAt)}
                  </span>
                  <span>{featuredProject.progress}% ready</span>
                  <span>{featuredProject.creativityLevel}</span>
                </div>
                <span className="directory-feature-cta">
                  Open workspace
                  <ArrowRight size={14} />
                </span>
              </div>
            </button>

            <aside className="directory-stats-panel glass-panel">
              <span className="section-kicker">Overview</span>
              <div className="directory-stats-grid">
                <div className="directory-stat">
                  <strong>{sortedProjects.length}</strong>
                  <span>projects tracked</span>
                </div>
                <div className="directory-stat">
                  <strong>{activeProjects}</strong>
                  <span>in progress</span>
                </div>
                <div className="directory-stat">
                  <strong>{completedProjects}</strong>
                  <span>completed</span>
                </div>
                <div className="directory-stat">
                  <strong>{averageProgress}%</strong>
                  <span>average readiness</span>
                </div>
              </div>
              <button
                type="button"
                className="directory-folder-link"
                onClick={() => void handleProjectClick(featuredProject)}
              >
                <FolderOpen size={15} />
                Jump back into the last opened project
              </button>
            </aside>
          </div>
        ) : (
          <div className="directory-empty-state glass-panel">
            <span className="workspace-empty-kicker">Fresh directory</span>
            <h2>No projects yet.</h2>
            <p>Start a project and Morpheus will generate the workspace, directory cover art, and the first review pack from here.</p>
            <button type="button" className="btn-accent" onClick={openCreateProjectModal}>
              <Plus size={15} />
              Create the first project
            </button>
          </div>
        )}

        <section className="directory-recent-section glass-panel">
          <div className="directory-section-header">
            <div>
              <span className="section-kicker">Recent Projects</span>
              <h2 className="directory-section-title">Recent projects stay centered.</h2>
              <p className="directory-section-description">
                Keep the current work visible without scrolling through duplicate layouts.
              </p>
            </div>
            {hasHiddenProjects ? (
              <button
                type="button"
                className="btn-secondary directory-browse-toggle"
                onClick={() => setShowAllProjects((value) => !value)}
              >
                {showAllProjects ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                {showAllProjects ? 'Show fewer' : 'Browse all'}
              </button>
            ) : null}
          </div>

          {visibleProjects.length > 0 ? (
            <div className="directory-project-grid">
              {visibleProjects.map((project, index) => renderProjectCard(project, index + 1))}
            </div>
          ) : featuredProject ? (
            <div className="directory-recent-empty">
              <p>The featured project is currently the only project in the directory.</p>
            </div>
          ) : null}
        </section>
      </section>

      {showNewProjectModal ? (
        <div
          className="modal-overlay"
          data-testid="create-project-modal"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
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

            {actionError ? (
              <div className="inline-alert" data-testid="create-project-error">
                {actionError}
              </div>
            ) : null}

            <div className="modal-form-group">
              <label className="field-label">Project Name *</label>
              <input
                type="text"
                className="input-field"
                data-testid="create-project-name"
                placeholder="e.g., The Lighthouse"
                value={newProjectName}
                onChange={(event) => setNewProjectName(event.target.value)}
                autoFocus
              />
            </div>

            <div className="modal-form-group">
              <label className="field-label">Description</label>
              <textarea
                className="input-field textarea-field"
                data-testid="create-project-description"
                placeholder="Brief description of your project..."
                value={newProjectDescription}
                onChange={(event) => setNewProjectDescription(event.target.value)}
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
                onClick={() => void handleCreateProject()}
                disabled={!newProjectName.trim()}
              >
                Create Project
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <HowItWorksModal isOpen={showHowItWorks} onClose={() => setShowHowItWorks(false)} />
    </div>
  );
}
