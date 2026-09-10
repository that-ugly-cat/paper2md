# paper2md — User Guide

paper2md turns a scientific PDF into clean prose text. One file in, text out — reading order reconstructed, tables and running heads and footnotes dropped, the bibliography cut. It does one thing and keeps no projects, no corpora and no history: it exists to hand a clean corpus to whatever you analyse with next.

---

## 1. Getting started

There is nothing to set up. The page at [paper2md.borant.eu](https://paper2md.borant.eu) needs **no account and no login** — you open it and convert. The only part of the service behind Borant ID is the admin panel, and the only thing that lives there is the list of API keys.

An **API key** matters in exactly two cases: it raises the upload cap from 10 MB to 50 MB, and it identifies a script that calls the service repeatedly. Keys are issued by hand from the admin panel — ask Spit. For a paper you convert in the browser, you do not need one.

## 2. Converting a paper

1. Drop a PDF on the box, or click to pick one. **One file at a time**, up to 10 MB without a key.
2. Set the two options (below).
3. **Convert**, and wait. A typical paper takes 10–30 seconds, because the pipeline runs a full document-layout model rather than pulling bytes out of the file — see §6 for why that is the price of getting two-column print layouts right.
4. Read the result in the box, then **Download .md** or **Download .txt**.

**Remove references** (on by default) drops the bibliography and everything after it. Author names, journal titles and DOIs are noise for text mining, embeddings and LLM prompts. Turn it off when you want the bibliography — to check or count citations, or to read the paper as a paper.

**Remove back matter** (off by default) also drops acknowledgements, funding, author contributions, conflict-of-interest and data-availability statements. It is off because it is the more aggressive cut: a data-availability or ethics statement occasionally holds something you want.

**The two options share one cut point, and this is the thing to know before you tick the second one.** Extraction stops at the *first* heading that matches either enabled set — it does not remove sections one by one. Many journals print acknowledgements *before* references, so enabling back matter on such a paper also takes the bibliography with it, even with "Remove references" switched off. Confirmed on a real paper, not deduced.

Two more facts about the run itself. The **progress bar is a time estimate, not progress** — conversion is a single synchronous call, so there is nothing to report per page. And the service runs **one conversion at a time**, deliberately, so it cannot starve the other tools on the same machine: with ten conversions already waiting you get "queue is full", and you retry. A conversion still running after five minutes is abandoned with a timeout. The counter under the result reads pages and seconds, where pages means *pages that contributed text that was kept*, not the page count of the file.

## 3. What comes out

- **`.txt`** — the clean prose, and the primary output. Paragraphs separated by blank lines, no markup, no tables, headings left in as plain lines. This is what the box on screen shows.
- **`.md`** — the same document with Docling's structure preserved: headings as headings, lists as lists, tables as tables.

**Both stop where the options say to stop** — tick "Remove references" and the bibliography is gone from the Markdown too. What still differs is everything the `.txt` drops to be prose: the Markdown keeps tables, figure structure and the rest of the document's shape, because that is the reason to download it instead of the text. So the choice is not "which one obeyed me", it is what you want to do next: prose for analysis, structure for reading.

Both files are built in your browser from the response — nothing is generated or kept server-side (§8).

## 4. What is left out, and why

Seven kinds of element are dropped before the text is assembled: **tables, figures, captions, formulas, page headers, page footers and footnotes**. Not because they are worthless, but because in a print-shaped PDF they are interleaved with the body: a running head or a footnote lands in the middle of a sentence, and a naive extraction hands you that sentence broken in half around it. If you need a table or an equation, read it in the PDF.

Publisher **marginal noise** goes the same way: lines that open with a copyright notice, `©` and a year, "distributed under", "exclusive licensee", "no claim to original", "creative commons", "downloaded from" or "downloaded on", plus any line consisting of nothing but a URL.

Where such an element interrupted a sentence, the two halves are **rejoined**: a block that does not end in sentence-final punctuation is glued to the next one when that one starts lowercase or with punctuation. Headings are never glued to anything, so section boundaries survive.

The abstract, the keywords and the section headings all stay.

## 5. What the cleanup pass fixes

The same pass runs over both outputs. Unicode is normalised (NFKC), soft hyphens and zero-width characters are removed, `fi`/`fl` ligatures are expanded, non-breaking spaces become spaces. Then three repairs that came out of real papers, not from theory:

- **Words hyphenated at a line break** are rejoined — `sophistica -tion` becomes `sophistication`. The pattern requires the space *before* the hyphen, which is how the layout model reports a line break, so genuine compounds (`grief-oriented`, `face-to-face`) are left alone.
- **Letter-spaced URLs** are reconstructed. Journals justify long URLs, and the text layer then reports them glyph by glyph (`h t t p s : / / d o i ...`) or in chunks (`https:/ /doi.or g/...`). Whitespace is collapsed only when the *domain* contains spaces, which no real domain can, so an ordinary URL sitting in a sentence is untouched.
- **DOIs keep their hyphens.** The cosmetic rule that turns a page range `122-142` into `122–142` used to eat DOI hyphens as well; every URL and `mailto:` is now shielded from punctuation normalisation for the whole pass.

## 6. Limits that belong to the PDF, not to the converter

**OCR is off**, so a scanned or image-only PDF comes back empty or nearly so — there is no text layer to read, and the layout model reconstructs reading order, it does not read pixels. The choice is deliberate: an OCR engine is weight the service does not carry until something actually needs it. The one-second test is to try selecting text in your PDF viewer; if you cannot select it, paper2md cannot read it.

**Some hyphens are already gone before the tool sees the file.** In long slugs — newspaper article URLs in reference lists, typically — certain PDFs encode internal hyphens as spaces in the text layer itself. Verified in the raw stream: those characters are not there, and nothing downstream can put them back. DOIs, the part that matters for citation, come out intact.

**Reading order is a model's judgement.** It is right on the ordinary two-column paper, which is the whole point of paying for it, but an unusual layout — boxed inserts, sidebars, poster-shaped pages — can still come out in the wrong order. Read the first and last paragraphs of the output before you trust the middle.

**And when the journal publishes XML, take the XML.** JMIR, PLOS and a handful of others expose structured full text where reading order is not a mystery and the metadata is simply there. For those articles this whole step is unnecessary.

## 7. Calling it from a script

`POST https://paper2md.borant.eu/convert`, multipart form. Exactly one of `file` (an upload) or `url` (an `http`/`https` address the server downloads itself) — both or neither is a `400`. Optional fields: `remove_references` (default `true`), `remove_end_matter` (default `false`), and `format`, which is `json` by default and `text` to get the plain text as the response body. `X-API-Key` raises the cap to 50 MB.

```bash
curl -F file=@paper.pdf https://paper2md.borant.eu/convert

curl -H "X-API-Key: p2m_..." \
     -F url=https://example.org/paper.pdf \
     -F remove_references=false -F format=text \
     https://paper2md.borant.eu/convert
```

The JSON body is `{"text": ..., "markdown": ..., "blocks": ..., "pages": ..., "seconds": ...}`. Failures come back as `{"detail": "..."}`: `400` for a file that does not start with a PDF header, `401` for a key that is invalid or revoked, `413` over the cap, `503` when the queue is full, `504` past the five-minute timeout. Retry a `503`; it means someone else's paper is converting.

paper2md has **no MCP surface** — unlike AutoCode, there is nothing here for an assistant to talk to. It is one HTTP endpoint.

## 8. Data protection

- The uploaded PDF is written to a **temporary directory for the duration of the request** and deleted when the request ends. There is no document store: the only thing the database holds is the list of issued API keys (name, email, note, key, last used).
- The conversion runs **on the server, start to finish**. There is no model API call, no external processor: the layout model is local. Nothing about your PDF leaves the machine.
- The one exception is `url=` in the API: there the server fetches the address you give it, so that outbound request comes from the server rather than from you.
- The page is open to anyone who has the address, which means there is no per-person audit trail of what was converted. Judge what you send accordingly: a PDF carrying personal data — a signed consent form, an appendix with participant details — does not belong on a public endpoint, transient processing or not.
- Whether you may run a given paper through a converter at all is usually a licence question rather than a data-protection one. A paywalled PDF stays under its publisher's terms after conversion.

## 9. Good practices

- **Convert one paper before you batch a hundred.** Read the opening and the closing paragraphs of the `.txt`: that is where a bad layout or a truncated cut point shows up.
- **Check where the acknowledgements sit** before enabling "Remove back matter", or you will lose the references you meant to keep (§2).
- **Keep the PDF as the object of record.** Quote and cite from the PDF; analyse from the text. The text is a derivative and it is lossy by design — no tables, no equations, no figures.
- **Name the output after the DOI or the citekey**, not after the publisher's download filename, so the corpus stays traceable back to the paper.
- **This output is an input.** It is the shape other tools want: a corpus for AutoCode, full text for a screening or extraction pass in LSSR, a claim's source for Contrarian, a prompt body for a model that would otherwise choke on two-column PDF soup. Clean text is not the deliverable — it is what makes the next step honest.
