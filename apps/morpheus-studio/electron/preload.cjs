const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('screenwire', {
  listProjects: () => ipcRenderer.invoke('screenwire:list-projects'),
  createProject: (payload) => ipcRenderer.invoke('screenwire:create-project', payload),
  selectProject: (projectId) => ipcRenderer.invoke('screenwire:select-project', projectId),
  returnToProjects: () => ipcRenderer.invoke('screenwire:return-to-projects'),
  getBackendState: () => ipcRenderer.invoke('screenwire:get-backend-state'),
  openProjectFolder: (projectId) => ipcRenderer.invoke('screenwire:open-project-folder', projectId),
  chooseFile: () => ipcRenderer.invoke('screenwire:choose-file'),
});
