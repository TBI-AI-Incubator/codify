-- Core corpus schema baseline. Generated from the apps/api chain at head 0123
-- and verified byte-identical (modulo pg_dump nonces) via a fresh-DB schema diff.
-- Frozen once adopted: existing databases are already stamped at this revision,
-- so corpus schema changes are NEW revisions in this chain, never edits here.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;

CREATE OR REPLACE FUNCTION public.set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $function$
;

CREATE OR REPLACE FUNCTION public.enforce_events_append_only()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = format('events table is append-only (op=%s, id=%s)', TG_OP,
                                 COALESCE(OLD.id::text, NEW.id::text));
        END;
        $function$
;

CREATE OR REPLACE FUNCTION public.examinations_no_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            RAISE EXCEPTION 'examinations rows are immutable (UPDATE blocked)'
                USING ERRCODE = 'insufficient_privilege';
        END;
        $function$
;

CREATE OR REPLACE FUNCTION public.enforce_versions_immutable()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (NEW.id, NEW.law_id, NEW.expression_uri, NEW.language,
                    NEW.expression_date, NEW.source_sha256, NEW.parent_version_id,
                    NEW.ingested_at, NEW.created_at)
                   IS NOT DISTINCT FROM
                   (OLD.id, OLD.law_id, OLD.expression_uri, OLD.language,
                    OLD.expression_date, OLD.source_sha256, OLD.parent_version_id,
                    OLD.ingested_at, OLD.created_at)
                THEN
                    -- Identifying columns unchanged. The remaining mutable set:
                    --   embedded_at / acquis_chapter (stamps from earlier)
                    --   akn_xml + repaired_at + repair_attribution (0042)
                    --   ocr_source / ocr_model (0076)
                    --   reviewed_at (this migration, monotonic)
                    -- If akn_xml changes, repaired_at and repair_attribution must
                    -- also be set so we never lose the provenance of a change.
                    IF NEW.akn_xml IS DISTINCT FROM OLD.akn_xml THEN
                        IF NEW.repaired_at IS NULL OR NEW.repair_attribution IS NULL THEN
                            RAISE EXCEPTION USING
                                ERRCODE = '23514',
                                MESSAGE = format(
                                    'versions.akn_xml change requires repaired_at + '
                                    'repair_attribution to be stamped together (id=%s)',
                                    NEW.id
                                );
                        END IF;
                    END IF;
                    IF OLD.reviewed_at IS NOT NULL
                       AND (NEW.reviewed_at IS NULL OR NEW.reviewed_at < OLD.reviewed_at)
                    THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            MESSAGE = format(
                                'versions.reviewed_at only moves forward: it cannot '
                                'be cleared or wound back (id=%s)', NEW.id
                            );
                    END IF;
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = format('versions row is immutable (op=%s, id=%s)', TG_OP,
                                 COALESCE(OLD.id::text, NEW.id::text));
        END;
        $function$
;

CREATE OR REPLACE FUNCTION public.page_read_disputes_no_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            RAISE EXCEPTION 'page_read_disputes rows are immutable (UPDATE blocked)'
                USING ERRCODE = 'insufficient_privilege';
        END;
        $function$
;



CREATE TABLE public.acquisitions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    jurisdiction_code text NOT NULL,
    frbr_work_uri text,
    source_url text NOT NULL,
    etag text,
    last_modified text,
    content_sha256 text NOT NULL,
    licence text,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.adjudications (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    run_id uuid,
    source_sha256 text DEFAULT ''::text NOT NULL,
    span_start integer NOT NULL,
    span_end integer NOT NULL,
    line text DEFAULT ''::text NOT NULL,
    span_reason text DEFAULT ''::text NOT NULL,
    emitted_by text DEFAULT ''::text NOT NULL,
    candidate_kinds text DEFAULT ''::text NOT NULL,
    kind text,
    number text,
    reason text NOT NULL,
    page_seen boolean DEFAULT false NOT NULL,
    page_no integer,
    model text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.amendment_effects (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version_id uuid NOT NULL,
    source_akn_wid text NOT NULL,
    target_frbr_uri text NOT NULL,
    target_akn_wid text,
    akn_category text NOT NULL,
    akn_action text NOT NULL,
    quoted jsonb,
    authority_uri text,
    mod_eid_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT amendment_effects_akn_action_check CHECK ((akn_action = ANY (ARRAY['repeal'::text, 'substitution'::text, 'insertion'::text, 'replacement'::text, 'renumbering'::text, 'split'::text, 'join'::text, 'variation'::text, 'termModification'::text, 'authenticInterpretation'::text, 'exceptionOfScope'::text, 'extensionOfScope'::text, 'entryIntoForce'::text, 'endOfEnactment'::text, 'postponementOfEntryIntoForce'::text, 'prorogationOfForce'::text, 'reEnactment'::text, 'unconstitutionality'::text, 'entryIntoEfficacy'::text, 'endOfEfficacy'::text, 'inapplication'::text, 'retroactivity'::text, 'extraefficacy'::text, 'postponementOfEfficacy'::text, 'prorogationOfEfficacy'::text]))),
    CONSTRAINT amendment_effects_akn_category_check CHECK ((akn_category = ANY (ARRAY['textual'::text, 'meaning'::text, 'scope'::text, 'force'::text, 'efficacy'::text]))),
    CONSTRAINT amendment_effects_source_akn_wid_check CHECK ((source_akn_wid <> ''::text)),
    CONSTRAINT amendment_effects_target_frbr_uri_check CHECK ((target_frbr_uri <> ''::text))
);

CREATE TABLE public.anticorruption_factors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    description text NOT NULL,
    akn_node_types jsonb NOT NULL,
    detection_hints jsonb DEFAULT '{}'::jsonb NOT NULL,
    source text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    risk_class text DEFAULT 'corruption'::text NOT NULL,
    nacp_example_uk text,
    nacp_example_en text,
    remediation_template text,
    CONSTRAINT anticorruption_factors_remediation_template_check CHECK (((remediation_template IS NULL) OR (remediation_template = ANY (ARRAY['exclude_provision'::text, 'define_exhaustive_list'::text, 'specify_deadlines_and_procedure'::text, 'regulate_directly_not_via_lower_acts'::text, 'remove_personal_contact'::text, 'clarify_rights_and_responsibilities'::text, 'ensure_appeal_path'::text, 'resolve_legal_collision'::text])))),
    CONSTRAINT anticorruption_factors_risk_class_check CHECK ((risk_class = ANY (ARRAY['corruption'::text, 'drafting-style'::text])))
);

CREATE TABLE public.anticorruption_phenomena (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    uncac_article text,
    scale text NOT NULL,
    description text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT anticorruption_phenomena_scale_check CHECK ((scale = ANY (ARRAY['grand'::text, 'petty'::text, 'administrative'::text, 'policy_capture'::text])))
);

CREATE TABLE public.anticorruption_schemes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    sector text NOT NULL,
    factor_signature jsonb NOT NULL,
    red_flags jsonb DEFAULT '[]'::jsonb NOT NULL,
    summary text NOT NULL,
    source text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    remediation_templates text[] DEFAULT ARRAY[]::text[] NOT NULL,
    CONSTRAINT anticorruption_schemes_remediation_templates_check CHECK ((remediation_templates <@ ARRAY['exclude_provision'::text, 'define_exhaustive_list'::text, 'specify_deadlines_and_procedure'::text, 'regulate_directly_not_via_lower_acts'::text, 'remove_personal_contact'::text, 'clarify_rights_and_responsibilities'::text, 'ensure_appeal_path'::text, 'resolve_legal_collision'::text]))
);

CREATE TABLE public.cross_references (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_provision_id uuid NOT NULL,
    target_provision_id uuid,
    target_uri text,
    ref_type text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    edge_class text NOT NULL,
    target_law_id uuid,
    target_section_id uuid,
    CONSTRAINT cross_references_check CHECK (((target_provision_id IS NOT NULL) OR (target_uri IS NOT NULL))),
    CONSTRAINT cross_references_edge_class_check CHECK ((edge_class = ANY (ARRAY['freetext_reference'::text, 'mod_textual'::text, 'mod_meaning'::text, 'mod_scope'::text, 'mod_force'::text, 'mod_efficacy'::text])))
);

CREATE TABLE public.events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    actor_id text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.examinations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    finding_id uuid NOT NULL,
    decision text NOT NULL,
    actor text NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    note text,
    prior_state text,
    run_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT examinations_decision_check CHECK ((decision = ANY (ARRAY['accepted'::text, 'dismissed'::text, 'escalated'::text, 'reopened'::text, 'carried_forward'::text])))
);

CREATE TABLE public.feedback (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provision_id uuid NOT NULL,
    version_id uuid NOT NULL,
    assessment_id uuid,
    finding_id uuid,
    langfuse_trace_id text,
    kind text NOT NULL,
    value jsonb DEFAULT '{}'::jsonb NOT NULL,
    comment text,
    user_id text NOT NULL,
    org text,
    jurisdiction text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT feedback_kind_check CHECK ((kind = ANY (ARRAY['thumbs'::text, 'score'::text, 'note'::text, 'alignment_correction'::text, 'suggestion_accepted'::text, 'suggestion_rejected'::text, 'suggestion_edited'::text])))
);

CREATE TABLE public.finding_notes (
    finding_id uuid NOT NULL,
    prompt_version character varying(16) NOT NULL,
    body text NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.findings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lens_run_id uuid NOT NULL,
    lens_name text NOT NULL,
    version_id uuid NOT NULL,
    provision_id uuid,
    provision_eid text NOT NULL,
    severity text NOT NULL,
    confidence real NOT NULL,
    rationale text NOT NULL,
    recommendation text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    review_status text DEFAULT 'open'::text NOT NULL,
    reviewed_by text,
    reviewed_at timestamp with time zone,
    decision_note text,
    CONSTRAINT findings_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT findings_review_status_check CHECK ((review_status = ANY (ARRAY['open'::text, 'accepted'::text, 'dismissed'::text, 'escalated'::text]))),
    CONSTRAINT findings_severity_check CHECK ((severity = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text, 'critical'::text])))
);

CREATE TABLE public.jurisdictions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    family text,
    calendar text DEFAULT 'gregorian'::text NOT NULL,
    languages text[] DEFAULT ARRAY[]::text[] NOT NULL,
    extra jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.law_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    jurisdiction_id uuid NOT NULL,
    source_ref text NOT NULL,
    target_ref text NOT NULL,
    relation smallint NOT NULL,
    edge_class text NOT NULL,
    source text NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.laws (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    jurisdiction_id uuid NOT NULL,
    title text NOT NULL,
    doctype text NOT NULL,
    year integer,
    number text,
    frbr_work_uri text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    title_translations jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'enacted'::text NOT NULL,
    gazette jsonb,
    short_title text,
    CONSTRAINT laws_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'enacted'::text])))
);

CREATE TABLE public.lens_run_directives (
    lens_run_id uuid NOT NULL,
    directive_law_id uuid NOT NULL
);

CREATE TABLE public.lens_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lens_name text NOT NULL,
    version_id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    prompt_version text,
    summary_text text,
    finding_count integer DEFAULT 0 NOT NULL,
    corruption_count integer DEFAULT 0 NOT NULL,
    drafting_style_count integer DEFAULT 0 NOT NULL,
    scheme_match_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    status character varying(16) DEFAULT 'running'::character varying NOT NULL,
    warnings text,
    CONSTRAINT lens_runs_status_check CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);

CREATE TABLE public.lifecycle_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version_id uuid NOT NULL,
    event_date date NOT NULL,
    event_type text NOT NULL,
    source_uri text,
    refers_uri text,
    originating_uri text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT lifecycle_events_event_type_check CHECK ((event_type = ANY (ARRAY['generation'::text, 'amendment'::text, 'repeal'::text])))
);

CREATE TABLE public.page_read_disputes (
    id uuid NOT NULL,
    page_read_id uuid NOT NULL,
    block_index integer,
    escalation_rung integer,
    verdict text NOT NULL,
    corrected_text text,
    note text,
    actor text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT page_read_disputes_block_index_nonneg CHECK (((block_index IS NULL) OR (block_index >= 0))),
    CONSTRAINT page_read_disputes_escalation_rung_nonneg CHECK (((escalation_rung IS NULL) OR (escalation_rung >= 0))),
    CONSTRAINT page_read_disputes_verdict_valid CHECK ((verdict = ANY (ARRAY['confirmed'::text, 'disputed'::text, 'corrected'::text])))
);

CREATE TABLE public.page_reads (
    id uuid NOT NULL,
    run_id uuid,
    version_id uuid,
    page_number integer NOT NULL,
    engine text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    dpi integer,
    text text DEFAULT ''::text NOT NULL,
    rival_text text DEFAULT ''::text NOT NULL,
    divergence real,
    layout jsonb,
    metrics jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.provision_embeddings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provision_id uuid NOT NULL,
    embedding public.halfvec(768) NOT NULL,
    model_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.provisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version_id uuid NOT NULL,
    section_id uuid,
    akn_eid text NOT NULL,
    akn_type text NOT NULL,
    text text NOT NULL,
    "position" integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    text_search tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text)) STORED,
    akn_wid text NOT NULL,
    normative boolean DEFAULT true NOT NULL,
    search_tokens text,
    search_pipeline_version smallint,
    search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, COALESCE(search_tokens, ''::text))) STORED,
    CONSTRAINT provisions_akn_wid_nonempty CHECK ((akn_wid <> ''::text))
);

CREATE TABLE public.registry_works (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    jurisdiction_id uuid NOT NULL,
    external_id text NOT NULL,
    ref text NOT NULL,
    title text NOT NULL,
    doc_types text,
    status smallint,
    law_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.repair_proposals (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    run_id uuid,
    eid text DEFAULT ''::text NOT NULL,
    check_name text NOT NULL,
    finding jsonb NOT NULL,
    ops jsonb NOT NULL,
    reasoning text DEFAULT ''::text NOT NULL,
    risk_class text NOT NULL,
    status text NOT NULL,
    audit jsonb NOT NULL,
    akn_sha256 text DEFAULT ''::text NOT NULL,
    source_sha256 text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    prompt_version text DEFAULT ''::text NOT NULL,
    decided_by text,
    decided_at timestamp with time zone,
    decision_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT repair_proposals_high_risk_decided CHECK (((risk_class <> 'high'::text) OR (status <> 'applied'::text) OR ((decided_by IS NOT NULL) AND (decided_at IS NOT NULL)))),
    CONSTRAINT repair_proposals_risk_valid CHECK ((risk_class = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
    CONSTRAINT repair_proposals_status_valid CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'applied'::text, 'superseded'::text])))
);

CREATE TABLE public.run_artifacts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    stage text NOT NULL,
    kind text NOT NULL,
    content_text text,
    content_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT run_artifacts_kind_check CHECK ((kind = ANY (ARRAY['page_texts'::text, 'bluebell'::text, 'akn_xml'::text, 'notes'::text, 'translated'::text, 'discovery.candidates'::text, 'discovery.result'::text, 'concordance_pdf'::text, 'structured_refs'::text, 'law_pdf'::text, 'law_docx'::text, 'repair_dossier'::text, 'repair_trace'::text, 'deliverable_zip'::text])))
);

CREATE TABLE public.runs (
    id uuid NOT NULL,
    kind text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    actor_id text,
    jurisdiction_code text,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    result jsonb,
    tallies jsonb,
    parent_run_id uuid,
    error text,
    object_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    image_ref text,
    CONSTRAINT runs_kind_check CHECK ((kind = ANY (ARRAY['ingest'::text, 'translation'::text, 'lens'::text, 'batch'::text, 'acquire'::text, 'eu_acquis_discover'::text, 'admin_batch'::text, 'embed'::text, 'regen_summary'::text, 'warm_notes'::text, 'concordance_export'::text, 'resolve_refs'::text, 'jurisdiction_sync'::text, 'repair'::text, 'normalise_eids'::text, 'reocr'::text, 'law_export'::text, 'translation_quality_grade'::text, 'purge_notes'::text, 'recanonicalise_nums'::text, 'structural_scan'::text, 'reclassify_doctype'::text, 'adjudicate'::text, 'structural_quality_grade'::text, 'rederive'::text, 'normalise_meta'::text, 'bulk_deliverable'::text, 'backfill_titles'::text, 'tokenise'::text]))),
    CONSTRAINT runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);

CREATE TABLE public.scheme_matches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lens_run_id uuid NOT NULL,
    lens_name text NOT NULL,
    scheme_id text NOT NULL,
    confidence real NOT NULL,
    findings jsonb DEFAULT '[]'::jsonb NOT NULL,
    rationale text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT scheme_matches_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision)))
);

CREATE TABLE public.sections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version_id uuid NOT NULL,
    parent_section_id uuid,
    akn_eid text NOT NULL,
    akn_type text NOT NULL,
    title text,
    "position" integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    akn_wid text NOT NULL,
    CONSTRAINT sections_akn_wid_nonempty CHECK ((akn_wid <> ''::text))
);

CREATE TABLE public.source_documents (
    sha256 text NOT NULL,
    original_filename text NOT NULL,
    byte_size bigint NOT NULL,
    page_count integer,
    object_key text NOT NULL,
    jurisdiction_code text,
    first_ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    pdf_title text,
    pdf_author text,
    pdf_producer text,
    pdf_creation_date timestamp with time zone
);

CREATE TABLE public.structural_findings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scan_run_id uuid NOT NULL,
    version_id uuid NOT NULL,
    law_id uuid NOT NULL,
    jurisdiction_code text NOT NULL,
    era text NOT NULL,
    doctype text NOT NULL,
    check_name text NOT NULL,
    failed boolean,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    scanned_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.translation_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_version_id uuid NOT NULL,
    target_language text NOT NULL,
    target_version_id uuid,
    status character varying(16) DEFAULT 'running'::character varying NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    progress_pct smallint,
    latest_eid text,
    error text,
    notes jsonb,
    audit jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT translation_runs_status_check CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);

CREATE TABLE public.version_source_texts (
    version_id uuid NOT NULL,
    text text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.version_unit_embeddings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version_id uuid NOT NULL,
    akn_eid text NOT NULL,
    model_id text NOT NULL,
    embedding public.halfvec(768) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    law_id uuid NOT NULL,
    expression_uri text NOT NULL,
    language text NOT NULL,
    expression_date date NOT NULL,
    parent_version_id uuid,
    akn_xml text NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_sha256 text,
    embedded_at timestamp with time zone,
    acquis_chapter smallint,
    repaired_at timestamp with time zone,
    repair_attribution text,
    ocr_source text,
    ocr_model text,
    translation_quality_grade text,
    source_akn_sha256 text,
    source_legibility real,
    structural_quality_grade text,
    structural_quality jsonb,
    reviewed_at timestamp with time zone,
    reviewed_by text,
    translation_quality jsonb
);

ALTER TABLE ONLY public.acquisitions
    ADD CONSTRAINT acquisitions_jurisdiction_code_source_url_content_sha256_key UNIQUE (jurisdiction_code, source_url, content_sha256);

ALTER TABLE ONLY public.acquisitions
    ADD CONSTRAINT acquisitions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.adjudications
    ADD CONSTRAINT adjudications_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.adjudications
    ADD CONSTRAINT adjudications_span_unique UNIQUE (version_id, span_start, span_end);

ALTER TABLE ONLY public.amendment_effects
    ADD CONSTRAINT amendment_effects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.anticorruption_factors
    ADD CONSTRAINT anticorruption_factors_code_key UNIQUE (code);

ALTER TABLE ONLY public.anticorruption_factors
    ADD CONSTRAINT anticorruption_factors_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.anticorruption_phenomena
    ADD CONSTRAINT anticorruption_phenomena_code_key UNIQUE (code);

ALTER TABLE ONLY public.anticorruption_phenomena
    ADD CONSTRAINT anticorruption_phenomena_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.anticorruption_schemes
    ADD CONSTRAINT anticorruption_schemes_code_key UNIQUE (code);

ALTER TABLE ONLY public.anticorruption_schemes
    ADD CONSTRAINT anticorruption_schemes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.cross_references
    ADD CONSTRAINT cross_references_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.examinations
    ADD CONSTRAINT examinations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.feedback
    ADD CONSTRAINT feedback_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.finding_notes
    ADD CONSTRAINT finding_notes_pkey PRIMARY KEY (finding_id, prompt_version);

ALTER TABLE ONLY public.findings
    ADD CONSTRAINT findings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.jurisdictions
    ADD CONSTRAINT jurisdictions_code_key UNIQUE (code);

ALTER TABLE ONLY public.jurisdictions
    ADD CONSTRAINT jurisdictions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.law_links
    ADD CONSTRAINT law_links_jurisdiction_id_source_ref_target_ref_relation_key UNIQUE (jurisdiction_id, source_ref, target_ref, relation);

ALTER TABLE ONLY public.law_links
    ADD CONSTRAINT law_links_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.laws
    ADD CONSTRAINT laws_frbr_work_uri_key UNIQUE (frbr_work_uri);

ALTER TABLE ONLY public.laws
    ADD CONSTRAINT laws_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.lens_run_directives
    ADD CONSTRAINT lens_run_directives_pkey PRIMARY KEY (lens_run_id, directive_law_id);

ALTER TABLE ONLY public.lens_runs
    ADD CONSTRAINT lens_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.lifecycle_events
    ADD CONSTRAINT lifecycle_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.page_read_disputes
    ADD CONSTRAINT page_read_disputes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.page_reads
    ADD CONSTRAINT page_reads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.page_reads
    ADD CONSTRAINT page_reads_run_page_unique UNIQUE (run_id, page_number);

ALTER TABLE ONLY public.provision_embeddings
    ADD CONSTRAINT provision_embeddings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.provision_embeddings
    ADD CONSTRAINT provision_embeddings_provision_id_model_id_key UNIQUE (provision_id, model_id);

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_version_id_akn_eid_key UNIQUE (version_id, akn_eid);

ALTER TABLE ONLY public.registry_works
    ADD CONSTRAINT registry_works_jurisdiction_id_external_id_key UNIQUE (jurisdiction_id, external_id);

ALTER TABLE ONLY public.registry_works
    ADD CONSTRAINT registry_works_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.repair_proposals
    ADD CONSTRAINT repair_proposals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.run_artifacts
    ADD CONSTRAINT run_artifacts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.scheme_matches
    ADD CONSTRAINT scheme_matches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT sections_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT sections_version_id_akn_eid_key UNIQUE (version_id, akn_eid);

ALTER TABLE ONLY public.source_documents
    ADD CONSTRAINT source_documents_pkey PRIMARY KEY (sha256);

ALTER TABLE ONLY public.structural_findings
    ADD CONSTRAINT structural_findings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.structural_findings
    ADD CONSTRAINT structural_findings_scan_run_id_version_id_check_name_key UNIQUE (scan_run_id, version_id, check_name);

ALTER TABLE ONLY public.translation_runs
    ADD CONSTRAINT translation_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.version_source_texts
    ADD CONSTRAINT version_source_texts_pkey PRIMARY KEY (version_id);

ALTER TABLE ONLY public.version_unit_embeddings
    ADD CONSTRAINT version_unit_embeddings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.version_unit_embeddings
    ADD CONSTRAINT version_unit_embeddings_version_id_akn_eid_model_id_key UNIQUE (version_id, akn_eid, model_id);

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_expression_uri_key UNIQUE (expression_uri);

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_pkey PRIMARY KEY (id);

CREATE INDEX acquisitions_frbr_idx ON public.acquisitions USING btree (frbr_work_uri);

CREATE INDEX acquisitions_jurisdiction_idx ON public.acquisitions USING btree (jurisdiction_code, fetched_at DESC);

CREATE INDEX acquisitions_url_idx ON public.acquisitions USING btree (jurisdiction_code, source_url, fetched_at DESC);

CREATE INDEX adjudications_run_idx ON public.adjudications USING btree (run_id);

CREATE INDEX adjudications_version_idx ON public.adjudications USING btree (version_id);

CREATE INDEX amendment_effects_akn_category_idx ON public.amendment_effects USING btree (akn_category);

CREATE INDEX amendment_effects_target_frbr_idx ON public.amendment_effects USING btree (target_frbr_uri);

CREATE INDEX amendment_effects_target_wid_idx ON public.amendment_effects USING btree (target_akn_wid) WHERE (target_akn_wid IS NOT NULL);

CREATE INDEX amendment_effects_version_id_idx ON public.amendment_effects USING btree (version_id);

CREATE INDEX anticorruption_factors_category_idx ON public.anticorruption_factors USING btree (category);

CREATE INDEX anticorruption_phenomena_scale_idx ON public.anticorruption_phenomena USING btree (scale);

CREATE INDEX anticorruption_schemes_sector_idx ON public.anticorruption_schemes USING btree (sector);

CREATE INDEX cross_references_edge_class_idx ON public.cross_references USING btree (edge_class);

CREATE INDEX cross_references_source_idx ON public.cross_references USING btree (source_provision_id);

CREATE INDEX cross_references_target_idx ON public.cross_references USING btree (target_provision_id) WHERE (target_provision_id IS NOT NULL);

CREATE INDEX cross_references_target_law_idx ON public.cross_references USING btree (target_law_id) WHERE (target_law_id IS NOT NULL);

CREATE INDEX cross_references_target_section_idx ON public.cross_references USING btree (target_section_id) WHERE (target_section_id IS NOT NULL);

CREATE INDEX events_actor_idx ON public.events USING btree (actor_id, created_at DESC);

CREATE INDEX events_entity_idx ON public.events USING btree (entity_type, entity_id, created_at DESC);

CREATE INDEX events_recent_idx ON public.events USING btree (created_at DESC);

CREATE INDEX feedback_assessment_idx ON public.feedback USING btree (assessment_id, created_at DESC) WHERE (assessment_id IS NOT NULL);

CREATE INDEX feedback_finding_idx ON public.feedback USING btree (finding_id, created_at DESC) WHERE (finding_id IS NOT NULL);

CREATE UNIQUE INDEX feedback_one_per_target_idx ON public.feedback USING btree (user_id, kind, provision_id, COALESCE(finding_id, '00000000-0000-0000-0000-000000000000'::uuid));

CREATE INDEX feedback_provision_idx ON public.feedback USING btree (provision_id, created_at DESC);

CREATE INDEX findings_lens_run_idx ON public.findings USING btree (lens_run_id);

CREATE INDEX findings_version_lens_idx ON public.findings USING btree (version_id, lens_name);

CREATE INDEX ix_examinations_finding_at ON public.examinations USING btree (finding_id, at DESC);

CREATE INDEX ix_finding_notes_finding_id ON public.finding_notes USING btree (finding_id);

CREATE INDEX ix_findings_provision_id ON public.findings USING btree (provision_id);

CREATE INDEX ix_findings_review_queue ON public.findings USING btree (lens_name, severity, confidence) WHERE (review_status = 'open'::text);

CREATE INDEX ix_lens_run_directives_directive ON public.lens_run_directives USING btree (directive_law_id);

CREATE INDEX ix_page_read_disputes_page_read_id ON public.page_read_disputes USING btree (page_read_id, created_at);

CREATE INDEX ix_runs_status ON public.runs USING btree (status);

CREATE INDEX ix_translation_runs_source_version ON public.translation_runs USING btree (source_version_id);

CREATE INDEX ix_translation_runs_status ON public.translation_runs USING btree (status);

CREATE INDEX ix_translation_runs_target_version_id ON public.translation_runs USING btree (target_version_id);

CREATE INDEX ix_versions_source_sha256 ON public.versions USING btree (source_sha256);

CREATE INDEX ix_versions_structural_quality_grade ON public.versions USING btree (structural_quality_grade);

CREATE INDEX ix_versions_translation_quality_grade ON public.versions USING btree (translation_quality_grade);

CREATE INDEX law_links_source_idx ON public.law_links USING btree (jurisdiction_id, source_ref);

CREATE INDEX law_links_target_idx ON public.law_links USING btree (jurisdiction_id, target_ref);

CREATE INDEX laws_doctype_year_idx ON public.laws USING btree (doctype, year);

CREATE INDEX laws_jurisdiction_id_idx ON public.laws USING btree (jurisdiction_id);

CREATE INDEX lens_runs_version_lens_started_idx ON public.lens_runs USING btree (version_id, lens_name, started_at DESC);

CREATE INDEX lifecycle_events_event_date_idx ON public.lifecycle_events USING btree (event_date);

CREATE INDEX lifecycle_events_version_id_idx ON public.lifecycle_events USING btree (version_id);

CREATE INDEX page_reads_version_idx ON public.page_reads USING btree (version_id, page_number);

CREATE INDEX provision_embeddings_hnsw_idx ON public.provision_embeddings USING hnsw (embedding public.halfvec_cosine_ops) WITH (m='16', ef_construction='64');

CREATE INDEX provision_embeddings_model_id_idx ON public.provision_embeddings USING btree (model_id);

CREATE INDEX provisions_akn_wid_idx ON public.provisions USING btree (akn_wid);

CREATE INDEX provisions_bm25_idx ON public.provisions USING bm25 (search_tokens) WITH (text_config=simple, k1='1.2', b='0.75');

CREATE INDEX provisions_search_tsv_idx ON public.provisions USING gin (search_tsv);

CREATE INDEX provisions_section_id_idx ON public.provisions USING btree (section_id) WHERE (section_id IS NOT NULL);

CREATE INDEX provisions_text_search_idx ON public.provisions USING gin (text_search);

CREATE INDEX provisions_version_id_idx ON public.provisions USING btree (version_id);

CREATE INDEX provisions_version_position_idx ON public.provisions USING btree (version_id, "position");

CREATE INDEX registry_works_ref_idx ON public.registry_works USING btree (jurisdiction_id, ref);

CREATE INDEX repair_proposals_pending_idx ON public.repair_proposals USING btree (status) WHERE (status = 'pending'::text);

CREATE INDEX repair_proposals_version_idx ON public.repair_proposals USING btree (version_id, status);

CREATE INDEX run_artifacts_run_stage_idx ON public.run_artifacts USING btree (run_id, stage);

CREATE INDEX runs_kind_status_created_idx ON public.runs USING btree (kind, status, created_at DESC);

CREATE INDEX runs_parent_idx ON public.runs USING btree (parent_run_id);

CREATE INDEX scheme_matches_run_idx ON public.scheme_matches USING btree (lens_run_id);

CREATE INDEX scheme_matches_scope_idx ON public.scheme_matches USING btree (lens_name, scheme_id);

CREATE INDEX sections_akn_wid_idx ON public.sections USING btree (akn_wid);

CREATE INDEX sections_parent_id_idx ON public.sections USING btree (parent_section_id) WHERE (parent_section_id IS NOT NULL);

CREATE INDEX sections_version_id_idx ON public.sections USING btree (version_id);

CREATE INDEX sections_version_position_idx ON public.sections USING btree (version_id, "position");

CREATE INDEX structural_findings_rate_idx ON public.structural_findings USING btree (scan_run_id, jurisdiction_code, era, check_name);

CREATE INDEX structural_findings_version_idx ON public.structural_findings USING btree (version_id);

CREATE UNIQUE INDEX uq_versions_law_lang_sha ON public.versions USING btree (law_id, language, source_sha256) WHERE (source_sha256 IS NOT NULL);

CREATE INDEX versions_acquis_chapter_idx ON public.versions USING btree (acquis_chapter) WHERE (acquis_chapter IS NOT NULL);

CREATE INDEX versions_embedded_at_idx ON public.versions USING btree (embedded_at) WHERE (embedded_at IS NULL);

CREATE INDEX versions_expression_date_idx ON public.versions USING btree (expression_date);

CREATE INDEX versions_law_id_idx ON public.versions USING btree (law_id);

CREATE INDEX versions_parent_id_idx ON public.versions USING btree (parent_version_id) WHERE (parent_version_id IS NOT NULL);

CREATE INDEX versions_repaired_at_idx ON public.versions USING btree (repaired_at) WHERE (repaired_at IS NOT NULL);

CREATE INDEX versions_reviewed_at_idx ON public.versions USING btree (reviewed_at) WHERE (reviewed_at IS NOT NULL);

CREATE TRIGGER events_enforce_append_only BEFORE DELETE OR UPDATE ON public.events FOR EACH ROW WHEN ((pg_trigger_depth() = 0)) EXECUTE FUNCTION public.enforce_events_append_only();

CREATE TRIGGER examinations_no_update BEFORE UPDATE ON public.examinations FOR EACH ROW EXECUTE FUNCTION public.examinations_no_update();

CREATE TRIGGER jurisdictions_set_updated_at BEFORE UPDATE ON public.jurisdictions FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TRIGGER laws_set_updated_at BEFORE UPDATE ON public.laws FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TRIGGER page_read_disputes_no_update BEFORE UPDATE ON public.page_read_disputes FOR EACH ROW EXECUTE FUNCTION public.page_read_disputes_no_update();

CREATE TRIGGER provisions_set_updated_at BEFORE UPDATE ON public.provisions FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TRIGGER sections_set_updated_at BEFORE UPDATE ON public.sections FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TRIGGER versions_enforce_immutable BEFORE DELETE OR UPDATE ON public.versions FOR EACH ROW WHEN ((pg_trigger_depth() = 0)) EXECUTE FUNCTION public.enforce_versions_immutable();

ALTER TABLE ONLY public.adjudications
    ADD CONSTRAINT adjudications_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.adjudications
    ADD CONSTRAINT adjudications_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.amendment_effects
    ADD CONSTRAINT amendment_effects_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.cross_references
    ADD CONSTRAINT cross_references_source_provision_id_fkey FOREIGN KEY (source_provision_id) REFERENCES public.provisions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.cross_references
    ADD CONSTRAINT cross_references_target_law_id_fkey FOREIGN KEY (target_law_id) REFERENCES public.laws(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.cross_references
    ADD CONSTRAINT cross_references_target_provision_id_fkey FOREIGN KEY (target_provision_id) REFERENCES public.provisions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.cross_references
    ADD CONSTRAINT cross_references_target_section_id_fkey FOREIGN KEY (target_section_id) REFERENCES public.sections(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.examinations
    ADD CONSTRAINT examinations_finding_id_fkey FOREIGN KEY (finding_id) REFERENCES public.findings(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.feedback
    ADD CONSTRAINT feedback_finding_id_fkey FOREIGN KEY (finding_id) REFERENCES public.findings(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.feedback
    ADD CONSTRAINT feedback_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES public.provisions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.feedback
    ADD CONSTRAINT feedback_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.finding_notes
    ADD CONSTRAINT finding_notes_finding_id_fkey FOREIGN KEY (finding_id) REFERENCES public.findings(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.findings
    ADD CONSTRAINT findings_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES public.provisions(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.findings
    ADD CONSTRAINT findings_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.law_links
    ADD CONSTRAINT law_links_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.laws
    ADD CONSTRAINT laws_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.lens_run_directives
    ADD CONSTRAINT lens_run_directives_directive_law_id_fkey FOREIGN KEY (directive_law_id) REFERENCES public.laws(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.lens_run_directives
    ADD CONSTRAINT lens_run_directives_lens_run_id_fkey FOREIGN KEY (lens_run_id) REFERENCES public.lens_runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.lens_runs
    ADD CONSTRAINT lens_runs_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.lifecycle_events
    ADD CONSTRAINT lifecycle_events_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.page_read_disputes
    ADD CONSTRAINT page_read_disputes_page_read_id_fkey FOREIGN KEY (page_read_id) REFERENCES public.page_reads(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.page_reads
    ADD CONSTRAINT page_reads_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.page_reads
    ADD CONSTRAINT page_reads_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.provision_embeddings
    ADD CONSTRAINT provision_embeddings_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES public.provisions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_section_id_fkey FOREIGN KEY (section_id) REFERENCES public.sections(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.registry_works
    ADD CONSTRAINT registry_works_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.registry_works
    ADD CONSTRAINT registry_works_law_id_fkey FOREIGN KEY (law_id) REFERENCES public.laws(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.repair_proposals
    ADD CONSTRAINT repair_proposals_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.repair_proposals
    ADD CONSTRAINT repair_proposals_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.run_artifacts
    ADD CONSTRAINT run_artifacts_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_parent_run_id_fkey FOREIGN KEY (parent_run_id) REFERENCES public.runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.scheme_matches
    ADD CONSTRAINT scheme_matches_lens_run_id_fkey FOREIGN KEY (lens_run_id) REFERENCES public.lens_runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT sections_parent_section_id_fkey FOREIGN KEY (parent_section_id) REFERENCES public.sections(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT sections_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.structural_findings
    ADD CONSTRAINT structural_findings_law_id_fkey FOREIGN KEY (law_id) REFERENCES public.laws(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.structural_findings
    ADD CONSTRAINT structural_findings_scan_run_id_fkey FOREIGN KEY (scan_run_id) REFERENCES public.runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.structural_findings
    ADD CONSTRAINT structural_findings_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.translation_runs
    ADD CONSTRAINT translation_runs_source_version_id_fkey FOREIGN KEY (source_version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.translation_runs
    ADD CONSTRAINT translation_runs_target_version_id_fkey FOREIGN KEY (target_version_id) REFERENCES public.versions(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.version_source_texts
    ADD CONSTRAINT version_source_texts_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.version_unit_embeddings
    ADD CONSTRAINT version_unit_embeddings_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_law_id_fkey FOREIGN KEY (law_id) REFERENCES public.laws(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_parent_version_id_fkey FOREIGN KEY (parent_version_id) REFERENCES public.versions(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_source_sha256_fkey FOREIGN KEY (source_sha256) REFERENCES public.source_documents(sha256) ON DELETE SET NULL;

