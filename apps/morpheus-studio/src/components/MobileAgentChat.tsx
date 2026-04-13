import { useState, useRef, useEffect } from 'react';
import { useMorpheusStore } from '../store';
import API from '../services/api';
import { Film, PanelLeft } from 'lucide-react';

export function MobileAgentChat() {
  const { 
    messages, 
    addMessage, 
    setMessages,
    currentProject,
    hydrateWorkspace,
    focusedItem, 
    setFocusedItem,
    selectedItems,
    setMobileView,
    setIsTimelineTrayOpen,
    highlightedItem,
    setHighlightedItem,
  } = useMorpheusStore();

  const [inputValue, setInputValue] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [chatMode, setChatMode] = useState<'suggest' | 'apply' | 'regenerate'>('suggest');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Check for injected draft from long press
  useEffect(() => {
    const draft = (window as any).__chatDraft;
    if (draft) {
      setInputValue(draft);
      (window as any).__chatDraft = null;
      inputRef.current?.focus();
    }
  }, [highlightedItem]);

  // Clear highlight after 3 seconds
  useEffect(() => {
    if (highlightedItem) {
      const timer = setTimeout(() => {
        setHighlightedItem(null);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [highlightedItem, setHighlightedItem]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (!currentProject) {
      return;
    }
    void API.chat.setFocus(currentProject.id, focusedItem).catch((error) => {
      console.error('Failed to sync mobile chat focus:', error);
    });
  }, [currentProject?.id, focusedItem]);

  const handleSend = async () => {
    if (!inputValue.trim()) return;

    const content = inputValue.trim();
    const focusTarget = focusedItem
      ? {
          type: focusedItem.type as 'storyboard' | 'entity' | 'scene' | 'frame',
          id: focusedItem.id,
          name: focusedItem.name,
        }
      : undefined;
    const focusTargets = selectedItems.length
      ? selectedItems.map((item) => ({
          type: item.type,
          id: item.id,
          name: item.name,
        }))
      : undefined;

    addMessage({
      role: 'user',
      content,
      mode: chatMode,
      focusTarget,
    });

    setInputValue('');
    setFocusedItem(null);
    setIsTyping(true);

    if (!currentProject) {
      setIsTyping(false);
      return;
    }

    try {
      await API.chat.sendMessage(currentProject.id, { content, mode: chatMode, focusTarget, focusTargets });
      const [history, snapshot] = await Promise.all([
        API.chat.getHistory(currentProject.id),
        API.workspace.get(currentProject.id),
      ]);
      setMessages(history);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to send mobile Morpheus message:', error);
      addMessage({
        role: 'agent',
        content: 'I could not reach the local Morpheus backend. Check that the project server is running and try again.',
      });
    } finally {
      setIsTyping(false);
      if (currentProject) {
        try {
          const [history, snapshot] = await Promise.all([
            API.chat.getHistory(currentProject.id),
            API.workspace.get(currentProject.id),
          ]);
          setMessages(history);
          hydrateWorkspace(snapshot);
        } catch {
          // keep optimistic messages if refresh fails
        }
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: 'var(--bg-primary)',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--bg-secondary)',
      }}>
        <button 
          onClick={() => setMobileView('details')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'none',
            border: 'none',
            color: 'var(--text-primary)',
            fontSize: '14px',
            padding: '8px',
          }}
        >
          <PanelLeft size={20} />
          <span>Details</span>
        </button>
        
        <span style={{ fontSize: '14px', fontWeight: 500 }}>Morpheus Agent</span>
        
        <button 
          onClick={() => setIsTimelineTrayOpen(true)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'none',
            border: 'none',
            color: 'var(--text-primary)',
            fontSize: '14px',
            padding: '8px',
          }}
        >
          <Film size={20} />
          <span>Timeline</span>
        </button>
      </div>

      {/* Highlight Banner */}
      {highlightedItem && (
        <div style={{
          padding: '10px 16px',
          background: 'rgba(16, 185, 129, 0.15)',
          borderBottom: '1px solid var(--success)',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          <div style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: 'var(--success)',
            animation: 'pulse 1s infinite',
          }} />
          <span style={{ fontSize: '13px', color: 'var(--success)' }}>
            Focusing on: <strong>{highlightedItem.name}</strong>
          </span>
        </div>
      )}

      {/* Messages */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
      }}>
        {messages.length === 0 && (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            gap: '16px',
            color: 'var(--text-muted)',
          }}>
            <p style={{ fontSize: '14px', textAlign: 'center' }}>
              How can I help you with your project today?
            </p>
          </div>
        )}
        
        {messages.map((message) => (
          <div
            key={message.id}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: message.role === 'user' ? 'flex-end' : 'flex-start',
              gap: '4px',
              animation: 'fadeIn 0.3s ease',
            }}
          >
            {message.focusTarget && message.role === 'user' && (
              <div style={{
                padding: '4px 10px',
                background: 'var(--accent-dim)',
                borderRadius: '12px',
                fontSize: '10px',
                color: 'var(--accent)',
              }}>
                Focusing on: {message.focusTarget.name}
              </div>
            )}
            <div style={{
              maxWidth: '85%',
              padding: '12px 16px',
              borderRadius: message.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
              background: message.role === 'user' ? 'var(--accent)' : 'var(--bg-secondary)',
              color: message.role === 'user' ? 'var(--bg-primary)' : 'var(--text-primary)',
              fontSize: '15px',
              lineHeight: 1.5,
            }}>
              {message.content}
            </div>
            <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
              {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        
        {isTyping && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '12px 16px',
            background: 'var(--bg-secondary)',
            borderRadius: '18px',
            alignSelf: 'flex-start',
          }}>
            <div style={{ display: 'flex', gap: '4px' }}>
              <span style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--text-muted)',
                animation: 'bounce 1.4s infinite ease-in-out both',
              }} />
              <span style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--text-muted)',
                animation: 'bounce 1.4s infinite ease-in-out both',
                animationDelay: '0.16s',
              }} />
              <span style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--text-muted)',
                animation: 'bounce 1.4s infinite ease-in-out both',
                animationDelay: '0.32s',
              }} />
            </div>
          </div>
        )}
        
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--border-subtle)',
        background: 'var(--bg-secondary)',
      }}>
        <div style={{ display: 'flex', gap: '6px', marginBottom: '10px', justifyContent: 'center' }}>
          {(['suggest', 'apply', 'regenerate'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setChatMode(mode)}
              style={{
                padding: '4px 10px',
                borderRadius: '999px',
                border: `1px solid ${chatMode === mode ? 'var(--accent)' : 'var(--border-subtle)'}`,
                background: chatMode === mode ? 'var(--accent-dim)' : 'transparent',
                color: chatMode === mode ? 'var(--accent)' : 'var(--text-secondary)',
                fontSize: '10px',
                textTransform: 'uppercase',
              }}
            >
              {mode}
            </button>
          ))}
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: '10px',
          background: 'var(--bg-primary)',
          borderRadius: '24px',
          padding: '8px 16px',
          border: '1px solid var(--border-subtle)',
        }}>
          <textarea
            ref={inputRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message..."
            rows={1}
            style={{
              flex: 1,
              background: 'transparent',
              border: 'none',
              color: 'var(--text-primary)',
              fontSize: '15px',
              resize: 'none',
              outline: 'none',
              padding: '8px 0',
              minHeight: '24px',
              maxHeight: '120px',
            }}
          />
          <button
            onClick={handleSend}
            disabled={!inputValue.trim()}
            style={{
              padding: '8px 16px',
              background: inputValue.trim() ? 'var(--accent)' : 'var(--bg-tertiary)',
              border: 'none',
              borderRadius: '16px',
              color: inputValue.trim() ? 'var(--bg-primary)' : 'var(--text-muted)',
              fontSize: '14px',
              fontWeight: 500,
              cursor: inputValue.trim() ? 'pointer' : 'not-allowed',
            }}
          >
            Send
          </button>
        </div>
        <p style={{ 
          fontSize: '11px', 
          color: 'var(--text-muted)', 
          textAlign: 'center',
          marginTop: '8px' 
        }}>
          Long press to focus, then choose suggest, apply, or regenerate.
        </p>
      </div>
    </div>
  );
}
