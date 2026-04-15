import { useEffect, useMemo, useState } from 'react';
import { useMorpheusStore } from '../store';
import {
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Clock3,
  Plus,
  Sparkles,
} from 'lucide-react';
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

function getStoredProjectId(key: string): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return window.localStorage.getItem(key);
}

export function HomeScreen() {
  const { projects, selectProject } = useMorpheusStore();
  const [showNewProjectModal, setShowNewProjectModal] = useState(false);
  const [authEmail, setAuthEmail] = useState('');
  const [emailIntent, setEmailIntent] = useState<'newsletter' | 'beta'>('beta');
  const [displayProjects, setDisplayProjects] = useState<Project[]>(projects);
  const [actionError, setActionError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
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

  const openCreateProjectModal = (intent: 'newsletter' | 'beta' = 'beta') => {
    setModalError(null);
    setEmailIntent(intent);
    setShowNewProjectModal(true);
  };

  const closeCreateProjectModal = () => {
    setShowNewProjectModal(false);
    setAuthEmail('');
    setModalError(null);
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
    setModalError(null);
    const email = authEmail.trim();
    if (!email) {
      return;
    }
    const isValidEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    if (!isValidEmail) {
      setModalError('Enter a valid email address.');
      return;
    }

    const subject =
      emailIntent === 'newsletter'
        ? 'ScreenWire newsletter signup'
        : 'ScreenWire beta tester application';
    const body =
      emailIntent === 'newsletter'
        ? `Please add ${email} to the ScreenWire newsletter.`
        : `Please use ${email} to log in or register me for the ScreenWire beta.`;

    if (typeof window !== 'undefined') {
      window.location.href = `mailto:info@houseepoch.com?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    }

    closeCreateProjectModal();
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
            <span className="hero-eyebrow">Invite Only:</span>
            <h1 className="home-splash-title">Natural Language Production Suite.</h1>
            <p className="home-splash-subtitle">
              Longform Video Generation with natural language.
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
            <p className="project-directory-invite">Invite Only:</p>
            <h1 className="project-directory-title">Natural Language Production Suite.</h1>
            <p className="project-directory-description">
              Longform Video Generation with natural language.
            </p>
            <p className="project-directory-description">
              To stay up to date with roadmap and developer note releases subscribe to our newsletter and be the first to know.
            </p>
          </div>
          <div className="project-directory-actions">
            <button
              type="button"
              className="btn-secondary project-directory-action"
              data-testid="home-see-how-it-works"
              onClick={() => openCreateProjectModal('newsletter')}
            >
              <Sparkles size={15} />
              Subscribe to newsletter
            </button>
            <button
              type="button"
              className="btn-accent project-directory-action"
              data-testid="home-start-creating"
              onClick={() => openCreateProjectModal('beta')}
            >
              <Plus size={15} />
              Apply to be a Beta Tester
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
              <h2 className="directory-section-title">Apply to be a Beta Tester.</h2>
              <p className="directory-section-description">
                To stay up to date with roadmap and developer note releases subscribe to our newsletter and be the first to know.
              </p>
              <p className="directory-section-description">
                Want to learn more? Contact us.{' '}
                <a className="project-directory-contact-link" href="mailto:info@houseepoch.com">
                  info@houseepoch.com
                </a>
              </p>
              <div className="project-directory-actions">
                <button
                  type="button"
                  className="btn-secondary project-directory-action"
                  onClick={() => openCreateProjectModal('newsletter')}
                >
                  <Sparkles size={15} />
                  Subscribe to newsletter
                </button>
                <button
                  type="button"
                  className="btn-accent project-directory-action"
                  onClick={() => openCreateProjectModal('beta')}
                >
                  <Plus size={15} />
                  Apply to be a Beta Tester
                </button>
              </div>
            </aside>
          </div>
        ) : (
          <div className="directory-empty-state glass-panel">
            <h2>Invite Only.</h2>
            <p>Natural Language Production Suite. Longform Video Generation with natural language.</p>
            <button type="button" className="btn-accent" onClick={() => openCreateProjectModal('beta')}>
              <Plus size={15} />
              Apply to be a Beta Tester
            </button>
          </div>
        )}

        <section className="directory-recent-section glass-panel">
          <div className="directory-section-header">
            <div>
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
              closeCreateProjectModal();
            }
          }}
        >
          <div className="modal-content create-project-modal" data-testid="create-project-dialog">
            <div className="modal-header">
              <h3 className="modal-title">Enter Email to login in or register.</h3>
              <p className="modal-subtitle">
                Want to learn more? Contact us.{' '}
                <a className="project-directory-contact-link" href="mailto:info@houseepoch.com">
                  info@houseepoch.com
                </a>
              </p>
            </div>

            {modalError ? (
              <div className="inline-alert" data-testid="create-project-error">
                {modalError}
              </div>
            ) : null}

            <div className="modal-form-group">
              <label className="field-label">Email</label>
              <input
                type="email"
                className="input-field"
                data-testid="create-project-name"
                placeholder="you@company.com"
                value={authEmail}
                onChange={(event) => setAuthEmail(event.target.value)}
                autoFocus
              />
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                data-testid="create-project-cancel"
                onClick={closeCreateProjectModal}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-accent"
                data-testid="create-project-submit"
                onClick={() => void handleCreateProject()}
                disabled={!authEmail.trim()}
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
