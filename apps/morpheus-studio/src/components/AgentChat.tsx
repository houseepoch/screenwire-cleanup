import { useState, useRef, useEffect } from 'react';
import { useMorpheusStore } from '../store';
import API from '../services/api';
import { Focus } from 'lucide-react';

export function AgentChat() {
  const { 
    messages, 
    addMessage, 
    setMessages,
    currentProject,
    hydrateWorkspace,
    focusedItem, 
    setFocusedItem,
    selectedItems,
  } = useMorpheusStore();

  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [chatMode, setChatMode] = useState<'suggest' | 'apply' | 'regenerate'>('suggest');
  const messagesEndRef = useRef<HTMLDivElement>(null);

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
      console.error('Failed to sync chat focus:', error);
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

    if (!currentProject) {
      return;
    }

    setIsSending(true);
    try {
      await API.chat.sendMessage(currentProject.id, { content, mode: chatMode, focusTarget, focusTargets });
      const [history, snapshot] = await Promise.all([
        API.chat.getHistory(currentProject.id),
        API.workspace.get(currentProject.id),
      ]);
      setMessages(history);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to send Morpheus message:', error);
      addMessage({
        role: 'agent',
        content: 'I could not reach the local Morpheus backend. Check that the project server is running and try again.',
      });
    } finally {
      setIsSending(false);
      if (currentProject) {
        try {
          const [history, snapshot] = await Promise.all([
            API.chat.getHistory(currentProject.id),
            API.workspace.get(currentProject.id),
          ]);
          setMessages(history);
          hydrateWorkspace(snapshot);
        } catch {
          // keep optimistic state if history refresh fails
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
    <div className="agent-panel">
      <div className="agent-header">
        <span className="agent-header-title">Morpheus Agent</span>
      </div>

      <div className="agent-messages">
        {messages.map((message) => (
          <div
            key={message.id}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: message.role === 'user' ? 'flex-end' : 'flex-start',
              gap: '4px',
            }}
          >
            {message.focusTarget && message.role === 'user' && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  padding: '4px 10px',
                  background: 'var(--accent-dim)',
                  borderRadius: '12px',
                  fontSize: '10px',
                  color: 'var(--accent)',
                  marginBottom: '4px',
                }}
              >
                <Focus size={10} />
                Focusing on: {message.focusTarget.name}
              </div>
            )}
            <div
              className={`chat-bubble ${message.role === 'user' ? 'chat-bubble-user' : 'chat-bubble-agent'}`}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                <span>{message.content}</span>
              </div>
            </div>
            <span style={{ fontSize: '9px', color: 'var(--text-muted)' }}>
              {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {focusedItem && (
        <div
          style={{
            padding: '8px 16px',
            background: 'var(--accent-dim)',
            borderTop: '1px solid var(--accent-border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px' }}>
            <Focus size={10} style={{ color: 'var(--accent)' }} />
            <span>Focused on: <strong>{focusedItem.name}</strong></span>
          </div>
          <button
            onClick={() => setFocusedItem(null)}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '4px',
            }}
          >
            ×
          </button>
        </div>
      )}

      <div className="agent-input-container">
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
                cursor: 'pointer',
              }}
            >
              {mode}
            </button>
          ))}
        </div>
        <div className="agent-input-wrapper">
          <textarea
            className="agent-input"
            placeholder="Ask the agent anything..."
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={isSending}
          />
          <button 
            className="agent-send-btn"
            onClick={handleSend}
            disabled={!inputValue.trim() || isSending}
          >
            {isSending ? 'Sending...' : 'Send'}
          </button>
        </div>
        <p style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '8px', textAlign: 'center' }}>
          Tip: shift-click items to focus Morpheus, then choose suggest, apply, or regenerate.
        </p>
      </div>
    </div>
  );
}
