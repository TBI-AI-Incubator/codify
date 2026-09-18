
const ISO_639_1 =
  'ar az bg bs cs cy da de el en eo es et fi fr ga he hr hu hy id is it ja ka ' +
  'kk ky lb lt lv mk mt nl no pl pt ro ru sk sl sq sr sv tr uk uz';

let _displayNames: Intl.DisplayNames | null = null;

function displayNames(): Intl.DisplayNames {
  if (!_displayNames) {
    _displayNames = new Intl.DisplayNames(['en'], { type: 'language' });
  }
  return _displayNames;
}

export function languageName(code: string): string {
  if (!code) return '';
  try {
    return displayNames().of(code) ?? code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}

let _languages: { value: string; label: string }[] | null = null;

export function getLanguages(): { value: string; label: string }[] {
  if (!_languages) {
    _languages = ISO_639_1.split(/\s+/)
      .map((code) => ({ value: code, label: languageName(code) }))
      .filter((l) => l.label && l.label.toUpperCase() !== l.value.toUpperCase())
      .sort((a, b) => a.label.localeCompare(b.label));
  }
  return _languages;
}
