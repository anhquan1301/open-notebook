import { viVN } from './vi-VN';
import { zhCN } from './zh-CN';
import { enUS } from './en-US';
import { zhTW } from './zh-TW';
import { ptBR } from './pt-BR';
import { jaJP } from './ja-JP';
import { itIT } from './it-IT';
import { frFR } from './fr-FR';
import { ruRU } from './ru-RU';
import { bnIN } from './bn-IN';
import { caES } from './ca-ES';
import { esES } from './es-ES';
import { deDE } from './de-DE';
import { plPL } from './pl-PL';
import { trTR } from './tr-TR';

export const resources = {
  'vi-VN': { translation: viVN },
  'en-US': { translation: enUS },
  'ja-JP': { translation: jaJP },
  'zh-CN': { translation: zhCN },
  'zh-TW': { translation: zhTW },
  'pt-BR': { translation: ptBR },
  'it-IT': { translation: itIT },
  'fr-FR': { translation: frFR },
  'ru-RU': { translation: ruRU },
  'bn-IN': { translation: bnIN },
  'ca-ES': { translation: caES },
  'es-ES': { translation: esES },
  'de-DE': { translation: deDE },
  'pl-PL': { translation: plPL },
  'tr-TR': { translation: trTR },
} as const;

export type TranslationKeys = typeof enUS;

export type LanguageCode = keyof typeof resources;

export type Language = {
  code: LanguageCode;
  label: string;
};

export const languages: Language[] = [
  { code: 'vi-VN', label: 'Tiếng Việt' },
  { code: 'ja-JP', label: '日本語' },
  { code: 'en-US', label: 'English' },
];

export { viVN, zhCN, enUS, zhTW, ptBR, jaJP, itIT, frFR, ruRU, bnIN, caES, esES, deDE, plPL, trTR };
