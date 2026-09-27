'use client'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

import { useThemeStore } from '@/lib/stores/theme-store'
import { oneDark as darkTheme } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { oneLight as lightTheme } from 'react-syntax-highlighter/dist/esm/styles/prism'
// PrismLight with an explicit language set instead of the full Prism build:
// bundling every refractor grammar costs ~600KB of client JS. Unregistered
// languages fall back to unhighlighted text.
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-light'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import c from 'react-syntax-highlighter/dist/esm/languages/prism/c'
import cpp from 'react-syntax-highlighter/dist/esm/languages/prism/cpp'
import csharp from 'react-syntax-highlighter/dist/esm/languages/prism/csharp'
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css'
import diff from 'react-syntax-highlighter/dist/esm/languages/prism/diff'
import docker from 'react-syntax-highlighter/dist/esm/languages/prism/docker'
import go from 'react-syntax-highlighter/dist/esm/languages/prism/go'
import java from 'react-syntax-highlighter/dist/esm/languages/prism/java'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx'
import kotlin from 'react-syntax-highlighter/dist/esm/languages/prism/kotlin'
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown'
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup'
import php from 'react-syntax-highlighter/dist/esm/languages/prism/php'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import r from 'react-syntax-highlighter/dist/esm/languages/prism/r'
import ruby from 'react-syntax-highlighter/dist/esm/languages/prism/ruby'
import rust from 'react-syntax-highlighter/dist/esm/languages/prism/rust'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import swift from 'react-syntax-highlighter/dist/esm/languages/prism/swift'
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'

import type {ExtraProps} from 'react-markdown'
import type {ComponentProps, ElementType} from 'react'

const LANGUAGES = {
  bash, c, cpp, csharp, css, diff, docker, go, java, javascript, json, jsx,
  kotlin, markdown, markup, php, python, r, ruby, rust, sql, swift, tsx,
  typescript, yaml,
}
for (const [name, language] of Object.entries(LANGUAGES)) {
  SyntaxHighlighter.registerLanguage(name, language)
}

type Components = {
  [Key in Extract<ElementType, string>]?: ElementType<ComponentProps<Key> & ExtraProps>
}

export function MarkdownRenderer({ children, components = {}}: { children: React.ReactNode , components?: Components }) {

  const { getEffectiveTheme } = useThemeStore()
  const isDark = getEffectiveTheme() === 'dark'

  return (
    <div className="prose prose-neutral dark:prose-invert max-w-none break-words text-xs leading-relaxed text-foreground prose-headings:font-semibold prose-a:text-primary prose-a:break-all prose-code:before:content-none prose-code:after:content-none prose-pre:p-0 prose-pre:bg-transparent">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{...{
            p: ({ children }) => <p className="mb-2 leading-relaxed text-xs">{children}</p>,
            h1: ({ children }) => <h1 className="text-sm font-bold mb-2 mt-2.5 text-foreground leading-snug">{children}</h1>,
            h2: ({ children }) => <h2 className="text-xs font-bold mb-1.5 mt-2 text-foreground leading-snug">{children}</h2>,
            h3: ({ children }) => <h3 className="text-xs font-semibold mb-1 mt-1.5 text-foreground leading-snug">{children}</h3>,
            h4: ({ children }) => <h4 className="text-xs font-semibold mb-1 mt-1 text-foreground">{children}</h4>,
            h5: ({ children }) => <h5 className="text-xs font-semibold mb-1 mt-1 text-foreground">{children}</h5>,
            h6: ({ children }) => <h6 className="text-xs font-semibold mb-1 mt-1 text-foreground">{children}</h6>,
            li: ({ children }) => <li className="mb-0.5 text-xs leading-relaxed">{children}</li>,
            ul: ({ children }) => <ul className="mb-2 space-y-0.5 list-disc pl-4 text-xs">{children}</ul>,
            ol: ({ children }) => <ol className="mb-2 space-y-0.5 list-decimal pl-4 text-xs">{children}</ol>,
            table: ({ children }) => (
              <div className="my-2 overflow-x-auto w-full max-w-full rounded-md border border-border/70">
                <table className="w-full min-w-full border-collapse text-xs">{children}</table>
              </div>
            ),
            thead: ({ children }) => <thead className="bg-muted/60 text-muted-foreground">{children}</thead>,
            tbody: ({ children }) => <tbody className="divide-y divide-border/60">{children}</tbody>,
            tr: ({ children }) => <tr className="border-b border-border/60 hover:bg-muted/20 transition-colors">{children}</tr>,
            th: ({ children }) => <th className="border border-border/60 px-2.5 py-1.5 text-left font-semibold whitespace-nowrap text-xs text-foreground">{children}</th>,
            td: ({ children }) => <td className="border border-border/60 px-2.5 py-1.5 align-top leading-snug text-xs break-words min-w-[80px]">{children}</td>,
            code: ({ children, className }) => {
              const match = /language-(\w+)/.exec(className || '')
              const isBlock = match || String(children).includes('\n')
              return isBlock ? (
                <SyntaxHighlighter
                  language={match ? match[1] : 'text'}
                  style={isDark ? darkTheme : lightTheme}
                  PreTag="div"
                  className="text-xs border border-border/70 rounded-md my-2"
                >
                  {String(children)}
                </SyntaxHighlighter>
              ) : (
                <code className="bg-muted border border-border/50 rounded px-1.5 py-0.5 text-[11px] font-mono">{children}</code>
              )
            },
            a: ({ children, href, ...props }) => {
              if (href === '#citation-target') {
                return (
                  <mark
                    id="citation-highlight"
                    className="bg-amber-200 text-amber-950 dark:bg-amber-900/80 dark:text-amber-100 px-1 py-0.5 rounded font-semibold ring-2 ring-amber-400 shadow-sm animate-pulse inline"
                  >
                    {children}
                  </mark>
                )
              }
              return (
                <a href={href} target="_blank" rel="noopener noreferrer" {...props} className="text-primary hover:underline">
                  {children}
                </a>
              )
            },
            img: ({ src, alt, ...props }) => (
              <span className="block my-2.5">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={src}
                  alt={alt || ''}
                  loading="lazy"
                  className="max-h-96 max-w-full rounded-md border border-border/60 object-contain shadow-sm bg-muted/20"
                  {...props}
                />
                {alt ? (
                  <span className="block text-[11px] text-muted-foreground mt-1 italic">
                    {alt}
                  </span>
                ) : null}
              </span>
            ),
          }, ...components}}
        >
          {String(children)}
        </ReactMarkdown>
      </div>
  )
}
