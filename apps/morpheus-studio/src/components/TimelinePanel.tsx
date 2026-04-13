import { useState } from 'react';
import { useMorpheusStore } from '../store';
import { 
  ChevronUp, 
  ChevronDown, 
  Scissors, 
  Plus, 
  Trash2, 
  RefreshCw,
  Image,
  FileText,
  Play
} from 'lucide-react';

export function TimelinePanel() {
  const { 
    timelineFrames, 
    dialogueBlocks,
    selectedFrameId, 
    setSelectedFrameId,
    isTimelineExpanded,
    setIsTimelineExpanded,
    mediaView,
    setMediaView,
    regenerateFrame,
    removeFrame,
    expandFrame,
    updateFrameDuration,
    approveTimeline,
    currentProject,
  } = useMorpheusStore();

  const [showFrameMenu, setShowFrameMenu] = useState<string | null>(null);

  const totalDuration = timelineFrames.reduce((acc, f) => acc + f.duration, 0);

  const handleFrameClick = (frameId: string) => {
    setSelectedFrameId(frameId);
  };

  const handleFrameDoubleClick = (frameId: string) => {
    setShowFrameMenu(showFrameMenu === frameId ? null : frameId);
  };

  const renderDialogueForFrame = (frameSequence: number) => {
    const dialogues = dialogueBlocks.filter(
      d => d.startFrame <= frameSequence && d.endFrame >= frameSequence
    );

    if (dialogues.length === 0) return null;

    return (
      <div style={{ 
        position: 'absolute', 
        top: '-32px', 
        left: 0, 
        right: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: '4px'
      }}>
        {dialogues.map((dialogue) => (
          <div 
            key={dialogue.id}
            className="dialogue-block"
            style={{
              fontSize: '10px',
              padding: '4px 8px',
              maxWidth: '100%',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            <span style={{ color: 'var(--accent)', fontWeight: 500 }}>{dialogue.character}:</span>
            <span style={{ marginLeft: '4px' }}>{dialogue.text}</span>
          </div>
        ))}
      </div>
    );
  };

  const renderMediaContent = (frame: typeof timelineFrames[0]) => {
    switch (mediaView) {
      case 'prompt':
        return (
          <div style={{ 
            padding: '8px', 
            fontSize: '10px', 
            color: 'var(--text-secondary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
          }}>
            {frame.prompt}
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
            <Image size={20} style={{ color: 'var(--text-muted)' }} />
          </div>
        );
    }
  };

  if (!isTimelineExpanded) {
    return (
      <div 
        style={{ 
          height: '48px', 
          background: 'var(--bg-secondary)',
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          padding: '0 24px',
          gap: '16px'
        }}
      >
        <button 
          onClick={() => setIsTimelineExpanded(true)}
          style={{ 
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '8px'
          }}
        >
          <ChevronUp size={16} />
          <span style={{ fontSize: '13px' }}>Show Timeline</span>
        </button>
        
        <div style={{ 
          flex: 1, 
          height: '4px', 
          background: 'var(--bg-tertiary)', 
          borderRadius: '2px',
          overflow: 'hidden'
        }}>
          <div style={{ 
            width: `${(selectedFrameId ? 
              timelineFrames.findIndex(f => f.id === selectedFrameId) + 1 : 0) / timelineFrames.length * 100}%`, 
            height: '100%', 
            background: 'var(--accent)', 
            borderRadius: '2px' 
          }} />
        </div>
        
        <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '12px', color: 'var(--text-secondary)' }}>
          {timelineFrames.length} frames · {totalDuration}s
        </span>
      </div>
    );
  }

  return (
    <div className="timeline-panel">
      <div className="timeline-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <button 
            onClick={() => setIsTimelineExpanded(false)}
            style={{ 
              background: 'none',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <ChevronDown size={16} />
          </button>
          <span className="timeline-title">Timeline</span>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {timelineFrames.length} frames · {totalDuration}s total
          </span>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* Media View Toggle */}
          <div style={{ 
            display: 'flex', 
            gap: '4px',
            padding: '4px',
            background: 'var(--bg-primary)',
            borderRadius: '8px'
          }}>
            {(['image', 'prompt', 'video'] as const).map((view) => (
              <button
                key={view}
                onClick={() => setMediaView(view)}
                style={{
                  padding: '6px 10px',
                  borderRadius: '6px',
                  border: 'none',
                  background: mediaView === view ? 'var(--accent)' : 'transparent',
                  color: mediaView === view ? 'var(--bg-primary)' : 'var(--text-secondary)',
                  fontSize: '11px',
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

          <div className="timeline-actions">
            <button 
              className="timeline-action-btn"
              title="Trim"
            >
              <Scissors size={14} />
            </button>
            <button 
              className="timeline-action-btn"
              title="Add Frame"
            >
              <Plus size={14} />
            </button>
          </div>

          {currentProject?.status === 'timeline_review' && (
            <button className="btn-accent" onClick={approveTimeline} style={{ padding: '8px 16px', fontSize: '13px' }}>
              Approve & Generate Video
            </button>
          )}
        </div>
      </div>

      <div className="timeline-content">
        <div className="timeline-rail">
          {/* Playhead */}
          <div className="timeline-playhead" />
          
          {/* Trim Handle Left */}
          <div className="trim-handle" style={{ position: 'absolute', left: 0, top: 0, bottom: 0 }} />

          {/* Frames */}
          {timelineFrames.map((frame) => (
            <div 
              key={frame.id}
              className="timeline-frame"
              style={{ position: 'relative' }}
            >
              {renderDialogueForFrame(frame.sequence)}
              
              <div
                className={`timeline-frame-thumb ${selectedFrameId === frame.id ? 'selected' : ''}`}
                onClick={() => handleFrameClick(frame.id)}
                onDoubleClick={() => handleFrameDoubleClick(frame.id)}
                style={{ position: 'relative' }}
              >
                {renderMediaContent(frame)}
                
                {/* Frame Menu */}
                {showFrameMenu === frame.id && (
                  <div 
                    style={{
                      position: 'absolute',
                      top: '100%',
                      left: '50%',
                      transform: 'translateX(-50%)',
                      marginTop: '8px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      padding: '8px',
                      zIndex: 100,
                      minWidth: '140px',
                      boxShadow: '0 4px 20px rgba(0, 0, 0, 0.3)'
                    }}
                  >
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        regenerateFrame(frame.id);
                        setShowFrameMenu(null);
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '8px 12px',
                        width: '100%',
                        background: 'none',
                        border: 'none',
                        color: 'var(--text-primary)',
                        fontSize: '12px',
                        cursor: 'pointer',
                        borderRadius: '4px',
                        textAlign: 'left'
                      }}
                    >
                      <RefreshCw size={14} />
                      Regenerate
                    </button>
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        expandFrame(frame.id, 'before');
                        setShowFrameMenu(null);
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '8px 12px',
                        width: '100%',
                        background: 'none',
                        border: 'none',
                        color: 'var(--text-primary)',
                        fontSize: '12px',
                        cursor: 'pointer',
                        borderRadius: '4px',
                        textAlign: 'left'
                      }}
                    >
                      <Plus size={14} />
                      Add Before
                    </button>
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        expandFrame(frame.id, 'after');
                        setShowFrameMenu(null);
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '8px 12px',
                        width: '100%',
                        background: 'none',
                        border: 'none',
                        color: 'var(--text-primary)',
                        fontSize: '12px',
                        cursor: 'pointer',
                        borderRadius: '4px',
                        textAlign: 'left'
                      }}
                    >
                      <Plus size={14} />
                      Add After
                    </button>
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        removeFrame(frame.id);
                        setShowFrameMenu(null);
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '8px 12px',
                        width: '100%',
                        background: 'none',
                        border: 'none',
                        color: 'var(--error)',
                        fontSize: '12px',
                        cursor: 'pointer',
                        borderRadius: '4px',
                        textAlign: 'left'
                      }}
                    >
                      <Trash2 size={14} />
                      Remove
                    </button>
                  </div>
                )}
              </div>
              
              <div className="timeline-frame-meta">
                <span className="timeline-frame-number">#{frame.sequence}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    value={frame.duration}
                    onChange={(e) => updateFrameDuration(frame.id, parseInt(e.target.value) || 1)}
                    style={{
                      width: '36px',
                      padding: '2px 4px',
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '4px',
                      fontSize: '10px',
                      color: 'var(--accent)',
                      textAlign: 'center'
                    }}
                    min={1}
                    max={10}
                  />
                  <span className="timeline-frame-duration">s</span>
                </div>
              </div>
            </div>
          ))}

          {/* Trim Handle Right */}
          <div className="trim-handle" style={{ position: 'absolute', right: 0, top: 0, bottom: 0 }} />
        </div>
      </div>
    </div>
  );
}
