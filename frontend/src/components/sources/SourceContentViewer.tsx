'use client'

import React, { useState, useMemo } from 'react'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  FileSpreadsheet,
  FileText,
  Eye,
  Code,
  Download,
  Copy,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  AlertTriangle,
  Image as ImageIcon,
  Sparkles,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { sourcesApi } from '@/lib/api/sources'

const PREVIEW_SAFE_LIMIT = 20000
const TABLE_ROW_SAFE_LIMIT = 150

interface SourceContentViewerProps {
  content: string
  filePath?: string
  sourceId?: string
  onDownload?: () => void
  isDownloading?: boolean
  highlightText?: string
  onClearHighlight?: () => void
}

interface SheetInfo {
  id: string
  name: string
  content: string
  charCount: number
}

interface HighlightResult {
  text: string
  matched: boolean
  matchedSnippet?: string
  before?: string
  after?: string
  score: number
  matchType?: 'exact' | 'phrase' | 'keywords'
}

function extractKeywords(query: string): string[] {
  const stopWords = new Set([
    'theo', 'tài', 'liệu', 'trong', 'được', 'này', 'dưới', 'đây', 'thông', 'tin',
    'xác', 'định', 'cho', 'biết', 'của', 'các', 'những', 'một', 'vào', 'ngày',
    'tháng', 'năm', 'có', 'thể', 'là', 'và', 'với', 'tại', 'về', 'giữa', 'phương',
    'thức', 'từ', 'trên', 'ra', 'sao', 'thế', 'nào', 'gì', 'khi', 'đã', 'bị', 'bởi',
    'như', 'vậy', 'do', 'đó', 'thì', 'chỉ', 'sau', 'trước', 'liên', 'quan', 'sau',
    'the', 'from', 'this', 'that', 'with', 'about', 'according', 'document', 'source',
    'between', 'what', 'which', 'where', 'when', 'how', 'does', 'will', 'would', 'could',
    'also', 'been', 'have', 'were', 'their', 'there'
  ])

  // Extract unicode words, alphanumeric identifiers, and CJK character sequences
  const rawTokens = query.match(/[\p{L}\p{N}_-]{2,}/gu) || []
  const unique = new Set<string>()
  const keywords: string[] = []

  for (const token of rawTokens) {
    const lower = token.toLowerCase()
    if (stopWords.has(lower)) continue
    // Skip small pure numbers unless length >= 4 (like year 2026)
    if (/^\d+$/.test(token) && token.length < 4) continue
    if (!unique.has(lower)) {
      unique.add(lower)
      keywords.push(token)
    }
  }
  return keywords
}

function hasWordBoundary(text: string, word: string): boolean {
  try {
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const regex = new RegExp(`(^|[\\s\\p{P}])${escaped}([\\s\\p{P}]|$)`, 'iu')
    return regex.test(text)
  } catch {
    return text.toLowerCase().includes(word.toLowerCase())
  }
}

function applyCitationHighlight(fullText: string, query?: string): HighlightResult {
  if (!fullText || !query) {
    return { text: fullText, matched: false, score: 0 }
  }

  const trimmedQuery = query.trim()
  if (trimmedQuery.length < 3) {
    return { text: fullText, matched: false, score: 0 }
  }

  const buildResult = (
    matchIndex: number,
    matchLength: number,
    score: number,
    matchType: 'exact' | 'phrase' | 'keywords'
  ): HighlightResult => {
    let matchedSnippet = fullText.substring(matchIndex, matchIndex + matchLength)
    let before = fullText.substring(0, matchIndex)
    const after = fullText.substring(matchIndex + matchLength)

    // Ensure we don't break markdown headers (e.g. ### Header)
    const lastNewline = before.lastIndexOf('\n')
    const currentLineStart = lastNewline === -1 ? 0 : lastNewline + 1
    const linePrefix = before.substring(currentLineStart)
    const headerMatch = linePrefix.match(/^(\s*#{1,6}\s+)/)
    if (headerMatch && matchedSnippet.startsWith(headerMatch[1])) {
      before = before + headerMatch[1]
      matchedSnippet = matchedSnippet.substring(headerMatch[1].length)
    }

    const highlightedText = `${before}[${matchedSnippet}](#citation-target)${after}`
    return {
      text: highlightedText,
      matched: true,
      matchedSnippet,
      before,
      after,
      score,
      matchType,
    }
  }

  // 0. Prioritize embedded quotes, e.g. "Yêu cầu phải đạt 100%."
  const embeddedQuoteMatch = trimmedQuery.match(/["“'«]([^"”'»]{3,200})["”'»]/)
  if (embeddedQuoteMatch) {
    const quotedText = embeddedQuoteMatch[1].trim()
    const qExact = fullText.indexOf(quotedText)
    if (qExact !== -1) {
      return buildResult(qExact, quotedText.length, 100, 'exact')
    }
    const qCase = fullText.toLowerCase().indexOf(quotedText.toLowerCase())
    if (qCase !== -1) {
      return buildResult(qCase, quotedText.length, 95, 'exact')
    }
  }

  // Clean query of surrounding pipes, quotes, and markdown formatting
  const cleanQuery = trimmedQuery
    .replace(/^[|"\s“‘`*#]+|[|"\s”’`*#]+$/g, '')
    .trim()

  // 1. Direct exact match (trimmed or cleaned)
  let matchIndex = fullText.indexOf(trimmedQuery)
  if (matchIndex !== -1) {
    return buildResult(matchIndex, trimmedQuery.length, 100, 'exact')
  }
  if (cleanQuery && cleanQuery !== trimmedQuery) {
    matchIndex = fullText.indexOf(cleanQuery)
    if (matchIndex !== -1) {
      return buildResult(matchIndex, cleanQuery.length, 98, 'exact')
    }
  }

  // 2. Case-insensitive exact match
  matchIndex = fullText.toLowerCase().indexOf(trimmedQuery.toLowerCase())
  if (matchIndex !== -1) {
    return buildResult(matchIndex, trimmedQuery.length, 90, 'exact')
  }
  if (cleanQuery && cleanQuery !== trimmedQuery) {
    matchIndex = fullText.toLowerCase().indexOf(cleanQuery.toLowerCase())
    if (matchIndex !== -1) {
      return buildResult(matchIndex, cleanQuery.length, 88, 'exact')
    }
  }

  // 3. Substring phrases (e.g. first 80, middle 50, last 50 chars of cleanQuery)
  const targetForSub = cleanQuery || trimmedQuery
  const subQueries = [
    targetForSub.slice(0, 80),
    targetForSub.slice(0, 50),
    targetForSub.slice(0, 30),
    targetForSub.slice(targetForSub.length - 60),
    targetForSub.slice(targetForSub.length - 40),
  ].filter(s => s.trim().length >= 10)

  for (const sub of subQueries) {
    const idx = fullText.toLowerCase().indexOf(sub.toLowerCase().trim())
    if (idx !== -1) {
      return buildResult(idx, sub.trim().length, 70, 'phrase')
    }
  }

  // 4. Consecutive word sequence (4 words)
  const words = targetForSub.split(/\s+/).filter(w => w.length > 2)
  if (words.length >= 4) {
    for (let i = 0; i <= words.length - 4; i++) {
      const phrase = words.slice(i, i + 4).join(' ')
      const idx = fullText.toLowerCase().indexOf(phrase.toLowerCase())
      if (idx !== -1) {
        return buildResult(idx, phrase.length, 50, 'phrase')
      }
    }
  }

  // 5. Smart Keyword & Entity Cluster Matching
  const keywords = extractKeywords(targetForSub)
  if (keywords.length > 0) {
    const lines = fullText.split('\n')
    let bestLineIndex = -1
    let bestScore = 0
    let bestMatchedTerm: string | null = null

    const isHighSignal = (kw: string) =>
      /^[A-Za-z0-9_-]{3,}$/.test(kw) || /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/.test(kw)

    let charOffset = 0
    const lineOffsets: number[] = []
    for (let i = 0; i < lines.length; i++) {
      lineOffsets.push(charOffset)
      charOffset += lines[i].length + 1 // +1 for newline
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim()
      if (!line || line.startsWith('| ---') || line === '---') continue

      let score = 0
      let primaryTerm = ''

      for (const kw of keywords) {
        if (hasWordBoundary(line, kw)) {
          const weight = isHighSignal(kw) ? 3 : 1
          score += weight
          if (!primaryTerm || (isHighSignal(kw) && !isHighSignal(primaryTerm))) {
            primaryTerm = kw
          }
        }
      }

      if (score > bestScore) {
        bestScore = score
        bestLineIndex = i
        bestMatchedTerm = primaryTerm
      }
    }

    // Require at least 2 points (or 1 high-signal term like BF026 / HULF / SMALINK)
    if (bestLineIndex !== -1 && bestScore >= 2) {
      const line = lines[bestLineIndex]
      const lineStart = lineOffsets[bestLineIndex]

      // Highlight the matching term or cell within the line
      let termIndex = -1
      let termLength = 0

      // If line is a markdown table row (| col1 | col2 |)
      if (line.trim().startsWith('|') && line.trim().endsWith('|') && bestMatchedTerm) {
        const cells = line.split('|')
        let cellOffset = 0
        for (let c = 1; c < cells.length - 1; c++) {
          const cell = cells[c]
          const pipeIndex = line.indexOf('|', cellOffset)
          const cellStartInLine = pipeIndex + 1
          if (cell.toLowerCase().includes(bestMatchedTerm.toLowerCase())) {
            const trimmedCell = cell.trim()
            const leadingSpaces = cell.indexOf(trimmedCell)
            termIndex = lineStart + cellStartInLine + (leadingSpaces >= 0 ? leadingSpaces : 0)
            termLength = trimmedCell.length
            break
          }
          cellOffset = cellStartInLine + cell.length
        }
      }

      // If not a table cell, find the term position
      if (termIndex === -1 && bestMatchedTerm) {
        const termPos = line.toLowerCase().indexOf(bestMatchedTerm.toLowerCase())
        if (termPos !== -1) {
          // Highlight a reasonable clause around the term if the line is short
          const trimmedLine = line.trim()
          if (trimmedLine.length <= 140 && !trimmedLine.startsWith('#')) {
            const leadingSpaces = line.indexOf(trimmedLine)
            termIndex = lineStart + (leadingSpaces >= 0 ? leadingSpaces : 0)
            termLength = trimmedLine.length
          } else {
            termIndex = lineStart + termPos
            termLength = bestMatchedTerm.length
          }
        }
      }

      if (termIndex === -1) {
        const trimmedLine = line.trim()
        const leadingSpaces = line.indexOf(trimmedLine)
        termIndex = lineStart + (leadingSpaces >= 0 ? leadingSpaces : 0)
        termLength = Math.min(trimmedLine.length, 120)
      }

      return buildResult(termIndex, termLength, 20 + bestScore, 'keywords')
    }
  }

  return { text: fullText, matched: false, score: 0 }
}

export function SourceContentViewer({
  content,
  filePath,
  sourceId,
  onDownload,
  isDownloading = false,
  highlightText,
  onClearHighlight,
}: SourceContentViewerProps) {
  const { t } = useTranslation()
  const isImage = useMemo(() => {
    if (!filePath) return false
    return /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(filePath)
  }, [filePath])

  const [viewMode, setViewMode] = useState<'formatted' | 'raw' | 'image'>('formatted')
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [isLoadingImage, setIsLoadingImage] = useState(false)
  const [selectedSheetIndex, setSelectedSheetIndex] = useState<number>(0)
  const [forceFullRender, setForceFullRender] = useState(false)
  const [copied, setCopied] = useState(false)

  // Load image preview blob when viewing image mode
  React.useEffect(() => {
    let active = true
    let createdUrl: string | null = null

    if (isImage && sourceId && viewMode === 'image' && !imageUrl) {
      setIsLoadingImage(true)
      sourcesApi
        .downloadFile(sourceId)
        .then((res) => {
          if (!active) return
          createdUrl = window.URL.createObjectURL(res.data)
          setImageUrl(createdUrl)
        })
        .catch((err) => {
          console.error('Failed to load image preview:', err)
        })
        .finally(() => {
          if (active) setIsLoadingImage(false)
        })
    }

    return () => {
      active = false
      if (createdUrl) {
        window.URL.revokeObjectURL(createdUrl)
      }
    }
  }, [isImage, sourceId, viewMode, imageUrl])

  // 1. Detect if content contains multiple sheets (e.g. from Excel / Spreadsheets)
  const sheets = useMemo<SheetInfo[]>(() => {
    if (!content) return []

    // Matches lines like: "# Sheet: 表紙" or "## Sheet: Sheet1"
    const sheetHeaderRegex = /^#+\s*Sheet:\s*(.+)$/gm
    const matches: { index: number; name: string }[] = []
    let match: RegExpExecArray | null

    while ((match = sheetHeaderRegex.exec(content)) !== null) {
      matches.push({
        index: match.index,
        name: match[1].trim(),
      })
    }

    if (matches.length <= 1) {
      return []
    }

    const result: SheetInfo[] = []
    for (let i = 0; i < matches.length; i++) {
      const current = matches[i]
      const next = matches[i + 1]
      const sheetContent = next
        ? content.slice(current.index, next.index).trim()
        : content.slice(current.index).trim()

      result.push({
        id: `sheet-${i}`,
        name: current.name || `Sheet ${i + 1}`,
        content: sheetContent,
        charCount: sheetContent.length,
      })
    }

    return result
  }, [content])

  // Determine what content to display
  const activeContent = useMemo(() => {
    if (sheets.length > 0 && selectedSheetIndex >= 0 && selectedSheetIndex < sheets.length) {
      return sheets[selectedSheetIndex].content
    }
    return content || ''
  }, [sheets, selectedSheetIndex, content])

  // Apply citation highlight if requested
  const highlightResult = useMemo(() => {
    return applyCitationHighlight(activeContent, highlightText)
  }, [activeContent, highlightText])

  // Count table rows in active content to gauge DOM complexity
  const tableRowCount = useMemo(() => {
    if (!activeContent) return 0
    let count = 0
    const lines = activeContent.split('\n')
    for (const line of lines) {
      if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
        count++
      }
    }
    return count
  }, [activeContent])

  const isOversized =
    activeContent.length > PREVIEW_SAFE_LIMIT || tableRowCount > TABLE_ROW_SAFE_LIMIT

  // Safely truncated content for preview if oversized and not forced
  const displayMarkdown = useMemo(() => {
    const textToRender = highlightResult.text
    if (!isOversized || forceFullRender) {
      return textToRender
    }
    return textToRender.slice(0, PREVIEW_SAFE_LIMIT)
  }, [highlightResult.text, isOversized, forceFullRender])

  // Auto-switch to the sheet that best matches the citation query across all sheets
  React.useEffect(() => {
    if (sheets.length > 0 && highlightText) {
      let bestIdx = -1
      let bestScore = 0

      for (let i = 0; i < sheets.length; i++) {
        const matchRes = applyCitationHighlight(sheets[i].content, highlightText)
        if (matchRes.matched) {
          if (matchRes.matchType === 'exact' || matchRes.matchType === 'phrase') {
            bestIdx = i
            break
          }
          if (matchRes.score > bestScore) {
            bestScore = matchRes.score
            bestIdx = i
          }
        }
      }

      if (bestIdx !== -1 && bestIdx !== selectedSheetIndex) {
        setSelectedSheetIndex(bestIdx)
      }
    }
  }, [sheets, highlightText, selectedSheetIndex])

  // If a citation was matched in an oversized document, automatically allow rendering so highlight is visible
  React.useEffect(() => {
    if (highlightResult.matched && isOversized && !forceFullRender) {
      setForceFullRender(true)
    }
  }, [highlightResult.matched, isOversized, forceFullRender])

  // Scroll to citation highlight when matched
  React.useEffect(() => {
    if (highlightResult.matched) {
      const timer = setTimeout(() => {
        const el = document.getElementById('citation-highlight')
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [highlightResult.matched, viewMode, selectedSheetIndex])

  const handleCopyContent = () => {
    if (!activeContent) return
    navigator.clipboard.writeText(activeContent)
    setCopied(true)
    toast.success(t('common.copied') || 'Copied to clipboard')
    setTimeout(() => setCopied(false), 2000)
  }

  if (!content) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        {t('sources.noContent')}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Control Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 p-2 text-sm">
        {/* Left: Sheet navigation or Document info */}
        <div className="flex items-center gap-2 flex-1 min-w-[240px]">
          {sheets.length > 0 ? (
            <>
              <div className="flex items-center gap-1.5 text-muted-foreground font-medium text-xs">
                <FileSpreadsheet className="h-4 w-4 text-teal" />
                <span>Sheet ({selectedSheetIndex + 1}/{sheets.length}):</span>
              </div>
              <Select
                value={String(selectedSheetIndex)}
                onValueChange={(val) => {
                  setSelectedSheetIndex(Number(val))
                  setForceFullRender(false)
                }}
              >
                <SelectTrigger className="h-8 max-w-[260px] text-xs font-mono">
                  <SelectValue placeholder="Chọn Sheet" />
                </SelectTrigger>
                <SelectContent>
                  {sheets.map((s, idx) => (
                    <SelectItem key={s.id} value={String(idx)} className="text-xs">
                      {idx + 1}. {s.name} ({Math.round(s.charCount / 1024 * 10) / 10} KB)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* Prev / Next Sheet buttons */}
              <div className="flex items-center gap-0.5">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  disabled={selectedSheetIndex <= 0}
                  onClick={() => {
                    setSelectedSheetIndex((prev) => Math.max(0, prev - 1))
                    setForceFullRender(false)
                  }}
                  title="Sheet trước"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  disabled={selectedSheetIndex >= sheets.length - 1}
                  onClick={() => {
                    setSelectedSheetIndex((prev) => Math.min(sheets.length - 1, prev + 1))
                    setForceFullRender(false)
                  }}
                  title="Sheet kế tiếp"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </>
          ) : (
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs">
              <FileText className="h-4 w-4" />
              <span>
                {content.length.toLocaleString()} {t('common.characters') || 'ký tự'}
                {tableRowCount > 0 && ` • ~${tableRowCount} dòng bảng`}
              </span>
            </div>
          )}
        </div>

        {/* Right: View Mode Toggle & Actions */}
        <div className="flex items-center gap-1.5">
          {/* Formatted vs Raw Mode Toggle */}
          <div className="flex rounded-md bg-muted p-0.5 border">
            {isImage && (
              <Button
                variant={viewMode === 'image' ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-2.5 text-xs gap-1"
                onClick={() => setViewMode('image')}
              >
                <ImageIcon className="h-3.5 w-3.5" />
                <span>Ảnh gốc</span>
              </Button>
            )}
            <Button
              variant={viewMode === 'formatted' ? 'secondary' : 'ghost'}
              size="sm"
              className="h-7 px-2.5 text-xs gap-1"
              onClick={() => setViewMode('formatted')}
            >
              <Eye className="h-3.5 w-3.5" />
              <span>Định dạng</span>
            </Button>
            <Button
              variant={viewMode === 'raw' ? 'secondary' : 'ghost'}
              size="sm"
              className="h-7 px-2.5 text-xs gap-1"
              onClick={() => setViewMode('raw')}
            >
              <Code className="h-3.5 w-3.5" />
              <span>Text thô</span>
            </Button>
          </div>

          {/* Copy Button */}
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={handleCopyContent}
            title="Sao chép nội dung"
          >
            {copied ? (
              <CheckCircle className="h-3.5 w-3.5 text-green-500" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>

          {/* Download Original File Button */}
          {filePath && onDownload && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2.5 text-xs gap-1"
              onClick={onDownload}
              disabled={isDownloading}
              title="Tải file gốc"
            >
              <Download className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Tải file gốc</span>
            </Button>
          )}
        </div>
      </div>

      {/* Citation Highlight Banner */}
      {highlightText && (
        <div className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700/60 dark:bg-amber-950/40 text-amber-900 dark:text-amber-200 text-xs animate-in fade-in duration-200">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <Sparkles className="h-4 w-4 text-amber-600 dark:text-amber-400 flex-shrink-0 animate-pulse" />
            <span className="truncate">
              <strong>{t('sources.viewingCitation') || 'Đang làm nổi bật trích dẫn:'}</strong> &ldquo;{highlightText}&rdquo;
            </span>
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {highlightResult.matched ? (
              <Button
                variant="outline"
                size="sm"
                className="h-6 px-2 text-[11px] border-amber-300 text-amber-900 dark:border-amber-700 dark:text-amber-200 hover:bg-amber-100 dark:hover:bg-amber-900/60"
                onClick={() => {
                  document.getElementById('citation-highlight')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                }}
              >
                {t('sources.jumpToCitation') || 'Cuộn tới đoạn trích dẫn'}
              </Button>
            ) : (
              <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-800 dark:text-amber-300">
                {t('sources.exactMatchNotFound') || 'Vị trí tương đối'}
              </Badge>
            )}
            {onClearHighlight && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-1.5 text-[11px] text-muted-foreground hover:text-foreground"
                onClick={onClearHighlight}
                title={t('sources.clearHighlight') || 'Bỏ làm nổi bật'}
              >
                ✕
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Warning Banner when Oversized in Formatted Mode */}
      {viewMode === 'formatted' && isOversized && !forceFullRender && (
        <Alert variant="default" className="bg-amber-500/10 border-amber-500/30 text-amber-900 dark:text-amber-200 py-2.5">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5" />
          <div className="flex-1 text-xs">
            <AlertTitle className="font-semibold text-xs">
              Nội dung lớn ({activeContent.length.toLocaleString()} ký tự, ~{tableRowCount} dòng bảng)
            </AlertTitle>
            <AlertDescription className="mt-1 flex flex-wrap items-center gap-2 text-xs">
              <span>
                Đang hiển thị bản xem trước {PREVIEW_SAFE_LIMIT.toLocaleString()} ký tự đầu để tránh làm đơ trình duyệt.
              </span>
              <div className="flex items-center gap-2 mt-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px] bg-background"
                  onClick={() => setViewMode('raw')}
                >
                  <Code className="h-3 w-3 mr-1" />
                  Xem toàn bộ dạng Text thô (Cực mượt)
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-[11px] text-muted-foreground hover:text-foreground"
                  onClick={() => setForceFullRender(true)}
                >
                  Vẫn muốn hiển thị toàn bộ Markdown
                </Button>
              </div>
            </AlertDescription>
          </div>
        </Alert>
      )}

      {/* Main Content Render Area */}
      {viewMode === 'image' ? (
        <div className="flex flex-col items-center justify-center p-6 border rounded-lg bg-muted/10 min-h-[300px]">
          {isLoadingImage ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <span className="animate-spin">⏳</span> Đang tải ảnh gốc...
            </div>
          ) : imageUrl ? (
            <div className="max-w-full overflow-auto text-center">
              <img
                src={imageUrl}
                alt={filePath ? filePath.split('/').pop() : 'Source Image'}
                className="max-h-[600px] w-auto mx-auto object-contain rounded border shadow-sm"
              />
            </div>
          ) : (
            <div className="text-center text-sm text-muted-foreground py-8">
              <p>Không thể hiển thị bản xem trước ảnh trực tiếp.</p>
              {onDownload && (
                <Button variant="outline" size="sm" className="mt-3 text-xs" onClick={onDownload}>
                  <Download className="h-3.5 w-3.5 mr-1" />
                  Tải file ảnh gốc
                </Button>
              )}
            </div>
          )}
        </div>
      ) : viewMode === 'formatted' ? (
        <div className="relative">
          <MarkdownRenderer>
            {displayMarkdown}
          </MarkdownRenderer>
          {isOversized && !forceFullRender && (
            <div className="mt-4 p-4 rounded-md border border-dashed text-center bg-muted/20">
              <p className="text-xs text-muted-foreground">
                (Đã ẩn {(activeContent.length - PREVIEW_SAFE_LIMIT).toLocaleString()} ký tự còn lại của phần này để bảo vệ hiệu năng)
              </p>
              <Button
                variant="link"
                size="sm"
                className="text-xs mt-1"
                onClick={() => setViewMode('raw')}
              >
                Chuyển sang xem toàn bộ dạng Text thô không giới hạn
              </Button>
            </div>
          )}
        </div>
      ) : (
        /* Raw Text / Monospace Mode (Fast, zero DOM bloat, smooth native scroll) */
        <div className="relative">
          <pre className="overflow-x-auto overflow-y-auto max-h-[650px] p-4 rounded-md bg-muted/60 border font-mono text-xs leading-relaxed whitespace-pre-wrap select-text">
            {highlightResult.matched && highlightResult.before !== undefined ? (
              <>
                {highlightResult.before}
                <mark
                  id="citation-highlight"
                  className="bg-amber-200 text-amber-950 dark:bg-amber-900/80 dark:text-amber-100 px-1 py-0.5 rounded font-semibold ring-2 ring-amber-400 shadow-sm animate-pulse inline"
                >
                  {highlightResult.matchedSnippet}
                </mark>
                {highlightResult.after}
              </>
            ) : (
              activeContent
            )}
          </pre>
          <div className="mt-1.5 flex justify-between items-center text-[11px] text-muted-foreground px-1">
            <span>Chế độ Text thô: {activeContent.length.toLocaleString()} ký tự • Không tạo thẻ DOM bảng • Cuộn siêu mượt 60 FPS</span>
            <Button
              variant="ghost"
              size="sm"
              className="h-5 px-1.5 text-[11px]"
              onClick={handleCopyContent}
            >
              Sao chép
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
