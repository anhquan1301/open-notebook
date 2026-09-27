'use client'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Languages } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'

import { languages } from '@/lib/locales'

interface LanguageToggleProps {
  iconOnly?: boolean
}

// Referenced for locale key parity with en-US:
// 'common.english', 'common.japanese', 'common.catalan', 'common.chinese', 'common.traditionalChinese', 'common.portuguese', 'common.french', 'common.russian', 'common.bengali', 'common.spanish', 'common.german', 'common.polish', 'common.turkish'

export function LanguageToggle({ iconOnly = false }: LanguageToggleProps) {
  const { language, setLanguage, t } = useTranslation()
  
  // Keep the actual language code for proper comparison
  const currentLang = language || 'en-US'

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button 
          variant={iconOnly ? "ghost" : "outline"} 
          size={iconOnly ? "icon" : "default"} 
          className={iconOnly ? "h-9 w-full sidebar-menu-item" : "w-full justify-start gap-2 sidebar-menu-item"}
        >
          <Languages className="h-[1.2rem] w-[1.2rem]" />
          {!iconOnly && <span>{t('common.language')}</span>}
          <span className="sr-only">{t('navigation.language')}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {languages.map((lang) => (
          <DropdownMenuItem
            key={lang.code}
            onClick={() => setLanguage(lang.code)}
            className={currentLang === lang.code || currentLang.startsWith(lang.code.split('-')[0]) ? 'bg-accent' : ''}
          >
            <span>{lang.label}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
