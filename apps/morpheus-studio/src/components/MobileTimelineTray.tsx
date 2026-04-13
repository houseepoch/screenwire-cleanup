// Mobile Timeline Tray Component
import { useMorpheusStore } from '../store';
import { useLongPress } from '../hooks/useLongPress';
import API from '../services/api';
import { X, Image, FileText, Play } from 'lucide-react';

export function MobileTimelineTray() {
  const { 
    timelineFrames, 
    dialogueBlocks,
    selectedFrameId, 
    setSelectedFrameId,
    mediaView,
    setMediaView,
    isTimelineTrayOpen,
    setIsTimelineTrayOpen,
    injectFocusToChat,
    currentProject,
    hydrateWorkspace,
  } = useMorpheusStore();

  const getDialogueForFrame = (frameSequence: number) => {
    return dialogueBlocks.find(
      d => d.startFrame <= frameSequence && d.endFrame >= frameSequence
    );
  };

  const handleFrameLongPress = (frame: typeof timelineFrames[0]) => {
    injectFocusToChat({
      type: 'frame',
      id: frame.id,
      name: `Frame ${frame.sequence}`,
    });
    setIsTimelineTrayOpen(false);
  };

  const handleFrameClick = (frameId: string) => {
    setSelectedFrameId(frameId);
  };

  const handleApproveTimeline = async () => {
    if (!currentProject) {
      return;
    }
    try {
      const snapshot = await API.workflow.approve(currentProject.id, 'timeline');
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to approve timeline:', error);
    }
  };

  const renderMediaContent = (frame: typeof timelineFrames[0]) => {
    switch (mediaView) {
      case 'prompt':
        return (
          <div style={{ 
            padding: '6px', 
            fontSize: '9px', 
            color: 'var(--text-secondary)',
            overflow: 'hidden',
            lineHeight: 1.2,
          }}>
            {frame.prompt.substring(0, 50)}...
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
            <Play size={16} style={{ color: 'var(--accent)' }} />
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
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center',
            height: '100%',
            background: 'var(--bg-tertiary)'
          }}>
            <Image size={18} style={{ color: 'var(--text-muted)' }} />
          </div>
        );
    }
  };

  if (!isTimelineTrayOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div 
        onClick={() => setIsTimelineTrayOpen(false)}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.5)',
          zIndex: 150,
        }}
      />
      
      {/* Tray */}
      <div style={{
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: '85%',
        maxWidth: '360px',
        background: 'var(--bg-secondary)',
        borderLeft: '1px solid var(--border-subtle)',
        zIndex: 200,
        display: 'flex',
        flexDirection: 'column',
        animation: 'slideInRight 0.3s ease',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px',
          borderBottom: '1px solid var(--border-subtle)',
        }}>
          <span style={{ fontSize: '16px', fontWeight: 600 }}>Timeline</span>
          <button
            onClick={() => setIsTimelineTrayOpen(false)}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-secondary)',
              padding: '8px',
            }}
          >
            <X size={24} />
          </button>
        </div>

        {/* Media View Toggle */}
        <div style={{
          display: 'flex',
          gap: '4px',
          padding: '12px 16px',
          borderBottom: '1px solid var(--border-subtle)',
        }}>
          {(['image', 'prompt', 'video'] as const).map((view) => (
            <button
              key={view}
              onClick={() => setMediaView(view)}
              style={{
                flex: 1,
                padding: '8px',
                background: mediaView === view ? 'var(--accent)' : 'var(--bg-primary)',
                border: 'none',
                borderRadius: '6px',
                color: mediaView === view ? 'var(--bg-primary)' : 'var(--text-secondary)',
                fontSize: '12px',
                textTransform: 'uppercase',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '4px',
              }}
            >
              {view === 'image' && <Image size={14} />}
              {view === 'prompt' && <FileText size={14} />}
              {view === 'video' && <Play size={14} />}
              {view}
            </button>
          ))}
        </div>

        {/* Frames List */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '12px',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {timelineFrames.map((frame) => {
              const dialogue = getDialogueForFrame(frame.sequence);
              const { handlers, isPressing } = useLongPress({
                onLongPress: () => handleFrameLongPress(frame),
                onClick: () => handleFrameClick(frame.id),
                ms: 600,
              });

              return (
                <div
                  key={frame.id}
                  {...handlers}
                  style={{
                    position: 'relative',
                    borderRadius: '8px',
                    overflow: 'hidden',
                    border: selectedFrameId === frame.id 
                      ? '2px solid var(--accent)' 
                      : isPressing 
                        ? '2px solid var(--success)' 
                        : '2px solid transparent',
                    transform: isPressing ? 'scale(0.98)' : 'scale(1)',
                    transition: 'all 0.2s ease',
                  }}
                >
                  {/* Frame Thumbnail */}
                  <div style={{ aspectRatio: '16/9' }}>
                    {renderMediaContent(frame)}
                  </div>
                  
                  {/* Frame Info Overlay */}
                  <div style={{
                    position: 'absolute',
                    bottom: 0,
                    left: 0,
                    right: 0,
                    padding: '8px',
                    background: 'linear-gradient(to top, rgba(0,0,0,0.8), transparent)',
                  }}>
                    <div style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}>
                      <span style={{ fontSize: '12px', fontWeight: 500 }}>
                        Frame #{frame.sequence}
                      </span>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        {frame.duration}s
                      </span>
                    </div>
                    {dialogue && (
                      <p style={{ 
                        fontSize: '10px', 
                        color: 'var(--text-secondary)',
                        marginTop: '4px',
                        fontStyle: 'italic'
                      }}>
                        "{dialogue.text.substring(0, 40)}..."
                      </p>
                    )}
                  </div>

                  {/* Long Press Indicator */}
                  {isPressing && (
                    <div style={{
                      position: 'absolute',
                      inset: 0,
                      background: 'rgba(16, 185, 129, 0.2)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}>
                      <span style={{
                        padding: '6px 12px',
                        background: 'var(--success)',
                        borderRadius: '12px',
                        fontSize: '12px',
                        fontWeight: 500,
                        color: 'var(--bg-primary)',
                      }}>
                        Hold to focus
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer */}
        <div style={{
          padding: '12px 16px',
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {timelineFrames.length} frames
          </span>
          <button 
            className="btn-accent"
            onClick={() => void handleApproveTimeline()}
            style={{ padding: '8px 16px', fontSize: '12px' }}
          >
            Approve
          </button>
        </div>
      </div>

      <style>{`
        @keyframes slideInRight {
          from {
            transform: translateX(100%);
          }
          to {
            transform: translateX(0);
          }
        }
      `}</style>
    </>
  );
}
