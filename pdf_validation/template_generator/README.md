# Teach-Once Extraction Templates

**The problem this solves:** a fund can only be spot-checked against its PDF if
someone hand-writes a parser for that fund's statement layout. Today that lives
in `src/pdf_validation/text_fallback.py` as three fund-specific regex parsers,
roughly 100 lines of Python each, with fund names and industry lists hardcoded:

```python
# parse_audited_portfolio_from_text — SYN only
r"(?P<industry>Cybersecurity|Software|Technology|Healthcare|Financial)\s+(?P<country>USA|Israel|UK|...)"

# parse_company_subtotals_from_text — fund names hardcoded into the skip list
skip_prefixes = ["syn ventures", "imaginary venture", ...]
```

That is why 2 of 23 funds are supported. Scaling means 23 bespoke parsers, each
of which breaks when a GP changes their template.

**The approach:** an LLM reads *one* sample statement and proposes a
**declarative template** — ordered line rules with named-group regexes. A human
reviews it once. After that, every quarterly run is deterministic with no LLM
involved.

```
propose   LLM, once per fund        ->  draft/
review    human, once               ->  (reads evidence, checks numbers)
approve   human, once               ->  approved/
run       deterministic, quarterly  ->  no LLM, no API key, no network
```

---

## Why the template is data, not generated code

The LLM never writes Python. It emits JSON describing line patterns:

```json
{
  "rule_id": "company_subtotal",
  "kind": "company_subtotal",
  "pattern": "^\\$\\s*(?P<cost>[\\d,]+)\\s+\\$\\s*(?P<fair_value>[\\d,]+)\\s+\\$\\s*(?P<unrealized_gain_loss>[\\d,]+)$",
  "notes": "Amounts sit on the line beneath the company name."
}
```

This matters for three reasons:

- **Auditable.** A reviewer reads a regex and its captured evidence, not
  generated code.
- **Deterministic.** Same PDF + same template always yields the same numbers.
  Nothing to re-run, nothing to drift.
- **Cheap.** One LLM call per fund, ever — not one per PDF per quarter.

---

## Workflow

### 1. Propose (the only LLM call)

```bash
cd FundHoldings/pdf_validation
python -m template_generator.cli propose --fund A41fefa --pdf path/to/statement.pdf
```

Requires `ANTHROPIC_API_KEY` and `pip install anthropic`. No API key? Use
`--backend manual`: it writes the prompt to a file, you paste the model's reply
back, and re-run.

Schedule pages are auto-detected; override with `--pages 6,7,8`.

The proposal is rejected outright if it fails structural validation — unknown
capture fields, invalid regex, a `company_subtotal` rule that doesn't capture
both `cost` and `fair_value`, and so on. Nothing is written in that case.

### 2. Review

```bash
python -m template_generator.cli review --template A41fefa_statement --pdf path/to/statement.pdf
```

This is the step that matters. It prints every rule, then the **evidence** — the
actual PDF lines each rule matched and what it pulled out of them:

```
  (p2) Northwind Robotics Technologies, Inc. Technology United States
       -> {'company_name': 'Northwind Robotics Technologies, Inc.', 'industry': 'Technology'}
  (p2) $ 9,399,996 $ 13,466,158 $ 4,066,162
       -> {'cost': '9,399,996', 'fair_value': '13,466,158'}
```

Your job: open the PDF, spot-check a few of these lines. If the numbers match,
the template is right.

Add `--show-unmatched` to see lines no rule matched.

### 3. Approve

```bash
python -m template_generator.cli approve --template A41fefa_statement \
    --reviewer "Kyulli" --pdf path/to/statement.pdf
```

Records who approved it and what the checks said. Refuses to promote a template
that failed its guardrail (override with `--force`, which is recorded in the
template).

### 4. Run, every quarter after that

```bash
python -m template_generator.cli run --fund A41fefa --pdf path/to/new_quarter.pdf \
    --out extracted.jsonl
```

No LLM. No API key. No network. Just the approved regexes.

---

## Guardrails

Generated templates get no more trust than any other parser.

**Arithmetic.** Every extracted row is checked against the statement's own
identity, `Fair Value − Cost = Unrealized Gain`, wherever the schedule reports
all three. A template that reads columns in the wrong order fails loudly:

```
  checks: FAIL
    arithmetic checks   : 5 run, 4 failed
    MISMATCH 'Northwind Robotics Technologies, Inc.':
      read Cost=13,466,158 FV=9,399,996 -> implies gain -4,066,162
      but statement reports 4,066,162
```

**Coverage.** The share of schedule lines that matched some rule. A sharp drop
between quarters is the signal that a GP changed their layout and the template
needs regenerating. Floor is 35% — audited statements legitimately contain a lot
of prose that no extraction rule should match.

**Human promotion.** Drafts are invisible to the runtime. Someone has to look at
the evidence and promote. This mirrors how `vendor_mapping_registry.json`
already gates which funds may be compared at all.

### Known limits

- The arithmetic check **cannot** catch a Cost/Fair Value swap when the two are
  equal (a position held at cost). Verified: 4 of 5 rows caught in the swap
  test, the fifth had `cost == fair_value`.
- It cannot catch an error where all three numbers are read from the wrong row
  but are internally consistent. Evidence review is the defence there.
- A template that extracts *some* companies correctly and silently drops others
  will pass arithmetic. Check `company_count` against the PDF.

Because of these, arithmetic is a **necessary, not sufficient** condition.
Evidence review is not optional.

---

## Files

| File | Role |
|---|---|
| `schema.py` | Template structure, validation, allowed capture fields |
| `page_reader.py` | PDF → text lines; shared by generator and replay so they can't diverge |
| `propose.py` | Prompt construction, response parsing, evidence collection (only LLM caller) |
| `backends.py` | Pluggable LLM backends: `anthropic`, `manual`, `echo` |
| `replay.py` | **Deterministic extraction.** The quarterly path. No LLM. |
| `approval.py` | Arithmetic guardrail, coverage check, draft → approved promotion |
| `cli.py` | `propose` / `review` / `approve` / `run` / `list` |

Templates are stored in `configs/generated_templates/{draft,approved}/`.

Nothing under `src/pdf_validation/` is modified by this package.

---

## Integration

`replay()` returns rows in exactly the shape
`src/pdf_validation/text_fallback.py::_company_row` produces, so its output
drops into the existing reconciliation and vendor-comparison stages without
changing either. Two extra fields are added for traceability:

- `extracted_by_rule` — which template rule produced this row
- `source_line` — the exact PDF line it came from

When a vendor comparison later flags a mismatch, these turn "the numbers
disagree" into "this rule read this line and got this value," which is what the
diagnosis work needs.

Wiring this into `cleaning_pipeline/pdf_check.py` as a third fallback tier
(after Camelot and pdfplumber) is the natural next step, and is not done yet.

---

## Cost

One call per fund, roughly 3-8k tokens of statement text. Twenty funds is a
one-time cost of a few dollars. Quarterly runs cost nothing — no LLM is invoked.

Regeneration is only needed when a GP changes their statement layout, which the
coverage metric will flag.

---

## Status

The module runs end to end and is verified against a synthetic statement that
reproduces the diagnosed failure mode (wrapped company names, no ruling lines).
Verified: page detection, proposal parsing, structural validation, deterministic
replay, arithmetic guardrail catching a column swap, and `approve()` refusing a
failed template.

**Not yet done:** validated against the real fund PDFs. They were not in the
mounted folder when this was built. The first real test should be A41fefa — it
sits on both the escalation list (82.3% completeness) and the Deal Status
exception list, so a working PDF cross-check there directly answers whether
those Phase 1 flags are vendor extraction errors or genuine GP reporting gaps.
