import { useEffect, useRef } from 'react';
import { useMorpheusStore } from './store';
import { Navigation } from './components/Navigation';
import { HomeScreen } from './components/HomeScreen';
import { OnboardingWizard } from './components/OnboardingWizard';
import { ProjectWorkspace } from './components/ProjectWorkspace';
import { GrainOverlay } from './components/GrainOverlay';
import { VideoExportWizard } from './components/VideoExportWizard';
import { desktopService } from './services';
import './App.css';

function App() {
  const { currentView, setCurrentView, currentProject, selectProject, setProjects } = useMorpheusStore();
  const reconciledDesktopRef = useRef(false);
  const showNavigation = currentView !== 'onboarding';

  useEffect(() => {
    if (!desktopService.isAvailable() || reconciledDesktopRef.current) {
      return;
    }
    let cancelled = false;
    reconciledDesktopRef.current = true;

    async function reconcileDesktopState() {
      try {
        const [backendState, desktopProjects] = await Promise.all([
          desktopService.getBackendState().catch(() => null),
          desktopService.listProjects().catch(() => []),
        ]);
        if (cancelled) {
          return;
        }

        setProjects(desktopProjects);

        const activeProjectId = backendState?.currentProjectId || null;
        const activeProject = activeProjectId
          ? desktopProjects.find((project) => project.id === activeProjectId) || null
          : null;
        const persistedStillValid = currentProject
          ? desktopProjects.some((project) => project.id === currentProject.id)
          : false;

        if (activeProject) {
          selectProject(activeProject);
          return;
        }

        if (currentProject && !persistedStillValid) {
          selectProject(null);
          setCurrentView('home');
          return;
        }

        if (!currentProject && currentView !== 'home') {
          setCurrentView('home');
        }
      } catch {
        // leave current UI state alone if desktop reconciliation fails
      }
    }

    void reconcileDesktopState();
    return () => {
      cancelled = true;
    };
  }, [currentProject, currentView, selectProject, setCurrentView, setProjects]);

  // Handle project selection navigation
  useEffect(() => {
    if (currentProject) {
      if (currentView === 'project') {
        return;
      }
      if (currentProject.status === 'onboarding' || currentProject.status === 'draft') {
        setCurrentView('onboarding');
      } else {
        setCurrentView('project');
      }
    }
  }, [currentProject, currentView, setCurrentView]);

  return (
    <div className="app">
      <GrainOverlay />
      {showNavigation ? <Navigation /> : null}
      
      <main className={`main-content ${showNavigation ? '' : 'without-navigation'}`.trim()}>
        {currentView === 'home' && <HomeScreen />}
        {currentView === 'onboarding' && <OnboardingWizard />}
        {currentView === 'project' && <ProjectWorkspace />}
      </main>
      <VideoExportWizard />
    </div>
  );
}

export default App;
