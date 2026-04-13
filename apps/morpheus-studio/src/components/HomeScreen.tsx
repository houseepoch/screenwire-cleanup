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

  const checkScroll = () => {
    const container = scrollContainerRef.current;
    if (container) {
      setCanScrollLeft(container.scrollLeft > 0);
      setCanScrollRight(
        container.scrollLeft < container.scrollWidth - container.clientWidth - 10
      );
    }
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

  return (
    <div className="home-screen">
      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-background">
          <img src="/hero-lighthouse.jpg" alt="Morpheus Studio" />
        </div>
        <div className="hero-vignette" />
        <div className="hero-content">
          <h1 className="hero-title">Turn a story into a production.</h1>
          <p className="hero-subtitle">
            Morpheus builds your outline, cast, locations, and storyboards—then 
            generates frames you can trim, rewrite, and ship.
          </p>
          <div className="hero-actions">
            <button 
              className="btn-accent"
              data-testid="home-start-creating"
              onClick={openCreateProjectModal}
            >
              Start creating
            </button>
            <button 
              className="secondary-link"
              onClick={() => setShowHowItWorks(true)}
            >
              See how it works
              <ChevronRight size={14} style={{ marginLeft: '4px', display: 'inline' }} />
            </button>
          </div>
        </div>
      </section>

      {/* Projects Section */}
      <section className="projects-section">
        <div className="section-header">
          <h2 className="section-title">Your Projects</h2>
        </div>
        
        {/* Desktop: Horizontal Scroll with Arrows */}
        <div className="projects-scroll-container desktop-only">
          {displayProjects.length > 0 && (
            <>
              <button 
                className={`scroll-arrow scroll-left ${canScrollLeft ? 'visible' : ''}`}
                onClick={() => scroll('left')}
              >
                <ChevronLeft size={24} />
              </button>
              <button 
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
            {/* New Project Card */}
            <div 
              className="project-card new-project-card"
              data-testid="new-project-card"
              onClick={openCreateProjectModal}
            >
              <div className="project-card-cover">
                <div className="new-project-overlay">
                  <Plus size={40} />
                  <span className="project-card-title">New Project</span>
                </div>
              </div>
            </div>

            {/* Existing Projects */}
            {displayProjects.map((project, index) => (
              <div 
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
                        background: project.status === 'complete' ? 'var(--success)' : 
                                   project.status.includes('generating') ? 'var(--accent)' : 'var(--warning)'
                      }}
                    />
                    <span>{project.progress}%</span>
                  </div>
                  <div className="project-card-info">
                    <span className="project-card-title">{project.name}</span>
                    <span className="project-card-date">{formatDate(project.updatedAt)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Mobile: Grid Layout */}
        <div className="projects-grid mobile-only">
          {/* New Project Card */}
          <div 
            className="project-card new-project-card"
            data-testid="new-project-card-mobile"
            onClick={openCreateProjectModal}
          >
            <div className="project-card-cover">
              <div className="new-project-overlay">
                <Plus size={32} />
                <span className="project-card-title">New Project</span>
              </div>
            </div>
          </div>

          {/* Existing Projects */}
          {displayProjects.map((project, index) => (
            <div 
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
                      background: project.status === 'complete' ? 'var(--success)' : 
                                 project.status.includes('generating') ? 'var(--accent)' : 'var(--warning)'
                    }}
                  />
                  <span>{project.progress}%</span>
                </div>
                <div className="project-card-info">
                  <span className="project-card-title">{project.name}</span>
                  <span className="project-card-date">{formatDate(project.updatedAt)}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* New Project Modal */}
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
          <div className="modal-content">
            <div className="modal-header">
              <h3 className="modal-title">Create New Project</h3>
              <p className="modal-subtitle">
                Start your creative journey. You can always change these details later.
              </p>
            </div>

            {actionError && (
              <div
                data-testid="create-project-error"
                style={{
                  marginBottom: '16px',
                  padding: '10px 12px',
                  borderRadius: '12px',
                  background: 'rgba(220, 38, 38, 0.12)',
                  border: '1px solid rgba(248, 113, 113, 0.35)',
                  color: '#fecaca',
                  fontSize: '13px',
                }}
              >
                {actionError}
              </div>
            )}
            
            <div style={{ marginBottom: '16px' }}>
              <label 
                style={{ 
                  display: 'block', 
                  fontSize: '13px', 
                  fontWeight: 500, 
                  marginBottom: '8px',
                  color: 'var(--text-secondary)'
                }}
              >
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
            
            <div style={{ marginBottom: '24px' }}>
              <label 
                style={{ 
                  display: 'block', 
                  fontSize: '13px', 
                  fontWeight: 500, 
                  marginBottom: '8px',
                  color: 'var(--text-secondary)'
                }}
              >
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
                className="btn-secondary"
                data-testid="create-project-cancel"
                onClick={() => setShowNewProjectModal(false)}
              >
                Cancel
              </button>
              <button 
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

      {/* How It Works Modal */}
      <HowItWorksModal 
        isOpen={showHowItWorks} 
        onClose={() => setShowHowItWorks(false)} 
      />
    </div>
  );
}
