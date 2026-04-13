import { useMorpheusStore } from '../store';
import { 
  Check, 
  MessageSquare,
  Play,
  SkipBack,
  SkipForward,
  Volume2,
  Maximize
} from 'lucide-react';

export function MainStage() {
  const { 
    currentProject, 
    skeletonPlan, 
    approveSkeleton, 
    requestSkeletonEdit,
    storyboardFrames,
    approveStoryboard,
    timelineFrames,
    selectedFrameId,
    dialogueBlocks,
  } = useMorpheusStore();

  const status = currentProject?.status;

  const renderSkeletonApproval = () => (
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <div 
          style={{ 
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 16px',
            background: 'var(--accent-dim)',
            borderRadius: '20px',
            marginBottom: '16px'
          }}
        >
          <span style={{ fontSize: '13px', color: 'var(--accent)' }}>
            Agent has generated a skeleton plan
          </span>
        </div>
        <h2 style={{ fontSize: '28px', fontWeight: 600, marginBottom: '8px' }}>
          Review Your Story Structure
        </h2>
        <p style={{ color: 'var(--text-secondary)' }}>
          This is the blueprint for your production. Approve it or ask for changes.
        </p>
      </div>

      <div className="skeleton-viewer" style={{ marginBottom: '24px' }}>
        {skeletonPlan ? (
          skeletonPlan.scenes.map((scene) => (
            <div key={scene.id} className="skeleton-scene">
              <div className="skeleton-scene-header">
                <span className="skeleton-scene-number">Scene {scene.number}</span>
                <span className="skeleton-scene-location">{scene.location}</span>
                <span style={{ 
                  fontSize: '11px', 
                  color: 'var(--text-muted)',
                  marginLeft: 'auto'
                }}>
                  ~{scene.estimatedFrames} frames
                </span>
              </div>
              <p className="skeleton-scene-description">{scene.description}</p>
              {scene.characters.length > 0 && (
                <div style={{ 
                  display: 'flex', 
                  gap: '8px', 
                  marginTop: '8px',
                  flexWrap: 'wrap'
                }}>
                  {scene.characters.map((char) => (
                    <span 
                      key={char}
                      style={{ 
                        fontSize: '11px',
                        padding: '4px 8px',
                        background: 'var(--bg-tertiary)',
                        borderRadius: '4px',
                        color: 'var(--text-secondary)'
                      }}
                    >
                      {char}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))
        ) : (
          <div style={{ textAlign: 'center', padding: '40px' }}>
            <div className="loading-dots" style={{ justifyContent: 'center', marginBottom: '16px' }}>
              <span /><span /><span />
            </div>
            <p style={{ color: 'var(--text-secondary)' }}>
              Generating skeleton plan...
            </p>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
        <button 
          className="btn-secondary"
          onClick={() => {
            const feedback = prompt('What would you like to change?');
            if (feedback) requestSkeletonEdit(feedback);
          }}
        >
          <MessageSquare size={16} style={{ marginRight: '8px' }} />
          Request Changes
        </button>
        <button className="btn-accent" onClick={approveSkeleton}>
          <Check size={16} style={{ marginRight: '8px' }} />
          Approve & Continue
        </button>
      </div>
    </div>
  );

  const renderStoryboardApproval = () => (
    <div style={{ maxWidth: '1000px', margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <div 
          style={{ 
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 16px',
            background: 'var(--accent-dim)',
            borderRadius: '20px',
            marginBottom: '16px'
          }}
        >
          <span style={{ fontSize: '13px', color: 'var(--accent)' }}>
            Storyboard is ready for review
          </span>
        </div>
        <h2 style={{ fontSize: '28px', fontWeight: 600, marginBottom: '8px' }}>
          Review Your Storyboard
        </h2>
        <p style={{ color: 'var(--text-secondary)' }}>
          These are the visual shots that will guide frame generation.
        </p>
      </div>

      <div className="storyboard-grid" style={{ marginBottom: '24px' }}>
        {storyboardFrames.map((frame) => (
          <div key={frame.id} className="storyboard-panel">
            <img src={frame.imageUrl} alt={frame.description} />
            <div className="storyboard-panel-caption">
              <span style={{ fontWeight: 500 }}>{frame.shotType}:</span>
              <span style={{ color: 'var(--text-secondary)', marginLeft: '4px' }}>
                {frame.description}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
        <button className="btn-secondary">
          <MessageSquare size={16} style={{ marginRight: '8px' }} />
          Request Changes
        </button>
        <button className="btn-accent" onClick={approveStoryboard}>
          <Check size={16} style={{ marginRight: '8px' }} />
          Approve & Generate Frames
        </button>
      </div>
    </div>
  );

  const renderTimelineReview = () => {
    const selectedFrame = timelineFrames.find(f => f.id === selectedFrameId);
    
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        {/* Main Preview */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div 
            style={{ 
              flex: 1,
              background: 'var(--bg-primary)',
              borderRadius: '12px',
              overflow: 'hidden',
              position: 'relative'
            }}
          >
            {selectedFrame?.imageUrl ? (
              <img 
                src={selectedFrame.imageUrl} 
                alt="Frame preview"
                style={{ width: '100%', height: '100%', objectFit: 'contain' }}
              />
            ) : (
              <div style={{ 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                height: '100%',
                flexDirection: 'column',
                gap: '16px'
              }}>
                <Play size={48} style={{ color: 'var(--text-muted)' }} />
                <p style={{ color: 'var(--text-secondary)' }}>
                  Select a frame to preview
                </p>
              </div>
            )}

            {/* Dialogue Overlay */}
            {selectedFrame && dialogueBlocks
              .filter(d => d.startFrame <= selectedFrame.sequence && d.endFrame >= selectedFrame.sequence)
              .map(dialogue => (
                <div 
                  key={dialogue.id}
                  style={{
                    position: 'absolute',
                    bottom: '80px',
                    left: '50%',
                    transform: 'translateX(-50%)',
                    background: 'rgba(11, 13, 16, 0.9)',
                    padding: '12px 20px',
                    borderRadius: '8px',
                    borderLeft: '3px solid var(--accent)',
                    maxWidth: '60%'
                  }}
                >
                  <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                    {dialogue.character}
                  </p>
                  <p style={{ fontFamily: 'IBM Plex Serif, serif', fontSize: '15px' }}>
                    "{dialogue.text}"
                  </p>
                </div>
              ))}

            {/* Video Controls */}
            <div style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              padding: '16px',
              background: 'linear-gradient(transparent, rgba(11, 13, 16, 0.9))',
              display: 'flex',
              alignItems: 'center',
              gap: '16px'
            }}>
              <button style={{ 
                width: '36px', 
                height: '36px', 
                borderRadius: '50%', 
                background: 'var(--accent)',
                border: 'none',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--bg-primary)',
                cursor: 'pointer'
              }}>
                <Play size={16} fill="currentColor" />
              </button>
              <button style={{ color: 'var(--text-secondary)', background: 'none', border: 'none', cursor: 'pointer' }}>
                <SkipBack size={18} />
              </button>
              <button style={{ color: 'var(--text-secondary)', background: 'none', border: 'none', cursor: 'pointer' }}>
                <SkipForward size={18} />
              </button>
              <div style={{ flex: 1, height: '4px', background: 'var(--bg-tertiary)', borderRadius: '2px', cursor: 'pointer' }}>
                <div style={{ width: '30%', height: '100%', background: 'var(--accent)', borderRadius: '2px' }} />
              </div>
              <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '12px', color: 'var(--text-secondary)' }}>
                00:12 / 00:45
              </span>
              <button style={{ color: 'var(--text-secondary)', background: 'none', border: 'none', cursor: 'pointer' }}>
                <Volume2 size={18} />
              </button>
              <button style={{ color: 'var(--text-secondary)', background: 'none', border: 'none', cursor: 'pointer' }}>
                <Maximize size={18} />
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const renderVideoPreview = () => (
    <div style={{ maxWidth: '900px', margin: '0 auto', height: '100%' }}>
      <div className="video-player" style={{ height: 'calc(100% - 80px)' }}>
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          height: '100%',
          flexDirection: 'column',
          gap: '24px'
        }}>
          <div 
            style={{ 
              width: '80px', 
              height: '80px', 
              borderRadius: '50%', 
              background: 'var(--accent-dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <Play size={32} style={{ color: 'var(--accent)', marginLeft: '4px' }} />
          </div>
          <div style={{ textAlign: 'center' }}>
            <h3 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '8px' }}>
              Your Video is Ready
            </h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
              Preview, trim, and export your final production
            </p>
          </div>
        </div>
      </div>
      
      <div style={{ display: 'flex', gap: '12px', marginTop: '24px', justifyContent: 'center' }}>
        <button className="btn-secondary">
          <MessageSquare size={16} style={{ marginRight: '8px' }} />
          Make Changes
        </button>
        <button className="btn-accent">
          <Check size={16} style={{ marginRight: '8px' }} />
          Export Video
        </button>
      </div>
    </div>
  );

  const renderContent = () => {
    switch (status) {
      case 'skeleton_review':
        return renderSkeletonApproval();
      case 'reference_review':
        return renderStoryboardApproval();
      case 'timeline_review':
        return renderTimelineReview();
      case 'complete':
      case 'generating_video':
        return renderVideoPreview();
      default:
        return (
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center',
            height: '100%',
            flexDirection: 'column',
            gap: '16px'
          }}>
            <div className="loading-dots">
              <span /><span /><span />
            </div>
            <p style={{ color: 'var(--text-secondary)' }}>
              Working on your production...
            </p>
          </div>
        );
    }
  };

  return (
    <div className="main-stage">
      <div className="stage-content">
        {renderContent()}
      </div>
    </div>
  );
}
