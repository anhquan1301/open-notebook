'use client'

import { memo, useCallback, useState, useRef, useEffect, useId, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Bot, User, Send, Loader2, Square, FileText, Lightbulb, StickyNote, Clock, Brain, ChevronDown, ChevronRight, Sparkles, Maximize2, Minimize2 } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import {
  SourceChatMessage,
  SourceChatContextIndicator,
  BaseChatSession
} from '@/lib/types/api'
import { ModelSelector } from './ModelSelector'
import { ContextIndicator } from '@/components/common/ContextIndicator'
import { SessionManager } from '@/components/sources/SessionManager'
import { MessageActions } from '@/components/sources/MessageActions'
import { convertReferencesToCompactMarkdown, createCompactReferenceLinkComponent } from '@/lib/utils/source-references'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'

interface NotebookContextStats {
  sourcesInsights: number
  sourcesFull: number
  notesCount: number
  tokenCount?: number
  charCount?: number
}

interface ChatPanelProps {
  messages: SourceChatMessage[]
  isStreaming: boolean
  contextIndicators: SourceChatContextIndicator | null
  onSendMessage: (message: string, modelOverride?: string) => void
  onCancel?: () => void
  modelOverride?: string
  onModelChange?: (model?: string) => void
  // Session management props
  sessions?: BaseChatSession[]
  currentSessionId?: string | null
  onCreateSession?: (title: string) => void
  onSelectSession?: (sessionId: string) => void
  onDeleteSession?: (sessionId: string) => void
  onUpdateSession?: (sessionId: string, title: string) => void
  loadingSessions?: boolean
  // Generic props for reusability
  title?: string
  contextType?: 'source' | 'notebook'
  // Notebook context stats (for notebook chat)
  notebookContextStats?: NotebookContextStats
  // Notebook ID for saving notes
  notebookId?: string
  // Maximize chat column toggle
  isMaximized?: boolean
  onToggleMaximize?: () => void
}

export function ChatPanel({
  messages,
  isStreaming,
  contextIndicators,
  onSendMessage,
  onCancel,
  modelOverride,
  onModelChange,
  sessions = [],
  currentSessionId,
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  onUpdateSession,
  loadingSessions = false,
  title,
  contextType = 'source',
  notebookContextStats,
  notebookId,
  isMaximized = false,
  onToggleMaximize,
}: ChatPanelProps) {
  const { t } = useTranslation()
  const [sessionManagerOpen, setSessionManagerOpen] = useState(false)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const { openModal } = useModalManager()

  // Stable reference-click handler so memoized messages don't re-render on
  // composer keystrokes (which no longer re-render this component at all, since
  // the input state lives in the ChatComposer child).
  const handleReferenceClick = useCallback((type: string, id: string, highlight?: string) => {
    const modalType = type === 'source_insight' ? 'insight' : type as 'source' | 'note' | 'insight'

    try {
      openModal(modalType, id, highlight)
      // Note: The modal system uses URL parameters and doesn't throw errors for missing items.
      // The modal component itself will handle displaying "not found" states.
      // This try-catch is here for future enhancements or unexpected errors.
    } catch {
      toast.error(t('common.noResults'))
    }
  }, [openModal, t])

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <>
    <Card className="flex flex-col h-full flex-1 overflow-hidden">
      <CardHeader className="py-2.5 px-4 flex-shrink-0 border-b border-border/40">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.13em] text-muted-foreground">
            <span aria-hidden className="h-3.5 w-[3px] rounded-full bg-teal" />
            {title || (contextType === 'source' ? t('chat.chatWith', { name: t('navigation.sources') }) : t('chat.chatWith', { name: t('common.notebook') }))}
          </CardTitle>
          <div className="flex items-center gap-1">
            {onSelectSession && onCreateSession && onDeleteSession && (
              <Dialog open={sessionManagerOpen} onOpenChange={setSessionManagerOpen}>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
                  onClick={() => setSessionManagerOpen(true)}
                  disabled={loadingSessions}
                >
                  <Clock className="h-3.5 w-3.5" />
                  <span className="text-xs">{t('chat.sessions')}</span>
                </Button>
                <DialogContent className="sm:max-w-[420px] p-0 overflow-hidden">
                  <DialogTitle className="sr-only">{t('chat.sessionsTitle')}</DialogTitle>
                  <SessionManager
                    sessions={sessions}
                    currentSessionId={currentSessionId ?? null}
                    onCreateSession={(title) => onCreateSession?.(title)}
                    onSelectSession={(sessionId) => {
                      onSelectSession(sessionId)
                      setSessionManagerOpen(false)
                    }}
                    onUpdateSession={(sessionId, title) => onUpdateSession?.(sessionId, title)}
                    onDeleteSession={(sessionId) => onDeleteSession?.(sessionId)}
                    loadingSessions={loadingSessions}
                  />
                </DialogContent>
              </Dialog>
            )}
            {onToggleMaximize && (
              <div>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-accent"
                      onClick={onToggleMaximize}
                      aria-label={isMaximized ? "Restore view" : "Maximize chat"}
                    >
                      {isMaximized ? (
                        <Minimize2 className="h-3.5 w-3.5" />
                      ) : (
                        <Maximize2 className="h-3.5 w-3.5" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>{isMaximized ? "Restore" : "Maximize"}</p>
                  </TooltipContent>
                </Tooltip>
              </div>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col min-h-0 p-0 overflow-hidden">
        <ScrollArea className="flex-1 min-h-0 w-full" viewportClassName="!overflow-x-hidden" ref={scrollAreaRef}>
          <div className="space-y-3 py-3 px-3.5 min-w-0 w-full">
            {messages.length === 0 ? (
              <div className="text-center text-muted-foreground py-8">
                <Bot className="h-8 w-8 mx-auto mb-3 opacity-40 text-teal" />
                <p className="text-xs font-medium">
                  {t('chat.startConversation', { type: contextType === 'source' ? t('navigation.sources') : t('common.notebook') })}
                </p>
                <p className="text-[11px] mt-1 text-muted-foreground/80">{t('chat.askQuestions')}</p>
              </div>
            ) : (
              messages.map((message, idx) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  notebookId={notebookId}
                  onReferenceClick={handleReferenceClick}
                  onSendMessage={onSendMessage}
                  isStreaming={isStreaming && idx === messages.length - 1}
                  isLast={idx === messages.length - 1}
                />
              ))
            )}
            {isStreaming && (!messages.length || messages[messages.length - 1].type !== 'ai') && (
              <div className="flex gap-2.5 justify-start">
                <div className="flex-shrink-0">
                  <div className="h-7 w-7 rounded-full bg-teal-tint flex items-center justify-center">
                    <Bot className="h-3.5 w-3.5 text-teal" />
                  </div>
                </div>
                <div className="rounded-lg px-3 py-2 bg-card border">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-teal" />
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>

        {/* Context Indicators */}
        {contextIndicators && (
          <div className="border-t border-border/40 px-3.5 py-1.5 bg-muted/20">
            <div className="flex flex-wrap gap-2 text-xs">
              {contextIndicators.sources?.length > 0 && (
                <Badge variant="outline" className="gap-1">
                  <FileText className="h-3 w-3" />
                  {contextIndicators.sources.length} {t('navigation.sources')}
                </Badge>
              )}
              {contextIndicators.insights?.length > 0 && (
                <Badge variant="outline" className="gap-1">
                  <Lightbulb className="h-3 w-3" />
                  {contextIndicators.insights.length} {contextIndicators.insights.length === 1 ? t('common.insight') : t('common.insights')}
                </Badge>
              )}
              {contextIndicators.notes?.length > 0 && (
                <Badge variant="outline" className="gap-1">
                  <StickyNote className="h-3 w-3" />
                  {contextIndicators.notes.length} {contextIndicators.notes.length === 1 ? t('common.note') : t('common.notes')}
                </Badge>
              )}
            </div>
          </div>
        )}

        {/* Notebook Context Indicator */}
        {notebookContextStats && (
          <ContextIndicator
            sourcesInsights={notebookContextStats.sourcesInsights}
            sourcesFull={notebookContextStats.sourcesFull}
            notesCount={notebookContextStats.notesCount}
            tokenCount={notebookContextStats.tokenCount}
            charCount={notebookContextStats.charCount}
          />
        )}

        {/* Input Area */}
        <ChatComposer
          onSendMessage={onSendMessage}
          onCancel={onCancel}
          isStreaming={isStreaming}
          modelOverride={modelOverride}
          onModelChange={onModelChange}
        />
      </CardContent>
    </Card>

    </>
  )
}

// Composer owns the input state so keystrokes (including IME composition) only
// re-render this small component instead of the whole message history.
interface ChatComposerProps {
  onSendMessage: (message: string, modelOverride?: string) => void
  onCancel?: () => void
  isStreaming: boolean
  modelOverride?: string
  onModelChange?: (model?: string) => void
}

function ChatComposer({
  onSendMessage,
  onCancel,
  isStreaming,
  modelOverride,
  onModelChange
}: ChatComposerProps) {
  const { t } = useTranslation()
  const chatInputId = useId()
  const [input, setInput] = useState('')

  const handleSend = () => {
    if (input.trim() && !isStreaming) {
      onSendMessage(input.trim(), modelOverride)
      setInput('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Detect platform for correct modifier key
    const isMac = typeof navigator !== 'undefined' && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0
    const isModifierPressed = isMac ? e.metaKey : e.ctrlKey

    if (e.key === 'Enter' && isModifierPressed) {
      e.preventDefault()
      handleSend()
    }
  }

  // Detect platform for placeholder text
  const isMac = typeof navigator !== 'undefined' && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0
  const keyHint = isMac ? '⌘+Enter' : 'Ctrl+Enter'

  return (
    <div className="flex-shrink-0 p-3 space-y-2 border-t border-border/40">
      {/* Model selector */}
      {onModelChange && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">{t('chat.model')}</span>
          <ModelSelector
            currentModel={modelOverride}
            onModelChange={onModelChange}
            disabled={isStreaming}
          />
        </div>
      )}

      <div className="flex gap-2 items-end min-w-0">
        <Textarea
          id={chatInputId}
          name="chat-message"
          autoComplete="off"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`${t('chat.sendPlaceholder')} (${t('chat.pressToSend', { key: keyHint })})`}
          disabled={isStreaming}
          className="flex-1 min-h-[36px] max-h-[96px] text-xs py-2 px-3 resize-none min-w-0"
          rows={1}
        />
        {isStreaming ? (
          <Button
            type="button"
            onClick={onCancel}
            size="icon"
            variant="destructive"
            className="h-[36px] w-[36px] flex-shrink-0"
            title={t('common.cancel')}
            aria-label={t('common.cancel')}
          >
            <Square className="h-3.5 w-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            type="button"
            onClick={handleSend}
            disabled={!input.trim()}
            size="icon"
            className="h-[36px] w-[36px] flex-shrink-0"
          >
            <Send className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  )
}

// Single chat message row. Memoized so historical messages don't re-render when
// unrelated state (e.g. the composer input) changes.
interface ChatMessageProps {
  message: SourceChatMessage
  notebookId?: string
  onReferenceClick: (type: string, id: string, highlight?: string) => void
  onSendMessage?: (message: string) => void
  isStreaming?: boolean
  isLast?: boolean
}

function extractFollowUps(content: string): { cleanedContent: string; followUps: string[] } {
  if (!content) return { cleanedContent: '', followUps: [] }

  const match = content.match(/<followups>([\s\S]*?)(?:<\/followups>|$)/i)
  if (!match) {
    const partialMatch = content.match(/<follow(?:ups)?[^>]*$/i)
    if (partialMatch) {
      return {
        cleanedContent: content.substring(0, partialMatch.index).trimEnd(),
        followUps: []
      }
    }
    return { cleanedContent: content, followUps: [] }
  }

  const rawBlock = match[1].trim()
  const followUps: string[] = []
  rawBlock.split('\n').forEach((line) => {
    const cleaned = line.replace(/^[-*•\d.]+\s*/, '').trim()
    if (cleaned && !cleaned.toLowerCase().includes('</followups>')) {
      followUps.push(cleaned)
    }
  })

  const cleanedContent = content.replace(/<followups>[\s\S]*?(?:<\/followups>|$)/gi, '').trimEnd()
  return { cleanedContent, followUps }
}

function ThinkingBlock({ thinking, isThinkingNow }: { thinking: string; isThinkingNow?: boolean }) {
  const [userToggled, setUserToggled] = useState<boolean | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Default to open while model is actively thinking; user can manually override by clicking
  const isOpen = userToggled !== null ? userToggled : Boolean(isThinkingNow)

  // Auto-scroll thinking container down as new thoughts stream in
  useEffect(() => {
    if (isThinkingNow && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [thinking, isThinkingNow])

  const preview = thinking.slice(0, 80).replace(/\s+/g, ' ') + (thinking.length > 80 ? '...' : '')

  return (
    <div className="mb-2.5 rounded-md border border-border/70 bg-muted/40 overflow-hidden text-xs">
      <button
        type="button"
        onClick={() => setUserToggled(!isOpen)}
        className="w-full flex items-center justify-between px-2.5 py-1.5 text-left font-medium text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
      >
        <span className="flex items-center gap-1.5 text-[11px]">
          <Brain className={`h-3.5 w-3.5 text-teal ${isThinkingNow ? 'animate-pulse' : ''}`} />
          <span>{isThinkingNow ? 'Đang suy nghĩ...' : `Quá trình suy nghĩ (${thinking.length.toLocaleString()} ký tự)`}</span>
        </span>
        <span className="text-muted-foreground">
          {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
      </button>
      {isOpen ? (
        <div
          ref={scrollRef}
          className="px-2.5 py-2 border-t border-border/50 font-mono text-[11.5px] text-muted-foreground whitespace-pre-wrap leading-relaxed max-h-[250px] overflow-y-auto bg-background/50 select-text"
        >
          {thinking}
          {isThinkingNow && (
            <span className="inline-block w-1.5 h-3.5 ml-1 bg-teal animate-pulse align-middle" />
          )}
        </div>
      ) : (
        <div className="px-2.5 py-0.5 pb-1 text-[11px] text-muted-foreground/70 truncate italic select-none">
          {preview}
        </div>
      )}
    </div>
  )
}

const ChatMessage = memo(function ChatMessage({
  message,
  notebookId,
  onReferenceClick,
  onSendMessage,
  isStreaming = false,
  isLast = false
}: ChatMessageProps) {
  const { t } = useTranslation()
  const isThinkingNow = isStreaming && !message.content
  const isContentStreaming = isStreaming && Boolean(message.content)

  const { cleanedContent, followUps: parsedFollowUps } = useMemo(() => {
    return extractFollowUps(message.content)
  }, [message.content])

  const effectiveFollowUps = (message.follow_ups && message.follow_ups.length > 0)
    ? message.follow_ups
    : parsedFollowUps

  return (
    <div
      className={`flex gap-2.5 w-full min-w-0 ${
        message.type === 'human' ? 'justify-end' : 'justify-start'
      }`}
    >
      {message.type === 'ai' && (
        <div className="flex-shrink-0">
          <div className="h-7 w-7 rounded-full bg-teal-tint flex items-center justify-center">
            <Bot className="h-3.5 w-3.5 text-teal" />
          </div>
        </div>
      )}
      <div
        className={`flex flex-col gap-1.5 min-w-0 ${
          message.type === 'human' ? 'max-w-[85%]' : 'flex-1 w-full'
        }`}
      >
        <div
          className={`rounded-lg px-3.5 py-2 border min-w-0 overflow-hidden ${
            message.type === 'human'
              ? 'bg-muted'
              : 'bg-card'
          }`}
        >
          {message.type === 'ai' ? (
            <>
              {message.thinking && (
                <ThinkingBlock thinking={message.thinking} isThinkingNow={isThinkingNow} />
              )}
              <AIMessageContent
                content={cleanedContent}
                onReferenceClick={onReferenceClick}
                isStreaming={isContentStreaming}
              />
            </>
          ) : (
            <p className="text-xs leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{message.content}</p>
          )}
        </div>
        {message.type === 'ai' && (
          <MessageActions
            content={cleanedContent}
            notebookId={notebookId}
          />
        )}
        {message.type === 'ai' && !isStreaming && effectiveFollowUps.length > 0 && isLast && (
          <div className="mt-1 flex flex-col gap-1.5 animate-in fade-in slide-in-from-top-1 duration-200">
            <span className="text-[11px] font-medium text-muted-foreground flex items-center gap-1.5">
              <Sparkles className="h-3 w-3 text-teal" />
              {t('chat.suggestedFollowUps') || 'Gợi ý câu hỏi tiếp theo:'}
            </span>
            <div className="flex flex-wrap gap-1.5">
              {effectiveFollowUps.map((question, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => onSendMessage?.(question)}
                  className="text-xs text-left px-2.5 py-1 rounded-full border border-teal/30 bg-teal-tint/40 hover:bg-teal-tint hover:border-teal text-foreground transition-all duration-150 flex items-center gap-1.5 group cursor-pointer shadow-2xs"
                >
                  <span>{question}</span>
                  <span className="text-teal opacity-60 group-hover:opacity-100 group-hover:translate-x-0.5 transition-transform text-xs font-bold">
                    →
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      {message.type === 'human' && (
        <div className="flex-shrink-0">
          <div className="h-7 w-7 rounded-full bg-muted border flex items-center justify-center">
            <User className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
        </div>
      )}
    </div>
  )
})

// Helper component to render AI messages with clickable references
function AIMessageContent({
  content,
  onReferenceClick,
  isStreaming = false
}: {
  content: string
  onReferenceClick: (type: string, id: string, highlight?: string) => void
  isStreaming?: boolean
}) {
  const { t } = useTranslation()
  // Convert references to compact markdown with numbered citations
  const markdownWithCompactRefs = convertReferencesToCompactMarkdown(content, t('common.references'))

  // Create custom link component for compact references
  const LinkComponent = createCompactReferenceLinkComponent(onReferenceClick)

  return (
    <div className="relative min-w-0 w-full overflow-hidden">
      <MarkdownRenderer components={{
        a: LinkComponent
      }}>
        {markdownWithCompactRefs}
      </MarkdownRenderer>
      {isStreaming && (
        <span className="inline-block w-1.5 h-3 ml-1 bg-primary animate-pulse align-middle" />
      )}
    </div>
  )
}
