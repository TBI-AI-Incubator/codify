/**
 * The pipeline stages, the chapter that opens each, and every guide chapter that belongs to it.
 * The two chapters on modelling a legal system come before the pipeline and carry no rail.
 */
export const journey = [
  { label: 'Acquire', slug: 'guide/acquire-sources', pages: ['guide/acquire-sources'] },
  { label: 'OCR', slug: 'guide/ocr', pages: ['guide/ocr'] },
  { label: 'Structure', slug: 'guide/structure', pages: ['guide/structure'] },
  { label: 'Validate', slug: 'guide/validate-and-repair', pages: ['guide/validate-and-repair'] },
  {
    label: 'Publish',
    slug: 'guide/serve-and-publish',
    pages: [
      'guide/amendments-and-versions',
      'guide/translate',
      'guide/search',
      'guide/serve-and-publish',
      'guide/interoperate',
    ],
  },
]
