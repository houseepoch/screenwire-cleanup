import { useState, useRef } from 'react';
import { useMorpheusStore } from '../store';
import API from '../services/api';
import { 
  Plus, 
  Trash2, 
  RefreshCw,
  Image,
  FileText,
  Play,
  ChevronUp,
  ChevronDown,
  Minus,
  GripVertical,
  Pencil
} from 'lucide-react';
import type { TimelineFrame, DialogueBlock } from '../types';

interface DragState {
  frameId: string | null;
  isDragging: boolean;
  overDialogueId: string | null;
}

export function TimelineBar() {
  const { 
    timelineFrames, 
    dialogueBlocks,
    selectedFrameId, 
    setSelectedFrameId,
    mediaView,
    setMediaView,
    regenerateFrame,
    removeFrame,
    expandFrame,
    updateFrameDuration,
    approveTimeline,
    currentProject,
    hydrateWorkspace,
    isTimelineExpanded,
    setIsTimelineExpanded,
    linkFrameToDialogue,
    unlinkFrameFromDialogue,
    toggleItemSelection,
    isItemSelected,
    injectFocusToChat,
  } = useMorpheusStore();

  const [showFrameMenu, setShowFrameMenu] = useState<string | null>(null);
  const [editingDuration, setEditingDuration] = useState<string | null>(null);
  const [editingDialogueId, setEditingDialogueId] = useState<string | null>(null);
  const [dialogueDraftText, setDialogueDraftText] = useState('');
  const [dialogueDraftCharacter, setDialogueDraftCharacter] = useState('');
  const [dialogueDraftDuration, setDialogueDraftDuration] = useState('0');
  const [dragState, setDragState] = useState<DragState>({
    frameId: null,
    isDragging: false,
    overDialogueId: null,
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadTargetFrame, setUploadTargetFrame] = useState<string | null>(null);

  const totalDuration = timelineFrames.reduce((acc, f) => acc + f.duration, 0);
  const isDropToUnlinkActive = dragState.isDragging && !dragState.overDialogueId;

  // Group frames by dialogue
  const getFramesForDialogue = (dialogueId: string): TimelineFrame[] => {
    return timelineFrames.filter((f) => f.dialogueId === dialogueId);
  };

  const getUnlinkedFrames = (): TimelineFrame[] => {
    return timelineFrames.filter((f) => !f.dialogueId);
  };

  const handleFrameClick = (frameId: string, e: React.MouseEvent) => {
    const isShiftClick = e.shiftKey;
    
    if (isShiftClick) {
      const frame = timelineFrames.find((f) => f.id === frameId);
      if (frame) {
        const focused = { type: 'frame' as const, id: frameId, name: `Frame ${frame.sequence}` };
        toggleItemSelection(focused, true);
        injectFocusToChat(focused);
      }
    } else {
      setSelectedFrameId(frameId);
    }
  };

  const handleDurationChange = async (frameId: string, newDuration: number) => {
    const clampedDuration = Math.max(1, Math.min(15, newDuration));
    updateFrameDuration(frameId, clampedDuration);
    if (!currentProject) {
      return;
    }
    try {
      await API.timeline.updateFrame(currentProject.id, frameId, { duration: clampedDuration });
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to persist frame duration:', error);
    }
  };

  const handleDragStart = (frameId: string) => {
    setDragState({ frameId, isDragging: true, overDialogueId: null });
  };

  const handleDragEnd = () => {
    const { frameId, overDialogueId } = dragState;
    if (frameId) {
      if (overDialogueId) {
        linkFrameToDialogue(frameId, overDialogueId);
        if (currentProject) {
          void API.timeline
            .updateFrame(currentProject.id, frameId, { dialogueId: overDialogueId })
            .then(() => API.workspace.get(currentProject.id))
            .then((snapshot) => hydrateWorkspace(snapshot))
            .catch((error) => {
              console.error('Failed to persist dialogue link:', error);
              void API.workspace.get(currentProject.id).then((snapshot) => hydrateWorkspace(snapshot)).catch(() => undefined);
            });
        }
      } else {
        const frame = timelineFrames.find((item) => item.id === frameId);
        if (frame?.dialogueId && currentProject) {
          unlinkFrameFromDialogue(frameId);
          void API.timeline
            .updateFrame(currentProject.id, frameId, { dialogueId: '' })
            .then(() => API.workspace.get(currentProject.id))
            .then((snapshot) => hydrateWorkspace(snapshot))
            .catch((error) => {
              console.error('Failed to persist dialogue unlink:', error);
              void API.workspace.get(currentProject.id).then((snapshot) => hydrateWorkspace(snapshot)).catch(() => undefined);
            });
        }
      }
    }
    setDragState({ frameId: null, isDragging: false, overDialogueId: null });
  };

  const handleDialogueDragOver = (dialogueId: string) => {
    if (dragState.isDragging) {
      setDragState((prev) => ({ ...prev, overDialogueId: dialogueId }));
    }
  };

  const handleDialogueDragLeave = () => {
    setDragState((prev) => ({ ...prev, overDialogueId: null }));
  };

  const handleStartDialogueEdit = (dialogue: DialogueBlock) => {
    setEditingDialogueId(dialogue.id);
    setDialogueDraftText(dialogue.text);
    setDialogueDraftCharacter(dialogue.character);
    setDialogueDraftDuration(String(dialogue.duration));
  };

  const handleSaveDialogue = async (dialogueId: string) => {
    if (!currentProject) {
      setEditingDialogueId(null);
      return;
    }
    try {
      await API.timeline.updateDialogue(currentProject.id, dialogueId, {
        text: dialogueDraftText,
        character: dialogueDraftCharacter,
        duration: parseFloat(dialogueDraftDuration) || 1,
      });
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
      setEditingDialogueId(null);
    } catch (error) {
      console.error('Failed to update dialogue block:', error);
    }
  };

  const handleFileUpload = async (frameId: string, file: File) => {
    if (!currentProject) {
      return;
    }
    try {
      await API.timeline.uploadFrame(currentProject.id, frameId, file);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to upload frame image:', error);
    }
  };

  const handleRegenerateFrame = async (frameId: string) => {
    if (!currentProject) {
      regenerateFrame(frameId);
      return;
    }
    try {
      await API.timeline.regenerateFrame(currentProject.id, frameId);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to regenerate frame:', error);
    }
  };

  const handleEditFrame = async (frameId: string) => {
    if (!currentProject) {
      return;
    }
    const prompt = window.prompt('Describe the image edit you want Morpheus to apply to this frame.');
    if (!prompt?.trim()) {
      return;
    }
    try {
      await API.timeline.editFrame(currentProject.id, frameId, prompt.trim());
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to edit frame:', error);
    }
  };

  const handleRemoveFrame = async (frameId: string) => {
    if (!currentProject) {
      removeFrame(frameId);
      return;
    }
    try {
      await API.timeline.removeFrame(currentProject.id, frameId);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to remove frame:', error);
    }
  };

  const handleExpandFrame = async (frameId: string, direction: 'before' | 'after') => {
    if (!currentProject) {
      expandFrame(frameId, direction);
      return;
    }
    try {
      await API.timeline.expandFrame(currentProject.id, frameId, direction);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to expand frame:', error);
    }
  };

  const handleApproveTimeline = async () => {
    if (!currentProject) {
      approveTimeline();
      return;
    }
    try {
      const snapshot = await API.workflow.approve(currentProject.id, 'timeline');
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to approve timeline:', error);
    }
  };

  const renderMediaContent = (frame: TimelineFrame, size: 'small' | 'large' = 'small') => {
    const isLarge = size === 'large';
    
    switch (mediaView) {
      case 'prompt':
        return (
          <div style={{ 
            padding: isLarge ? '8px 12px' : '4px 6px', 
            fontSize: isLarge ? '10px' : '8px', 
            color: 'var(--text-secondary)',
            overflow: 'hidden',
            lineHeight: 1.3,
            height: '100%',
            display: 'flex',
            alignItems: 'center',
          }}>
            <span style={{ 
              display: '-webkit-box',
              WebkitLineClamp: isLarge ? 5 : 3,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}>
              {frame.prompt}
            </span>
          </div>
        );
      case 'video':
        return (
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center',
            height: '100%',
            background: 'var(--bg-tertiary)'
          }}>
            <Play size={isLarge ? 20 : 14} style={{ color: 'var(--accent)' }} />
          </div>
        );
      case 'image':
      default:
        return frame.imageUrl ? (
          <img 
            src={frame.imageUrl} 
            alt={`Frame ${frame.sequence}`}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        ) : (
          <div 
            style={{ 
              display: 'flex', 
              flexDirection: 'column',
              alignItems: 'center', 
              justifyContent: 'center',
              height: '100%',
              background: 'var(--bg-tertiary)',
              gap: '4px',
              cursor: 'pointer',
            }}
            onClick={(e) => {
              e.stopPropagation();
              setUploadTargetFrame(frame.id);
              fileInputRef.current?.click();
            }}
          >
            <Image size={isLarge ? 24 : 16} style={{ color: 'var(--text-muted)' }} />
            <span style={{ fontSize: isLarge ? '10px' : '8px', color: 'var(--text-muted)' }}>Click to upload</span>
          </div>
        );
    }
  };

  const renderFrame = (frame: TimelineFrame, isInDialogue: boolean = false) => {
    const isSelected = selectedFrameId === frame.id;
    const isMultiSelected = isItemSelected(frame.id);
    const isEditing = editingDuration === frame.id;
    
    return (
      <div 
        key={frame.id}
        className="timeline-frame"
        style={{
          position: 'relative',
          flexShrink: 0,
        }}
        draggable
        onDragStart={() => handleDragStart(frame.id)}
        onDragEnd={handleDragEnd}
      >
        {/* Duration Badge */}
        <div
          style={{
            position: 'absolute',
            top: '-18px',
            left: '50%',
            transform: 'translateX(-50%)',
            background: isEditing ? 'var(--accent)' : 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '4px',
            padding: '2px 6px',
            fontSize: '10px',
            fontFamily: 'IBM Plex Mono, monospace',
            color: isEditing ? 'var(--bg-primary)' : 'var(--text-primary)',
            zIndex: 10,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
          onClick={(e) => {
            e.stopPropagation();
            setEditingDuration(frame.id);
          }}
        >
          {isEditing ? (
            <input
              type="number"
              min={1}
              max={15}
              step={0.5}
              defaultValue={frame.duration}
              autoFocus
              style={{
                width: '40px',
                background: 'transparent',
                border: 'none',
                color: 'var(--bg-primary)',
                fontSize: '10px',
                fontFamily: 'IBM Plex Mono, monospace',
                outline: 'none',
              }}
              onBlur={(e) => {
                void handleDurationChange(frame.id, parseFloat(e.target.value) || 1);
                setEditingDuration(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  void handleDurationChange(frame.id, parseFloat((e.target as HTMLInputElement).value) || 1);
                  setEditingDuration(null);
                }
              }}
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <>
              <span>{frame.duration}s</span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1px' }}>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDurationChange(frame.id, frame.duration + 0.5);
                  }}
                  style={{
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    color: 'inherit',
                    fontSize: '8px',
                    lineHeight: 1,
                  }}
                >
                  <ChevronUp size={8} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDurationChange(frame.id, frame.duration - 0.5);
                  }}
                  style={{
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    color: 'inherit',
                    fontSize: '8px',
                    lineHeight: 1,
                  }}
                >
                  <ChevronDown size={8} />
                </button>
              </div>
            </>
          )}
        </div>
        
        <div
          className={`timeline-frame-thumb ${isSelected ? 'selected' : ''}`}
          onClick={(e) => handleFrameClick(frame.id, e)}
          onDoubleClick={() => setShowFrameMenu(showFrameMenu === frame.id ? null : frame.id)}
          style={{
            position: 'relative',
            width: isTimelineExpanded ? '140px' : '100px',
            height: isTimelineExpanded ? '84px' : '56px',
            border: isMultiSelected ? '2px solid var(--success)' : undefined,
            boxShadow: isMultiSelected ? '0 0 8px var(--success)' : undefined,
          }}
        >
          {/* Drag Handle */}
          <div
            style={{
              position: 'absolute',
              left: '2px',
              top: '50%',
              transform: 'translateY(-50%)',
              cursor: 'grab',
              zIndex: 5,
              opacity: 0.7,
            }}
          >
            <GripVertical size={12} style={{ color: 'var(--text-muted)' }} />
          </div>

          {renderMediaContent(frame, isTimelineExpanded ? 'large' : 'small')}
          
          {/* Frame Menu */}
          {showFrameMenu === frame.id && (
            <div 
              style={{
                position: 'absolute',
                bottom: '100%',
                left: '50%',
                transform: 'translateX(-50%)',
                marginBottom: '8px',
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '6px',
                padding: '4px',
                zIndex: 100,
                minWidth: '90px',
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)'
              }}
            >
              <button 
                onClick={(e) => {
                  e.stopPropagation();
                  void handleRegenerateFrame(frame.id);
                  setShowFrameMenu(null);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 8px',
                  width: '100%',
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-primary)',
                  fontSize: '10px',
                  cursor: 'pointer',
                  borderRadius: '3px',
                  textAlign: 'left'
                }}
              >
                <RefreshCw size={10} />
                Regen
              </button>
              <button 
                onClick={(e) => {
                  e.stopPropagation();
                  void handleEditFrame(frame.id);
                  setShowFrameMenu(null);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 8px',
                  width: '100%',
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-primary)',
                  fontSize: '10px',
                  cursor: 'pointer',
                  borderRadius: '3px',
                  textAlign: 'left'
                }}
              >
                <Pencil size={10} />
                Edit
              </button>
              <button 
                onClick={(e) => {
                  e.stopPropagation();
                  void handleExpandFrame(frame.id, 'before');
                  setShowFrameMenu(null);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 8px',
                  width: '100%',
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-primary)',
                  fontSize: '10px',
                  cursor: 'pointer',
                  borderRadius: '3px',
                  textAlign: 'left'
                }}
              >
                <Plus size={10} />
                Add
              </button>
              {isInDialogue && (
                <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    unlinkFrameFromDialogue(frame.id);
                    if (currentProject) {
                      void API.timeline
                        .updateFrame(currentProject.id, frame.id, { dialogueId: '' })
                        .then(() => API.workspace.get(currentProject.id))
                        .then((snapshot) => hydrateWorkspace(snapshot))
                        .catch((error) => {
                          console.error('Failed to persist dialogue unlink:', error);
                          void API.workspace.get(currentProject.id).then((snapshot) => hydrateWorkspace(snapshot)).catch(() => undefined);
                        });
                    }
                    setShowFrameMenu(null);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 8px',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    color: 'var(--accent)',
                    fontSize: '10px',
                    cursor: 'pointer',
                    borderRadius: '3px',
                    textAlign: 'left'
                  }}
                >
                  <Minus size={10} />
                  Unlink
                </button>
              )}
              <button 
                onClick={(e) => {
                  e.stopPropagation();
                  void handleRemoveFrame(frame.id);
                  setShowFrameMenu(null);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 8px',
                  width: '100%',
                  background: 'none',
                  border: 'none',
                  color: 'var(--error)',
                  fontSize: '10px',
                  cursor: 'pointer',
                  borderRadius: '3px',
                  textAlign: 'left'
                }}
              >
                <Trash2 size={10} />
                Del
              </button>
            </div>
          )}
        </div>
        
        <span className="timeline-frame-number">#{frame.sequence}</span>
      </div>
    );
  };

  const renderDialogueBlock = (dialogue: DialogueBlock) => {
    const linkedFrames = getFramesForDialogue(dialogue.id);
    const isDragOver = dragState.overDialogueId === dialogue.id;

    return (
      <div
        key={dialogue.id}
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          padding: '8px 12px',
          background: isDragOver ? 'var(--accent-dim)' : 'var(--bg-secondary)',
          border: `1px solid ${isDragOver ? 'var(--accent)' : 'var(--border-subtle)'}`,
          borderRadius: '8px',
          minWidth: isTimelineExpanded ? '200px' : '160px',
          transition: 'all 0.2s ease',
        }}
        onDragOver={(e) => {
          e.preventDefault();
          handleDialogueDragOver(dialogue.id);
        }}
        onDragLeave={handleDialogueDragLeave}
      >
        {/* Dialogue Text */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          {editingDialogueId === dialogue.id ? (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 64px auto auto', gap: '6px', alignItems: 'center' }}>
                <input
                  value={dialogueDraftCharacter}
                  onChange={(e) => setDialogueDraftCharacter(e.target.value)}
                  style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '6px 8px', color: 'var(--text-primary)', fontSize: '10px' }}
                />
                <input
                  value={dialogueDraftDuration}
                  onChange={(e) => setDialogueDraftDuration(e.target.value)}
                  type="number"
                  min={1}
                  max={15}
                  step={0.5}
                  style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '6px 8px', color: 'var(--text-primary)', fontSize: '10px' }}
                />
                <button onClick={() => void handleSaveDialogue(dialogue.id)} className="btn-accent" style={{ padding: '4px 8px', fontSize: '10px' }}>Save</button>
                <button onClick={() => setEditingDialogueId(null)} className="btn-secondary" style={{ padding: '4px 8px', fontSize: '10px' }}>Cancel</button>
              </div>
              <textarea
                value={dialogueDraftText}
                onChange={(e) => setDialogueDraftText(e.target.value)}
                rows={2}
                style={{
                  background: 'var(--bg-primary)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '6px',
                  padding: '8px',
                  color: 'var(--text-primary)',
                  fontSize: '11px',
                  resize: 'vertical',
                }}
              />
            </>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ fontSize: '10px', color: 'var(--accent)', fontWeight: 500 }}>
                  {dialogue.character}
                </span>
                <span style={{ fontSize: '9px', color: 'var(--text-muted)' }}>
                  {dialogue.duration}s
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleStartDialogueEdit(dialogue);
                  }}
                  style={{ marginLeft: 'auto', background: 'none', border: 'none', padding: 0, color: 'var(--text-muted)', cursor: 'pointer' }}
                >
                  <Pencil size={10} />
                </button>
              </div>
              <p 
                style={{ 
                  fontSize: '11px', 
                  color: 'var(--text-primary)',
                  lineHeight: 1.4,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  margin: 0,
                }}
              >
                "{dialogue.text}"
              </p>
            </>
          )}
        </div>

        {/* Linked Frames */}
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
          {linkedFrames.map((frame) => renderFrame(frame, true))}
          {linkedFrames.length === 0 && (
            <div
              style={{
                padding: '8px 12px',
                background: 'var(--bg-tertiary)',
                border: '1px dashed var(--border-subtle)',
                borderRadius: '6px',
                fontSize: '10px',
                color: 'var(--text-muted)',
                textAlign: 'center',
              }}
            >
              Drag frame here
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div 
      className="timeline-bar"
      style={{
        height: isTimelineExpanded ? 'calc(33.333vh - 64px)' : '140px',
        minHeight: isTimelineExpanded ? '200px' : '140px',
        transition: 'height 0.3s ease',
      }}
    >
      {/* Hidden file input for uploads */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && uploadTargetFrame) {
            void handleFileUpload(uploadTargetFrame, file);
          }
          setUploadTargetFrame(null);
        }}
      />

      {/* Header - Centered */}
      <div 
        className="timeline-bar-header"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
        }}
      >
        {/* Left section */}
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: '12px',
          position: 'absolute',
          left: '16px',
        }}>
          <button 
            onClick={() => setIsTimelineExpanded(!isTimelineExpanded)}
            style={{ 
              background: 'none',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              padding: '4px',
            }}
          >
            {isTimelineExpanded ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
          <span className="timeline-bar-title">Timeline</span>
          <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
            {timelineFrames.length} frames · {totalDuration.toFixed(1)}s
          </span>
        </div>
        
        {/* Center section - Media View Toggle */}
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: '2px',
          padding: '2px',
          background: 'var(--bg-primary)',
          borderRadius: '4px'
        }}>
          {(['image', 'prompt', 'video'] as const).map((view) => (
            <button
              key={view}
              onClick={() => setMediaView(view)}
              style={{
                padding: '4px 8px',
                borderRadius: '3px',
                border: 'none',
                background: mediaView === view ? 'var(--accent)' : 'transparent',
                color: mediaView === view ? 'var(--bg-primary)' : 'var(--text-secondary)',
                fontSize: '10px',
                textTransform: 'uppercase',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px'
              }}
            >
              {view === 'image' && <Image size={12} />}
              {view === 'prompt' && <FileText size={12} />}
              {view === 'video' && <Play size={12} />}
              {view}
            </button>
          ))}
        </div>

        {/* Right section */}
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: '8px',
          position: 'absolute',
          right: '16px',
        }}>
          {currentProject?.status === 'timeline_review' && (
            <button 
              className="btn-accent" 
              onClick={() => void handleApproveTimeline()}
              style={{ padding: '4px 12px', fontSize: '11px' }}
            >
              Approve
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div 
        className="timeline-bar-content"
        style={{
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
        }}
      >
        {/* Dialogue Blocks */}
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'nowrap' }}>
          {dialogueBlocks.map((dialogue) => renderDialogueBlock(dialogue))}
        </div>

        {/* Unlinked Frames Section */}
        {getUnlinkedFrames().length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <span style={{ fontSize: '10px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Unlinked Frames
            </span>
            <div
              style={{
                display: 'flex',
                gap: '12px',
                minHeight: '92px',
                padding: '8px',
                borderRadius: '8px',
                border: `1px dashed ${isDropToUnlinkActive ? 'var(--accent)' : 'var(--border-subtle)'}`,
                background: isDropToUnlinkActive ? 'var(--accent-dim)' : 'transparent',
                transition: 'all 0.2s ease',
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragState((prev) => ({ ...prev, overDialogueId: null }));
              }}
            >
              {getUnlinkedFrames().map((frame) => renderFrame(frame, false))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
