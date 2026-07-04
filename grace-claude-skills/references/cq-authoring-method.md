# CQ authoring method (the "combined / A3" approach)

This mirrors the native GrACE `cq_generation.generation_mode: combined` mode so
Claude-authored CQs are interchangeable with native ones. The native engine made
ONE multi-perspective, rationale-first, few-shot call per document group covering all
docs. Claude does the same reasoning — better — without gpt-oss.

## Principles
1. **Schema-shaping, not fact-listing.** A CQ describes a question the *ontology*
   must answer. "What is Acme's address?" is a fact lookup — skip it. "What address
   properties must an Organization carry?" shapes the schema — keep it.
2. **One combined pass, four lenses.** In a single pass over a domain's corpus, cover:
   - **Top-down:** the major entity classes and their headline relationships.
   - **Bottom-up:** concrete fields actually present in the docs (named amounts,
     dates, identifiers) → these become properties.
   - **Middle-out:** cross-document and cross-domain links (the connective tissue,
     e.g. policy → insured entity, entity → owned property).
   - **Negative evidence:** integrity/validating questions — "does every X reference
     a Y?", "can an X exist without a Z?" → these force required links + constraints.
3. **Rationale-first.** For each CQ, state in one line which schema element it forces
   (a type, a relationship, a property, or a constraint). If you can't, the CQ is
   probably a fact lookup — drop it.
4. **Balanced coverage.** Use every document in the bundle, not just the longest ones.
   The corpus export already balances this for you.
5. **Compact, not exhaustive.** Target ~20–30 strong CQs per domain. The native merge
   will still cluster near-duplicates into a canonical set, but don't lean on it to
   clean up sloppy over-generation — one good CQ beats five rephrasings.

## cq_type mapping
| Need the CQ expresses                    | cq_type       | Schema effect              |
|------------------------------------------|---------------|----------------------------|
| Bound the domain / which classes exist   | SCOPING       | which types to include     |
| A core, always-present concept           | FOUNDATIONAL  | a primary type             |
| A link between two things                | RELATIONSHIP  | an edge (set richness_tier)|
| An attribute / temporal / quantity       | METAPROPERTY  | a property on a type/edge  |
| An integrity check / "must reference"    | VALIDATING    | required link / constraint |

## Worked mini-example (corporate_structure)
- SCOPING: "Which legal entity forms (LLC, trust, partnership, corp) appear as owners?"
  → include `Legal_Entity` with an `entity_form` property.
- RELATIONSHIP: "What percentage does each parent hold in each subsidiary?"
  → `owns_interest_in` edge, richness_tier=attributed (`ownership_percentage`).
- METAPROPERTY: "What jurisdiction was each entity formed in?" → `jurisdiction` prop.
- VALIDATING: "Does every subsidiary trace to an ultimate parent?" → required-link check.

## Provenance
import_cqs.py stamps every CQ with `source=HUMAN_AUTHORED` (these are operator-curated
— reviewed before import) and `metadata_extra.authoring_method="combined-a3"`. The
`rationale` you write is stored in metadata for review.
