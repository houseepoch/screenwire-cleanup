// Services Index - Export all backend integration services

export { API, wsService, WebSocketService, configureBackendBase, setApiBaseUrl, setWsBaseUrl, backendConfig } from './api';
export type { WebSocketMessage } from './api';
export { desktopService } from './desktop';

// Re-export all API namespaces for convenience
export {
  ProjectsAPI,
  CreativeConceptAPI,
  SkeletonAPI,
  EntitiesAPI,
  StoryboardAPI,
  TimelineAPI,
  VideoAPI,
  AgentChatAPI,
  WorkersAPI,
} from './api';
