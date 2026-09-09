# OCR and source evidence

PDF processing can use extracted text and configured model-based page reads.
Provider credentials and network access are needed for model calls. Provider
availability, refusal and output quality vary by document.

Use retained page evidence, coverage measurements and validation findings to
review a result. A successful request or well-formed XML does not demonstrate
complete transcription. Do not infer comparative model quality from the default
configuration: this repository does not publish a representative benchmark.

See `codify/pipeline/formats` for format dispatch and the CLI help for available
processing options. Keep provider keys outside the repository.
