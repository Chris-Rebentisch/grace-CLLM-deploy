# GrACE — Product Definition

**Status:** Canonical
**Last updated:** 2026-05-30
**Companion docs:** GrACE-Roadmap.md (phase plan), GrACE-Backlog.md (chunk index), GrACE-Decisions.md (D-series registry), GrACE-Doc-Map.md (index of all docs)

---

## 1. What GrACE Is

GrACE is the Graph as Auditable Context Engine. It is an audit-grade rationale memory layer for regulated, document-heavy workflows where exceptions, precedents, and approvals matter. It is built first for buyers whose primary requirement is a defensible audit trail and whose data cannot leave their network.

GrACE is two things that must be understood separately to be built correctly. It is a domain-agnostic tool system — the nine-module stack that discovers, builds, manages, refines, and operates knowledge-graph ontologies. And it is a domain-specific work product — the ontology each deployment produces, governing what that deployment is permitted to know. The tool builds the knowledge. The ontology defines the boundaries. The same tool produces different ontologies for different domains; that separation is what makes the system deployable across industries without rebuilding it.

The architecture compresses documents, decision traces from operational systems, and filtered communications into a knowledge graph governed by an ontology. Every claim in the graph carries provenance, a confidence score, and temporal validity. Natural language is regenerated from the graph on demand. Nothing is stored without evidence; nothing is recalled without a source. The engine is horizontal infrastructure; the visible product at any deployment is a single vertical workflow served by a vertical ontology module.

## 2. The Wedge

GrACE is not a general workplace AI assistant, an enterprise search tool, or a category competitor to Palantir. It claims a narrower position: provenance-first, ontology-governed, airgap-default graph memory for regulated buyers whose primary requirement is a defensible audit trail and a measurable productivity gain. Inside that position the architecture is differentiated; outside it the platform makes no claim.

The buyer GrACE is built for meets all five of the following criteria. Meaningful fit exists for buyers who meet three or four. Senior adjudicators, partners, trust officers, claims reviewers, and lead underwriters who carry a high hourly rate and a high opportunity cost on every hour spent reconstructing rather than deciding. Decisions whose inconsistency carries real institutional cost — audit findings, bad-faith exposure, regulatory action, beneficiary disputes, litigation. Document-heavy precedent where rationale chains accumulate across years of memos, approvals, and policy interpretations and are not findable in any one document. Mandatory citation and provenance, where the regulator or auditor requires every system answer to trace back to a primary source or a ratified human approval. And a weak tolerance for cloud-only AI, whether by regulation, contract, or risk posture.

Inside that five-criterion intersection, the strongest near-term commercial match is specialty insurance claims and underwriting. Boutique legal matter memory and trust administration follow. Wealth advisory and organization serve as a proving ground for the trust-administration ontology module rather than as a primary external commercial wedge. The underlying capabilities — ontology-governed extraction, per-claim provenance, source-linked answers — are general enough to support hybrid and cloud-native deployments where audit is one requirement among several. The platform wins in its primary wedge if it owns auditable rationale capture for regulated, document-heavy domains better than any incumbent. It does not need to displace Palantir from operational ontology, Glean from workplace context, or AWS from managed graph-grounded retrieval to succeed inside the wedge.

## 3. The Compression Engine

Most current memory systems treat a knowledge graph as a retrieval strategy that sits alongside vector search. GrACE reframes the relationship: the graph is not an index over the text, the graph replaces the text as the primary storage format. Compression is extraction; decompression is regeneration; the ontology is what makes both well-defined operations rather than open-ended LLM behavior.

Compression is what happens at write time. A document is read against the relevant ontology module and produces, on average, five to ten entities and ten to twenty triples — totaling a small fraction of the original token volume. The compression is fact-preserving in a specific sense: every entity, relationship, dollar amount, date, signatory, and timestamp is retained as graph structure with provenance, confidence, and temporal metadata attached. What is discarded is syntax — exact wording, paragraph order, conversational flow. This is decision-useful structured retention with explicit tradeoffs. The term "lossless" is reserved for the facts, not the bits.

Decompression is what happens at query time. The relevant subgraph — typically five to ten entities and eight to fifteen relationships — is retrieved, serialized into natural language, and assembled into the LLM prompt. Total prompt context drops sharply versus chunk-retrieval systems while every claim in the response remains verifiable against its primary source through a single click. The ontology is the boundary that makes this tractable: an auditor asking what the system knows and how it knows it gets a structured answer at three levels. The ontology defines what the system is capable of knowing. The graph holds what the system actually knows. The provenance layer shows how the system came to know each individual fact. The three-level answer is constructible from a single query in seconds, and that is the operational meaning of audit-grade.

## 4. The Nine Modules

GrACE decomposes into nine modules. The first seven are the engine — the modules that do the work of turning documents into a governed, queryable graph and turning queries back into grounded natural-language responses. The last two are the surfaces through which the engine is exposed: an MCP server for AI-agent consumers and a web frontend for human users. Each module has defined inputs, defined outputs, a configuration schema governing its behavior, quality metrics for monitoring, and a feedback loop for tuning. The modules communicate through defined interfaces; no module reaches into another module's internals.

### 4.1 Discovery

The Discovery module turns raw data and human expertise into a proposed ontology schema. Document processing handles binary formats. Competency-question generation runs in parallel passes — top-down, bottom-up, negative-evidence, and middle-out — and merges results through a three-tier pipeline. Schema seeding draws on reference standards (FIBO, LKIF, Schema.org, PROV-O) with industry-specific selection. Three-pass schema triangulation produces a candidate schema; a four-input merge with a CQ coverage matrix consolidates it. Edge property detection classifies relationships as simple, attributed, or reified. The output is a SeedSchema ready for human Guided Review.

### 4.2 Ontology Management

The Ontology Management module stores, versions, and evolves the production ontology. The schema store is append-only with a SHA-256 hash chain, RFC 6902 diffs, and OM4OV-style change categorization. The Guided Review interface presents nine decision types with real-time CQ-impact preview. The Adaptive Evolution agent monitors six classes of failure signal and queues schema-change proposals for human review. The CQ Test Runner verifies competency questions against the production schema and gates promotion below a configurable pass-rate threshold. The agent never modifies the schema directly until the Earned Autonomy framework has measured a calibration record sufficient to grant auto-commit rights on specific change classes.

### 4.3 Extraction (Compression)

The Extraction module converts unstructured text into ontology-grounded graph triples. LLM-based extraction emits structured Pydantic output with per-triple confidence scoring. A second-model verification pass classifies each triple as supported, refuted, or insufficient with evidence spans cited. A three-tier entity resolution cascade — exact name match, embedding similarity via a native vector index (server-side ANN over persisted entity embeddings), and LLM disambiguation for the ambiguous band — assigns canonical identifiers. Temporal tagging normalizes dates and validity windows. A pre-write constraint validator enforces ontology compliance with three severity levels. Every accepted triple writes to the graph with an Extraction_Event provenance vertex linking it to its source span. The ontology is injected per-module rather than as the full schema, which materially improves extraction quality on complex domains.

### 4.4 Graph Management

The Graph Management module maintains the health, structure, and performance of the graph database. ArcadeDB is the storage substrate, accessed through a thin REST client. Schema mapping translates the ontology into graph types via a DDL generator. Schema sync orchestrates incremental migration through OM4OV-style diffs and KGCL-language change commands. Graceful deprecation never drops types or properties. Migration_Event, Correction_Event, and Extraction_Event provenance vertices follow the PROV-O pattern. Static and dynamic indexing support analytics. Orphan detection, duplicate detection, temporal windowing, and namespace management for federated child graphs round out the operational surface. grace_id (UUID4) is the external identifier on every vertex and edge; ArcadeDB internal RIDs never leave the module.

### 4.5 Retrieval

The Retrieval module finds the most relevant subgraph for any query through four parallel strategies. Graph traversal uses OpenCypher variable-length matching with configurable depth. Semantic search uses 768-dimensional embeddings with cosine similarity. BM25 keyword search maintains a persistent index. Temporal filtering operates either on graph results or as a separate fusion strategy. Reciprocal Rank Fusion merges the four ranked lists at k=60. A cross-encoder reranker on CPU produces the final ordering. Subgraph serialization for LLM consumption is offered through three serializers — template-based, Turtle/RDF, and LLM-generated prose — selected by query intent. An iterative retrieval mode adds a second round of graph and semantic enrichment when initial results are thin.

### 4.6 Regeneration (Decompression)

The Regeneration module converts subgraphs back into natural language for LLM consumption. Prompt assembly enforces an explicit token budget across system prompt, serialized context, and user query. Response synthesis runs through the shared LLM provider abstraction in prose mode. Claim-span detection annotates substantive factual claims with one of four certainty bands — high, medium, low, insufficient evidence — at claim-span granularity. Numeric confidence scores never reach the user surface; the certainty bands are the contract. The phase_state parameter (prepare, open, structure, clarify, close) shifts response style to match the active Elicitation Protocol phase. Deterministic prompt assembly: same inputs produce byte-identical prompts.

### 4.7 Analytics

The Analytics module monitors the health and performance of every other module and emits the signals that feed the Adaptive Evolution agent. OpenTelemetry instrumentation runs across the engine. Prometheus scrapes the metrics endpoint. Grafana hosts nine dashboards — module-specific health views plus signals, correlations, eval-quality, and ingestion dashboards and a system-overview entry point. The metric catalog is locked and contract-tested at CI; new metrics require a co-committed registry edit. North-star measures — compression ratio, decompression faithfulness, MINE-1 retention — sit alongside per-module operational metrics. The MINE-1 sampling harness measures fact retention against the source documents. A signal-generation pipeline emits the six failure-signal classes that drive ontology evolution. A cross-module correlation engine identifies root causes that no single module can see through five pattern detectors reading from the signal substrate and curated raw metrics; threshold and trend-based alerting via Grafana Unified Alerting surfaces operational degradation early.

### 4.8 MCP Ecosystem

The MCP Ecosystem module exposes GrACE to Model Context Protocol hosts — primarily Claude Desktop in current use, with broader host compatibility tracked. The server runs over stdio transport only; no TCP bind. The tool catalog is curated and read-only by contract: thirteen tools across retrieval, graph inspection, ontology lookup, CQ summary, and capability metadata. Tool descriptions are static literals enforced by a forbidden-pattern AST scan. The adapter calls FastAPI over local HTTP and inherits any auth middleware applied to the underlying routes. Airgap posture is enforced at startup and before every outbound request through literal hostname checks and loopback verification. The tool catalog grows by spec amendment, not by drift; route allowlists are contract-tested. The MCP server is the channel through which AI agents consume GrACE; it is not a substitute for the human Frontend.

### 4.9 Frontend / Web UI

The Frontend module is the human surface. A modern web stack with strict TypeScript hosts a chat interface, a graph viewer, a retrieval inspector, a Guided Review dual-panel with CQ canvas, quarantined claim review, source selection, and LLM settings. The frontend consumes the engine over typed HTTP. Source linkback rendering follows directly from the provenance layer: every claim in a response is one click from its primary source. Numeric confidence scores never reach the DOM; certainty bands and progress indicators are the visual vocabulary. The chat surface owns the Open and Close phases of the Elicitation Protocol; the Guided Review surface owns Structure and Clarify with three v1 instruments (Laddering, Card Sort, Teach-Back). Telemetry events emit to a local PostgreSQL append-only table; nothing leaves the network.

## 5. The Four-Layer Graph Architecture

The graph is organized into four layers, each addressing a different question about the data. The four layers are partitioned into two access zones.

The Domain layer holds the actual factual content of the deployment — entities, relationships, and properties as defined by the production ontology. This is the layer answering "what is the case." When a regulator asks what the system knows, the Domain layer is the answer.

The Temporal layer attaches validity windows to every claim through valid_from, valid_to, and extracted_at timestamps. The graph answers "what was true on a given date" natively. Temporal data is treated as a first-class peer of the Domain content, not as an afterthought property; the architecture supports retroactive correction (a fact backdated to its true valid_from after extraction) without rewriting history.

The Provenance layer holds Extraction_Event, Correction_Event, and Migration_Event meta-entities aligned to the W3C PROV-O standard. Every triple in the Domain layer carries an Extraction_Event vertex linking it to the source document, the source span, the model that performed the extraction, the confidence score, the timestamp, and any verification or human ratification that followed. This is the layer answering "how do we know."

The Governance layer holds the human-decision record: CQ authoring, Guided Review outcomes, Adaptive Evolution proposals and dispositions, Earned Autonomy actions, sensitivity classifications. This is the layer answering "who decided." Governance data is private by default and never surfaces in shared retrieval.

The two access zones partition the four layers. The shared zone (Domain plus Temporal) is governed by Role-Based Access Control. The private zone (Provenance plus Governance) is governed by whitelist. Layer-selective federation between mother and child graphs lets a parent organization publish ratified ontology to subsidiaries without exposing private decision history.

## 6. The Reconciliation Layer

Standard knowledge graph systems try to produce one canonical ontology that the organization agrees on. Every real organization has multiple simultaneous ontologies held by different stakeholders. The Reconciliation Layer architecturally accepts that reality. It produces one evidence-grounded base layer (the document-derived graph) and surfaces multiple interpretive views on top of it. It is the architecturally distinctive capability inside the wedge — the most defensible differentiation against open-source competitors retrofitting their stacks and enterprise incumbents whose architectures didn't anticipate it.

Three artifacts make the capability operational. Perception–Evidence Gap Reports show, per executive and per review session, which approved concepts have strong document evidence, which have weak or no evidence, and which evidence the executive did not prioritize during review. Cross-Executive Divergence Maps surface disagreements between two reviewers over the same segment with the evidence attached for each side; the system does not adjudicate. Documented Reality Reports give a periodic neutral descriptive account of what the documents actually describe, untouched by interpretation, available on demand.

The political design is as critical as the technical design. Surfacing the gap between organizational belief and document evidence is, on its face, dangerous. The architecture mitigates the risk on three axes. Framing is observational rather than evaluative — the system reports what is, not what should be. Reports are participatory — each stakeholder sees their own evidence-grounded view first, comparison views second, never the inverse. No reconciliation report is published outside the originating reviewer's session without that reviewer's consent. Reconciliation outputs are also subject to the sensitivity classification gate (see §8); privileged or sensitive content is excluded unless explicitly authorized. The capability is grounded in established organizational-learning research — Argyris and Schön on espoused theory versus theory-in-use; subsequent work on defensive reasoning in digital management tools.

## 7. The Four Input Streams

Decision rationales reach the graph through four parallel input streams. Each contributes different volumes and different fidelity. Human review is one of the four — a quality gate, not the throughput driver. Every stream passes through the sensitivity-classification gate (see §8) before any extraction occurs.

Documents are the largest volume source. Contracts, memos, reports, filings, trust instruments, partnership agreements, internal policy documents. The Extraction module reads each document against the relevant ontology module, produces typed entities and relationships with per-claim confidence and provenance, resolves entities against a canonical registry, and writes to the graph. Throughput is bounded by inference cost, not by human time. This is where most decision context already lives in writing — and where most of the graph builds itself.

Operational decision traces are pulled from operational systems through the federated mother-child connector pattern. Federation here means multiple connected graphs that share an ontology backbone but maintain separate ownership and access controls. Each operational system gets its own connector, its own child ontology extending the mother ontology, and its own sync schedule. The mother graph holds the cross-system bridges and the canonical entity registry.

Communications are the noisy stream — email, chat exports, meeting transcripts, voicemails — filtered through the graph itself. Most current tools attempt to summarize this content in isolation, which is why they fail. With the graph as a filter, communication ingestion becomes a four-tier triage: deterministic noise rejection, entity-mention lookup against the canonical registry, ontology-relevance check against the ratified schema, and an LLM semantic filter on what survives the cheap layers. The cheap layers do most of the work. What enters the graph is the small fraction of communication that actually contains new rationale not already present in documents or operational traces.

Human review is the quality gate. The expert reviewer's role is to validate, disambiguate, and ratify what the other three streams produced — to author the initial ontology during onboarding, to adjudicate disagreements the agent surfaces, and to ratify schema changes the Adaptive Evolution agent proposes. The five-phase Elicitation Protocol makes the reviewer's time productive. Onboarding is intensive expert time over the initial ontology pass. Steady-state maintenance is structured as a short weekly review against whatever the agent has flagged. The Earned Autonomy framework lets the agent auto-commit changes in classes where its calibration record is strong, so the human reviews only what genuinely needs review.

## 8. The Sensitivity Gate

A graph that captures decision rationale also creates evidentiary surface area. A casual ratification of a sensitive entity, written into a queryable, timestamped provenance chain, becomes discoverable in ways the customer did not intend. The architecture treats this as a first-class design problem rather than a deployment afterthought.

Input streams pass through a customer-specified, customer-authored sensitivity classification gate before extraction. Content tagged sensitive is held outside the production graph, never surfaces as an elicitation question, and is excluded from Reconciliation reports unless explicitly authorized. The classification rules themselves live in the Governance layer — versioned, audited, and reviewable by counsel.

The gate is a first-class product component, not a deployment configuration. It is customer-authored. It is governance-layer-versioned. It has its own approval workflow and its own audit trail. It integrates into the elicitation protocol such that sensitive content cannot surface as an elicitation question. General Counsel signs off on the classification rules during onboarding and reviews them on a configurable cadence. Competitors with permissions and access controls treat sensitive content as a query-time filter; here the gate runs at extraction time and prevents the content from entering the production graph at all. That is a structural commitment, not a configuration toggle.

## 9. The Tool / Work-Product Separation

The GrACE module stack is the tool. The ontology is the work product. This is the foundation of the entire architecture and the reason the platform is deployable across industries without rebuilding it.

The tool system is domain-agnostic. The same nine modules — Discovery, Ontology Management, Extraction, Graph Management, Retrieval, Regeneration, Analytics, MCP Ecosystem, Frontend — operate identically regardless of the deployment domain. They are configured, not coded, per deployment.

The ontology is domain-specific. For specialty insurance, it defines Claim, Exception, Precedent, Policy_Clause, Reviewer, Approval_Event. For boutique legal practice, it defines Matter, Brief, Citation, Court, Jurisdiction, Strategic_Memo. For trust administration, it defines Trust_Instrument, Beneficiary, Distribution, Grantor_Intent_Memo, Amendment. Different domains, different ontologies, same machine.

The ontology is incremental through module composition — vertical-specific modules compound across customers in the same domain — and continuously evolving within each deployment as the document corpus reveals concepts the schema does not yet represent. Ontology evolution is a first-class capability, not a periodic upgrade event. A vertical-specific module that has been refined across two or three deployments seeds the next deployment in that vertical with a substantially built ontology and compresses the services component sharply. The expected ratio runs services-heavy in year one and software-leading in subsequent years as the module library matures.

## 10. Design Principles

Six principles define GrACE's position between fully automated memory systems (which are fast to deploy but prone to schema drift, silent errors, and uncontrollable memory corruption) and fully manual ontology engineering (which gives humans complete control but cannot adapt to changing data).

The human defines the boundaries. Every entity type and relationship type in the production ontology has explicit human approval. The LLM discovers and proposes; the human decides. This is the fundamental difference from systems where the agent manages its own memory autonomously.

The agent proposes, the human disposes. The Adaptive Evolution agent never modifies the schema directly until the Earned Autonomy framework has measured a calibration record sufficient to grant auto-commit rights on specific change classes. Until then it queues proposals with evidence for human review. Tier 3 changes — hierarchy restructuring, type deprecation, domain or range changes — are always human-reviewed regardless of calibration.

Consistent errors are correctable; random errors are invisible. A human-designed ontology has consistent blind spots that are detectable and fixable. An LLM-inferred schema has random errors that are harder to detect. The three-phase lifecycle — Discovery, Guided Review, Adaptive Evolution — mitigates both.

The ontology is the cognitive architecture. What the ontology can represent is what GrACE can remember. A type that exists but is too broad produces noisy, undifferentiated nodes. A type that is missing produces permanent information loss. The ontology is not metadata; it is the system's theory of its domain.

Everything is airgapped, everything is auditable. All LLM inference runs locally by default; cloud providers are selectable by configuration when the customer's risk posture allows. No data leaves the network without explicit configuration. Every schema change, extraction event, retrieval score, and human decision is recorded with timestamps and provenance for regulatory compliance.

The tool and the work product are separate. The module stack is domain-agnostic infrastructure. The ontology is the domain-specific output. This separation enables deployment across industries without rebuilding the system, and it is the foundation of the services-to-software trajectory described in §9.

## 11. Pointers to Deep-Reference Documents

Each topic in this document is elaborated by a deep-reference document or a normative spec. Do not duplicate content from those documents here; refer to them by name and let the Doc Map (GrACE-Doc-Map.md) be the index.

Four-Layer Graph Architecture detail: GrACE-Four-Layer-Graph-Architecture.md.
Reconciliation Layer specification: GrACE-Reconciliation-Layer.docx.
Earned Autonomy framework: GrACE-Earned-Autonomy-System.docx.
Elicitation Protocol normative spec: GrACE-Elicitation-Protocol.docx.
Sensitivity Gate (shipped in Phase 5.5, Chunk 43): see Roadmap §7 and security-posture.md §27.
Communications ingestion: GrACE-Communication-Ingestion.md.
Federated mother-child architecture: Federated-KG-Architecture-Research.docx.
Buyer-facing positioning: GrACE-Technology-Thesis-May-2026.docx.

The Roadmap (GrACE-Roadmap.md) maps these capabilities onto build phases and chunks. The Backlog (GrACE-Backlog.md) is the per-chunk index. The Decisions registry (GrACE-Decisions.md) holds the D-series log. The Doc Map (GrACE-Doc-Map.md) is the index of all docs in this directory.
