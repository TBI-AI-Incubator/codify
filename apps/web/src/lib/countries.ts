import countries from 'i18n-iso-countries';
import en from 'i18n-iso-countries/langs/en.json';

let _countries: { value: string; label: string }[] | null = null;

export function getCountries() {
  if (!_countries) {
    countries.registerLocale(en);
    const all = countries.getNames('en', { select: 'official' });
    _countries = Object.entries(all)
      .map(([code, name]) => ({ value: code.toLowerCase(), label: name }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }
  return _countries;
}

const NON_ISO_NAMES: Record<string, string> = {
  eu: 'European Union',
};

export function countryName(code: string): string {
  if (!code) return '';
  getCountries();
  return (
    countries.getName(code.toUpperCase(), 'en') ??
    NON_ISO_NAMES[code.toLowerCase()] ??
    code.toUpperCase()
  );
}
