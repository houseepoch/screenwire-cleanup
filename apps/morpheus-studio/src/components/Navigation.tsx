import { useMorpheusStore } from '../store';
import { ChevronLeft, Plus } from 'lucide-react';

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
      {currentProject ? (
        // Inside project - show back button
        <button 
          onClick={handleBackToProjects}
          style={{ 
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'none',
            border: 'none',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            fontSize: '14px',
            fontWeight: 500,
          }}
        >
          <ChevronLeft size={18} />
          Back to projects
        </button>
      ) : (
        // Home screen - show logo
        <div className="nav-logo">
          Morpheus
        </div>
      )}
      
      {/* Only show start button on home screen when no project */}
      {!currentProject && (
        <div className="nav-actions">
          <button
            className="btn-accent"
            aria-label="Create new project"
            title="Create new project"
            style={{
              width: '40px',
              height: '40px',
              padding: 0,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: '999px',
            }}
            onClick={handleStartCreating}
          >
            <Plus size={18} />
          </button>
        </div>
      )}
    </nav>
  );
}
