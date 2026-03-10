import NSpell from 'nspell';
import {
  getPersonalDictionary,
  addPersonalDictionaryWord,
  removePersonalDictionaryWord,
  getSpellcheckIgnoredWords,
  addSpellcheckIgnoredWord,
  removeSpellcheckIgnoredWord,
} from './api';

export const SPELLCHECK_LANGUAGES: Record<string, { label: string }> = {
  'en-GB': { label: 'English (GB)' },
  'en-US': { label: 'English (US)' },
  'cs':    { label: 'Czech' },
  'da':    { label: 'Danish' },
  'nl':    { label: 'Dutch' },
  'fr':    { label: 'French' },
  'de':    { label: 'German' },
  'hr':    { label: 'Croatian' },
  'hu':    { label: 'Hungarian' },
  'it':    { label: 'Italian' },
  'nb':    { label: 'Norwegian' },
  'pl':    { label: 'Polish' },
  'pt-BR': { label: 'Portuguese (Brazil)' },
  'pt-PT': { label: 'Portuguese (Portugal)' },
  'ro':    { label: 'Romanian' },
  'ru':    { label: 'Russian' },
  'es':    { label: 'Spanish' },
  'sv':    { label: 'Swedish' },
  'tr':    { label: 'Turkish' },
  'uk':    { label: 'Ukrainian' },
};

type ReadyCallback = () => void;

class SpellCheckService {
  private speller: NSpell | null = null;
  private currentLocale: string | null = null;
  private loading = false;
  private readyCallbacks: ReadyCallback[] = [];
  private personalDictionary: string[] = [];
  private ignoredWords: Set<string> = new Set();
  private dictionaryCache: Map<string, { aff: string; dic: string }> = new Map();

  isReady(): boolean {
    return this.speller !== null && !this.loading;
  }

  isDisabled(): boolean {
    return this.currentLocale === 'disabled' || this.currentLocale === null;
  }

  getLocale(): string | null {
    return this.currentLocale;
  }

  onReady(callback: ReadyCallback): () => void {
    if (this.isReady()) {
      callback();
    }
    this.readyCallbacks.push(callback);
    return () => {
      this.readyCallbacks = this.readyCallbacks.filter((cb) => cb !== callback);
    };
  }

  private notifyReady(): void {
    this.readyCallbacks.forEach((cb) => cb());
  }

  async initialise(locale: string): Promise<void> {
    if (locale === 'disabled') {
      this.speller = null;
      this.currentLocale = 'disabled';
      this.notifyReady();
      return;
    }

    if (locale === this.currentLocale && this.speller) {
      return;
    }

    this.loading = true;

    try {
      const dictData = await this.loadDictionary(locale);
      this.speller = NSpell(dictData.aff, dictData.dic);
      this.currentLocale = locale;

      await this.loadUserData();

      this.loading = false;
      this.notifyReady();
    } catch (err) {
      console.error(`[SpellCheckService] Failed to load dictionary for '${locale}':`, err);
      this.speller = null;
      this.currentLocale = null;
      this.loading = false;
    }
  }

  private async loadDictionary(locale: string): Promise<{ aff: string; dic: string }> {
    const cached = this.dictionaryCache.get(locale);
    if (cached) return cached;

    const basePath = `/dictionaries/${locale}`;
    const [affResponse, dicResponse] = await Promise.all([
      fetch(`${basePath}/index.aff`),
      fetch(`${basePath}/index.dic`),
    ]);

    if (!affResponse.ok || !dicResponse.ok) {
      throw new Error(`Dictionary files not found for locale '${locale}'.`);
    }

    const aff = await affResponse.text();
    const dic = await dicResponse.text();

    const data = { aff, dic };
    this.dictionaryCache.set(locale, data);
    return data;
  }

  private async loadUserData(): Promise<void> {
    try {
      const [dictionary, ignored] = await Promise.all([
        getPersonalDictionary(),
        getSpellcheckIgnoredWords(),
      ]);

      this.personalDictionary = dictionary;
      this.ignoredWords = new Set(ignored);

      if (this.speller) {
        this.personalDictionary.forEach((word) => this.speller!.add(word));
      }
    } catch (err) {
      console.error('[SpellCheckService] Failed to load user data:', err);
    }
  }

  async changeLanguage(locale: string): Promise<void> {
    await this.initialise(locale);
  }

  check(word: string): boolean {
    if (!this.speller || this.isDisabled()) return true;
    if (this.ignoredWords.has(word)) return true;
    return this.speller.correct(word);
  }

  suggest(word: string): string[] {
    if (!this.speller || this.isDisabled()) return [];
    return this.speller.suggest(word).slice(0, 5);
  }

  isIgnored(word: string): boolean {
    return this.ignoredWords.has(word);
  }

  async addToPersonalDictionary(word: string): Promise<void> {
    const normalised = word.trim();
    if (!normalised) return;

    if (this.speller) {
      this.speller.add(normalised);
    }
    if (!this.personalDictionary.includes(normalised)) {
      this.personalDictionary.push(normalised);
    }

    try {
      await addPersonalDictionaryWord(normalised);
    } catch (err) {
      console.error('[SpellCheckService] Failed to persist personal dictionary word:', err);
    }

    this.notifyReady();
  }

  async removeFromPersonalDictionary(word: string): Promise<void> {
    this.personalDictionary = this.personalDictionary.filter((w) => w !== word);

    try {
      await removePersonalDictionaryWord(word);
    } catch (err) {
      console.error('[SpellCheckService] Failed to remove personal dictionary word:', err);
    }
  }

  async addToIgnored(word: string): Promise<void> {
    const normalised = word.trim();
    if (!normalised) return;

    this.ignoredWords.add(normalised);

    try {
      await addSpellcheckIgnoredWord(normalised);
    } catch (err) {
      console.error('[SpellCheckService] Failed to persist ignored word:', err);
    }

    this.notifyReady();
  }

  async removeFromIgnored(word: string): Promise<void> {
    this.ignoredWords.delete(word);

    try {
      await removeSpellcheckIgnoredWord(word);
    } catch (err) {
      console.error('[SpellCheckService] Failed to remove ignored word:', err);
    }
  }

  getPersonalDictionaryWords(): string[] {
    return [...this.personalDictionary];
  }

  async clearPersonalDictionary(): Promise<void> {
    const words = [...this.personalDictionary];
    this.personalDictionary = [];

    for (const word of words) {
      try {
        await removePersonalDictionaryWord(word);
      } catch (err) {
        console.error('[SpellCheckService] Failed to remove word during clear:', err);
      }
    }

    this.notifyReady();
  }
}

export const spellCheckService = new SpellCheckService();
